# SPDX-License-Identifier: LGPL-2.1-or-later
# SolidWorks Import for FreeCAD - https://github.com/djbarrow/freecad_solidworks_plugin
# Written by Denis Joseph Barrow <barrow_dj@yahoo.com> with the help of Claude Code.
# If you find this project useful, please support it with a 10 euro tip
# via PayPal to barrow_dj@yahoo.com.

"""FreeCAD import plugin for SolidWorks parts and assemblies.

FreeCAD calls open() for File > Open and insert() for File > Import.
The conversion itself is done by the bundled sw2fc package.
"""

import os
import sys

import FreeCAD

_here = os.path.dirname(os.path.abspath(__file__))
if _here not in sys.path:
    sys.path.insert(0, _here)

from sw2fc import assembly as _asm          # noqa: E402
from sw2fc import cache as _cache           # noqa: E402
from sw2fc.convert import ConversionError, convert, _doc_name   # noqa: E402

PART_EXTS = (".sldprt", ".x_t", ".x_b", ".xmt_txt", ".xmt_bin")
ASM_EXTS = (".sldasm",)

# While open_folder() runs: problems are collected here for one summary
# instead of a dialog per file, and missing parts are looked for in the
# chosen folder instead of asking.
_batch_errors = None
_search_dirs = []


def _msg(text):
    FreeCAD.Console.PrintMessage("SolidWorks import: %s\n" % text)


def _warn(text):
    FreeCAD.Console.PrintWarning("SolidWorks import: %s\n" % text)


def _error(text):
    FreeCAD.Console.PrintError("SolidWorks import: %s\n" % text)
    if _batch_errors is not None:
        _batch_errors.append(text)
    elif FreeCAD.GuiUp:
        try:
            from PySide import QtGui
            QtGui.QMessageBox.warning(None, "SolidWorks import", text)
        except Exception:
            pass


def open(filename):
    """File > Open: create a new document named after the file."""
    base = os.path.splitext(os.path.basename(filename))[0]
    doc = FreeCAD.newDocument(_doc_name(base))
    doc.Label = base
    try:
        _import(filename, doc)
    except Exception:
        FreeCAD.closeDocument(doc.Name)   # do not leave an empty tab behind
        raise
    return doc


def insert(filename, docname):
    """File > Import: add the file to an existing document."""
    try:
        doc = FreeCAD.getDocument(docname)
    except NameError:
        doc = FreeCAD.newDocument(docname)
    FreeCAD.setActiveDocument(doc.Name)
    _import(filename, doc)
    return doc


def _import(filename, doc):
    ext = os.path.splitext(filename)[1].lower()
    try:
        if ext in ASM_EXTS:
            _import_assembly(filename, doc)
        else:
            _import_part(filename, doc)
    except (ConversionError, _asm.AssemblyError) as exc:
        _error("%s: %s" % (os.path.basename(filename), exc))
        raise
    doc.recompute()
    if FreeCAD.GuiUp:
        import FreeCADGui
        FreeCADGui.SendMsgToActiveView("ViewFit")


CACHE_OPTIONS = {"sew_tolerance": 0.01, "keep_duplicates": False}


def _convert(path):
    """[(label, shape, appearance)] for a part file, from the disk cache when
    this exact file was converted before."""
    hit = _cache.load(path, CACHE_OPTIONS)
    if hit is not None:
        bodies, warnings, st = hit
    else:
        results, log = convert(path, verbose=False, **CACHE_OPTIONS)
        apps = list(getattr(log, "appearances", []))
        apps += [None] * (len(results) - len(apps))
        bodies = [(label, shape, kind, app)
                  for (label, shape, kind), app in zip(results, apps)]
        warnings, st = log.warnings, getattr(log, "stats", {})
        _cache.store(path, CACHE_OPTIONS, bodies, warnings, st)
    for w in warnings:
        _warn(w)
    if st.get("failed_faces"):
        _warn("%s: %d of %d faces could not be rebuilt"
              % (os.path.basename(path), st["failed_faces"], st["faces"]))
    return [(label, shape, app) for label, shape, _, app in bodies]


def _add_bodies(doc, bodies, container=None, visible=True):
    objs = []
    for label, shape, app in bodies:
        obj = doc.addObject("Part::Feature", _doc_name(label))
        obj.Label = label
        obj.Shape = shape
        if container is not None:
            container.addObject(obj)
        _apply_appearance(obj, app, visible)
        objs.append(obj)
    return objs


def _import_part(filename, doc):
    bodies = _convert(filename)
    _add_bodies(doc, bodies)
    _msg("imported %s (%d bod%s)" % (os.path.basename(filename), len(bodies),
                                     "y" if len(bodies) == 1 else "ies"))


def _import_assembly(filename, doc):
    root = _asm.read_tree(filename)
    top = doc.addObject("App::Part", _doc_name(root.name))
    top.Label = root.name
    leaves = [(c, m) for c, m in root.walk() if c.doc_type == "PART"]
    paths = _resolve_components(leaves, filename)
    cache = {}
    placed = missing = 0
    for comp, matrix in leaves:
        path = paths.get(comp.path)
        if not path:
            missing += 1
            continue
        if path not in cache:
            try:
                cache[path] = _convert(path)
            except Exception as exc:
                cache[path] = None
                _warn("component %s: %s" % (comp.name, exc))
        bodies = cache[path]
        if not bodies:
            continue
        holder = doc.addObject("App::Part", _doc_name(comp.name))
        holder.Label = "%s-%d" % (comp.name, comp.instance)   # as in the SolidWorks tree
        holder.Placement = _placement(matrix)
        top.addObject(holder)
        # shapes are shared between instances; copy so each object owns its own
        _add_bodies(doc, [(lbl, shp.copy(), app) for lbl, shp, app in bodies],
                    holder, visible=not comp.hidden)
        placed += 1
    _msg("imported %s: %d component(s) placed%s" % (
        os.path.basename(filename), placed,
        ", %d skipped (file not found)" % missing if missing else ""))
    lost = sorted({_asm._basename(p) for p, found in paths.items() if not found})
    if lost:
        _error("%s: these part files were not found, so %d component(s) were left out:\n\n%s\n\n"
               "Put them next to the assembly (or in a folder below it) and import again."
               % (os.path.basename(filename), missing, "\n".join("  " + n for n in lost)))


def _resolve_components(leaves, asm_path):
    """{stored path: local path or None}. In the GUI, ask for a folder when
    some part files are not next to the assembly."""
    stored = sorted({c.path for c, _ in leaves})
    found = {p: _asm.resolve_file(p, asm_path, _search_dirs) for p in stored}
    lost = [p for p in stored if not found[p]]
    if lost and FreeCAD.GuiUp and _batch_errors is None:
        try:
            from PySide import QtGui
            names = "\n".join("  " + _asm._basename(p) for p in lost)
            ask = QtGui.QMessageBox.question(
                None, "SolidWorks import",
                "These part files used by the assembly were not found:\n\n%s\n\n"
                "Choose the folder that contains them?" % names,
                QtGui.QMessageBox.Yes | QtGui.QMessageBox.No)
            if ask == QtGui.QMessageBox.Yes:
                folder = QtGui.QFileDialog.getExistingDirectory(
                    None, "Folder with the SolidWorks part files",
                    os.path.dirname(os.path.abspath(asm_path)))
                if folder:
                    for p in lost:
                        found[p] = _asm.resolve_file(p, asm_path, [folder])
        except Exception as exc:
            _warn("could not ask for the parts folder: %s" % exc)
    for p in stored:
        if not found[p]:
            _warn("part file not found: %s" % p)
    return found


# ------------------------------------------------------------ folders
FOLDER_EXTS = (".sldprt", ".sldasm")


def _key(path):
    return os.path.normcase(os.path.realpath(path))


def solidworks_files(folder, recursive=False):
    """SolidWorks parts and assemblies in `folder`, sorted, without the
    ~$ lock files SolidWorks leaves while a file is open."""
    out = []
    for dirpath, dirnames, names in os.walk(folder):
        dirnames.sort()
        for n in sorted(names):
            if n.startswith("~$") or os.path.splitext(n)[1].lower() not in FOLDER_EXTS:
                continue
            out.append(os.path.join(dirpath, n))
        if not recursive:
            break
    return out


def has_subfolder_files(folder):
    top = {_key(p) for p in solidworks_files(folder)}
    return any(_key(p) not in top for p in solidworks_files(folder, True))


def _all_components(comp):
    for c in comp.children:
        yield c
        for g in _all_components(c):
            yield g


def plan_folder(folder, recursive=False):
    """(assemblies, parts) to open for a folder: every assembly no other
    assembly in the folder uses, and every part those assemblies do not use
    (so nothing is opened twice). Also returns how many files were skipped
    because an assembly already contains them."""
    files = solidworks_files(folder, recursive)
    asms = [f for f in files if f.lower().endswith(".sldasm")]
    parts = [f for f in files if not f.lower().endswith(".sldasm")]
    used = set()
    for a in asms:
        try:
            root = _asm.read_tree(a)
        except Exception:
            continue                      # reported when it is opened
        for comp in _all_components(root):
            p = _asm.resolve_file(comp.path, a, [folder])
            if p:
                used.add(_key(p))
    top = [a for a in asms if _key(a) not in used]
    loose = [p for p in parts if _key(p) not in used]
    skipped = len(files) - len(top) - len(loose)
    return top, loose, skipped


def open_folder(folder, recursive=False):
    """Open a folder of SolidWorks files, one document per top-level
    assembly or unused part. Returns the new documents."""
    global _batch_errors, _search_dirs
    top, loose, skipped = plan_folder(folder, recursive)
    items = top + loose
    if not items:
        _error("no SolidWorks parts or assemblies found in %s" % folder)
        return []
    docs = []
    errors = []
    _batch_errors, _search_dirs = errors, [folder]
    progress = None
    try:
        try:
            progress = FreeCAD.Base.ProgressIndicator()
            progress.start("Importing SolidWorks folder", len(items))
        except Exception:
            progress = None
        for i, path in enumerate(items):
            _msg("(%d/%d) %s" % (i + 1, len(items), path))
            try:
                docs.append(open(path))
            except (ConversionError, _asm.AssemblyError):
                pass                      # already recorded by _error()
            except Exception as exc:
                errors.append("%s: %s" % (os.path.basename(path), exc))
            if progress is not None:
                progress.next()
            if FreeCAD.GuiUp:
                from PySide import QtGui
                QtGui.QApplication.processEvents()
    finally:
        if progress is not None:
            progress.stop()
        _batch_errors, _search_dirs = None, []
    summary ="Opened %d of %d: %d assembl%s and %d part%s." % (
        len(docs), len(items), len(top), "y" if len(top) == 1 else "ies",
        len(loose), "" if len(loose) == 1 else "s")
    if skipped:
        summary += "\n%d file%s used inside the assemblies %s not opened separately." % (
            skipped, "" if skipped == 1 else "s", "was" if skipped == 1 else "were")
    if errors:
        shown = errors[:15]
        summary += "\n\nProblems:\n\n" + "\n".join("  " + e for e in shown)
        if len(errors) > len(shown):
            summary += "\n  ... and %d more (see the Report view)" % (len(errors) - len(shown))
    _msg(summary.replace("\n\n", "\n"))
    if FreeCAD.GuiUp:
        try:
            from PySide import QtGui
            box = QtGui.QMessageBox.warning if errors else QtGui.QMessageBox.information
            box(None, "SolidWorks import", summary)
        except Exception:
            pass
    return docs


def _placement(m):
    """4x4 column-vector matrix in metres -> FreeCAD placement in mm."""
    fm = FreeCAD.Matrix(m[0][0], m[0][1], m[0][2], m[0][3] * 1000.0,
                        m[1][0], m[1][1], m[1][2], m[1][3] * 1000.0,
                        m[2][0], m[2][1], m[2][2], m[2][3] * 1000.0,
                        0.0, 0.0, 0.0, 1.0)
    return FreeCAD.Placement(fm)


def _apply_appearance(obj, app, visible=True):
    """Copy SolidWorks colours onto the object's view provider (GUI only)."""
    if not FreeCAD.GuiUp:
        return
    vo = obj.ViewObject
    if vo is None:
        return
    vo.Visibility = visible
    if not app or not (app.get("faces") or app.get("color")):
        return
    trans = float(app.get("transparency") or 0.0)
    cols = app.get("faces") or []
    if len(cols) != len(obj.Shape.Faces):
        cols = [app.get("color") or cols[0]] if (app.get("color") or cols) else []
    if not cols:
        return
    try:
        mats = []
        for rgb in cols:
            mat = FreeCAD.Material()
            mat.DiffuseColor = tuple(rgb[:3])
            mat.Transparency = trans
            mats.append(mat)
        vo.ShapeAppearance = mats          # FreeCAD 1.0+
    except Exception:
        try:                               # FreeCAD 0.21
            vo.DiffuseColor = [tuple(rgb[:3]) + (trans,) for rgb in cols]
        except Exception as exc:
            _warn("could not set colours on %s: %s" % (obj.Label, exc))
    try:
        vo.Transparency = int(round(trans * 100))
    except Exception:
        pass
