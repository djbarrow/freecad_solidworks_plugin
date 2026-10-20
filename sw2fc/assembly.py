# SPDX-License-Identifier: LGPL-2.1-or-later
# SolidWorks Import for FreeCAD - https://github.com/djbarrow/SolidWorksImport
# Written by Denis Joseph Barrow <barrow_dj@yahoo.com> with the help of Claude Code.
# If you find this project useful, please support it with a 10 euro tip
# via PayPal to barrow_dj@yahoo.com.

"""SolidWorks assemblies (.SLDASM): component tree, file lookup and placements.

The component tree lives in the XML stream `swXmlContents/COMPINSTANCETREE`:

* <swFile id swDocType swPath>      the documents involved (absolute Windows paths)
* <swModel id swName swFileRef ...> one per (document, configuration)
* <swReference swModelRef swTransform swSuppressed ...>  a placed component,
  nested inside the swModel of the assembly that contains it.

swTransform is 16 numbers: a row-major 4x4 for row vectors (p' = p R + t, with
the translation in the last row), in metres. Sub-assembly trees are inlined, so
only the top-level file needs to be read; the part files must be present.

This module is pure Python; it does not need FreeCAD.
"""

import os
import re
import xml.etree.ElementTree as ET

from . import container


class AssemblyError(ValueError):
    pass


def _local(tag):
    return tag.rsplit("}", 1)[-1]


class Component:
    def __init__(self, name, path, doc_type, config_name, config_id, matrix, suppressed,
                 hidden, children, instance):
        self.name = name
        self.path = path              # path as stored in the file
        self.doc_type = doc_type      # PART or ASSEMBLY
        self.config_name = config_name
        self.config_id = config_id
        self.matrix = matrix          # 4x4 column-vector matrix (metres), list of rows
        self.suppressed = suppressed
        self.hidden = hidden
        self.children = children
        self.instance = instance      # swReferenceNumber, e.g. 1, 2, ...

    def walk(self, parent=None):
        """Yield (component, world_matrix) for every leaf part, depth first."""
        m = self.matrix if parent is None else matmul(parent, self.matrix)
        if self.suppressed:
            return
        if self.doc_type == "PART" or not self.children:
            yield self, m
        for c in self.children:
            for item in c.walk(m):
                yield item


IDENTITY = [[1.0, 0.0, 0.0, 0.0], [0.0, 1.0, 0.0, 0.0], [0.0, 0.0, 1.0, 0.0], [0.0, 0.0, 0.0, 1.0]]


def matmul(a, b):
    return [[sum(a[i][k] * b[k][j] for k in range(4)) for j in range(4)] for i in range(4)]


def parse_transform(text):
    vals = [float(x) for x in text.split()]
    if len(vals) != 16:
        return [row[:] for row in IDENTITY]
    # row-vector convention -> transpose to column-vector matrix
    rv = [vals[i * 4:(i + 1) * 4] for i in range(4)]
    return [[rv[j][i] for j in range(4)] for i in range(4)]


def read_tree(path):
    """Parse an assembly file into its root Component."""
    data = open(path, "rb").read()
    kind, streams = container.read_streams(data)
    xml = container.find_stream(streams, "swXmlContents/COMPINSTANCETREE")
    if xml is None:
        if any(k.endswith("__Zip") for k in streams):
            raise AssemblyError("pre-2011 assembly format is not supported")
        raise AssemblyError("no component tree found in the assembly")
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        root = ET.fromstring(xml.decode("utf-8", "replace").encode("utf-8"))

    files = {}
    models = {}
    for el in root.iter():
        tag = _local(el.tag)
        if tag == "swFile":
            files[el.get("id")] = (el.get("swDocType", ""), el.get("swPath", ""))
        elif tag == "swModel":
            models[el.get("id")] = el

    def model_info(mid):
        el = models.get(mid)
        if el is None:
            return None
        doc_type, fpath = files.get(el.get("swFileRef"), ("", ""))
        return el, doc_type, fpath

    def build(model_el, depth=0):
        if depth > 50:
            raise AssemblyError("assembly nesting too deep")
        kids = []
        for ref in model_el:
            if _local(ref.tag) != "swReference":
                continue
            info = model_info(ref.get("swModelRef"))
            if info is None:
                continue
            sub_el, doc_type, fpath = info
            children = build(sub_el, depth + 1) if doc_type == "ASSEMBLY" else []
            # nested component references can also appear directly under swReference
            for nested in ref:
                if _local(nested.tag) == "swModel":
                    children += build(nested, depth + 1)
            kids.append(Component(
                name=ref.get("swName") or sub_el.get("swName"),
                path=fpath, doc_type=doc_type,
                config_name=ref.get("swConfigurationName") or sub_el.get("swConfigurationName"),
                config_id=_int(ref.get("swConfigurationId") or sub_el.get("swConfigurationId")),
                matrix=parse_transform(ref.get("swTransform", "")),
                suppressed=ref.get("swSuppressed", "NO").upper() == "YES",
                hidden=ref.get("swHidden", "NO").upper() == "YES",
                children=children,
                instance=_int(ref.get("swReferenceNumber")) or 1))
        return kids

    # the top model is the one whose file is this assembly (first ASSEMBLY file)
    top = None
    want = os.path.basename(path).lower()
    for mid, el in models.items():
        doc_type, fpath = files.get(el.get("swFileRef"), ("", ""))
        if doc_type == "ASSEMBLY" and _basename(fpath).lower() == want:
            top = el
            break
    if top is None:
        cands = [el for el in models.values()
                 if files.get(el.get("swFileRef"), ("",))[0] == "ASSEMBLY"]
        if not cands:
            raise AssemblyError("could not find the top-level assembly in the component tree")
        top = cands[0]
    name = os.path.splitext(os.path.basename(path))[0]
    return Component(name, path, "ASSEMBLY", top.get("swConfigurationName"), 0,
                     [row[:] for row in IDENTITY], False, False, build(top), 1)


def _int(s):
    try:
        return int(s)
    except (TypeError, ValueError):
        return None


def _basename(p):
    return re.split(r"[\\/]", p)[-1]


def resolve_file(stored_path, asm_path, search_dirs=()):
    """Find a referenced document on this machine.

    SolidWorks stores absolute Windows paths; look next to the assembly first,
    then along the stored relative structure, then in the search directories.
    """
    if stored_path and os.path.exists(stored_path):
        return stored_path
    base = _basename(stored_path)
    asm_dir = os.path.dirname(os.path.abspath(asm_path))
    dirs = [asm_dir] + list(search_dirs)
    parts = [p for p in re.split(r"[\\/]", stored_path) if p and not p.endswith(":")]
    for d in dirs:
        # same name, case-insensitively
        try:
            names = os.listdir(d)
        except OSError:
            continue
        for n in names:
            if n.lower() == base.lower():
                return os.path.join(d, n)
        # trailing directories of the stored path
        for k in range(len(parts) - 1, 0, -1):
            cand = os.path.join(d, *parts[-k - 1:])
            if os.path.exists(cand):
                return cand
    for d in dirs:
        for dp, _, fns in os.walk(d):
            for n in fns:
                if n.lower() == base.lower():
                    return os.path.join(dp, n)
    return None
