# SPDX-License-Identifier: LGPL-2.1-or-later
# SolidWorks Import for FreeCAD - https://github.com/djbarrow/freecad_solidworks_plugin
# Written by Denis Joseph Barrow <barrow_dj@yahoo.com> with the help of Claude Code.
# If you find this project useful, please support it with a 10 euro tip
# via PayPal to barrow_dj@yahoo.com.

"""Rebuild Parasolid XT bodies as FreeCAD (OpenCASCADE) shapes.

Must run inside FreeCAD's Python (needs the FreeCAD and Part modules).
Output is in millimetres, as FreeCAD expects; Parasolid stores metres.
"""

import math

import FreeCAD
import Part

from . import xtgeom as G

V = FreeCAD.Vector
MM = 1000.0
TWO_PI = 2.0 * math.pi


class BuildError(RuntimeError):
    pass


def _v(p):
    return V(p[0] * MM, p[1] * MM, p[2] * MM)


def _d(p):
    return V(p[0], p[1], p[2])


def _placement_matrix(origin, z, x):
    """Matrix taking the canonical frame (origin, Z, X) onto the given one (mm)."""
    z = G.unit(z)
    x = G.unit(G.sub(x, G.mul(z, G.dot(x, z))))
    y = G.cross(z, x)
    o = _v(origin)
    return FreeCAD.Matrix(x[0], y[0], z[0], o.x,
                          x[1], y[1], z[1], o.y,
                          x[2], y[2], z[2], o.z,
                          0, 0, 0, 1)


def _start_point(e):
    """Start point of an edge, honouring its orientation."""
    if e.Orientation == "Reversed":
        return e.valueAt(e.LastParameter)
    return e.valueAt(e.FirstParameter)


class Log:
    def __init__(self, verbose=False):
        self.verbose = verbose
        self.warnings = []

    def warn(self, msg):
        self.warnings.append(msg)
        if self.verbose:
            FreeCAD.Console.PrintWarning("sw2fc: %s\n" % msg)

    def info(self, msg):
        if self.verbose:
            FreeCAD.Console.PrintMessage("sw2fc: %s\n" % msg)


class Builder:
    def __init__(self, transmit, log=None, sew_tolerance=0.01):
        self.t = transmit
        self.log = log or Log()
        self.sew_tol = sew_tolerance
        self._surf = {}
        self._edge = {}
        self._curve = {}
        self._edge_override = {}
        self.appearances = []
        self.last_appearance = None
        self.stats = {"faces": 0, "failed_faces": 0, "approx_faces": 0}

    # ------------------------------------------------------------ topology
    def node(self, idx):
        return self.t[idx]

    def bodies(self):
        """BODY nodes that own at least one face, with their faces grouped by shell."""
        out = {}
        for f in self.t.of_type(14):
            shell = self.node(f["shell"]) or self.node(f.get("front_shell"))
            body = self.node(shell["body"]) if shell else None
            if body is None and shell is not None:
                region = self.node(shell["region"])
                body = self.node(region["body"]) if region else None
            key = body.index if body else 0
            out.setdefault(key, {}).setdefault(shell.index if shell else 0, []).append(f)
        result = []
        for bidx, shells in sorted(out.items()):
            body = self.node(bidx)
            result.append((body, [shells[k] for k in sorted(shells)]))
        return result

    def point_of(self, vertex_idx):
        vx = self.node(vertex_idx)
        if vx is None:
            return None
        pt = self.node(vx["point"])
        return pt["pvec"] if pt else None

    def loop_fins(self, loop):
        fins = []
        start = loop["fin"]
        f = start
        guard = 0
        while f:
            fins.append(self.node(f))
            f = self.node(f)["forward"]
            guard += 1
            if f == start or guard > 100000:
                break
        return fins

    def face_loops(self, face):
        loops = []
        li = face["loop"]
        guard = 0
        while li and guard < 100000:
            loop = self.node(li)
            loops.append(loop)
            li = loop["next"]
            guard += 1
        return loops

    # ------------------------------------------------------------ curves
    def edge_ends(self, edge):
        """(start_point, end_point) of an edge in metres, or (None, None) for a ring."""
        fin = self.node(edge["fin"])
        if fin is None:
            return None, None
        other = self.node(fin["other"])
        if fin["sense"] == "+":
            end = self.point_of(fin["vertex"])
            start = self.point_of(other["vertex"]) if other else None
        else:
            start = self.point_of(fin["vertex"])
            end = self.point_of(other["vertex"]) if other else None
        return start, end

    def edge_curve_node(self, edge):
        c = self.node(edge["curve"])
        if c is not None:
            return c, False
        fin = self.node(edge["fin"])
        seen = set()
        while fin is not None and fin.index not in seen:
            seen.add(fin.index)
            c = self.node(fin["curve"])
            if c is not None:
                return c, True
            fin = self.node(fin["other"])
        return None, False

    def edge_shape(self, edge_idx):
        """Shape of an EDGE, oriented from its start vertex to its end vertex."""
        if edge_idx in self._edge:
            return self._edge[edge_idx]
        edge = self.node(edge_idx)
        p0, p1 = self.edge_ends(edge)
        cnode, on_fin = self.edge_curve_node(edge)
        ring = p0 is None or p1 is None
        try:
            if cnode is None:
                if ring:
                    raise BuildError("edge %d has no curve and no vertices" % edge_idx)
                shape = Part.LineSegment(_v(p0), _v(p1)).toShape()
                self.log.warn("edge %d has no curve; using a straight line" % edge_idx)
            else:
                shape = self._make_edge(cnode, p0, p1, ring, on_fin)
        except Exception as exc:
            if ring or p0 is None:
                raise
            self.log.warn("edge %d (%s): %s; using a straight line" % (
                edge_idx, cnode.name if cnode else "?", exc))
            shape = Part.LineSegment(_v(p0), _v(p1)).toShape()
        tol = 1e-4
        etol = edge.get("tolerance")
        if etol:
            tol = max(tol, etol * MM * 1.5)
        try:
            shape.fixTolerance(tol)
        except Exception:
            pass
        self._edge[edge_idx] = shape
        return shape

    def _make_edge(self, c, p0, p1, ring, on_fin):
        ty = c.type
        if ty == 30 and not ring:
            return Part.LineSegment(_v(p0), _v(p1)).toShape()
        if ty == 133:
            return self._trimmed_edge(c, p0, p1, ring)
        if ty in (31, 32, 134):
            g = self.exact_curve(c)
            return self._trim_exact(g, c["sense"], p0, p1, ring)
        if ty in (38, 137):
            pts, closed = self.sample_curve(c)
            return self._polyline_edge(pts, closed, c["sense"], p0, p1, ring)
        raise BuildError("unsupported curve type %s" % c.name)

    def exact_curve(self, c):
        if c.index in self._curve:
            return self._curve[c.index]
        ty = c.type
        if ty == 30:
            g = Part.Line(_v(c["pvec"]), _v(G.add(c["pvec"], c["direction"])))
        elif ty == 31:
            g = Part.Circle()
            g.Radius = c["radius"] * MM
            g.transform(_placement_matrix(c["centre"], c["normal"], c["x_axis"]))
        elif ty == 32:
            a, b = c["major_radius"] * MM, c["minor_radius"] * MM
            g = Part.Ellipse(V(a, 0, 0), V(0, b, 0), V(0, 0, 0))
            g.transform(_placement_matrix(c["centre"], c["normal"], c["x_axis"]))
        elif ty == 134:
            nb, nc = G.nurbs_curve(self.t, c)
            if nb.dim != 3:
                raise BuildError("expected a 3-D B-curve")
            poles, weights = nb.points_weights()
            g = Part.BSplineCurve()
            g.buildFromPolesMultsKnots([_v(p) for p in poles], nb.mults, nb.distinct,
                                       False, nb.p, weights if nb.rational else None)
        else:
            raise BuildError("no exact form for %s" % c.name)
        self._curve[c.index] = g
        return g

    def _trim_exact(self, g, sense, p0, p1, ring):
        periodic = g.isPeriodic() if hasattr(g, "isPeriodic") else False
        if not periodic:
            closed = (g.StartPoint - g.EndPoint).Length < 1e-6 if hasattr(g, "StartPoint") else False
        else:
            closed = True
        if ring:
            e = g.toShape(g.FirstParameter, g.LastParameter)
            return e.reversed() if sense == "-" else e
        u0 = g.parameter(_v(p0))
        u1 = g.parameter(_v(p1))
        same = G.dist(p0, p1) < 1e-9
        if sense == "-":
            a, b = u1, u0
        else:
            a, b = u0, u1
        if periodic:
            period = g.period() if hasattr(g, "period") else TWO_PI
            while b <= a + 1e-12:
                b += period
            if same:
                b = a + period
        else:
            if same and closed:
                a, b = g.FirstParameter, g.LastParameter
            elif b < a:
                a, b = b, a
        e = g.toShape(a, b)
        return e.reversed() if sense == "-" else e

    def _trimmed_edge(self, c, p0, p1, ring):
        basis = self.node(c["basis_curve"])
        s0, s1 = c["parm_1"], c["parm_2"]
        if basis.type == 30:
            a, b = _v(G.curve_eval(self.t, basis, s0)), _v(G.curve_eval(self.t, basis, s1))
            e = Part.LineSegment(a, b).toShape()
        elif basis.type in (31, 32, 134):
            g = self.exact_curve(basis)
            lo, hi = (s0, s1) if s0 <= s1 else (s1, s0)
            if basis.type in (31, 32):
                e = g.toShape(lo, hi)
            else:
                e = g.toShape(max(lo, g.FirstParameter), min(hi, g.LastParameter))
            if s1 < s0:
                e = e.reversed()
        elif basis.type in (38, 137):
            # Trim the sampled basis by the trimmed curve's end points; the basis sense
            # says which way the trimmed portion runs along it.
            pts, closed = self.sample_curve(basis)
            q0, q1 = tuple(c["point_1"]), tuple(c["point_2"])
            e = self._polyline_edge(pts, closed, basis["sense"], q0, q1, False)
        else:
            n = self._sample_count(basis)
            pts = [G.curve_eval(self.t, basis, s0 + (s1 - s0) * i / (n - 1)) for i in range(n)]
            e = self._interp_edge([_v(p) for p in pts], False)
        if ring or p0 is None:
            return e
        # orient start -> end
        sp = _start_point(e)
        if (sp - _v(p0)).Length > (sp - _v(p1)).Length:
            e = e.reversed()
        return e

    # ------------------------------------------------------------ sampled curves
    def _sample_count(self, c):
        try:
            if c.type == 137:
                nb, _ = G.nurbs_curve(self.t, self.node(c["b_curve"]))
                return max(24, (len(nb.distinct) - 1) * 8 + 1)
            if c.type == 134:
                nb, _ = G.nurbs_curve(self.t, c)
                return max(24, (len(nb.distinct) - 1) * 8 + 1)
        except Exception:
            pass
        return 48

    def sample_curve(self, c):
        """Ordered points (metres) along a curve in its natural direction, and closed flag."""
        ty = c.type
        if ty == 38:
            chart = self.node(c["chart"])
            pts = [tuple(h) for h in chart["hvec"] if h]
            start, end = self.node(c["start"]), self.node(c["end"])
            if start is not None and start["type"] == "T" and start["hvec"]:
                pts.insert(0, tuple(start["hvec"][0]))
            if end is not None and end["type"] == "T" and end["hvec"]:
                pts.append(tuple(end["hvec"][0]))
            dedup = [pts[0]]
            for p in pts[1:]:
                if G.dist(p, dedup[-1]) > 1e-12:
                    dedup.append(p)
            closed = len(dedup) > 2 and G.dist(dedup[0], dedup[-1]) < 1e-9
            if closed:
                dedup = dedup[:-1]
            if start is not None and start["type"] == "H":
                closed = True
            key = ("ix", c.index)
            if key not in self._curve:
                try:
                    self._curve[key] = self._refine_intersection(c, dedup, closed)
                except Exception as exc:
                    self.log.warn("intersection %d: refinement failed (%s); using chart" % (
                        c["node_id"], exc))
                    self._curve[key] = dedup
            return self._curve[key], closed
        if ty == 137:
            nb, nc = G.nurbs_curve(self.t, self.node(c["b_curve"]))
            n = self._sample_count(c)
            surf = self.node(c["surface"])
            pts = []
            for i in range(n):
                s = nb.t0 + (nb.t1 - nb.t0) * i / (n - 1)
                uv = nb.eval(s)
                pts.append(G.surface_eval(self.t, surf, uv[0], uv[1]))
            closed = G.dist(pts[0], pts[-1]) < 1e-9
            if closed:
                pts = pts[:-1]
            return pts, closed
        raise BuildError("cannot sample %s" % c.name)

    def _refine_intersection(self, c, pts, closed, per_segment=8):
        """Densify an intersection chart and snap every point onto both surfaces.

        The chart is only a coarse polyline; Parasolid recomputes the exact curve
        on demand, so we do the same with a Newton iteration on the two surfaces.
        """
        snodes = [self.node(i) for i in c["surface"]]
        bounds = [s for s in snodes if s is not None and s.type == 59]
        if bounds:
            try:
                return self._blend_boundary_points(bounds[0], pts, closed)
            except Exception as exc:
                self.log.info("blend boundary %d: %s" % (c["node_id"], exc))
        if any(s is None or s.type in (56, 59, 120) for s in snodes):
            # blend / construction surfaces: just keep the chart on the real one
            real = [s for s in snodes if s is not None and s.type not in (56, 59, 120)]
            if not real:
                return pts
            g, _ = self.surface(real[0])
            out = []
            for p in pts:
                u, v = g.parameter(_v(p))
                q = g.value(u, v)
                out.append((q.x / MM, q.y / MM, q.z / MM))
            return out
        g1, _ = self.surface(snodes[0])
        g2, _ = self.surface(snodes[1])
        n = len(pts)
        segs = n if closed else n - 1
        dense = []
        for i in range(segs):
            a, b = pts[i], pts[(i + 1) % n]
            for k in range(per_segment):
                dense.append(G.add(a, G.mul(G.sub(b, a), k / float(per_segment))))
        if not closed:
            dense.append(pts[-1])
        out = []
        for k, p in enumerate(dense):
            # keep chart vertices' tangent direction for the Newton constraint
            i = min(len(dense) - 2, k) if len(dense) > 1 else 0
            j = i + 1 if len(dense) > 1 else 0
            tdir = _d(G.sub(dense[j], dense[i])) if j != i else V(1, 0, 0)
            out.append(self._snap(g1, g2, _v(p), tdir))
        return [(q.x / MM, q.y / MM, q.z / MM) for q in out]

    def _blend_boundary_points(self, bb, chart, closed):
        """Exact boundary of a rolling-ball blend: where the ball touches the
        support surface, i.e. the foot of each spine point on that surface."""
        blend = self.node(bb["blend"])
        support = self.node(blend["surface"][1 - int(bb["boundary"])])
        r = blend["range"]
        if support.type == 56 or not (r[0] or r[1]):
            raise BuildError("unsupported support for blend boundary")
        g, _ = self.surface(support)
        spine = self.node(blend["spine"])
        if spine.type in (38, 137):
            spts, sclosed = self.sample_curve(spine)
        elif spine.type in (31, 32):
            spts = [G.curve_eval(self.t, spine, TWO_PI * i / 180.0) for i in range(180)]
        else:
            raise BuildError("spine type %s" % spine.name)
        feet = [self._foot(g, _v(p)) for p in spts]
        # keep only the stretch covered by the chart, in chart order
        i0 = min(range(len(feet)), key=lambda i: G.dist(feet[i], chart[0]))
        i1 = min(range(len(feet)), key=lambda i: G.dist(feet[i], chart[-1]))
        if closed:
            k = i0
            out = []
            step = 1
            # walk the way the chart goes
            if len(chart) > 1:
                ahead = min(range(len(feet)), key=lambda i: G.dist(feet[i], chart[1]))
                if (ahead - i0) % len(feet) > len(feet) // 2:
                    step = -1
            for _ in range(len(feet)):
                out.append(feet[k])
                k = (k + step) % len(feet)
            return out
        lo, hi = (i0, i1) if i0 <= i1 else (i1, i0)
        seg = feet[max(0, lo - 1):hi + 2]
        if i0 > i1:
            seg.reverse()
        if len(seg) < 2:
            raise BuildError("boundary too short")
        # the chart ends are exact limits of the curve
        seg[0], seg[-1] = chart[0], chart[-1]
        return seg

    @staticmethod
    def _snap(g1, g2, q, tdir, iters=12):
        """Newton step onto the intersection of g1 and g2, staying in the plane
        through q normal to the curve direction."""
        x = FreeCAD.Vector(q)
        for _ in range(iters):
            u1, v1 = g1.parameter(x)
            u2, v2 = g2.parameter(x)
            p1, p2 = g1.value(u1, v1), g2.value(u2, v2)
            n1, n2 = g1.normal(u1, v1), g2.normal(u2, v2)
            t = n1.cross(n2)
            if t.Length < 1e-9:
                # tangent surfaces: settle for the midpoint of the two projections
                return (p1 + p2) * 0.5
            if t.dot(tdir) < 0:
                t = t * -1
            # solve n1.y = n1.p1, n2.y = n2.p2, t.y = t.x
            rhs = (n1.dot(p1), n2.dot(p2), t.dot(x))
            det = n1.dot(n2.cross(t))
            y = (n2.cross(t) * rhs[0] + t.cross(n1) * rhs[1] + n1.cross(n2) * rhs[2]) * (1.0 / det)
            step = (y - x).Length
            x = y
            if step < 1e-10:
                break
        return x

    @staticmethod
    def _locate(pts, closed, p):
        """Fractional index of the point on the polyline closest to p."""
        best = (float("inf"), 0.0)
        n = len(pts)
        segs = n if closed else n - 1
        for i in range(segs):
            a, b = pts[i], pts[(i + 1) % n]
            ab = G.sub(b, a)
            l2 = G.dot(ab, ab)
            s = 0.0 if l2 == 0 else max(0.0, min(1.0, G.dot(G.sub(p, a), ab) / l2))
            q = G.add(a, G.mul(ab, s))
            d = G.dist(p, q)
            if d < best[0]:
                best = (d, i + s)
        return best[1]

    def _interp_edge(self, vpts, periodic):
        clean = [vpts[0]]
        for p in vpts[1:]:
            if (p - clean[-1]).Length > 1e-7:
                clean.append(p)
        if periodic and len(clean) > 2 and (clean[0] - clean[-1]).Length < 1e-7:
            clean = clean[:-1]
        if len(clean) < 2:
            raise BuildError("degenerate sampled curve")
        if len(clean) == 2 and not periodic:
            return Part.LineSegment(clean[0], clean[1]).toShape()
        bs = Part.BSplineCurve()
        bs.interpolate(clean, PeriodicFlag=periodic)
        return bs.toShape()

    def _polyline_edge(self, pts, closed, sense, p0, p1, ring):
        n = len(pts)
        if ring:
            vpts = [_v(p) for p in pts]
            e = self._interp_edge(vpts, closed)
            return e.reversed() if sense == "-" else e
        s0 = self._locate(pts, closed, p0)
        s1 = self._locate(pts, closed, p1)
        same = G.dist(p0, p1) < 1e-9
        if sense == "-":
            a, b, pa, pb = s1, s0, p1, p0
        else:
            a, b, pa, pb = s0, s1, p0, p1
        if closed:
            if b <= a + 1e-9 or same:
                b += n
        elif b < a:
            a, b, pa, pb = b, a, pb, pa
        inner = []
        k = int(math.floor(a)) + 1
        while k < b - 1e-6:
            inner.append(pts[k % n])
            k += 1
        seq = [pa] + inner + [pb]
        e = self._interp_edge([_v(p) for p in seq], False)
        return e.reversed() if sense == "-" else e

    # ------------------------------------------------------------ surfaces
    def surface(self, s):
        """(Part surface, flip) where flip says the OCC normal opposes the XT natural normal."""
        if s.index in self._surf:
            return self._surf[s.index]
        res = self._make_surface(s)
        self._surf[s.index] = res
        return res

    def _make_surface(self, s):
        ty = s.type
        if ty == 50:
            g = Part.Plane()
            g.transform(_placement_matrix(s["pvec"], s["normal"], s["x_axis"]))
            return g, False
        if ty == 51:
            g = Part.Cylinder()
            g.Radius = s["radius"] * MM
            g.transform(_placement_matrix(s["pvec"], s["axis"], s["x_axis"]))
            return g, False
        if ty == 52:
            sin_a, cos_a = s["sin_half_angle"], s["cos_half_angle"]
            r = s["radius"]
            a = s["axis"]
            tan_a = sin_a / cos_a
            # The radius grows along +axis (see xtgeom.surface_eval).
            h = max(r, 1e-3)
            p1 = s["pvec"]
            p2 = G.add(p1, G.mul(a, h))
            r2 = r + h * tan_a
            if r2 <= 0:
                raise BuildError("degenerate cone")
            g = Part.Cone(_v(p1), _v(p2), r * MM, r2 * MM)
            return g, None
        if ty == 53:
            g = Part.Sphere()
            g.Radius = s["radius"] * MM
            g.transform(_placement_matrix(s["centre"], s["axis"], s["x_axis"]))
            return g, False
        if ty == 54 and abs(s["major_radius"]) <= s["minor_radius"] * (1 + 1e-12):
            # apple / lemon / osculating torus: OCC's Toroid refuses minor >= major,
            # so revolve the tube circle about the axis instead (same surface).
            a, x = s["axis"], s["x_axis"]
            ring = G.add(s["centre"], G.mul(x, s["major_radius"]))
            tube = Part.Circle()
            tube.Radius = s["minor_radius"] * MM
            tube.transform(_placement_matrix(ring, G.cross(a, x), x))
            g = Part.SurfaceOfRevolution(tube, _v(s["centre"]), _d(a))
            return g, None
        if ty == 54:
            g = Part.Toroid()
            g.MajorRadius = abs(s["major_radius"]) * MM
            g.MinorRadius = s["minor_radius"] * MM
            g.transform(_placement_matrix(s["centre"], s["axis"], s["x_axis"]))
            return g, False
        if ty == 124:
            ns, raw = G.nurbs_surface(self.t, s)
            poles, weights = ns.poles_weights()
            g = Part.BSplineSurface()
            g.buildFromPolesMultsKnots(
                [[_v(p) for p in row] for row in poles],
                ns.umult, ns.vmult, ns.uknots, ns.vknots,
                False, False, ns.pu, ns.pv,
                weights if ns.rational else None)
            return g, False
        if ty == 67:
            sec = self.node(s["section"])
            curve = self._full_curve(sec)
            g = Part.SurfaceOfExtrusion(curve, _d(s["sweep"]))
            return g, False
        if ty == 68:
            prof = self.node(s["profile"])
            curve = self._full_curve(prof)
            g = Part.SurfaceOfRevolution(curve, _v(s["base"]), _d(s["axis"]))
            return g, True
        if ty == 60:
            base = self.node(s["surface"])
            bg, bflip = self.surface(base)
            if bflip is None:
                bflip = False
            off = s["offset"] * MM
            if base["sense"] == "-":
                off = -off
            if bflip:
                off = -off
            g = Part.OffsetSurface(bg, off)
            return g, bflip
        raise BuildError("unsupported surface type %s" % s.name)

    def _full_curve(self, c):
        """A Part curve covering the whole extent of a curve node (for sweeps)."""
        if c.type in (30, 31, 32, 134):
            return self.exact_curve(c)
        if c.type == 133:
            return self._full_curve(self.node(c["basis_curve"]))
        pts, closed = self.sample_curve(c)
        bs = Part.BSplineCurve()
        bs.interpolate([_v(p) for p in pts], PeriodicFlag=closed)
        return bs

    def _orientation_flip(self, s, g, flip, face):
        """Resolve flip numerically for surfaces whose convention is not fixed."""
        if flip is not None and s.type != 52:
            return flip
        # Sample points on the face boundary (edge midpoints avoid cone apexes).
        cands = []
        for loop in self.face_loops(face):
            for fin in self.loop_fins(loop):
                if fin.get("edge"):
                    try:
                        e = self.edge_shape(fin["edge"])
                        m = e.valueAt(0.5 * (e.FirstParameter + e.LastParameter))
                        cands.append((m.x / MM, m.y / MM, m.z / MM))
                    except Exception:
                        pass
        if s.type == 52:
            apex_t = s["radius"] * s["cos_half_angle"] / s["sin_half_angle"] if s["sin_half_angle"] else 0
            h = max(abs(apex_t), 1e-3)
            cands.append(G.surface_eval(self.t, s, 0.3, -apex_t + h if s["radius"] == 0 else 0.0))
        for p in cands:
            try:
                xt_n = G.analytic_normal_at(s, p)
            except G.GeomError:
                continue
            if xt_n is None:
                return False
            u, v = g.parameter(_v(p))
            try:
                on = g.normal(u, v)
            except Exception:
                continue
            if on.Length < 1e-12:
                continue
            return (on.x * xt_n[0] + on.y * xt_n[1] + on.z * xt_n[2]) < 0
        raise BuildError("could not determine surface orientation")

    # ------------------------------------------------------------ faces
    def face_shape(self, face):
        snode = self.node(face["surface"])
        if snode is None:
            raise BuildError("face %d has no surface" % face["node_id"])
        approx = False
        if snode.type == 56:
            try:
                # u follows the spine, v runs from support 0 to support 1, as in
                # Parasolid; a negative spine sense swaps the v direction.
                g = self._support_surface(snode, face) if snode["range"][0] or snode["range"][1] \
                    else None
                if g is None or not hasattr(g, "isUPeriodic"):
                    raise BuildError("degenerate blend")
                flip = self.node(snode["spine"])["sense"] == "-"
            except Exception as exc:
                self.log.warn("blend face %d: %s; using a filled surface" % (face["node_id"], exc))
                g, flip = self._filled_surface(face), None
                approx = True
        elif snode.type in (59, 120):
            g, flip = self.approx_surface(face, snode), None
            approx = True
        else:
            g, flip = self.surface(snode)
            flip = self._orientation_flip(snode, g, flip, face)
        reverse = (face["sense"] == "-") ^ (snode["sense"] == "-") ^ bool(flip)
        wires = []
        loop_edges = []
        for loop in self.face_loops(face):
            fins = [f for f in self.loop_fins(loop) if f.get("edge")]
            if not fins:
                continue
            edges = []
            for fin in fins:
                pieces = self._edge_override.get(fin["edge"])
                if pieces is None:
                    pieces = [self.edge_shape(fin["edge"])]
                if fin["sense"] == "+":
                    edges.extend(pieces)
                else:
                    edges.extend(pc.reversed() for pc in reversed(pieces))
            loop_edges.append(edges)
            try:
                w = Part.Wire(edges)
            except Exception:
                w = None                  # edges do not touch exactly
            if w is None or not w.isClosed():
                w = self._close_wire(edges)
            wires.append(w.reversed() if reverse and not approx else w)
        if not wires:
            f = g.toShape()
        else:
            sf = Part.ShapeFix.Face(g, 1e-6)
            # OCC 7.8 can crash in FixPeriodicDegenerated (cone apex), so it is
            # disabled; faces it would have repaired go through _split_face.
            sf.FixPeriodicDegeneratedMode = 0
            for w in wires:
                sf.add(w)
            sf.perform()
            f = sf.face()
            n_expected = sum(len(x) for x in loop_edges)
            if not approx and not self._face_ok(f, n_expected):
                # Cut the surface with the boundary. If that leaves a piece with
                # extra loops (a wrong chunk of surface: stray sheets on shoe
                # soles) and the boundary itself closed, repair the face as
                # built instead.
                bounded = (f.Area > 1e-9 and len(f.Edges) >= n_expected
                           and all(w.isClosed() for w in f.Wires))
                f2 = None
                try:
                    f2 = self._split_face(g, snode, loop_edges, reverse)
                except Exception as exc:
                    self.log.warn("face %d: split fallback failed: %s" % (face["node_id"], exc))
                if f2 is not None and (not bounded or len(f2.Wires) <= len(loop_edges)):
                    self.log.info("face %d rebuilt by splitting" % face["node_id"])
                    if reverse:
                        f2.reverse()
                    return f2
                if bounded:
                    fixed = self._repair_face(f, n_expected)
                    if fixed is not None:
                        f = fixed
                    self.log.info("face %d kept as bounded (split gave extra loops)" % face["node_id"])
        if reverse and not approx:
            f.reverse()
        if approx:
            self.stats["approx_faces"] += 1
        return f

    @staticmethod
    def _repair_face(f, n_expected):
        """Standard OCC repair (e.g. a boundary that crosses itself in the
        surface's parameter space). Returns the repaired face only if it is
        still one face with the same boundary and area, else None."""
        try:
            sfs = Part.ShapeFix.Shape(f.copy())
            sfs.perform()
            r = sfs.shape()
            if r.ShapeType != "Face" or not r.isValid():
                return None
            if len(r.Wires) != len(f.Wires) or len(r.Edges) < n_expected:
                return None
            if abs(r.Area - f.Area) > 0.01 * max(abs(f.Area), 1e-9):
                return None
            return r
        except Exception:
            return None

    @staticmethod
    def _face_ok(f, n_expected):
        try:
            return f.isValid() and f.Area > 1e-9 and len(f.Edges) >= n_expected
        except Exception:
            return False

    def _split_face(self, g, snode, loop_edges, reverse):
        """Cut an oversized patch of the surface with the face boundary; keep the
        piece on the material side of the first boundary edge."""
        edges = [e for le in loop_edges for e in le]
        uvs = []
        for e in edges:
            for k in range(9):
                p = e.valueAt(e.FirstParameter + (e.LastParameter - e.FirstParameter) * k / 8.0)
                uvs.append(g.parameter(p))
        us = [uv[0] for uv in uvs]
        vs = [uv[1] for uv in uvs]
        name = g.__class__.__name__
        if name in ("Sphere", "Toroid"):
            big = g.toShape()
        else:
            ulo, uhi = min(us), max(us)
            vlo, vhi = min(vs), max(vs)
            du = (uhi - ulo) * 0.1 + 1e-3
            dv = (vhi - vlo) * 0.1 + 1e-3
            ulo, uhi = ulo - du, uhi + du
            vlo, vhi = vlo - dv, vhi + dv
            try:
                if g.isUPeriodic():
                    ulo, uhi = 0.0, g.UPeriod()
            except Exception:
                pass
            try:
                if g.isVPeriodic():
                    vlo, vhi = 0.0, g.VPeriod()
            except Exception:
                pass
            if name == "Cone":
                va = -g.Radius / math.sin(g.SemiAngle)
                # extend to the apex (the face may contain it), never past it
                if va <= min(vs) + 1e-9:
                    vlo = va
                elif va >= max(vs) - 1e-9:
                    vhi = va
            b = g.bounds()
            if name not in ("Plane", "Cylinder", "Cone", "SurfaceOfExtrusion"):
                ulo, uhi = max(ulo, b[0]), min(uhi, b[1])
                vlo, vhi = max(vlo, b[2]), min(vhi, b[3])
            big = g.toShape(ulo, uhi, vlo, vhi)
        res, _ = big.generalFuse(edges, 1e-5)
        pieces = res.Faces
        if not pieces:
            return None
        # material side: left of the first edge w.r.t. the (oriented) face normal
        e = loop_edges[0][0]
        tm = 0.5 * (e.FirstParameter + e.LastParameter)
        m = e.valueAt(tm)
        tan = e.tangentAt(tm)
        if e.Orientation == "Reversed":
            tan = tan * -1
        u, v = g.parameter(m)
        n = g.normal(u, v)
        if reverse:
            n = n * -1
        left = n.cross(tan)
        if left.Length < 1e-12:
            return None
        left.normalize()
        eps = min(0.05, max(1e-3, 0.02 * e.Length))
        q = m + left * eps
        qu, qv = g.parameter(q)
        q = g.value(qu, qv)
        best = min(pieces, key=lambda pc: pc.distToShape(Part.Vertex(q))[0])
        return best

    def _close_wire(self, edges):
        try:
            sw = Part.Wire(Part.__sortEdges__(edges))
            if sw.isClosed():
                return sw
        except Exception:
            pass
        # Ends that miss each other by a hair (SolidWorks leaves gaps of ~1e-3 mm):
        # join the loop with slightly wider vertex tolerances. Fresh edges are
        # made from the curves, because the cached ones may carry vertices that
        # a neighbouring face's repair moved or bloated. Without this the face
        # falls back to _split_face, which can keep the wrong piece of surface
        # (a stray hoop on a shoe rim).
        try:
            fresh = []
            for e in edges:
                ne = e.Curve.toShape(e.FirstParameter, e.LastParameter)
                fresh.append(ne.reversed() if e.Orientation == "Reversed" else ne)
            n = len(fresh)
            gap = max((_start_point(fresh[i].reversed()) - _start_point(fresh[(i + 1) % n])).Length
                      for i in range(n))
            if gap < self.sew_tol:
                for ne in fresh:
                    ne.fixTolerance(max(gap * 1.5, 1e-6), Part.Vertex)
                w = Part.Wire(fresh)
                if w.isClosed() and len(w.Edges) == n:
                    return w
        except Exception:
            pass
        w = Part.Wire(edges)
        fix = Part.ShapeFix.Wire()
        fix.load(w)
        fix.ClosedWireMode = True
        fix.perform()
        return fix.wire()

    # ------------------------------------------------------------ blends
    def _support_surface(self, s, face, depth=0):
        """Surface a blend rolls on; may itself be a (cached, approximated) blend."""
        if s.type != 56:
            return self.surface(s)[0]
        r = s["range"]
        if not r[0] and not r[1]:
            # cliff edge: the ball touches the edge curve (the blend's spine)
            spine = self.node(s["spine"])
            if spine.type in (30, 31, 32, 134):
                return self.exact_curve(spine)
            return self._full_curve(spine)
        key = ("blend", s.index)
        if key not in self._surf:
            if depth > 4:
                raise BuildError("blend chain too deep")
            self._surf[key] = self._rolling_ball(face, s, depth + 1)
        return self._surf[key]

    def approx_surface(self, face, s):
        """B-spline approximation of a surface with no OpenCASCADE equivalent."""
        if s.type == 56:
            try:
                return self._rolling_ball(face, s)
            except Exception as exc:
                self.log.warn("blend face %d: %s; using a filled surface" % (face["node_id"], exc))
        return self._filled_surface(face)

    def _filled_surface(self, face):
        edges = []
        for loop in self.face_loops(face):
            for fin in self.loop_fins(loop):
                if fin.get("edge"):
                    edges.append(self.edge_shape(fin["edge"]))
            break
        filled = Part.makeFilledFace(edges)
        return filled.Surface

    def _rolling_ball(self, face, s, depth=0):
        spine = self.node(s["spine"])
        s1, s2 = self.node(s["surface"][0]), self.node(s["surface"][1])
        r1, r2 = s["range"]
        radius = abs(r1 if r1 else r2)
        if radius <= 0:
            raise BuildError("zero blend radius")
        g1 = self._support_surface(s1, face)
        g2 = self._support_surface(s2, face)
        spts, closed = self._spine_points(spine, face)
        rows = []
        for c in spts:
            cv = _v(c)
            q1 = self._foot(g1, cv)
            q2 = self._foot(g2, cv)
            d1 = G.unit(G.sub(q1, c))
            d2 = G.unit(G.sub(q2, c))
            ang = math.acos(max(-1.0, min(1.0, G.dot(d1, d2))))
            row = []
            m = 9
            for j in range(m):
                tt = j / (m - 1)
                if ang < 1e-9:
                    d = d1
                else:
                    a = math.sin((1 - tt) * ang) / math.sin(ang)
                    b = math.sin(tt * ang) / math.sin(ang)
                    d = G.add(G.mul(d1, a), G.mul(d2, b))
                row.append(_v(G.add(c, G.mul(d, radius))))
            rows.append(row)
        if closed:
            rows.append(rows[0])
        bs = Part.BSplineSurface()
        bs.interpolate(rows)
        return bs

    @staticmethod
    def _foot(g, pv):
        """Closest point (metres) on a support surface or, for cliff edges, a curve."""
        uv = g.parameter(pv)
        q = g.value(*uv) if isinstance(uv, tuple) else g.value(uv)
        return (q.x / MM, q.y / MM, q.z / MM)

    def _spine_points(self, spine, face):
        if spine.type in (38, 137):
            pts, closed = self.sample_curve(spine)
            if not closed and len(pts) >= 2:
                # extend a little past both ends so the surface covers the face
                a0, a1 = pts[0], pts[1]
                b0, b1 = pts[-1], pts[-2]
                pts = [G.add(a0, G.mul(G.sub(a0, a1), 1.0))] + pts + [G.add(b0, G.mul(G.sub(b0, b1), 1.0))]
            return self._resample(pts, closed), closed
        if spine.type in (31, 32):
            n = 72
            return [G.curve_eval(self.t, spine, TWO_PI * i / n) for i in range(n)], True
        rng = G.curve_range(self.t, spine)
        if spine.type == 30 or rng is None:
            # bound an infinite line by the face's vertices
            ps = []
            for loop in self.face_loops(face):
                for fin in self.loop_fins(loop):
                    p = self.point_of(fin["vertex"])
                    if p is not None:
                        ps.append(G.dot(G.sub(p, spine["pvec"]), spine["direction"]))
            if not ps:
                raise BuildError("cannot bound blend spine")
            lo, hi = min(ps), max(ps)
            pad = 0.05 * (hi - lo) + 1e-6
            rng = (lo - pad, hi + pad)
        n = 24
        pts = [G.curve_eval(self.t, spine, rng[0] + (rng[1] - rng[0]) * i / (n - 1)) for i in range(n)]
        return pts, False

    @staticmethod
    def _resample(pts, closed, max_pts=60):
        if len(pts) <= max_pts:
            return pts
        step = len(pts) / float(max_pts)
        out = [pts[int(i * step)] for i in range(max_pts)]
        if not closed and out[-1] is not pts[-1]:
            out.append(pts[-1])
        return out

    # ------------------------------------------------------------ bodies
    def build_body(self, body, shells):
        built = []
        for shell_faces in shells:
            for f in shell_faces:
                self.stats["faces"] += 1
                try:
                    built.append([f, self.face_shape(f)])
                except Exception as exc:
                    self.stats["failed_faces"] += 1
                    self.log.warn("face %s skipped: %s" % (f["node_id"], exc))
        if not built:
            raise BuildError("no faces could be built")
        self._propagate_splits(built)
        body_type = body["body_type"] if body is not None else None
        shape, kind = self._assemble([fs for _, fs in built], body_type)
        self.last_appearance = self.appearance(body, built, shape)
        return shape, kind

    # ------------------------------------------------------------ colours
    def colour_attributes(self):
        """{owner index: (r, g, b)} and {owner index: translucency} from the
        Parasolid colour attributes SolidWorks writes (SDL/TYSA_COLOUR[_2],
        SDL/TYSA_TRANSLUCENCY[_2]); values are 0..1."""
        if hasattr(self, "_colours"):
            return self._colours
        names = {}
        for d in self.t.of_type(80):
            ident = self.node(d["identifier"])
            names[d.index] = ident["string"] if ident else ""
        colours, trans = {}, {}
        for a in self.t.of_type(81):
            name = names.get(a["definition"], "")
            if not name.startswith(("SDL/TYSA_COLOUR", "SDL/TYSA_TRANSLUCENCY")):
                continue
            vals = []
            for fi in a["fields"] or []:
                v = self.node(fi)
                if v is not None and v.type == 83 and v["values"]:
                    vals.extend(v["values"])
            if name.startswith("SDL/TYSA_COLOUR") and len(vals) >= 3:
                colours[a["owner"]] = tuple(max(0.0, min(1.0, float(x))) for x in vals[:3])
            elif name.startswith("SDL/TYSA_TRANSLUCENCY") and vals:
                trans[a["owner"]] = max(0.0, min(1.0, float(vals[0])))
        self._colours = (colours, trans)
        return self._colours

    def appearance(self, body, built, shape):
        """Colours for a rebuilt body: {'color', 'transparency', 'faces'} where
        'faces' lists one colour per face of `shape` (or is None if all equal)."""
        colours, trans = self.colour_attributes()
        bidx = body.index if body is not None else None
        body_col = colours.get(bidx)
        out = {"color": body_col, "transparency": trans.get(bidx, 0.0), "faces": None}
        face_cols = [colours.get(f.index, body_col) for f, _ in built]
        if not any(c is not None and c != body_col for c in face_cols):
            return out
        default = body_col or (0.8, 0.8, 0.9)
        try:
            out["faces"] = self._map_face_colours(built, shape, [c or default for c in face_cols], default)
        except Exception as exc:
            self.log.warn("face colours could not be mapped: %s" % exc)
        if out["color"] is None and out["faces"]:
            # most common face colour stands in for the body colour
            out["color"] = max(set(out["faces"]), key=out["faces"].count)
        return out

    @staticmethod
    def _map_face_colours(built, shape, cols, default):
        """Match faces of the finished shape to the faces we built (sewing and
        repair can copy faces) by centre of mass and area, then give each its colour."""
        grid = {}
        info = []
        for i, (_, fs) in enumerate(built):
            try:
                c, a = fs.CenterOfMass, fs.Area
            except Exception:
                continue
            info.append((i, c, a, fs))
            key = (round(c.x, 1), round(c.y, 1), round(c.z, 1))
            grid.setdefault(key, []).append(len(info) - 1)
        result = []
        for f in shape.Faces:
            c, a = f.CenterOfMass, f.Area
            best = None
            kx, ky, kz = round(c.x, 1), round(c.y, 1), round(c.z, 1)
            for dx in (-0.1, 0, 0.1):
                for dy in (-0.1, 0, 0.1):
                    for dz in (-0.1, 0, 0.1):
                        for j in grid.get((round(kx + dx, 1), round(ky + dy, 1), round(kz + dz, 1)), ()):
                            i, c2, a2, _ = info[j]
                            d = (c - c2).Length + abs(a - a2) / max(a, 1e-9)
                            if best is None or d < best[0]:
                                best = (d, i)
            if best is None or best[0] > 1e-2:
                # fall back: the built face that contains a point of this face
                try:
                    u0, u1, v0, v1 = f.ParameterRange
                    p = f.valueAt(0.5 * (u0 + u1), 0.5 * (v0 + v1))
                    if not f.isInside(p, 1e-6, True):
                        p = f.Vertexes[0].Point if f.Vertexes else c
                    vx = Part.Vertex(p)
                    best = min(((fs.distToShape(vx)[0], i) for i, _, _, fs in info), default=None)
                except Exception:
                    best = None
            result.append(cols[best[1]] if best is not None else default)
        return result

    def _face_edge_ids(self, face):
        ids = []
        for loop in self.face_loops(face):
            for fin in self.loop_fins(loop):
                if fin.get("edge"):
                    ids.append(fin["edge"])
        return ids

    def _pieces_on(self, fshape, eshape):
        """Edges of a built face lying on a given XT edge, ordered start -> end."""
        out = []
        start = _start_point(eshape)
        c = eshape.Curve
        for fe in fshape.Edges:
            m = fe.valueAt(0.5 * (fe.FirstParameter + fe.LastParameter))
            if eshape.distToShape(Part.Vertex(m))[0] > 1e-4:
                continue
            a, b = fe.valueAt(fe.FirstParameter), fe.valueAt(fe.LastParameter)
            if (a - b).Length < 1e-9 and fe.Length < 1e-7:
                continue  # degenerate
            if fe.isSeam(fshape) if hasattr(fe, "isSeam") else False:
                continue
            out.append(fe)
        if len(out) < 2:
            return out
        # orient each piece along the XT edge and sort by distance travelled
        ordered = []
        for fe in out:
            a, b = _start_point(fe), _start_point(fe.reversed())
            try:
                ta, tb = c.parameter(a), c.parameter(b)
                key_a, key_b = ta, tb
                forward = (tb > ta) == (eshape.Orientation != "Reversed")
            except Exception:
                key_a, key_b = (a - start).Length, (b - start).Length
                forward = key_b > key_a
            piece = fe if forward else fe.reversed()
            ordered.append(((a - start).Length if forward else (b - start).Length, piece))
        ordered.sort(key=lambda x: x[0])
        return [pc for _, pc in ordered]

    def _propagate_splits(self, built):
        """If face repair split a shared edge in one face, give the neighbouring
        face the same pieces, so the two faces share identical edges."""
        users = {}
        for item in built:
            for eid in set(self._face_edge_ids(item[0])):
                users.setdefault(eid, []).append(item)
        rebuild = set()
        for eid, items in users.items():
            if len(items) != 2 or eid in self._edge_override:
                continue
            eshape = self._edge.get(eid)
            if eshape is None:
                continue
            counts = [self._pieces_on(fs, eshape) for _, fs in items]
            n = [len(x) for x in counts]
            if n[0] == n[1] or min(n) == 0:
                continue
            k = 0 if n[0] > n[1] else 1
            if n[1 - k] != 1:
                continue
            self._edge_override[eid] = counts[k]
            rebuild.add(id(items[1 - k]))
        for item in built:
            if id(item) in rebuild:
                try:
                    item[1] = self.face_shape(item[0])
                    self.log.info("face %d rebuilt with split edges" % item[0]["node_id"])
                except Exception as exc:
                    self.log.warn("face %d: rebuild with split edges failed: %s" % (
                        item[0]["node_id"], exc))

    def _assemble(self, faces, body_type):
        comp = Part.Compound(faces)
        sewn = comp.copy()
        try:
            sewn.sewShape(self.sew_tol)
        except Exception as exc:
            self.log.warn("sewing failed: %s" % exc)
        shells = sewn.Shells
        if not shells and sewn.Faces:
            try:
                sh = Part.Shell(sewn.Faces)
                if sh.isClosed():
                    shells = [sh]
            except Exception:
                pass
        if body_type == 2 or not shells:
            # sheet / wire bodies, or nothing sewed
            return sewn, "sheet"
        solids = []
        open_shells = []
        for sh in shells:
            try:
                fix = Part.ShapeFix.Shell(sh)
                fix.perform()
                sh = fix.shell()
            except Exception:
                pass
            if not sh.isClosed():
                open_shells.append(sh)
                continue
            try:
                so = Part.Solid(sh)
                fs = Part.ShapeFix.Solid(so)
                fs.perform()
                so = fs.solid()
                solids.append(so)
            except Exception as exc:
                self.log.warn("solid creation failed: %s" % exc)
                open_shells.append(sh)
        loose = [f for f in sewn.Faces if not any(f.isSame(g) for sh in shells for g in sh.Faces)]
        if open_shells or loose:
            self.log.warn("%d open shell(s): result is not a closed solid" % (len(open_shells) + len(loose)))
        if solids and not open_shells and not loose:
            lumps = [x for x in solids if x.Volume > 0]
            voids = [x for x in solids if x.Volume < 0]
            if voids and lumps:
                result = lumps[0] if len(lumps) == 1 else Part.Compound(lumps)
                for vd in voids:
                    vd.reverse()
                    result = result.cut(vd)
                return result, "solid"
            if len(solids) == 1:
                so = solids[0]
                if so.Volume < 0:
                    so.reverse()
                return so, "solid"
            return Part.Compound(solids), "solid"
        return Part.Compound(solids + open_shells + loose), "partial"


def build_all(transmit, log=None, sew_tolerance=0.01):
    """Build every face-bearing body. Returns (list of (shape, kind, body_node), builder)."""
    b = Builder(transmit, log=log, sew_tolerance=sew_tolerance)
    out = []
    for body, shells in b.bodies():
        try:
            shape, kind = b.build_body(body, shells)
            out.append((shape, kind, body))
            b.appearances.append(b.last_appearance)
        except Exception as exc:
            b.log.warn("body %s failed: %s" % (body.index if body else "?", exc))
    return out, b
