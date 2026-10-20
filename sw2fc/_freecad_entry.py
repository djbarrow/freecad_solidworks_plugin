# SPDX-License-Identifier: LGPL-2.1-or-later
# SolidWorks Import for FreeCAD - https://github.com/djbarrow/freecad_solidworks_plugin
# Written by Denis Joseph Barrow <barrow_dj@yahoo.com> with the help of Claude Code.
# If you find this project useful, please support it with a 10 euro tip
# via PayPal to barrow_dj@yahoo.com.

# Executed by freecadcmd; arguments arrive JSON-encoded in SW2FC_ARGS.
import json
import os
import sys

_here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _here not in sys.path:
    sys.path.insert(0, _here)

from sw2fc.cli import run_inside_freecad, ENV_ARGS  # noqa: E402

try:
    _rc = run_inside_freecad(json.loads(os.environ.get(ENV_ARGS, "[]")))
except SystemExit as _e:
    _rc = _e.code or 0
print("SW2FC_RC=%d" % _rc)
