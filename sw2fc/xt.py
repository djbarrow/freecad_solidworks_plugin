# SPDX-License-Identifier: LGPL-2.1-or-later
# SolidWorks Import for FreeCAD - https://github.com/djbarrow/SolidWorksImport
# Written by Denis Joseph Barrow <barrow_dj@yahoo.com> with the help of Claude Code.
# If you find this project useful, please support it with a 10 euro tip
# via PayPal to barrow_dj@yahoo.com.

"""Reader for Parasolid XT transmit data (neutral binary and text).

Field layouts follow the public "Parasolid XT Format Reference" (Oct 2006).
Files written by Parasolid V14+ embed edit scripts that describe how each node
type differs from base schema SCH_13006; BASE below is that base schema.
"""

import re
import struct

NULL_INT = -32764
NULL_DBL = -3.14158e13

_GEOM = "node_id:d attributes_groups:p owner:p next:p previous:p geometric_owner:p sense:c"


def _spec(s):
    out = []
    for tok in s.split():
        parts = tok.split(":")
        name, kind = parts[0], parts[1]
        n = parts[2] if len(parts) > 2 else "1"
        out.append((name, kind, "*" if n == "*" else int(n)))
    return out


# Effective base fields of SCH_13006 (type -> [(name, kind, count)]).
BASE = {
    10: _spec("highest_node_id:d attributes_groups:p attribute_chains:p list:p surface:p curve:p point:p "
              "key:p res_size:f res_linear:f ref_instance:p next:p previous:p state:u owner:p type:u "
              "sub_instance:p"),
    11: _spec("node_id:d attributes_groups:p type:u part:p transform:p assembly:p next_in_part:p "
              "prev_in_part:p next_of_part:p prev_of_part:p"),
    12: _spec("highest_node_id:d attributes_groups:p attribute_chains:p surface:p curve:p point:p key:p "
              "res_size:f res_linear:f ref_instance:p next:p previous:p state:u owner:p body_type:u "
              "nom_geom_state:u shell:p boundary_surface:p boundary_curve:p boundary_point:p region:p "
              "edge:p vertex:p"),
    13: _spec("node_id:d attributes_groups:p body:p next:p face:p edge:p vertex:p region:p front_face:p"),
    14: _spec("node_id:d attributes_groups:p tolerance:f next:p previous:p loop:p shell:p surface:p sense:c "
              "next_on_surface:p previous_on_surface:p next_front:p previous_front:p front_shell:p"),
    15: _spec("node_id:d attributes_groups:p fin:p face:p next:p"),
    16: _spec("node_id:d attributes_groups:p tolerance:f fin:p previous:p next:p curve:p next_on_curve:p "
              "previous_on_curve:p owner:p"),
    17: _spec("attributes_groups:p loop:p forward:p backward:p vertex:p other:p edge:p curve:p "
              "next_at_vx:p sense:c"),
    18: _spec("node_id:d attributes_groups:p fin:p previous:p next:p point:p tolerance:f owner:p"),
    19: _spec("node_id:d attributes_groups:p body:p next:p previous:p shell:p type:c"),
    29: _spec("node_id:d attributes_groups:p owner:p next:p previous:p pvec:v"),
    30: _spec(_GEOM + " pvec:v direction:v"),
    31: _spec(_GEOM + " centre:v normal:v x_axis:v radius:f"),
    32: _spec(_GEOM + " centre:v normal:v x_axis:v major_radius:f minor_radius:f"),
    38: _spec(_GEOM + " surface:p:2 chart:p start:p end:p"),
    40: _spec("base_parameter:f base_scale:f chart_count:d chordal_error:f angular_error:f "
              "parameter_error:f:2 hvec:h:*"),
    41: _spec("type:c hvec:h:*"),
    45: _spec("vertices:f:*"),
    50: _spec(_GEOM + " pvec:v normal:v x_axis:v"),
    51: _spec(_GEOM + " pvec:v axis:v radius:f x_axis:v"),
    52: _spec(_GEOM + " pvec:v axis:v radius:f sin_half_angle:f cos_half_angle:f x_axis:v"),
    53: _spec(_GEOM + " centre:v radius:f axis:v x_axis:v"),
    54: _spec(_GEOM + " centre:v axis:v major_radius:f minor_radius:f x_axis:v"),
    56: _spec(_GEOM + " blend_type:c surface:p:2 spine:p range:f:2 thumb_weight:f:2 boundary:p:2 "
              "start:p end:p"),
    59: _spec(_GEOM + " boundary:n blend:p"),
    60: _spec(_GEOM + " check:c true_offset:l surface:p offset:f scale:f"),
    67: _spec(_GEOM + " section:p sweep:v scale:f"),
    68: _spec(_GEOM + " profile:p base:v axis:v start:v end:v start_param:f end_param:f x_axis:v scale:f"),
    70: _spec("node_id:d owner:p next:p previous:p list_type:d list_length:d block_length:d "
              "size_of_entry:d list_block:p"),
    74: _spec("n_entries:d next_block:p entries:p:*"),
    79: _spec("string:c:*"),
    80: _spec("next:p identifier:p type_id:d actions:u:8 field_names:p legal_owners:l:14 fields:u:*"),
    81: _spec("node_id:d definition:p owner:p next:p previous:p next_of_type:p previous_of_type:p "
              "fields:p:*"),
    82: _spec("values:d:*"),
    83: _spec("values:f:*"),
    84: _spec("values:c:*"),
    85: _spec("values:v:*"),
    86: _spec("values:v:*"),
    87: _spec("values:v:*"),
    88: _spec("values:d:*"),
    89: _spec("values:v:*"),
    90: _spec("node_id:d attributes_groups:p owner:p next:p previous:p type:u first_member:p"),
    91: _spec("dummy_node_id:d owning_group:p owner:p next:p previous:p next_member:p previous_member:p"),
    98: _spec("values:w:*"),
    99: _spec("names:p:*"),
    100: _spec("node_id:d owner:p next:p previous:p rotation_matrix:f:9 translation_vector:v scale:f "
               "flag:d perspective_vector:v"),
    101: _spec("assembly:p attribute:p body:p transform:p surface:p curve:p point:p alive:l "
               "attrib_def:p highest_id:d current_id:d"),
    102: _spec("string:c:*"),
    120: _spec(_GEOM + " type:c data:p tf:p internal_geom:p:*"),
    121: _spec("geom_type:d real_array:p int_array:p"),
    122: _spec("key:p real_array:p int_array:p"),
    124: _spec(_GEOM + " nurbs:p data:p"),
    125: _spec("original_uint:i original_vint:i extended_uint:i extended_vint:i self_int:u "
               "original_u_start:c original_u_end:c original_v_start:c original_v_end:c "
               "extended_u_start:c extended_u_end:c extended_v_start:c extended_v_end:c "
               "analytic_form_type:c swept_form_type:c spun_form_type:c blend_form_type:c "
               "analytic_form:p swept_form:p spun_form:p blend_form:p"),
    126: _spec("u_periodic:l v_periodic:l u_degree:n v_degree:n n_u_vertices:d n_v_vertices:d "
               "u_knot_type:u v_knot_type:u n_u_knots:d n_v_knots:d rational:l u_closed:l v_closed:l "
               "surface_form:u vertex_dim:n bspline_vertices:p u_knot_mult:p v_knot_mult:p u_knots:p "
               "v_knots:p"),
    127: _spec("mult:n:*"),
    128: _spec("knots:f:*"),
    130: _spec(_GEOM + " type:c data:p tf:p internal_geom:p:*"),
    133: _spec(_GEOM + " basis_curve:p point_1:v point_2:v parm_1:f parm_2:f"),
    134: _spec(_GEOM + " nurbs:p data:p"),
    135: _spec("self_int:u analytic_form:p"),
    136: _spec("degree:n n_vertices:d vertex_dim:n n_knots:d knot_type:u periodic:l closed:l rational:l "
               "curve_form:u bspline_vertices:p knot_mult:p knots:p"),
    137: _spec(_GEOM + " surface:p b_curve:p original:p tolerance_to_original:f"),
    141: _spec("owner:p next:p previous:p shared_geometry:p"),
    163: _spec("axis_pt:v axis_dir:v point:v hand:c turns:i pitch:f tol:f"),
    184: _spec("axis_pt:v axis_dir:v hand:c turns:i pitch:f gap:f tol:f"),
}

NAMES = {
    10: "ASSEMBLY", 11: "INSTANCE", 12: "BODY", 13: "SHELL", 14: "FACE", 15: "LOOP", 16: "EDGE",
    17: "FIN", 18: "VERTEX", 19: "REGION", 29: "POINT", 30: "LINE", 31: "CIRCLE", 32: "ELLIPSE",
    38: "INTERSECTION", 40: "CHART", 41: "LIMIT", 45: "BSPLINE_VERTICES", 50: "PLANE",
    51: "CYLINDER", 52: "CONE", 53: "SPHERE", 54: "TORUS", 56: "BLENDED_EDGE", 59: "BLEND_BOUND",
    60: "OFFSET_SURF", 67: "SWEPT_SURF", 68: "SPUN_SURF", 70: "LIST", 74: "POINTER_LIS_BLOCK",
    79: "ATT_DEF_ID", 80: "ATTRIB_DEF", 81: "ATTRIBUTE", 82: "INT_VALUES", 83: "REAL_VALUES",
    84: "CHAR_VALUES", 85: "POINT_VALUES", 86: "VECTOR_VALUES", 87: "AXIS_VALUES",
    88: "TAG_VALUES", 89: "DIRECTION_VALUES", 90: "GROUP", 91: "MEMBER_OF_GROUP",
    98: "UNICODE_VALUES", 99: "FIELD_NAMES", 100: "TRANSFORM", 101: "WORLD", 102: "KEY",
    120: "PE_SURF", 121: "INT_PE_DATA", 122: "EXT_PE_DATA", 124: "B_SURFACE", 125: "SURFACE_DATA",
    126: "NURBS_SURF", 127: "KNOT_MULT", 128: "KNOT_SET", 130: "PE_CURVE", 133: "TRIMMED_CURVE",
    134: "B_CURVE", 135: "CURVE_DATA", 136: "NURBS_CURVE", 137: "SP_CURVE", 141: "GEOMETRIC_OWNER",
    163: "HELIX_CU_FORM", 184: "HELIX_SU_FORM",
}

_KINDS = set("ucl nwdpfivbht".replace(" ", ""))
_WIDTH = {"v": 3, "h": 3, "i": 2, "b": 6}


class XTError(ValueError):
    pass


class Node:
    __slots__ = ("type", "index", "f")

    def __init__(self, ntype, index, fields):
        self.type = ntype
        self.index = index
        self.f = fields

    @property
    def name(self):
        return NAMES.get(self.type, "TYPE_%d" % self.type)

    def __getitem__(self, k):
        return self.f[k]

    def get(self, k, default=None):
        return self.f.get(k, default)

    def __repr__(self):
        return "<%s #%d>" % (self.name, self.index)


class _Reader:
    def __init__(self, data, binary):
        self.binary = binary
        if binary:
            self.b = data
        else:
            self.b = data.replace(b"\r", b"").replace(b"\n", b"")
        self.p = 0

    def need(self, n):
        if self.p + n > len(self.b):
            raise XTError("truncated at byte %d" % self.p)

    def byte(self):
        self.need(1)
        v = self.b[self.p]
        self.p += 1
        return v

    def chr(self):
        return chr(self.byte())

    _num_re = re.compile(rb"[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?")

    def _text_token(self):
        while self.p < len(self.b) and self.b[self.p] == 0x20:
            self.p += 1
        if self.p < len(self.b) and self.b[self.p:self.p + 1] == b"?":
            self.p += 1
            if self.p < len(self.b) and self.b[self.p] == 0x20:
                self.p += 1
            return None
        m = self._num_re.match(self.b, self.p)
        if not m:
            raise XTError("expected number at byte %d" % self.p)
        self.p = m.end()
        if self.p < len(self.b) and self.b[self.p] == 0x20:
            self.p += 1
        return m.group(0)

    def num(self, kind):
        if not self.binary:
            t = self._text_token()
            if t is None:
                return None
            return float(t) if kind == "f" else int(float(t))
        if kind in "ulc":
            return self.byte()
        if kind in "nw":
            self.need(2)
            v = struct.unpack_from(">h", self.b, self.p)[0]
            self.p += 2
            return None if v == NULL_INT else v
        if kind in "dt":
            self.need(4)
            v = struct.unpack_from(">i", self.b, self.p)[0]
            self.p += 4
            return None if v == NULL_INT else v
        if kind == "f":
            self.need(8)
            v = struct.unpack_from(">d", self.b, self.p)[0]
            self.p += 8
            return None if v == NULL_DBL else v
        raise XTError("unknown numeric kind %r" % kind)

    def ptr(self):
        if not self.binary:
            t = self._text_token()
            return int(t) if t is not None else 0
        self.need(2)
        r = struct.unpack_from(">h", self.b, self.p)[0]
        self.p += 2
        if r == 0:
            raise XTError("invalid pointer at byte %d" % (self.p - 2))
        if r > 0:
            return r - 1
        self.need(2)
        q = struct.unpack_from(">h", self.b, self.p)[0]
        self.p += 2
        if q <= 0:
            raise XTError("invalid long pointer at byte %d" % (self.p - 4))
        return q * 32767 - r - 1

    def string(self, n):
        if n is None or n < 0 or n > 10000000:
            raise XTError("bad string length")
        if self.binary:
            self.need(n)
            s = self.b[self.p:self.p + n].decode("latin-1")
            self.p += n
            return s
        out = []
        while len(out) < n:
            c = self.chr()
            if c == "\\":
                e = self.chr()
                rep = {"0": "\0", "n": "\n", "r": "\r", "\\": "\\", "9": " " * 9}.get(e)
                if rep is None:
                    raise XTError("unknown escape")
                out.extend(rep)
            else:
                out.append(c)
        return "".join(out[:n])

    def shortstr(self):
        return self.string(self.num("u" if self.binary else "d"))

    def value(self, kind):
        if kind == "p":
            return self.ptr()
        if kind == "c":
            return self.chr()
        if kind == "l":
            if self.binary:
                v = self.byte()
                return bool(v)
            c = self.chr()
            return c == "T"
        if kind in _WIDTH:
            if not self.binary:
                save = self.p
                while self.p < len(self.b) and self.b[self.p] == 0x20:
                    self.p += 1
                if self.b[self.p:self.p + 1] == b"?":
                    self.p += 1
                    if self.p < len(self.b) and self.b[self.p] == 0x20:
                        self.p += 1
                    return None
                self.p = save
            vals = [self.num("f") for _ in range(_WIDTH[kind])]
            return None if all(v is None for v in vals) else vals
        return self.num(kind)

    def field_descriptor(self):
        name = self.shortstr()
        ptr_class = self.num("n")
        n = self.ptr()
        kind = "p"
        if not ptr_class:
            kind = self.shortstr()
        if not re.match(r"^[A-Za-z_][A-Za-z_0-9]*$", name or "") or len(kind) != 1 or kind not in _KINDS:
            raise XTError("invalid field descriptor %r/%r" % (name, kind))
        transmit = True
        if n == 1:
            transmit = self.value("l")
        count = 1 if n == 0 else ("*" if n == 1 else n)
        return (name, kind, count, transmit)


def _header(r):
    data = r.b
    marker = b"**END_OF_HEADER"
    at = data.find(marker)
    if r.binary:
        if at >= 0:
            nl = data.find(b"\n", at)
            r.p = nl + 1 if nl >= 0 else at + len(marker)
        start = data.find(b"PS\0\0", r.p)
        if start < 0 or start > r.p + 16:
            raise XTError("missing neutral-binary magic")
        r.p = start + 4
        desc = r.string(r.num("n"))
        schema = r.string(r.num("d"))
    else:
        if at < 0:
            raise XTError("missing text banner")
        r.p = at + len(marker)
        while r.p < len(data) and data[r.p:r.p + 1] == b"*":
            r.p += 1
        if r.chr() != "T":
            raise XTError("missing text format flag")
        desc = r.string(r.num("d"))
        schema = r.string(r.num("d"))
    if not re.match(r"^SCH_\d+_\d+(_\d+)?$", schema):
        raise XTError("unexpected schema name %r" % schema)
    embedded = schema.count("_") >= 3
    max_types = None
    if embedded:
        if not schema.endswith("_13006"):
            raise XTError("unsupported base schema %s" % schema)
        max_types = r.num("n")
    userfield = r.num("d")
    if userfield:
        raise XTError("user fields are not supported")
    return dict(description=desc, schema=schema, embedded=embedded, max_types=max_types)


class Transmit:
    """A decoded transmit file: header plus nodes keyed by index."""

    def __init__(self, header, nodes):
        self.header = header
        self.nodes = nodes

    def __getitem__(self, idx):
        return self.nodes.get(idx) if idx else None

    def of_type(self, ntype):
        return [n for n in self.nodes.values() if n.type == ntype]

    def counts(self):
        out = {}
        for n in self.nodes.values():
            out[n.name] = out.get(n.name, 0) + 1
        return out


def parse(data, binary=None):
    """Decode a transmit file. `binary` is auto-detected when None."""
    if binary is None:
        binary = (data[:4] == b"PS\0\0") or (b"PS\0\0" in data[:4096])
    r = _Reader(data, binary)
    header = _header(r)
    schemas = {}
    nodes = {}
    n_total = len(r.b)
    while True:
        if r.p >= n_total:
            raise XTError("missing terminator")
        ntype = r.num("n")
        if ntype == 1:
            r.ptr()
            break
        if ntype is None or ntype <= 0:
            raise XTError("bad node type at byte %d" % r.p)
        if ntype not in schemas:
            schemas[ntype] = _read_schema(r, ntype, header["embedded"])
        fields = schemas[ntype]
        length = None
        if any(c == "*" for _, _, c, _ in fields):
            length = r.num("d")
            if length is None or length < 0 or length > 50000000:
                raise XTError("bad variable length for type %d" % ntype)
        index = r.ptr()
        if index <= 0 or index in nodes:
            raise XTError("bad or duplicate node index %d" % index)
        vals = {}
        for name, kind, count, transmit in fields:
            if count == "*":
                if not transmit:
                    vals[name] = None
                    continue
                if kind == "c":
                    vals[name] = r.string(length)
                else:
                    vals[name] = [r.value(kind) for _ in range(length)]
            elif count != 1:
                vals[name] = [r.value(kind) for _ in range(count)]
            else:
                vals[name] = r.value(kind)
        nodes[index] = Node(ntype, index, vals)
    return Transmit(header, nodes)


def _read_schema(r, ntype, embedded):
    base = BASE.get(ntype)
    if not embedded:
        if base is None:
            raise XTError("node type %d not in built-in schema" % ntype)
        return [(n, k, c, True) for n, k, c in base]
    declared = r.byte()
    if declared == 255:
        if base is None:
            raise XTError("node type %d has no base schema" % ntype)
        return [(n, k, c, True) for n, k, c in base]
    if base is None:
        name = r.shortstr()
        r.shortstr()  # description
        NAMES.setdefault(ntype, name)
        return [r.field_descriptor() for _ in range(declared)]
    fields = []
    cursor = 0
    while True:
        op = r.chr()
        if op == "Z":
            break
        if op in "CD":
            if cursor >= len(base):
                raise XTError("schema edit overruns base for type %d" % ntype)
            if op == "C":
                n, k, c = base[cursor]
                fields.append((n, k, c, True))
            cursor += 1
        elif op in "IA":
            fields.append(r.field_descriptor())
        else:
            raise XTError("unknown schema edit %r for type %d" % (op, ntype))
    if cursor != len(base) or len(fields) != declared:
        raise XTError("schema edit count mismatch for type %d" % ntype)
    return fields
