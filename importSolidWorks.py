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


def _msg(text):
    FreeCAD.Console.PrintMessage("SolidWorks import: %s\n" % text)


def _warn(text):
    FreeCAD.Console.PrintWarning("SolidWorks import: %s\n" % text)


def _error(text):
    FreeCAD.Console.PrintError("SolidWorks import: %s\n" % text)
    if FreeCAD.GuiUp:
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
    _import(filename, doc)
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
        _error("These part files were not found, so %d component(s) were left out:\n\n%s\n\n"
               "Put them next to the assembly (or in a folder below it) and import again."
               % (missing, "\n".join("  " + n for n in lost)))


def _resolve_components(leaves, asm_path):
    """{stored path: local path or None}. In the GUI, ask for a folder when
    some part files are not next to the assembly."""
    stored = sorted({c.path for c, _ in leaves})
    found = {p: _asm.resolve_file(p, asm_path) for p in stored}
    lost = [p for p in stored if not found[p]]
    if lost and FreeCAD.GuiUp:
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
