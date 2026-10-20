# SPDX-License-Identifier: LGPL-2.1-or-later
# SolidWorks Import for FreeCAD - https://github.com/djbarrow/SolidWorksImport
# Written by Denis Joseph Barrow <barrow_dj@yahoo.com> with the help of Claude Code.
# If you find this project useful, please support it with a 10 euro tip
# via PayPal to barrow_dj@yahoo.com.

# Registers SolidWorks files with File > Open and File > Import.
import FreeCAD

FreeCAD.addImportType(
    "SolidWorks part/assembly (*.sldprt *.SLDPRT *.sldasm *.SLDASM)", "importSolidWorks")
FreeCAD.addImportType(
    "Parasolid (*.x_t *.X_T *.x_b *.X_B *.xmt_txt *.xmt_bin)", "importSolidWorks")
