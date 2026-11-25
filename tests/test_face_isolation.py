"""FreeCAD integration regressions; no external CAD fixtures required.

Run with: freecadcmd tests/test_face_isolation.py
The final FACE_ISOLATION_RESULT line also reports success for FreeCAD builds
that do not propagate Python exceptions to the process exit status.
"""
import json
import math
from pathlib import Path
import sys
import tempfile
import unittest

import FreeCAD
import Part

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sw2fc.build import Builder
from sw2fc.xt import Node

V = FreeCAD.Vector


def rectangle(x0=0, y0=0, x1=10, y1=10):
    return Part.makePolygon([V(x0, y0, 0), V(x1, y0, 0), V(x1, y1, 0),
                             V(x0, y1, 0), V(x0, y0, 0)])


def tolerances(shape):
    return ([e.getTolerance(1, Part.Edge) for e in shape.Edges],
            [v.getTolerance(1, Part.Vertex) for v in shape.Vertexes])


class FaceIsolationTests(unittest.TestCase):
    def test_sewing_handles_a_split_boundary_without_rebuilding_neighbours(self):
        faces = Part.makeBox(10, 10, 10).Faces
        side = next(f for f in faces if abs(f.CenterOfMass.x) < 1e-8)
        split, _ = side.generalFuse([Part.makeLine(V(0, 5, 0), V(0, 5, 10))])
        self.assertEqual(len(split.Faces), 2)
        faces = [f for f in faces if not f.isSame(side)] + split.Faces
        shape, kind = Builder(None)._assemble(faces, 1)
        self.assertEqual(kind, 'solid')
        self.assertTrue(shape.isValid())
        self.assertTrue(shape.isClosed())
        self.assertEqual(len(shape.Solids), 1)
        self.assertEqual(len(shape.Faces), 7)
        self.assertAlmostEqual(shape.Volume, 1000, places=6)

    def test_split_keeps_both_sides_of_a_periodic_seam(self):
        cylinder = Part.Cylinder()
        cylinder.Radius = 10
        source = cylinder.toShape(-.2, .2, 1, 11)
        result = Builder(None)._split_face(cylinder, Node(51, 1, {}),
                                           [source.Wires[0].OrderedEdges], False)
        self.assertTrue(result.isValid())
        self.assertAlmostEqual(result.Area, source.Area, places=7)
        # The old first-piece-only result retains just one half of this patch.
        for angle in (-.1, .1):
            p = V(10 * math.cos(angle), 10 * math.sin(angle), 6)
            self.assertLess(result.distToShape(Part.Vertex(p))[0], 1e-7)

    def test_open_chart_interval_cannot_double_back_at_endpoints(self):
        feet = [(float(i), 0, 0) for i in range(7)]
        chart = [(1.2, 0, 0), (2.5, 0, 0), (3.8, 0, 0)]
        clipped = Builder._chart_interval(feet, False, chart)
        self.assertEqual(clipped, [chart[0], feet[2], feet[3], chart[-1]])
        self.assertEqual(Builder._chart_interval(feet, False, list(reversed(chart))),
                         list(reversed(clipped)))

    def test_short_chart_interval_does_not_import_outside_samples(self):
        feet = [(float(i), 0, 0) for i in range(4)]
        chart = [(1.1, 0, 0), (1.2, 0, 0)]
        self.assertEqual(Builder._chart_interval(feet, False, chart), chart)

    def test_closed_spine_interval_can_cross_parameter_seam(self):
        feet = [(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)]
        chart = [(0, .4, 0), (0, 0, 0), (.6, 0, 0)]
        expected = [chart[0], feet[0], chart[-1]]
        self.assertEqual(Builder._chart_interval(feet, True, chart), expected)
        self.assertEqual(Builder._chart_interval(feet, True, list(reversed(chart))),
                         list(reversed(expected)))

    def test_spindle_torus_contact_follows_ball_radius_not_nearest_sheet(self):
        torus = Node(54, 1, {'centre': (0, 0, 0), 'axis': (0, 0, 1),
                            'x_axis': (1, 0, 0), 'major_radius': .01,
                            'minor_radius': .05})
        g, _ = Builder(None).surface(torus)
        centre = V(2, 0, math.sqrt(48 ** 2 - 8 ** 2))
        expected = V(10, 0, 0) + (centre - V(10, 0, 0)) * (50 / 48)
        wrong = V(*Builder._foot(g, centre)) * 1000
        self.assertGreater(abs((wrong - centre).Length - 2), .1)
        actual = V(*Builder._support_foot(torus, g, centre, 2)) * 1000
        self.assertLess((actual - expected).Length, 1e-9)
        self.assertAlmostEqual((actual - centre).Length, 2, places=9)

    def test_repair_does_not_change_input_edge_or_vertex_tolerances(self):
        wire = rectangle()
        before = tolerances(wire)
        # A small surface/boundary mismatch makes ShapeFix increase tolerances.
        # It is below the importer's default 0.01 mm sewing tolerance.
        plane = Part.Plane(V(0, 0, 0.001), V(0, 0, 1))
        face = Builder._bounded_face(plane, [wire])
        self.assertTrue(face.isValid())
        self.assertGreater(face.getTolerance(1, Part.Edge), 0.0009)
        self.assertEqual(tolerances(wire), before)

    def test_repair_does_not_modify_an_existing_face_sharing_the_wire(self):
        wire = rectangle()
        existing = Part.Face(wire)
        before = tolerances(existing)
        Builder._bounded_face(Part.Plane(V(0, 0, 0.001), V(0, 0, 1)), [wire])
        self.assertTrue(existing.isValid())
        self.assertEqual(tolerances(existing), before)

    def test_next_face_does_not_inherit_previous_repair_tolerance(self):
        wire = rectangle()
        exact_plane = Part.Plane(V(0, 0, 0), V(0, 0, 1))
        clean = Builder._bounded_face(exact_plane, [rectangle()])
        Builder._bounded_face(Part.Plane(V(0, 0, 0.001), V(0, 0, 1)), [wire])
        next_face = Builder._bounded_face(exact_plane, [wire])
        self.assertTrue(next_face.isValid())
        self.assertEqual(tolerances(next_face), tolerances(clean))

    def test_planar_face_retains_inner_wire(self):
        outer, inner = rectangle(), rectangle(3, 3, 7, 7).reversed()
        face = Builder._bounded_face(Part.Plane(V(0, 0, 0), V(0, 0, 1)),
                                     [outer, inner])
        self.assertTrue(face.isValid())
        self.assertEqual(len(face.Wires), 2)
        self.assertEqual(len(face.Edges), 8)
        self.assertAlmostEqual(face.Area, 84, places=7)

    def test_cylindrical_face_preserves_periodic_seam_and_step_roundtrip(self):
        source = next(f for f in Part.makeCylinder(5, 10).Faces
                      if isinstance(f.Surface, Part.Cylinder))
        face = Builder._bounded_face(source.Surface, source.Wires)
        self.assertTrue(face.isValid())
        self.assertAlmostEqual(face.Area, source.Area, places=7)
        self.assertEqual(len(face.Edges), len(source.Edges))
        self.assertEqual(sum(e.isSeam(face) for e in face.Edges),
                         sum(e.isSeam(source) for e in source.Edges))
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / 'cylinder-face.step')
            face.exportStep(path)
            restored = Part.Shape()
            restored.read(path)
            self.assertTrue(restored.isValid())
            self.assertEqual(len(restored.Faces), 1)
            self.assertAlmostEqual(restored.Area, source.Area, places=6)


if __name__ == '__main__':
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(FaceIsolationTests)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    print('FACE_ISOLATION_RESULT ' + json.dumps({
        'success': result.wasSuccessful(), 'tests': result.testsRun,
        'failures': len(result.failures), 'errors': len(result.errors),
        'freecad': FreeCAD.Version(), 'occt': Part.OCC_VERSION}), flush=True)
    if not result.wasSuccessful():
        raise SystemExit(1)
