#!/usr/bin/env python3
# SPDX-License-Identifier: LGPL-2.1-or-later
# SolidWorks Import for FreeCAD - https://github.com/djbarrow/freecad_solidworks_plugin
# Written by Denis Joseph Barrow <barrow_dj@yahoo.com> with the help of Claude Code.
# If you find this project useful, please support it with a 10 euro tip
# via PayPal to barrow_dj@yahoo.com.
"""Install the SolidWorks import plugin into FreeCAD by hand.

The FreeCAD Addon Manager does this for you; use this script only when
installing from a download or a git checkout.

    python3 install.py              # copy the plugin into FreeCAD's Mod folder
    python3 install.py --link       # symlink instead (for development)
    python3 install.py --uninstall  # remove it
    python3 install.py --dest DIR   # use this Mod folder instead of asking FreeCAD

Restart FreeCAD afterwards. SolidWorks files then appear in File > Open and
File > Import.
"""

import argparse
import os
import platform
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ADDON_NAME = "SolidWorksImport"
ADDON_SRC = HERE          # this repository is the addon

sys.path.insert(0, HERE)
from sw2fc.cli import find_freecad  # noqa: E402


def freecad_command(exe):
    cmd = [exe]
    low = os.path.basename(exe).lower()
    if low.endswith(".appimage") or low in ("freecad", "freecad.exe"):
        cmd.append("freecadcmd")
    return cmd


def ask_freecad_user_dir(exe):
    """FreeCAD's own user data folder (it differs between versions)."""
    code = 'import FreeCAD; print("SW2FC_DIR=" + FreeCAD.getUserAppDataDir())'
    try:
        out = subprocess.run(freecad_command(exe) + ["-c", code], stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT, universal_newlines=True,
                             timeout=180).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    for line in out.splitlines():
        if line.startswith("SW2FC_DIR="):
            return line.split("=", 1)[1].strip()
    return None


def default_user_dir():
    system = platform.system()
    if system == "Windows":
        return os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "FreeCAD")
    if system == "Darwin":
        return os.path.expanduser("~/Library/Application Support/FreeCAD")
    return os.path.join(os.environ.get("XDG_DATA_HOME", os.path.expanduser("~/.local/share")),
                        "FreeCAD")


def remove(path):
    if os.path.islink(path) or os.path.isfile(path):
        os.remove(path)
    elif os.path.isdir(path):
        shutil.rmtree(path)


def install(dest, link):
    target = os.path.join(dest, ADDON_NAME)
    os.makedirs(dest, exist_ok=True)
    remove(target)
    if link:
        os.symlink(ADDON_SRC, target)
    else:
        shutil.copytree(ADDON_SRC, target,
                        ignore=shutil.ignore_patterns(".git", "__pycache__", "*.pyc"))
    return target


def verify(exe):
    code = ("import FreeCAD, importSolidWorks\n"
            "m = FreeCAD.getImportType('sldprt')\n"
            "m = [m] if isinstance(m, str) else list(m or [])\n"
            "print('SW2FC_OK' if 'importSolidWorks' in m else 'SW2FC_MISSING %r' % (m,))\n")
    try:
        out = subprocess.run(freecad_command(exe) + ["-c", code], stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT, universal_newlines=True,
                             timeout=180).stdout
    except (OSError, subprocess.SubprocessError) as exc:
        return False, str(exc)
    return "SW2FC_OK" in out, out


def main():
    p = argparse.ArgumentParser(description="Install the SolidWorks import plugin into FreeCAD.")
    p.add_argument("--dest", help="FreeCAD Mod folder (default: ask FreeCAD)")
    p.add_argument("--freecad", help="path to FreeCAD, freecadcmd or the FreeCAD AppImage")
    p.add_argument("--link", action="store_true",
                   help="symlink to this checkout instead of copying (for development)")
    p.add_argument("--uninstall", action="store_true", help="remove the plugin")
    p.add_argument("--no-verify", action="store_true", help="skip the test load in FreeCAD")
    args = p.parse_args()

    exe = find_freecad(args.freecad)
    if args.dest:
        dest = os.path.abspath(os.path.expanduser(args.dest))
    else:
        user_dir = ask_freecad_user_dir(exe) if exe else None
        if not user_dir:
            user_dir = default_user_dir()
            print("NOTE  could not ask FreeCAD for its user folder; using %s" % user_dir)
        dest = os.path.join(user_dir, "Mod")

    if args.uninstall:
        target = os.path.join(dest, ADDON_NAME)
        if os.path.lexists(target):
            remove(target)
            print("Removed %s" % target)
        else:
            print("Not installed in %s" % dest)
        return 0

    try:
        target = install(dest, args.link)
    except OSError as exc:
        print("FAILED  could not install into %s: %s" % (dest, exc), file=sys.stderr)
        return 1
    print("Installed %s%s" % (target, " (link)" if args.link else ""))

    if exe and not args.no_verify:
        ok, out = verify(exe)
        if ok:
            print("Checked  FreeCAD loads the plugin")
        else:
            print("WARNING  FreeCAD could not load the plugin:\n" + out, file=sys.stderr)
            return 1
    elif not exe:
        print("NOTE  FreeCAD not found, so the install was not tested.")
    print("Restart FreeCAD, then use File > Open or File > Import on a .SLDPRT or .SLDASM file.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
