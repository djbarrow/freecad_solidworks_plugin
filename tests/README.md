# Geometry regressions

Run inside FreeCAD's headless Python (not a regular Python interpreter):

```sh
freecadcmd tests/test_face_isolation.py
freecadcmd tests/check_sewing_tolerance.py
```

The tests create their geometry procedurally; no external model or extra Python
package is required. `FACE_ISOLATION_RESULT` reports the outcome explicitly.
The sewing diagnostic reports behaviour and versions, rather than failing a
test or selecting a workaround based on a version number.

## Fixes covered

- Keep edge and vertex tolerances private to each repaired face.
- Trim sampled blend boundaries to their chart endpoints, without overshooting
  and doubling back.
- Follow the specified rolling-ball radius when projecting onto a spindle
  torus, where the nearest sheet need not be the intended contact surface.
- Preserve material on both sides of an artificial periodic seam.
- Let kernel sewing handle split boundary edges instead of rebuilding neighbours
  using midpoint-matched fragments.

The reconstruction also evaluates intersections against rebuilt blend support
surfaces instead of leaving those boundaries at coarse chart resolution.
This path was exercised by the wheel integration experiment below; it does not
yet have a standalone procedural regression fixture.

## Tested environment and limitations

Tests were run on macOS arm64 with FreeCAD **1.1.4**, commit
`4fd3bf320d9566a27e60069fc8387448aaa3a094`, and **OCCT 7.8.1**.
All 11 procedural regressions pass. The three mutation regressions fail before
the wire-isolation fix. Other FreeCAD/OCCT versions were not tested, and the
maintainer's environment is unknown.

The sewing diagnostic in this particular build leaves a 0.001 mm box gap open
at both 0.000001 mm and 0.01 mm requested tolerances. Its
[`TopoShapePy::sewShape`](https://github.com/FreeCAD/FreeCAD/blob/4fd3bf320d9566a27e60069fc8387448aaa3a094/src/Mod/Part/App/TopoShapePyImp.cpp#L840)
parses the argument but calls `sewShape()` without forwarding it. This is a
separate FreeCAD binding issue, not evidence that every build behaves the same.
No version-specific workaround or additional runtime dependency is introduced.

## Wheel integration experiment

Input: [BMW Wheel Rim by keyurm](https://www.cadcrowd.com/3d-models/car-wheel-rim-1),
SLDPRT SHA-256
`563ad5794a0f1cd0ff11375f04db5c41e6f3f18d3ec7b06024bfa4a4486d0aae`.
The external model is not included in this repository.

At baseline `5850e1c`, the importer returned an invalid partial compound with
303 faces and no solids. With these geometry fixes, all 303 source faces are
retained as valid geometry (304 output faces because one region spans a periodic
seam). **The unmodified tested FreeCAD build still returns an open result.**

For independent validation, the patched output was sewn in **OCCT 8** with an
explicit 0.01 mm tolerance: zero free edges, zero multiple edges, one valid
closed shell. FreeCAD 1.1.4 / OCCT 7.8.1 then created a solid from that shell;
exporting and reopening STEP retained **one valid closed solid / 304 faces**.
This is a mixed-kernel verification pipeline, not a claim that the native
FreeCAD importer now finishes end to end on the tested build.

The result was also checked against a separately reconstructed reference CAD:

- 1,409 candidate-to-reference and 1,491 reference-to-candidate sample points,
  including vertices and interior samples on every face: maximum sampled
  distances 0.0002545 mm and 0.0003286 mm, respectively.
- All 14,788 vertices of the input file's display mesh: maximum distance
  0.364899 mm, RMS 0.035240 mm; all within 0.5 mm.

These are sampled geometric comparisons, not certified Hausdorff bounds,
manufacturing tolerances, or proof that the reconstructed reference is exact.
No reference geometry, wheel-specific face IDs, or 72-degree symmetry rules
are used by the importer.
