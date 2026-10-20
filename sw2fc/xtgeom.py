# SPDX-License-Identifier: LGPL-2.1-or-later
# SolidWorks Import for FreeCAD - https://github.com/djbarrow/SolidWorksImport
# Written by Denis Joseph Barrow <barrow_dj@yahoo.com> with the help of Claude Code.
# If you find this project useful, please support it with a 10 euro tip
# via PayPal to barrow_dj@yahoo.com.

"""Pure-Python evaluation of Parasolid XT curves and surfaces.

Coordinates are in the file's units (metres); parameters follow the XT
definitions exactly. This is used to sample curves that have no direct
OpenCASCADE equivalent (intersection curves, SP-curves) and to evaluate
surfaces in Parasolid's own parameterisation.
"""

import math


class GeomError(ValueError):
    pass


# ---------------------------------------------------------------- vectors

def add(a, b):
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def mul(a, s):
    return (a[0] * s, a[1] * s, a[2] * s)


def dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def norm(a):
    return math.sqrt(dot(a, a))


def unit(a):
    n = norm(a)
    if n == 0:
        raise GeomError("zero-length vector")
    return (a[0] / n, a[1] / n, a[2] / n)


def dist(a, b):
    return norm(sub(a, b))


def perpendicular(a):
    a = unit(a)
    t = (1.0, 0.0, 0.0) if abs(a[0]) < 0.9 else (0.0, 1.0, 0.0)
    return unit(cross(cross(a, t), a))


# ---------------------------------------------------------------- NURBS

class Nurbs:
    """A B-spline curve (dim = point dimension) or surface in homogeneous form."""

    def __init__(self, degree, knots, mults, vertices, dim, rational):
        self.p = degree
        self.dim = dim
        self.rational = rational
        self.U = []
        for k, m in zip(knots, mults):
            self.U.extend([k] * m)
        self.distinct = list(knots)
        self.mults = list(mults)
        stride = dim + (1 if rational else 0)
        self.n = len(vertices) // stride
        self.cp = [tuple(vertices[i * stride:(i + 1) * stride]) for i in range(self.n)]
        if not rational:
            self.cp = [c + (1.0,) for c in self.cp]
        if len(self.U) != self.n + self.p + 1:
            raise GeomError("knot/vertex count mismatch")

    @property
    def t0(self):
        return self.U[self.p]

    @property
    def t1(self):
        return self.U[len(self.U) - self.p - 1]

    def points_weights(self):
        """Cartesian poles and weights."""
        poles = [tuple(c[i] / c[-1] for i in range(self.dim)) for c in self.cp]
        weights = [c[-1] for c in self.cp]
        return poles, weights

    def span(self, t):
        p, U = self.p, self.U
        n = self.n - 1
        if t >= U[n + 1]:
            return n
        if t <= U[p]:
            return p
        lo, hi = p, n + 1
        mid = (lo + hi) // 2
        while t < U[mid] or t >= U[mid + 1]:
            if t < U[mid]:
                hi = mid
            else:
                lo = mid
            mid = (lo + hi) // 2
        return mid

    def eval_h(self, t, cps=None):
        """Homogeneous point at t (de Boor)."""
        cps = cps or self.cp
        p, U = self.p, self.U
        k = self.span(t)
        d = [list(cps[j + k - p]) for j in range(p + 1)]
        for r in range(1, p + 1):
            for j in range(p, r - 1, -1):
                i = j + k - p
                den = U[i + p - r + 1] - U[i]
                a = 0.0 if den == 0 else (t - U[i]) / den
                d[j] = [(1 - a) * x + a * y for x, y in zip(d[j - 1], d[j])]
        return d[p]

    def eval(self, t):
        h = self.eval_h(t)
        w = h[-1]
        return tuple(h[i] / w for i in range(self.dim))


class NurbsSurface:
    def __init__(self, ns, vertices_node, umult, vmult, uknots, vknots):
        self.rational = bool(ns["rational"])
        self.dim = 3
        stride = int(ns["vertex_dim"])
        self.nu = int(ns["n_u_vertices"])
        self.nv = int(ns["n_v_vertices"])
        self.pu = int(ns["u_degree"])
        self.pv = int(ns["v_degree"])
        verts = vertices_node
        if len(verts) != self.nu * self.nv * stride:
            raise GeomError("B-surface vertex count mismatch")
        cps = [tuple(verts[i * stride:(i + 1) * stride]) for i in range(self.nu * self.nv)]
        if stride == 3:
            cps = [c + (1.0,) for c in cps]
        # Parasolid stores the vertices with v varying fastest.
        self.grid = [cps[i * self.nv:(i + 1) * self.nv] for i in range(self.nu)]
        self.ucurve = _BasisOnly(self.pu, uknots, umult, self.nu)
        self.vcurve = _BasisOnly(self.pv, vknots, vmult, self.nv)
        self.uknots, self.vknots, self.umult, self.vmult = uknots, vknots, umult, vmult

    def eval(self, u, v):
        cols = [self.vcurve.eval_h(v, row) for row in self.grid]
        h = self.ucurve.eval_h(u, cols)
        return (h[0] / h[3], h[1] / h[3], h[2] / h[3])

    def poles_weights(self):
        poles = [[(c[0] / c[3], c[1] / c[3], c[2] / c[3]) for c in row] for row in self.grid]
        weights = [[c[3] for c in row] for row in self.grid]
        return poles, weights


class _BasisOnly(Nurbs):
    def __init__(self, p, knots, mults, n):
        self.p = p
        self.U = []
        for k, m in zip(knots, mults):
            self.U.extend([k] * m)
        self.n = n
        self.cp = None
        if len(self.U) != n + p + 1:
            raise GeomError("knot/vertex count mismatch")


def nurbs_curve(t, bnode):
    """Nurbs object for a B_CURVE node (2-D or 3-D)."""
    nc = t[bnode["nurbs"]]
    verts = t[nc["bspline_vertices"]]["vertices"]
    mult = t[nc["knot_mult"]]["mult"]
    knots = t[nc["knots"]]["knots"]
    vdim = int(nc["vertex_dim"])
    rational = bool(nc["rational"])
    dim = vdim - 1 if rational else vdim
    return Nurbs(int(nc["degree"]), knots, mult, verts, dim, rational), nc


def nurbs_surface(t, snode):
    ns = t[snode["nurbs"]]
    verts = t[ns["bspline_vertices"]]["vertices"]
    return NurbsSurface(ns, verts,
                        t[ns["u_knot_mult"]]["mult"], t[ns["v_knot_mult"]]["mult"],
                        t[ns["u_knots"]]["knots"], t[ns["v_knots"]]["knots"]), ns


# ---------------------------------------------------------------- evaluators

def curve_eval(t, c, s):
    """Point on curve node `c` at XT parameter s (metres)."""
    ty = c.type
    if ty == 30:
        return add(c["pvec"], mul(c["direction"], s))
    if ty == 31:
        x = c["x_axis"]
        y = cross(c["normal"], x)
        r = c["radius"]
        return add(c["centre"], add(mul(x, r * math.cos(s)), mul(y, r * math.sin(s))))
    if ty == 32:
        x = c["x_axis"]
        y = cross(c["normal"], x)
        return add(c["centre"], add(mul(x, c["major_radius"] * math.cos(s)),
                                    mul(y, c["minor_radius"] * math.sin(s))))
    if ty == 134:
        nb, _ = nurbs_curve(t, c)
        return nb.eval(s)
    if ty == 133:
        return curve_eval(t, t[c["basis_curve"]], s)
    if ty == 137:
        nb, _ = nurbs_curve(t, t[c["b_curve"]])
        uv = nb.eval(s)
        return surface_eval(t, t[c["surface"]], uv[0], uv[1])
    raise GeomError("cannot evaluate curve type %s" % c.name)


def curve_range(t, c):
    ty = c.type
    if ty in (31, 32):
        return 0.0, 2 * math.pi
    if ty == 134:
        nb, _ = nurbs_curve(t, c)
        return nb.t0, nb.t1
    if ty == 137:
        nb, _ = nurbs_curve(t, t[c["b_curve"]])
        return nb.t0, nb.t1
    if ty == 133:
        return c["parm_1"], c["parm_2"]
    return None


def surface_eval(t, s, u, v):
    ty = s.type
    if ty == 50:
        x = s["x_axis"]
        y = cross(s["normal"], x)
        return add(s["pvec"], add(mul(x, u), mul(y, v)))
    if ty == 51:
        a, x = s["axis"], s["x_axis"]
        y = cross(a, x)
        r = s["radius"]
        return add(add(s["pvec"], mul(a, v)), add(mul(x, r * math.cos(u)), mul(y, r * math.sin(u))))
    if ty == 52:
        a, x = s["axis"], s["x_axis"]
        y = cross(a, x)
        # Observed in SolidWorks files (apex vertex positions): the radius grows
        # along +axis, i.e. R(u,v) = P + vA + (r + v tan a)(X cos u + Y sin u).
        rr = s["radius"] + v * s["sin_half_angle"] / s["cos_half_angle"]
        return add(add(s["pvec"], mul(a, v)), add(mul(x, rr * math.cos(u)), mul(y, rr * math.sin(u))))
    if ty == 53:
        a, x = s["axis"], s["x_axis"]
        y = cross(a, x)
        r = s["radius"]
        ring = add(mul(x, math.cos(u)), mul(y, math.sin(u)))
        return add(s["centre"], add(mul(ring, r * math.cos(v)), mul(a, r * math.sin(v))))
    if ty == 54:
        a, x = s["axis"], s["x_axis"]
        y = cross(a, x)
        R, r = s["major_radius"], s["minor_radius"]
        ring = add(mul(x, math.cos(u)), mul(y, math.sin(u)))
        return add(s["centre"], add(mul(ring, R + r * math.cos(v)), mul(a, r * math.sin(v))))
    if ty == 124:
        ns, _ = nurbs_surface(t, s)
        return ns.eval(u, v)
    if ty == 67:
        return add(curve_eval(t, t[s["section"]], u), mul(s["sweep"], v))
    if ty == 68:
        c = curve_eval(t, t[s["profile"]], u)
        a = s["axis"]
        z = add(s["base"], mul(a, dot(sub(c, s["base"]), a)))
        r = sub(c, z)
        return add(z, add(mul(r, math.cos(v)), mul(cross(a, r), math.sin(v))))
    if ty == 60:
        base = t[s["surface"]]
        p = surface_eval(t, base, u, v)
        n = surface_normal(t, base, u, v)
        if base["sense"] == "-":
            n = mul(n, -1)
        return add(p, mul(n, s["offset"]))
    raise GeomError("cannot evaluate surface type %s" % s.name)


def surface_normal(t, s, u, v, h=1e-6):
    """Natural (unsensed) unit normal dP/du x dP/dv, by central differences."""
    pu = sub(surface_eval(t, s, u + h, v), surface_eval(t, s, u - h, v))
    pv = sub(surface_eval(t, s, u, v + h), surface_eval(t, s, u, v - h))
    return unit(cross(pu, pv))


def analytic_normal_at(s, p):
    """Natural unit normal of an analytic surface at a point on (or near) it."""
    ty = s.type
    if ty == 50:
        return tuple(s["normal"])
    if ty in (51, 52):
        a = s["axis"]
        d = sub(p, s["pvec"])
        radial = unit(sub(d, mul(a, dot(d, a))))
        if ty == 51:
            return radial
        tan_a = s["sin_half_angle"] / s["cos_half_angle"]
        # The natural XT cone normal points away from the axis (a solid cone has
        # face and surface senses both '+'); it is perpendicular to the
        # generator direction (axis + tan_a * radial).
        return unit(sub(radial, mul(a, tan_a)))
    if ty == 53:
        return unit(sub(p, s["centre"]))
    if ty == 54:
        a = s["axis"]
        d = sub(p, s["centre"])
        radial = unit(sub(d, mul(a, dot(d, a))))
        tube_centre = add(s["centre"], mul(radial, s["major_radius"]))
        return unit(sub(p, tube_centre))
    return None
