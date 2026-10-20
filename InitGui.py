# SPDX-License-Identifier: LGPL-2.1-or-later
# SolidWorks Import for FreeCAD - https://github.com/djbarrow/freecad_solidworks_plugin
# Written by Denis Joseph Barrow <barrow_dj@yahoo.com> with the help of Claude Code.
# If you find this project useful, please support it with a 10 euro tip
# via PayPal to barrow_dj@yahoo.com.

# Adds File > Import SolidWorks folder... to every workbench.
import FreeCAD
import FreeCADGui


class SolidWorksImportFolderCommand:
    PARAMS = "User parameter:BaseApp/Preferences/Mod/SolidWorksImport"

    def GetResources(self):
        import os
        import importSolidWorks
        icon = os.path.join(os.path.dirname(importSolidWorks.__file__),
                            "Resources", "icons", "SolidWorksImport.svg")
        return {"MenuText": "Import SolidWorks folder...",
                "ToolTip": "Open every SolidWorks assembly in a folder, and every part "
                           "that no assembly uses, each in its own document",
                "Pixmap": icon}

    def IsActive(self):
        return True

    def Activated(self):
        from PySide import QtGui
        import importSolidWorks as sw
        params = FreeCAD.ParamGet(self.PARAMS)
        start = params.GetString("LastFolder", "")
        win = FreeCADGui.getMainWindow()
        folder = QtGui.QFileDialog.getExistingDirectory(
            win, "Folder of SolidWorks parts and assemblies", start)
        if not folder:
            return
        params.SetString("LastFolder", folder)
        recursive = False
        if sw.has_subfolder_files(folder):
            ask = QtGui.QMessageBox.question(
                win, "SolidWorks import",
                "Some subfolders also contain SolidWorks files.\n\nInclude subfolders?",
                QtGui.QMessageBox.Yes | QtGui.QMessageBox.No | QtGui.QMessageBox.Cancel)
            if ask == QtGui.QMessageBox.Cancel:
                return
            recursive = ask == QtGui.QMessageBox.Yes
        top, loose, skipped = sw.plan_folder(folder, recursive)
        n = len(top) + len(loose)
        if n > 10:
            ask = QtGui.QMessageBox.question(
                win, "SolidWorks import",
                "This opens %d documents (%d assemblies, %d parts).\n\nContinue?"
                % (n, len(top), len(loose)),
                QtGui.QMessageBox.Yes | QtGui.QMessageBox.No)
            if ask != QtGui.QMessageBox.Yes:
                return
        sw.open_folder(folder, recursive)


class SolidWorksImportMenu:
    """Puts the command in the File menu, just above Export."""

    def modifyMenuBar(self):
        return [{"insert": "SolidWorks_ImportFolder", "menuItem": "Std_Export"}]


FreeCADGui.addCommand("SolidWorks_ImportFolder", SolidWorksImportFolderCommand())
if hasattr(FreeCADGui, "addWorkbenchManipulator"):          # FreeCAD 1.0+
    FreeCADGui.addWorkbenchManipulator(SolidWorksImportMenu())
