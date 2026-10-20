# SPDX-License-Identifier: LGPL-2.1-or-later
# SolidWorks Import for FreeCAD - https://github.com/djbarrow/SolidWorksImport
# Written by Denis Joseph Barrow <barrow_dj@yahoo.com> with the help of Claude Code.
# If you find this project useful, please support it with a 10 euro tip
# via PayPal to barrow_dj@yahoo.com.

"""Command-line entry point.

Run with:  sw2fc INPUT.SLDPRT [-o OUTPUT.FCStd] [--step] [-v]

If FreeCAD's Python modules are not importable, the command re-runs itself
under FreeCAD's headless interpreter (freecadcmd / the FreeCAD AppImage).
"""

import argparse
import json
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENV_ARGS = "SW2FC_ARGS"

INPUT_EXTS = (".sldprt", ".x_t", ".x_b", ".xmt_txt", ".xmt_bin")


def parse_args(argv):
    p = argparse.ArgumentParser(
        prog="sw2fc",
        description="Convert SolidWorks parts (.SLDPRT, 2011 and later) to FreeCAD.")
    p.add_argument("inputs", nargs="+", help="SolidWorks .SLDPRT files (or Parasolid .x_t/.x_b)")
    p.add_argument("-o", "--output",
                   help="output file (.FCStd, .step, .stp, .brep, .iges, .stl) or directory; "
                        "default: next to the input as .FCStd")
    p.add_argument("--format", default=None,
                   choices=["fcstd", "step", "brep", "iges", "stl"],
                   help="output format when -o is a directory or omitted (default fcstd)")
    p.add_argument("--tolerance", type=float, default=0.01,
                   help="sewing tolerance in mm (default 0.01)")
    p.add_argument("-v", "--verbose", action="store_true", help="print warnings")
    p.add_argument("-nocolours", "--nocolours", "--no-colours", "--no-colors",
                   dest="colours", action="store_false",
                   help="do not copy SolidWorks colours into the .FCStd")
    p.add_argument("--keep-duplicates", action="store_true",
                   help="keep bodies that exactly duplicate another body (dropped by default)")
    p.add_argument("--check", action="store_true",
                   help="only check that the inputs can be converted (fast, no FreeCAD); prints JSON")
    p.add_argument("--freecad", default=None,
                   help="path to freecadcmd or the FreeCAD AppImage")
    return p.parse_args(argv)


EXT = {"fcstd": ".FCStd", "step": ".step", "brep": ".brep", "iges": ".iges", "stl": ".stl"}


def out_path_for(inp, args, multiple):
    fmt = args.format or "fcstd"
    base = os.path.splitext(os.path.basename(inp))[0] + EXT[fmt]
    if args.output:
        if os.path.isdir(args.output) or multiple or args.output.endswith(os.sep):
            os.makedirs(args.output, exist_ok=True)
            return os.path.join(args.output, base)
        return args.output
    return os.path.join(os.path.dirname(os.path.abspath(inp)), base)


def run_inside_freecad(argv):
    args = parse_args(argv)
    if HERE not in sys.path:
        sys.path.insert(0, HERE)
    from sw2fc.convert import write_outputs, ConversionError
    failures = 0
    multiple = len(args.inputs) > 1
    for inp in args.inputs:
        out = out_path_for(inp, args, multiple)
        out_dir = os.path.dirname(os.path.abspath(out))
        if not args.output and not os.access(out_dir, os.W_OK):
            # input folder is read-only: fall back to the current folder, then home
            for alt in (os.getcwd(), os.path.expanduser("~")):
                if os.access(alt, os.W_OK):
                    out = os.path.join(alt, os.path.basename(out))
                    print("NOTE    %s is not writable; saving to %s" % (out_dir, alt))
                    out_dir = alt
                    break
        if not os.access(out_dir, os.W_OK):
            failures += 1
            print("FAILED  %s: cannot write to folder %s (permission denied). "
                  "Choose another output location with -o, e.g. -o ~/converted/" % (inp, out_dir))
            continue
        try:
            results, log = write_outputs(inp, out, verbose=args.verbose,
                                         sew_tolerance=args.tolerance, colours=args.colours,
                                         keep_duplicates=args.keep_duplicates)
        except Exception as exc:
            failures += 1
            print("FAILED  %s: %s" % (inp, exc))
            continue
        kinds = ", ".join("%s (%s)" % (lbl, k) for lbl, _, k in results)
        st = getattr(log, "stats", {})
        extra = ""
        if st.get("failed_faces"):
            extra = "  [%d of %d faces could not be rebuilt]" % (st["failed_faces"], st["faces"])
        print("OK      %s -> %s  %s%s" % (inp, out, kinds, extra))
        if args.verbose:
            for w in log.warnings:
                print("        warning: %s" % w)
    return 1 if failures else 0


def find_freecad(explicit=None):
    cands = [explicit] if explicit else []
    cands += [os.environ.get("SW2FC_FREECAD"), shutil.which("freecadcmd"),
              shutil.which("FreeCADCmd"), shutil.which("freecad.cmd"),
              "/opt/FreeCAD.AppImage", shutil.which("FreeCAD"), shutil.which("freecad")]
    for c in cands:
        if c and os.path.exists(c):
            return c
    return None


def run_check(args):
    if HERE not in sys.path:
        sys.path.insert(0, HERE)
    from sw2fc.loader import check
    results = {inp: check(inp) for inp in args.inputs}
    print(json.dumps(results))
    return 0 if all(r["ok"] for r in results.values()) else 1


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--check" in argv:
        return run_check(parse_args(argv))
    try:
        import FreeCAD  # noqa: F401
        import Part  # noqa: F401
        return run_inside_freecad(argv)
    except ImportError:
        pass
    args = parse_args(argv)  # validate before launching FreeCAD
    exe = find_freecad(args.freecad)
    if not exe:
        print("sw2fc: FreeCAD not found. Install FreeCAD 0.21+ or pass --freecad PATH.",
              file=sys.stderr)
        return 2
    cmd = [exe]
    low = os.path.basename(exe).lower()
    if low.endswith(".appimage") or low in ("freecad", "freecad.exe"):
        cmd.append("freecadcmd")
    cmd.append(os.path.join(HERE, "sw2fc", "_freecad_entry.py"))
    env = dict(os.environ)
    env[ENV_ARGS] = json.dumps([os.path.abspath(a) if os.path.exists(a) else a for a in argv])
    proc = subprocess.run(cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                          universal_newlines=True)
    rc = 0
    for line in proc.stdout.splitlines():
        if line.startswith("SW2FC_RC="):
            rc = int(line.split("=", 1)[1])
            continue
        if line.startswith(("FreeCAD ", "(C) 20", "FreeCAD is free", "Exception while processing")):
            continue
        print(line)
    if proc.returncode and not rc:
        rc = proc.returncode
    return rc


if __name__ == "__main__":
    sys.exit(main())
