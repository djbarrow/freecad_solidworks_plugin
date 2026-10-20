# SolidWorks Import for FreeCAD

Open SolidWorks files directly in FreeCAD, without SolidWorks or any converter
installed:

* **Parts** (`.SLDPRT`, SolidWorks 2011 and later)
* **Assemblies** (`.SLDASM`), with every component in its SolidWorks position
* **Parasolid** files (`.x_t`, `.x_b`)

The files appear in **File → Open** and **File → Import**, and
**File → Import SolidWorks folder…** opens a whole folder at once. Each body becomes a
solid with its SolidWorks colours. Each assembly component becomes an
`App::Part` labelled as in the SolidWorks tree (for example `crank-1`).

## Support the plugin

A SolidWorks licence costs around **3500 euro a year**. This plugin opens your
SolidWorks files in free FreeCAD, which makes it excellent value.

This is very useful for getting solidworks files from [**GrabCAD**](https://www.grabcad.com/) and similar.


If you like the plugin, please support it by paying **10 euro** via
[**PayPal**](https://www.paypal.com/) to **barrow_dj@yahoo.com**.

On [paypal.com](https://www.paypal.com/), choose **Send**, enter
`barrow_dj@yahoo.com`, and send 10 EUR.

## Install

### With the Addon Manager (recommended)

1. In FreeCAD, open **Tools → Addon manager**.
2. Open the settings (gear icon). Under **Custom repositories**, add
   `https://github.com/djbarrow/freecad_solidworks_plugin` with branch `main`.
3. Find **SolidWorks Import** in the list, click **Install**, and restart FreeCAD.

### By hand

Download or clone this repository, then run:

    ./install.sh                # Linux / macOS
    py install.py               # Windows

Restart FreeCAD afterwards. The installer asks FreeCAD where its `Mod` folder
is and copies the plugin there. Other options:

    ./install.sh --link         # symlink to this checkout (for development)
    ./install.sh --uninstall
    ./install.sh --dest DIR     # install into a specific Mod folder
    ./install.sh --freecad PATH # use this FreeCAD executable or AppImage

## Assemblies

SolidWorks stores the full Windows path of every part used by an assembly. The
plugin looks for each part next to the assembly and in folders below it. If a
part is not found, FreeCAD asks for the folder that contains it. Components whose
files are still missing are left out, and their names are listed.

## Folders

**File → Import SolidWorks folder…** opens every `.SLDASM` and `.SLDPRT` in a
folder, each in its own document:

* Every assembly that no other assembly in the folder uses, with all its
  components.
* Every part that none of those assemblies use.

Parts and sub-assemblies that an assembly already contains are not opened a
second time. If the folder has subfolders with SolidWorks files, you are asked
whether to include them. Problems (unreadable files, missing parts) are listed
in one summary at the end instead of stopping the import.

## Speed

Each file is converted once and the result is kept in a cache, so opening the
same file again (or an assembly whose parts were opened before) is about 20
times faster. The cache is keyed by the file's contents, so a changed file is
always converted afresh. It lives in FreeCAD's cache folder, under `sw2fc`
(on Linux `~/.cache/FreeCAD/Cache/sw2fc`), and is safe to delete.

## Limitations

* SolidWorks files saved before 2011 use an older format that is not supported.
  Open and re-save them in a newer SolidWorks.
* Only the geometry is imported, not the feature history, sketches or
  dimensions. Only each part's default configuration is used.
* Some complex surfaces may not rebuild exactly. Any faces that could not be
  rebuilt are reported in the Report view.

## Command line

`sw2fc/cli.py` converts files without opening the FreeCAD window:

    python3 sw2fc/cli.py part.SLDPRT -o part.step

Output formats: `.FCStd`, `.step`, `.iges`, `.brep`, `.stl`. Run with `--help`
for all options.

## Author and licence

Written by Denis Joseph Barrow (barrow_dj@yahoo.com) with the help of
[Claude Code](https://claude.com/claude-code).

LGPL-2.1-or-later. See [LICENSE](LICENSE).

SolidWorks and Parasolid are trademarks of Dassault Systèmes and Siemens
respectively. This plugin is an independent project, not affiliated with or
endorsed by either company.
