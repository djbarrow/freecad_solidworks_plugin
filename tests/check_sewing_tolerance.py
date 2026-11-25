"""Diagnostic, not a version gate: freecadcmd tests/check_sewing_tolerance.py.

Some FreeCAD builds accept the sewShape(tolerance) argument without forwarding
it to OCCT. Report observed behaviour; do not infer it from a version number.
"""
import json
import FreeCAD
import Part

faces = Part.makeBox(10, 10, 10).Faces
top = max(range(len(faces)), key=lambda i: faces[i].CenterOfMass.z)
faces[top] = faces[top].copy()
faces[top].translate(FreeCAD.Vector(0, 0, .001))
results = []
for tolerance in (1e-6, .01):
    shape = Part.Compound([f.copy() for f in faces])
    shape.sewShape(tolerance)
    results.append({'tolerance_mm': tolerance, 'closed': shape.isClosed(),
                    'valid': shape.isValid(), 'faces': len(shape.Faces),
                    'shells': len(shape.Shells)})
print('SEWING_TOLERANCE_RESULT ' + json.dumps({
    'freecad': FreeCAD.Version(), 'occt': Part.OCC_VERSION,
    'gap_mm': .001, 'observations': results,
    'tolerance_effect_observed': not results[0]['closed'] and results[1]['closed'],
}), flush=True)
