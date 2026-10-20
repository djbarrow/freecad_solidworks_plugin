# SPDX-License-Identifier: LGPL-2.1-or-later
# SolidWorks Import for FreeCAD - https://github.com/djbarrow/SolidWorksImport
# Written by Denis Joseph Barrow <barrow_dj@yahoo.com> with the help of Claude Code.
# If you find this project useful, please support it with a 10 euro tip
# via PayPal to barrow_dj@yahoo.com.

"""High-level conversion: SolidWorks part -> FreeCAD document / STEP / BREP.

Runs inside FreeCAD's Python.
"""

import os
import zipfile

import FreeCAD
import Part

from .build import Log, build_all


from .loader import ConversionError, load_transmit  # noqa: F401  (re-exported)


def convert(path, verbose=False, sew_tolerance=0.01, keep_duplicates=False):
    """Return (list of (label, shape, kind), log).

    Bodies that exactly duplicate another body (same place, same shape) are
    dropped unless `keep_duplicates`: both would be drawn on top of each other
    and flicker between their colours."""
    t, kind = load_transmit(path)
    log = Log(verbose)
    bodies, builder = build_all(t, log=log, sew_tolerance=sew_tolerance)
    if not bodies:
        raise ConversionError("no bodies could be rebuilt: " + "; ".join(log.warnings[:5]))
    apps = list(builder.appearances)
    apps += [None] * (len(bodies) - len(apps))
    items = [(shape, k, app) for (shape, k, _), app in zip(bodies, apps)]
    if not keep_duplicates:
        items = _drop_duplicate_bodies(items, log)
    base = os.path.splitext(os.path.basename(path))[0]
    out = []
    for i, (shape, k, _) in enumerate(items):
        label = base if len(items) == 1 else "%s_Body%d" % (base, i + 1)
        out.append((label, shape, k))
    log.stats = builder.stats
    log.appearances = [app for _, _, app in items]   # parallel to `out`
    return out, log


# SolidWorks' default part appearance (202, 209, 238): a body still in this
# colour was never painted.
_SW_DEFAULT_RGB = (202, 209, 238)


def _painted_faces(app):
    if not app:
        return 0
    cols = app.get("faces") or ([app["color"]] if app.get("color") else [])
    return sum(1 for c in cols
               if tuple(int(round(x * 255)) for x in c[:3]) != _SW_DEFAULT_RGB)


def _same_body(a, b):
    """True if shapes `a` and `b` occupy the same space."""
    if len(a.Faces) != len(b.Faces):
        return False
    ba, bb = a.BoundBox, b.BoundBox
    tol = max(1e-3, 1e-5 * ba.DiagonalLength)
    if any(abs(x - y) > tol for x, y in zip(
            (ba.XMin, ba.YMin, ba.ZMin, ba.XMax, ba.YMax, ba.ZMax),
            (bb.XMin, bb.YMin, bb.ZMin, bb.XMax, bb.YMax, bb.ZMax))):
        return False
    if abs(a.Area - b.Area) > 1e-4 * max(a.Area, 1e-9):
        return False
    va, vb = abs(a.Volume), abs(b.Volume)
    if va < 1e-9 or vb < 1e-9:
        return True                       # sheets: matching box, faces and area
    if abs(va - vb) > 1e-4 * va:
        return False
    try:
        return a.common(b).Volume >= 0.99 * va
    except Exception:
        return False


def _drop_duplicate_bodies(items, log):
    """Remove bodies that duplicate an earlier one, keeping whichever copy has
    more painted (non-default colour) faces."""
    keep = list(items)
    i = 0
    while i < len(keep):
        j = i + 1
        while j < len(keep):
            if _same_body(keep[i][0], keep[j][0]):
                if _painted_faces(keep[j][2]) > _painted_faces(keep[i][2]):
                    keep[i] = keep[j]
                del keep[j]
                log.warn("dropped a duplicate body lying exactly on top of another "
                         "(use --keep-duplicates to keep it)")
            else:
                j += 1
        i += 1
    return keep


def to_document(path, doc=None, verbose=False, sew_tolerance=0.01, keep_duplicates=False):
    """Convert into a FreeCAD document (a new one unless `doc` is given)."""
    results, log = convert(path, verbose=verbose, sew_tolerance=sew_tolerance,
                           keep_duplicates=keep_duplicates)
    base = os.path.splitext(os.path.basename(path))[0]
    if doc is None:
        doc = FreeCAD.newDocument(_doc_name(base))
    log.appearance_by_name = {}
    apps = getattr(log, "appearances", [])
    for i, (label, shape, kind) in enumerate(results):
        obj = doc.addObject("Part::Feature", _doc_name(label))
        obj.Label = label
        obj.Shape = shape
        log.appearance_by_name[obj.Name] = apps[i] if i < len(apps) else None
    doc.recompute()
    return doc, results, log


def write_outputs(path, out_path, verbose=False, sew_tolerance=0.01, colours=True,
                  keep_duplicates=False):
    """Convert `path` and write `out_path` (.FCStd, .step/.stp, .brep, .stl).
    `colours=False` leaves SolidWorks colours out of an .FCStd."""
    ext = os.path.splitext(out_path)[1].lower()
    if ext == ".fcstd":
        doc, results, log = to_document(path, verbose=verbose, sew_tolerance=sew_tolerance,
                                        keep_duplicates=keep_duplicates)
        names = [o.Name for o in doc.Objects]
        doc.saveAs(out_path)
        FreeCAD.closeDocument(doc.Name)
        add_gui_document(out_path, [(n, log.appearance_by_name.get(n) if colours else None)
                                    for n in names])
        return results, log
    results, log = convert(path, verbose=verbose, sew_tolerance=sew_tolerance,
                           keep_duplicates=keep_duplicates)
    shapes = [s for _, s, _ in results]
    shape = shapes[0] if len(shapes) == 1 else Part.Compound(shapes)
    if ext in (".step", ".stp"):
        shape.exportStep(out_path)
    elif ext in (".brep", ".brp"):
        shape.exportBrep(out_path)
    elif ext == ".stl":
        import Mesh
        Mesh.Mesh(shape.tessellate(0.05)).write(out_path)
    elif ext in (".iges", ".igs"):
        shape.exportIges(out_path)
    else:
        raise ConversionError("unsupported output type %s" % ext)
    return results, log


DEFAULT_MATERIAL_UUID = "7f9fd73b-50c9-41d8-b7b2-575a030c1eeb"   # FreeCAD's "Default" appearance


def _packed(rgb, transparency):
    # App::Color packs RGBA as 0xRRGGBBAA where the low byte is transparency
    # (0 = opaque), not opacity.
    r, g, b = (int(round(max(0.0, min(1.0, c)) * 255)) for c in rgb)
    return (r << 24) | (g << 16) | (b << 8) | int(round(max(0.0, min(1.0, transparency)) * 255))


def material_list_bytes(colours, transparency=0.0):
    """Body of a FreeCAD 1.0 App::PropertyMaterialList file (version 3).

    Layout (as FreeCAD's own SaveDocFile writes it): count, then the colour
    block of *every* material, then the strings of every material. Interleaving
    the strings per material makes FreeCAD read colour bytes as string lengths
    and crash on any list with more than one entry."""
    import struct
    t = float(transparency)
    out = [struct.pack("<I", len(colours))]
    for rgb in colours:
        out.append(struct.pack("<IIII", _packed((0.333, 0.333, 0.333), t), _packed(rgb, t),
                               _packed((0.533, 0.533, 0.533), t), _packed((0, 0, 0), t)))
        out.append(struct.pack("<ff", 0.9, t))
    for _ in colours:
        for text in ("", "", DEFAULT_MATERIAL_UUID):     # image, image path, material uuid
            b = text.encode("utf-8")
            out.append(struct.pack("<I", len(b)) + b)
    return b"".join(out)


def add_gui_document(fcstd_path, objects):
    """Add the GUI half of an .FCStd (GuiDocument.xml): every object visible, with
    its SolidWorks colours and transparency.

    `objects` is a list of names or (name, appearance) pairs, where appearance is
    {'color': rgb|None, 'transparency': 0..1, 'faces': [rgb per face]|None}.
    Headless FreeCAD (freecadcmd) does not write GuiDocument.xml, so the GUI
    would otherwise fall back to its own defaults when the file is opened.
    """
    from xml.sax.saxutils import quoteattr
    rows = []
    extra_files = {}
    for k, item in enumerate(objects):
        name, app = (item, None) if isinstance(item, str) else item
        props = ['                <Property name="Visibility" type="App::PropertyBool" status="1">\n'
                 '                    <Bool value="true"/>\n'
                 '                </Property>\n']
        if app and (app.get("faces") or app.get("color")):
            trans = float(app.get("transparency") or 0.0)
            colours = app["faces"] or [app["color"]]
            fname = "ShapeAppearance" + (str(k) if k else "")
            extra_files[fname] = material_list_bytes(colours, trans)
            props.append('                <Property name="ShapeAppearance" type="App::PropertyMaterialList" status="1">\n'
                         '                    <MaterialList file="%s" version="3"/>\n'
                         '                </Property>\n' % fname)
            props.append('                <Property name="Transparency" type="App::PropertyPercent" status="1">\n'
                         '                    <Integer value="%d"/>\n'
                         '                </Property>\n' % int(round(trans * 100)))
        rows.append('        <ViewProvider name=%s expanded="0" treeRank="-1">\n'
                    '            <Properties Count="%d" TransientCount="0">\n%s'
                    '            </Properties>\n'
                    '        </ViewProvider>\n' % (quoteattr(name), len(props), "".join(props)))
    # Same layout FreeCAD 1.0 writes; the GUI rejects the file (and then hides
    # every object) if the Expand element or a parsable camera is missing.
    camera = ("OrthographicCamera {&#10;  viewportMapping ADJUST_CAMERA&#10;  position 0 0 1&#10;"
              "  orientation 0 0 1  0&#10;  nearDistance 0.99900001&#10;  farDistance 10.01&#10;"
              "  aspectRatio 1&#10;  focalDistance 5&#10;  height 4.1421356&#10;&#10;}&#10;")
    xml = ("<?xml version='1.0' encoding='utf-8'?>\n"
           '<Document SchemaVersion="1" HasExpansion="1">\n'
           '    <Expand />\n'
           '    <ViewProviderData Count="%d">\n%s    </ViewProviderData>\n'
           '    <Camera settings="%s"/>\n'
           '</Document>\n') % (len(rows), "".join(rows), camera)
    with zipfile.ZipFile(fcstd_path, "a", zipfile.ZIP_DEFLATED) as z:
        names = set(z.namelist())
        if "GuiDocument.xml" not in names:
            z.writestr("GuiDocument.xml", xml)
            for fname, data in extra_files.items():
                if fname not in names:
                    z.writestr(fname, data)


def _doc_name(s):
    out = "".join(c if c.isalnum() or c == "_" else "_" for c in s)
    if not out or out[0].isdigit():
        out = "P_" + out
    return out
