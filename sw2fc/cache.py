# SPDX-License-Identifier: LGPL-2.1-or-later
# SolidWorks Import for FreeCAD - https://github.com/djbarrow/freecad_solidworks_plugin
# Written by Denis Joseph Barrow <barrow_dj@yahoo.com> with the help of Claude Code.
# If you find this project useful, please support it with a 10 euro tip
# via PayPal to barrow_dj@yahoo.com.

"""Disk cache of converted parts, so a file is only rebuilt once.

Entries are keyed by the file's contents and a fingerprint of the converter's
own source, so editing a part or updating the plugin never returns stale
shapes. Runs inside FreeCAD's Python.
"""

import glob
import hashlib
import json
import os
import shutil
import tempfile

import FreeCAD
import Part

MAX_ENTRIES = 300

_code_hash = None


def _code_fingerprint():
    global _code_hash
    if _code_hash is None:
        h = hashlib.sha1()
        here = os.path.dirname(os.path.abspath(__file__))
        for f in sorted(glob.glob(os.path.join(here, "*.py"))):
            with open(f, "rb") as fh:
                h.update(fh.read())
        _code_hash = h.hexdigest()[:16]
    return _code_hash


def cache_dir():
    try:
        base = FreeCAD.getUserCachePath()
    except Exception:
        base = os.path.join(os.path.expanduser("~"), ".cache")
    return os.path.join(base, "sw2fc")


def _key(path, options):
    h = hashlib.sha1()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    h.update(_code_fingerprint().encode())
    h.update(json.dumps(options, sort_keys=True).encode())
    return h.hexdigest()


def _labels(path, n):
    base = os.path.splitext(os.path.basename(path))[0]
    return [base] if n == 1 else ["%s_Body%d" % (base, i + 1) for i in range(n)]


def load(path, options):
    """(bodies, warnings, stats) from the cache, or None.
    bodies is a list of (label, shape, kind, appearance)."""
    try:
        d = os.path.join(cache_dir(), _key(path, options))
        with open(os.path.join(d, "meta.json")) as fh:
            meta = json.load(fh)
        shapes = []
        for i in range(len(meta["bodies"])):
            s = Part.Shape()
            s.importBrep(os.path.join(d, "body%d.brp" % i))
            shapes.append(s)
        os.utime(d)                       # keep recently used entries when pruning
    except Exception:
        return None
    labels = _labels(path, len(shapes))
    bodies = [(lbl, s, b["kind"], b["appearance"])
              for lbl, s, b in zip(labels, shapes, meta["bodies"])]
    return bodies, meta.get("warnings", []), meta.get("stats", {})


def store(path, options, bodies, warnings, stats):
    """Save a conversion. Failures are ignored: the cache is only a speed-up."""
    try:
        root = cache_dir()
        os.makedirs(root, exist_ok=True)
        final = os.path.join(root, _key(path, options))
        tmp = tempfile.mkdtemp(dir=root, prefix=".tmp")
        for i, (_, shape, _, _) in enumerate(bodies):
            shape.exportBrep(os.path.join(tmp, "body%d.brp" % i))
        meta = {"bodies": [{"kind": k, "appearance": a} for _, _, k, a in bodies],
                "warnings": list(warnings), "stats": dict(stats or {})}
        with open(os.path.join(tmp, "meta.json"), "w") as fh:
            json.dump(meta, fh)
        if os.path.isdir(final):
            shutil.rmtree(tmp, ignore_errors=True)
        else:
            os.replace(tmp, final)
        _prune(root)
    except Exception as exc:
        FreeCAD.Console.PrintWarning("sw2fc: could not write cache: %s\n" % exc)


def _prune(root):
    import time
    for n in os.listdir(root):            # leftovers of an interrupted store()
        p = os.path.join(root, n)
        if n.startswith(".tmp") and time.time() - os.path.getmtime(p) > 3600:
            shutil.rmtree(p, ignore_errors=True)
    entries = [os.path.join(root, n) for n in os.listdir(root) if not n.startswith(".")]
    if len(entries) <= MAX_ENTRIES:
        return
    entries.sort(key=lambda p: os.path.getmtime(p))
    for p in entries[:len(entries) - MAX_ENTRIES]:
        shutil.rmtree(p, ignore_errors=True)


def clear():
    shutil.rmtree(cache_dir(), ignore_errors=True)
