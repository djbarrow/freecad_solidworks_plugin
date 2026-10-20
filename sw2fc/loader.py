# SPDX-License-Identifier: LGPL-2.1-or-later
# SolidWorks Import for FreeCAD - https://github.com/djbarrow/freecad_solidworks_plugin
# Written by Denis Joseph Barrow <barrow_dj@yahoo.com> with the help of Claude Code.
# If you find this project useful, please support it with a 10 euro tip
# via PayPal to barrow_dj@yahoo.com.

"""Read the Parasolid geometry out of a SolidWorks part. Pure Python (no FreeCAD)."""

import os

from . import container, partition, xt


class ConversionError(RuntimeError):
    pass


def _short(exc, limit=80):
    """Error text fit for a message box: decoder errors can quote raw bytes."""
    text = "".join(c if c.isprintable() and ord(c) < 128 else "?" for c in str(exc))
    return text if len(text) <= limit else text[:limit] + "..."


def load_transmit(path):
    """Decode the Parasolid body of a SolidWorks part (or a raw .x_t/.x_b file)."""
    data = open(path, "rb").read()
    ext = os.path.splitext(path)[1].lower()
    if ext in (".x_t", ".xmt_txt"):
        return xt.parse(data, binary=False), "x_t"
    if ext in (".x_b", ".xmt_bin"):
        return xt.parse(data, binary=True), "x_b"
    try:
        kind, streams = container.read_streams(data)
    except Exception as exc:
        raise ConversionError("not a readable SolidWorks file: %s" % exc)
    cands = partition.candidates(streams)
    if not cands:
        if any(k.endswith("__Zip") for k in streams):
            raise ConversionError(
                "this file uses the pre-2011 SolidWorks storage format, which is not supported; "
                "open and re-save it in a newer SolidWorks version")
        raise ConversionError("no Parasolid geometry found in the file")
    errors = []
    for name, idx, k, payload in cands:
        try:
            t = xt.parse(payload, binary=True)
        except xt.XTError as exc:
            if name.rsplit("/", 1)[-1] == "Config-0-Partition":
                # never fall back to ghost / feature bodies when the main
                # geometry is unreadable: that silently drops most of the part
                raise ConversionError("could not decode the part geometry (%s)" % _short(exc))
            errors.append("%s: %s" % (name, _short(exc)))
            continue
        if t.of_type(14):
            return t, kind
    if errors:
        raise ConversionError("could not decode geometry: " + "; ".join(errors))
    raise ConversionError("the file contains no solid or surface geometry")


def check(path):
    """Fast pre-flight check: can this file be converted? Returns a dict."""
    try:
        t, kind = load_transmit(path)
    except ConversionError as exc:
        return {"ok": False, "message": str(exc)}
    except Exception as exc:  # malformed input must never crash the caller
        return {"ok": False, "message": "unreadable file (%s)" % exc.__class__.__name__}
    faces = t.of_type(14)
    bodies = set()
    for f in faces:
        shell = t[f["shell"]]
        if shell is not None:
            bodies.add(shell["body"])
    return {"ok": True, "container": kind, "faces": len(faces), "bodies": max(1, len(bodies)),
            "message": "ok"}
