# SPDX-License-Identifier: GPL-3.0-or-later

"""The packed form of a mesh's geometry: small, read once from start to end.

Connectivity walks faces across shared edges over positions welded together, so a face
usually costs one symbol per new corner. Each stream then follows that order:

    Connectivity: gates, then for each corner of a face, new or a nearby open edge
    Splits: which of a position's vertices each corner uses
    Positions: parallelogram prediction on the grid, across the edge reaching a face
    Normals: prediction from the vertex normal of the decoded positions
    UVs: prediction from the triangle's shape in space

Every stream takes corners in walk order in one pass, needing no tables of neighbours,
which is what keeps decoding fast. Each predicted value is a rANS number. Everything
else a mesh holds stays in its usual sections. All prediction uses IEEE doubles and
rounds halves to even, so every reader gets exactly the same integers.
"""

import math
import struct
from array import array
from dataclasses import replace

from ..container.layout import NO_GRID, MeshFlags, SectionKind
from ..container.sections import Section, SectionReader
from ..errors import FormatError
from ..mesh import Mesh
from ..processing.faces import corner_normals_from_faces, face_edges, face_lists
from ..processing.reordering import reorder_by
from ..storage.rans import SymbolReader, SymbolWriter
from .quantize import decode_normal, decode_uv, encode_normal, encode_uv

_VERTEX_NEW = 0
_VERTEX_INTO_LAST = 1
_VERTEX_INTO_FIRST = 2
_VERTEX_INTO_LAST_OLDER = 3
_VERTEX_FAR = 4
_VERTEX_FORWARD = 5
_VERTEX_BACK = 6
_VERTEX_SYMBOLS = 7
# Contexts tell apart new, the three candidates and anything further.
_CONTEXT_SYMBOLS = 5
# The furthest a corner may name a position along the open boundary.
_MAX_STEPS = 64
_CANDIDATE_KINDS = 4
_REMAINING_KINDS = 5
_GATE_REMAINING = 3
_COUNT_CONTEXTS = 10
_TYPICAL_FACE_COUNT = 6
_SPLIT_CONTEXTS = 8
_POSITION_CONTEXTS = 4
_POSITION_HISTORY_STEP = 4
_POSITION_CONTEXT_MIN_ROWS = 4000
_ACROSS_KINDS = 8
_ACROSS_HISTORY_KINDS = 6
_ACROSS_HISTORY_STEP = 3
_FACING_KINDS = 5
_POSITION_KEYINGS = (0, 1, 2)
_MAX_SEEN_CONTEXT = 2
_HISTORY_MAX = 14
_NORMAL_HISTORY_STEP = 3
_NORMAL_HISTORY_BUCKETS = _HISTORY_MAX // _NORMAL_HISTORY_STEP + 1
_NORMAL_VALUE_CONTEXTS = 2 * _NORMAL_HISTORY_BUCKETS
_NORMAL_CONTEXTS = _MAX_SEEN_CONTEXT + 1 + _NORMAL_VALUE_CONTEXTS
_UV_HISTORY_STEP = 4
_UV_HISTORY_BUCKETS = _HISTORY_MAX // _UV_HISTORY_STEP + 1
_UV_SIDE_CONTEXT = _MAX_SEEN_CONTEXT + 1
_UV_VALUE_FIRST = _UV_SIDE_CONTEXT + 1
_UV_CONTEXTS = _UV_VALUE_FIRST + _UV_HISTORY_BUCKETS
_AXES = 3
_SEEN_LOOKUPS = (0, 1, 2)
_PAIR_KINDS = 8
_UV_PREDICTION_KINDS = 3
_CHOICE_ESCAPE = 200
_STEP = struct.Struct("<f")
_SPAN_BITS = 20
_STEP_MANTISSA_BITS = 12
# Grid integers stay this far inside Float32's precision, so a position read back
# always rounds to the same integer again.
_SPAN_HEADROOM_BITS = 21


def _write_choice(writer: SymbolWriter, context: int, choice: int) -> None:
    """Write a small whole number as one symbol, with room for the odd large one."""
    if choice < _CHOICE_ESCAPE:
        writer.write(context, choice)
    else:
        writer.write(context, _CHOICE_ESCAPE)
        writer.write_number(context, choice - _CHOICE_ESCAPE)


def _read_choice(reader: SymbolReader, context: int) -> int:
    choice = reader.read(context)
    if choice == _CHOICE_ESCAPE:
        return _CHOICE_ESCAPE + reader.read_number(context)
    return choice


def _magnitude(value: int) -> int:
    return min(abs(value).bit_length(), _HISTORY_MAX)


class _OpenEdges:
    """Directed edges with a face on one side so far. A walk's only working state.

    A gate is (u, v, the corners holding u, v, and the face's next corner after v).
    When a face closes an open edge, each of its two corners there gets the corner
    across: the earlier face's corner at the same position.
    """

    def __init__(self):
        self.into = {}
        self.out_of = {}
        self.gates = []
        self.edge_corners = {}
        self.across = {}

    def is_open(self, u: int, v: int) -> bool:
        return u in self.into.get(v, ())

    def remove(self, u: int, v: int) -> tuple:
        """Close the newest open edge from u to v; return the corners holding u, v."""
        into = self.into[v]
        del into[len(into) - 1 - into[::-1].index(u)]
        out_of = self.out_of[u]
        del out_of[len(out_of) - 1 - out_of[::-1].index(v)]
        return self.edge_corners[(u, v)].pop()

    def add_face(self, ring: list, first_corner: int) -> None:
        count = len(ring)
        for at in range(count):
            u, v = ring[at], ring[(at + 1) % count]
            corner_u, corner_v = first_corner + at, first_corner + (at + 1) % count
            if self.is_open(v, u):
                earlier_v, earlier_u = self.remove(v, u)
                self.across.setdefault(corner_u, earlier_u)
                self.across.setdefault(corner_v, earlier_v)
            else:
                self.into.setdefault(v, []).append(u)
                self.out_of.setdefault(u, []).append(v)
                self.edge_corners.setdefault((u, v), []).append((corner_u, corner_v))
                self.gates.append(
                    (
                        u,
                        v,
                        first_corner + at,
                        first_corner + (at + 1) % count,
                        first_corner + (at + 2) % count,
                    )
                )

    def candidates(self, last: int | None, first: int | None) -> tuple:
        """Return (the vertex closing into last, out of first, older into last)."""
        into_last = self.into.get(last, ()) if last is not None else ()
        out_of_first = self.out_of.get(first, ()) if first is not None else ()
        return (
            into_last[-1] if into_last else None,
            out_of_first[-1] if out_of_first else None,
            into_last[-2] if len(into_last) > 1 else None,
        )


class _ConnectivityModel:
    """Which context each connectivity symbol takes. The stream's first byte picks one.

    Model 0 keys a corner on the symbol before it and whether it ends its face. Model 1
    adds the symbol before that and which candidates exist, which pays on larger meshes.
    Model 2 stores each new position's face count, then keys a corner on how many faces
    its face's first and last positions still need, and a gate on its two ends: on
    regular meshes that settles most corners.
    """

    def __init__(self, model: int):
        self.model = model
        if model == 2:
            vertex_contexts = _REMAINING_KINDS * _REMAINING_KINDS * 2 * _CANDIDATE_KINDS
            gate_contexts = _GATE_REMAINING * _GATE_REMAINING
        else:
            vertex_contexts = _CONTEXT_SYMBOLS * 2
            if model:
                vertex_contexts *= _CONTEXT_SYMBOLS * _CANDIDATE_KINDS
            gate_contexts = 1
        self.writes_counts = model == 2
        self.gate_first = vertex_contexts
        self.far = vertex_contexts + gate_contexts
        self.arity = self.far + 1
        self.steps = self.far + 2
        self.count_first = self.far + 3
        counts = _COUNT_CONTEXTS * (_COUNT_CONTEXTS + 1)
        self.count = self.count_first + (counts if model == 2 else 0)

    def vertex(self, previous, before, is_last, kinds, first_left, last_left) -> int:
        if self.model == 2:
            context = _remaining_kind(first_left) * _REMAINING_KINDS
            context = (context + _remaining_kind(last_left)) * 2 + (1 if is_last else 0)
            return context * _CANDIDATE_KINDS + kinds
        previous = min(previous, _CONTEXT_SYMBOLS - 1)
        context = previous * 2 + (1 if is_last else 0)
        if self.model:
            before = min(before, _CONTEXT_SYMBOLS - 1)
            context = (context * _CONTEXT_SYMBOLS + before) * _CANDIDATE_KINDS + kinds
        return context

    def gate(self, u_left: int, v_left: int) -> int:
        if self.model != 2:
            return self.gate_first
        u_kind = min(u_left, _GATE_REMAINING - 1)
        return (
            self.gate_first
            + u_kind * _GATE_REMAINING
            + min(v_left, _GATE_REMAINING - 1)
        )

    def face_count(self, previous_count: int, first_count) -> int:
        """Key a face count on the one before and on the ring's first position's."""
        first = 0 if first_count is None else 1 + min(first_count, _COUNT_CONTEXTS - 1)
        context = min(previous_count, _COUNT_CONTEXTS - 1) * (_COUNT_CONTEXTS + 1)
        return self.count_first + context + first


_CONNECTIVITY_MODELS = tuple(_ConnectivityModel(model) for model in range(3))


def _remaining_kind(left) -> int:
    """Return 0 for no position, else 1 + the faces it still needs, up to 3."""
    return 0 if left is None else 1 + min(max(left, 0), _REMAINING_KINDS - 2)


class _FaceCounts:
    """How many faces each position has in all, and how many are placed so far."""

    def __init__(self):
        self.total = {}
        self.placed = {}

    def left(self, position) -> int | None:
        if position is None:
            return None
        return self.total.get(position, 0) - self.placed.get(position, 0)

    def place(self, ring: list) -> None:
        for position in ring:
            self.placed[position] = self.placed.get(position, 0) + 1


def _candidate_kinds(open_edges: _OpenEdges, ring: list) -> int:
    """Return bit 0 set if an open edge leads into the ring's last vertex, bit 1 out
    of its first."""
    if not ring:
        return 0
    into = 1 if open_edges.into.get(ring[-1]) else 0
    return into | (2 if open_edges.out_of.get(ring[0]) else 0)


def _along(open_edges: _OpenEdges, start: int, steps: int, forward: bool):
    """Return the position this many open edges on from start, or None."""
    links = open_edges.out_of if forward else open_edges.into
    at = start
    for _ in range(steps):
        following = links.get(at)
        if not following:
            return None
        at = following[-1]
    return at


def _steps_to(open_edges: _OpenEdges, start: int, target: int, forward: bool):
    """Return how many open edges on from start the target is, within reach, or None."""
    links = open_edges.out_of if forward else open_edges.into
    at = start
    for steps in range(1, _MAX_STEPS + 1):
        following = links.get(at)
        if not following:
            return None
        at = following[-1]
        if at == target:
            return steps
    return None


def _vertex_symbol(open_edges, vertex, ring, newest) -> tuple:
    """Return (the symbol naming this corner's position, then its number or None).

    A position no candidate names is usually a few open edges along the boundary,
    forward from the ring's last corner or back from its first. Only one beyond
    reach is counted back from the newest position.
    """
    if vertex == newest + 1:
        return _VERTEX_NEW, None
    last = ring[-1] if ring else None
    first = ring[0] if ring else None
    into_last, into_first, older = open_edges.candidates(last, first)
    for symbol, candidate in (
        (_VERTEX_INTO_LAST, into_last),
        (_VERTEX_INTO_FIRST, into_first),
        (_VERTEX_INTO_LAST_OLDER, older),
    ):
        if candidate == vertex:
            return symbol, None
    if ring:
        forward = _steps_to(open_edges, last, vertex, True)
        back = _steps_to(open_edges, first, vertex, False)
        if forward is not None and (back is None or forward <= back):
            return _VERTEX_FORWARD, forward - 1
        if back is not None:
            return _VERTEX_BACK, back - 1
    return _VERTEX_FAR, newest - vertex


def _read_vertex(reader, model, open_edges, ring, newest, context) -> tuple:
    """Return (vertex, newest vertex, the symbol used)."""
    symbol = reader.read(context)
    if symbol == _VERTEX_NEW:
        return newest + 1, newest + 1, symbol
    if symbol == _VERTEX_FAR:
        vertex = newest - reader.read_number(model.far)
        if not 0 <= vertex <= newest:
            raise FormatError("A packed corner names a missing position")
        return vertex, newest, symbol
    if symbol in (_VERTEX_FORWARD, _VERTEX_BACK):
        steps = _read_choice(reader, model.steps) + 1
        if not ring or not 1 <= steps <= _MAX_STEPS:
            raise FormatError("A packed corner reaches too far along the boundary")
        forward = symbol == _VERTEX_FORWARD
        vertex = _along(open_edges, ring[-1] if forward else ring[0], steps, forward)
        if vertex is None:
            raise FormatError("A packed corner names a missing open edge")
        return vertex, newest, symbol
    if symbol >= _VERTEX_SYMBOLS:
        raise FormatError("A packed corner has an unknown symbol")
    last = ring[-1] if ring else None
    first = ring[0] if ring else None
    vertex = open_edges.candidates(last, first)[symbol - 1]
    if vertex is None:
        raise FormatError("A packed corner names a missing open edge")
    return vertex, newest, symbol


class _Walk:
    """The encoder's walk over faces, choosing their order and starting corners."""

    def __init__(self, faces: list, materials: list, is_uniform: bool):
        self.faces = faces
        self.materials = materials
        self.is_uniform = is_uniform
        # What the walk decided, in order, to write under either model.
        self.events = []
        self.ids = {}
        self.sources = []
        self.newest = -1
        self.previous = _VERTEX_NEW
        self.before = _VERTEX_NEW
        self.open_edges = _OpenEdges()
        self.counts = _FaceCounts()
        self.source_counts = {}
        for face in faces:
            for source in face:
                self.source_counts[source] = self.source_counts.get(source, 0) + 1
        self.previous_count = _TYPICAL_FACE_COUNT
        self.used = [False] * len(faces)
        self.order = []
        self.turns = {}
        self.corner = 0
        self.by_edge = {}
        for index, face in enumerate(faces):
            for at, u in enumerate(face):
                edge = (u, face[(at + 1) % len(face)])
                self.by_edge.setdefault(edge, []).append((index, at))

    def _place(self, index: int, turn: int, known: int) -> None:
        """Write a face starting at this corner, its first known corners implied."""
        face = self.faces[index]
        count = len(face)
        turned = face[turn:] + face[:turn]
        if not self.is_uniform:
            self.events.append(("arity", count))
        ring = [self.ids[source] for source in turned[:known]]
        for at, source in enumerate(turned[known:], known):
            walk_id = self.ids.get(source, self.newest + 1)
            symbol, number = _vertex_symbol(self.open_edges, walk_id, ring, self.newest)
            kinds = _candidate_kinds(self.open_edges, ring)
            first_left = self.counts.left(ring[0] if ring else None)
            last_left = self.counts.left(ring[-1] if ring else None)
            self.events.append(
                (
                    "vertex",
                    symbol,
                    self.previous,
                    self.before,
                    at == count - 1,
                    kinds,
                    first_left,
                    last_left,
                )
            )
            if symbol == _VERTEX_FAR:
                self.events.append(("far", number))
            elif number is not None:
                self.events.append(("steps", number))
            if symbol == _VERTEX_NEW:
                self.newest = walk_id
                total = self.source_counts[source]
                self.counts.total[walk_id] = total
                first_count = self.counts.total.get(ring[0]) if ring else None
                self.events.append(("count", total, self.previous_count, first_count))
                self.previous_count = total
            self.before, self.previous = self.previous, symbol
            if source not in self.ids:
                self.ids[source] = walk_id
                self.sources.append(source)
            ring.append(walk_id)
        self.open_edges.add_face(ring, self.corner)
        self.counts.place(ring)
        self.corner += count
        self.used[index] = True
        self.order.append(index)
        self.turns[index] = turn

    def _next_across(self, u: int, v: int, material: int):
        for index, at in self.by_edge.get((self.sources[v], self.sources[u]), ()):
            if not self.used[index] and self.materials[index] == material:
                return index, at
        return None

    def run(self) -> None:
        starts = sorted(range(len(self.faces)), key=self.materials.__getitem__)
        placed = 0
        total = len(self.faces)
        gates = self.open_edges.gates
        for start in starts:
            if self.used[start]:
                continue
            self._place(start, 0, 0)
            placed += 1
            material = self.materials[start]
            while gates and placed < total:
                u, v, *_ = gates.pop()
                if not self.open_edges.is_open(u, v):
                    continue
                found = self._next_across(u, v, material)
                ends = (self.counts.left(u), self.counts.left(v))
                self.events.append(("gate", 1 if found else 0, *ends))
                if not found:
                    self.open_edges.remove(u, v)
                    continue
                self._place(found[0], found[1], 2)
                placed += 1
            if placed == total:
                return

    def to_bytes(self) -> bytes:
        """Return the walk under whichever model makes it smaller."""
        return min((self._written(model) for model in _CONNECTIVITY_MODELS), key=len)

    def _written(self, model: _ConnectivityModel) -> bytes:
        writer = SymbolWriter(model.count)
        for event in self.events:
            if event[0] == "vertex":
                writer.write(model.vertex(*event[2:]), event[1])
            elif event[0] == "far":
                writer.write_number(model.far, event[1])
            elif event[0] == "steps":
                _write_choice(writer, model.steps, event[1])
            elif event[0] == "gate":
                writer.write(model.gate(*event[2:]), event[1])
            elif event[0] == "count":
                if model.writes_counts:
                    _write_choice(writer, model.face_count(*event[2:]), event[1])
            else:
                _write_choice(writer, model.arity, event[1])
        return bytes((model.model,)) + writer.to_bytes()


def read_connectivity(data: bytes, faces: int, arity: int, corner_count: int) -> tuple:
    """Return (welded position per corner, face starts, gate per face, positions)."""
    if not data or data[0] >= len(_CONNECTIVITY_MODELS):
        raise FormatError("Packed connectivity names no known model")
    model = _CONNECTIVITY_MODELS[data[0]]
    reader = SymbolReader(data[1:], model.count)
    open_edges = _OpenEdges()
    counts = _FaceCounts()
    welded = []
    starts = []
    gates_of_faces = []
    newest = -1
    previous = before = _VERTEX_NEW
    previous_count = _TYPICAL_FACE_COUNT

    def read_face(known: list, gate) -> None:
        nonlocal newest, previous, before, previous_count
        count = arity or _read_choice(reader, model.arity)
        if count < 3 or len(welded) + count > corner_count:
            raise FormatError("A packed face has an impossible size")
        ring = list(known)
        for at in range(len(known), count):
            kinds = _candidate_kinds(open_edges, ring)
            first_left = counts.left(ring[0] if ring else None)
            last_left = counts.left(ring[-1] if ring else None)
            context = model.vertex(
                previous, before, at == count - 1, kinds, first_left, last_left
            )
            vertex, newest, symbol = _read_vertex(
                reader, model, open_edges, ring, newest, context
            )
            if symbol == _VERTEX_NEW and model.writes_counts:
                first_count = counts.total.get(ring[0]) if ring else None
                context = model.face_count(previous_count, first_count)
                total = _read_choice(reader, context)
                if not 1 <= total <= corner_count:
                    raise FormatError("A packed position has an impossible face count")
                counts.total[vertex] = total
                previous_count = total
            before, previous = previous, symbol
            ring.append(vertex)
        first = len(welded)
        starts.append(first)
        gates_of_faces.append(gate)
        welded.extend(ring)
        open_edges.add_face(ring, first)
        counts.place(ring)

    gates = open_edges.gates
    while len(starts) < faces:
        read_face([], None)
        while gates and len(starts) < faces:
            u, v, corner_u, corner_v, corner_third = gates.pop()
            if not open_edges.is_open(u, v):
                continue
            if reader.read(model.gate(counts.left(u), counts.left(v))):
                read_face([v, u], (corner_v, corner_u, corner_third))
            else:
                open_edges.remove(u, v)
    starts.append(len(welded))
    across = [open_edges.across.get(corner) for corner in range(len(welded))]
    return welded, starts, gates_of_faces, newest + 1, across


class _Layout:
    """What each stream reads from connectivity, shared by writing and reading."""

    def __init__(self, welded, starts, gates, position_count, across):
        self.across = across
        self.welded = welded
        self.starts = starts
        self.gates = gates
        self.position_count = position_count

    def corners(self):
        """Yield (corner, its face's first corner, its place in the face, the face)."""
        for face in range(len(self.starts) - 1):
            first = self.starts[face]
            for corner in range(first, self.starts[face + 1]):
                yield corner, first, corner - first, face


def _parallelogram(layout, first: int, at: int, face: int, rows: list, shrink: int = 0):
    """Return a corner's predicted position from those before it in the walk.

    Shrunk, the parallelogram's far corner moves a quarter of the way back toward the
    shared edge's midpoint: on irregular scans a full one overshoots. In integers,
    (7 (a + b) - 6 c + 4) // 8, so every reader agrees.
    """
    welded = layout.welded
    if at == 0:
        return None
    if at == 1:
        return rows[welded[first]]
    a = rows[welded[first + at - 1]]
    third = _third_corner(layout, first, at, face)
    if third is None:
        return a
    b = rows[welded[first]]
    c = rows[welded[third]]
    if shrink:
        return tuple((7 * (a[i] + b[i]) - 6 * c[i] + 4) // 8 for i in range(_AXES))
    return (a[0] + b[0] - c[0], a[1] + b[1] - c[1], a[2] + b[2] - c[2])


def _third_corner(layout: _Layout, first: int, at: int, face: int):
    """Return the corner a parallelogram subtracts, or None if it has none."""
    if at < 2:
        return None
    if at > 2:
        return first + 1
    gate = layout.gates[face]
    return None if gate is None else gate[2]


def _normal_shares(layout: _Layout, first: int, at: int, face: int, rows: list):
    """Return, per axis, a quarter step of how much the parallelogram's triangle faces
    along it, up to 3, or None without one. Integers only, so readers agree."""
    third = _third_corner(layout, first, at, face)
    if third is None:
        return None
    welded = layout.welded
    a = rows[welded[first + at - 1]]
    b = rows[welded[first]]
    c = rows[welded[third]]
    ux, uy, uz = a[0] - c[0], a[1] - c[1], a[2] - c[2]
    vx, vy, vz = b[0] - c[0], b[1] - c[1], b[2] - c[2]
    normal = (abs(uy * vz - uz * vy), abs(uz * vx - ux * vz), abs(ux * vy - uy * vx))
    total = sum(normal)
    if not total:
        return None
    return [min(4 * part // total, 3) for part in normal]


def packing_step(positions, exponent: int) -> float:
    """Return the grid step packed positions use, or 0 for the power-of-two grid.

    The step is the model's largest size over 2^20, as Draco's 20-bit grid, rounded
    down to 6 significant bits, so no point moves more than 1 / 2^21 of that size: the
    same bound as the power-of-two grid, which can be up to twice as fine. Positions
    already exactly on a neighbouring step keep it, so saving again changes nothing.
    Kept only when coarser than the power-of-two grid and when positions read back
    from Float32 land on the same integers.
    """
    if exponent == NO_GRID or not positions:
        return 0.0
    extent = max(
        max(positions[axis::_AXES]) - min(positions[axis::_AXES])
        for axis in range(_AXES)
    )
    if extent <= 0:
        return 0.0
    scale = math.frexp(math.ldexp(extent, -_SPAN_BITS))[1] - _STEP_MANTISSA_BITS
    whole = math.floor(math.ldexp(extent, -_SPAN_BITS - scale))
    steps = [math.ldexp(value, scale) for value in (whole, whole - 1, whole + 1)]
    step = next((step for step in steps if _on_grid(positions, step)), steps[0])
    largest = max(abs(value) for value in positions)
    if step <= math.ldexp(1.0, exponent):
        return 0.0
    return 0.0 if largest > math.ldexp(step, _SPAN_HEADROOM_BITS) else step


def _on_grid(positions, step: float) -> bool:
    if step <= 0:
        return False
    snapped = array("f", [round(value / step) * step for value in positions])
    return snapped == positions


def snap_to_step(
    mesh: Mesh, source, step: float, exponent: int, from_faces: bool
) -> Mesh:
    """Return the mesh with positions on the packing grid, as a reader gets them back.

    Positions round from source, the positions before any snapping, so each moves at
    most half a step. Shape targets keep their moves in steps of 2^exponent, as their
    sections store them.
    """
    if not step:
        return mesh
    old = mesh.positions
    new = array("f", [round(value / step) * step for value in source])
    unit = math.ldexp(1.0, exponent)
    targets = []
    for target in mesh.shape_targets:
        moved = array("f", new)
        offsets = [buffer for buffer in (target.normal_offsets, target.tangent_offsets)]
        for vertex in range(len(new) // _AXES):
            slots = range(vertex * _AXES, vertex * _AXES + _AXES)
            if all(target.positions[slot] == old[slot] for slot in slots) and not any(
                buffer[slot] for buffer in offsets if buffer for slot in slots
            ):
                continue
            # A changed vertex comes back from the base's power-of-two grid point.
            for slot in slots:
                delta = round(target.positions[slot] / unit) - round(old[slot] / unit)
                moved[slot] = math.ldexp(round(new[slot] / unit) + delta, exponent)
        targets.append(replace(target, positions=moved))
    snapped = replace(mesh, positions=new, shape_targets=targets)
    if from_faces:
        snapped.corner_normals = corner_normals_from_faces(new, face_lists(snapped))
    return snapped


def _encode_positions(layout: _Layout, rows: list, extra: list, step: float) -> bytes:
    """Return the step, then positions in whichever form is smallest.

    The form byte's low two bits are the fine level; bit 4 shrinks the parallelogram;
    bits 2 and 3 say what keys each axis:
    0 its own last size, 1 also the earlier axes of the same position, 2 also how much
    the parallelogram's triangle faces along it, since a prediction misses most
    across the surface.
    """
    best = min(
        (
            bytes((fine | keying << 2,))
            + _encode_positions_with(layout, rows, extra, fine, keying)
            for fine in (0, 1, 2)
            for keying in _POSITION_KEYINGS
        ),
        key=len,
    )
    # Shrinking is tried with the form that won, which keeps saving time down.
    form = best[0]
    shrunk = bytes((form | 16,)) + _encode_positions_with(
        layout, rows, extra, form & 3, form >> 2, 1
    )
    return _STEP.pack(step) + min(best, shrunk, key=len)


def _position_context(
    keying: int, uses_history: bool, history, earlier, axis, shares
) -> int:
    own = min(history[axis] // _ACROSS_HISTORY_STEP, _ACROSS_HISTORY_KINDS - 1)
    if keying == 2:
        facing = 0 if shares is None else shares[axis] + 1
        return facing * _ACROSS_HISTORY_KINDS + own
    if keying:
        reach = max(earlier) if earlier else history[axis]
        return min(reach // 2, _ACROSS_KINDS - 1) * _ACROSS_HISTORY_KINDS + own
    context = history[axis] // _POSITION_HISTORY_STEP if uses_history else 0
    return min(context, _POSITION_CONTEXTS - 1)


def _position_contexts(keying: int) -> int:
    if keying == 2:
        return _FACING_KINDS * _ACROSS_HISTORY_KINDS
    return _ACROSS_KINDS * _ACROSS_HISTORY_KINDS if keying else _POSITION_CONTEXTS


def _encode_positions_with(
    layout: _Layout, rows: list, extra: list, fine: int, keying: int, shrink: int = 0
) -> bytes:
    writer = SymbolWriter(_position_contexts(keying), fine=fine)
    previous = (0, 0, 0)
    history = [0, 0, 0]
    uses_history = layout.position_count >= _POSITION_CONTEXT_MIN_ROWS
    made = 0
    for corner, first, at, face in layout.corners():
        position = layout.welded[corner]
        if position != made:
            continue
        made += 1
        guess = _parallelogram(layout, first, at, face, rows, shrink) or previous
        shares = _normal_shares(layout, first, at, face, rows) if keying == 2 else None
        row = rows[position]
        earlier = []
        for axis in range(_AXES):
            difference = row[axis] - guess[axis]
            context = _position_context(
                keying, uses_history, history, earlier, axis, shares
            )
            writer.write_number(context, difference)
            history[axis] = _magnitude(difference)
            earlier.append(history[axis])
        previous = row
    for row in extra:
        for axis in range(_AXES):
            writer.write_number(0, row[axis] - previous[axis])
        previous = row
    return writer.to_bytes()


def _read_positions(layout: _Layout, data: bytes, extra: int) -> tuple:
    form = data[_STEP.size] if len(data) > _STEP.size else 255
    if form & 3 == 3 or form >> 2 & 3 not in _POSITION_KEYINGS or form >> 5:
        raise FormatError("Packed positions name no known number form")
    keying = form >> 2 & 3
    shrink = form >> 4
    reader = SymbolReader(
        data[_STEP.size + 1 :], _position_contexts(keying), fine=form & 3
    )
    rows = []
    previous = (0, 0, 0)
    history = [0, 0, 0]
    uses_history = layout.position_count >= _POSITION_CONTEXT_MIN_ROWS
    for corner, first, at, face in layout.corners():
        if layout.welded[corner] != len(rows):
            continue
        guess = _parallelogram(layout, first, at, face, rows, shrink) or previous
        shares = _normal_shares(layout, first, at, face, rows) if keying == 2 else None
        values = []
        earlier = []
        for axis in range(_AXES):
            context = _position_context(
                keying, uses_history, history, earlier, axis, shares
            )
            difference = reader.read_number(context)
            values.append(guess[axis] + difference)
            history[axis] = _magnitude(difference)
            earlier.append(history[axis])
        row = tuple(values)
        rows.append(row)
        previous = row
    extras = []
    for _ in range(extra):
        row = tuple(previous[axis] + reader.read_number(0) for axis in range(_AXES))
        extras.append(row)
        previous = row
    return rows, extras


class _Splits:
    """Which vertex at its position each corner uses, read and written alike.

    Model 0 names a vertex by when it was made, from 1. Model 1 names first a guess:
    for a face's first two corners, the vertex across its gate, else the vertex last
    used here; then the rest, newest first. Model 2 guesses the vertex of the corner
    across wherever a face closed an open edge there, and since seams run in chains,
    also keys on the face's last choice and on whether the vertex across began at a
    seam. Either way 0 is a new vertex.
    """

    def __init__(self, layout: _Layout, by_gate: int):
        self.layout = layout
        self.by_gate = by_gate
        # Only corners recorded so far, so writer and reader see exactly the same.
        self.vertex_of = {}
        self.seen = {}
        self.recent = {}
        self.face = None
        self.face_choice = 0
        self.seams = set()

    def options(self, corner: int, at: int, face: int) -> tuple | None:
        """Return (context, the vertices this corner may name), or None if none yet."""
        position = self.layout.welded[corner]
        known = self.seen.get(position)
        if not known:
            return None
        if not self.by_gate:
            return min(len(known), 3), known
        if self.by_gate == 2:
            if face != self.face:
                self.face, self.face_choice = face, 0
            # A face may close its own edge, across to a corner not read yet.
            guess = self.vertex_of.get(self.layout.across[corner])
            from_gate = guess is not None
            if not from_gate:
                guess = self.recent[position]
            rest = [vertex for vertex in reversed(known) if vertex != guess]
            seam = 1 if from_gate and guess in self.seams else 0
            through = 1 if self.layout.gates[face] is not None and at < 2 else 0
            context = min(len(known), 3) + (4 if from_gate else 0)
            context += 8 * (self.face_choice * 2 + seam)
            return context + 64 * through, [guess, *rest]
        gate = self.layout.gates[face]
        from_gate = gate is not None and at < 2
        guess = self.vertex_of[gate[at]] if from_gate else self.recent[position]
        rest = [vertex for vertex in reversed(known) if vertex != guess]
        return min(len(known), 3) + (4 if from_gate else 0), [guess, *rest]

    def record(self, corner: int, vertex: int, index: int | None) -> None:
        """Note the vertex a corner uses; index is the choice made, or None if none."""
        position = self.layout.welded[corner]
        known = self.seen.setdefault(position, [])
        if vertex not in known:
            known.append(vertex)
        self.recent[position] = vertex
        self.vertex_of[corner] = vertex
        if index is not None:
            self.face_choice = 1 if index == 0 else 3 if index == 1 else 2
            if index == 0:
                self.seams.add(vertex)


def _split_contexts(model: int) -> int:
    return _SPLIT_CONTEXTS * (16 if model == 2 else 1)


def _encode_splits(layout: _Layout, corners: list) -> bytes:
    """Return the splits under whichever model makes them smaller."""
    written = []
    for by_gate in (0, 1, 2):
        writer = SymbolWriter(_split_contexts(by_gate))
        splits = _Splits(layout, by_gate)
        for corner, _first, at, face in layout.corners():
            vertex = corners[corner]
            choice = splits.options(corner, at, face)
            index = None
            if choice is not None:
                context, options = choice
                index = options.index(vertex) + 1 if vertex in options else 0
                _write_choice(writer, context, index)
            splits.record(corner, vertex, index)
        written.append(bytes((by_gate,)) + writer.to_bytes())
    return min(written, key=len)


def _read_splits(data: bytes, layout: _Layout) -> tuple:
    """Return (each corner's vertex, each vertex's welded position)."""
    if not data or data[0] > 2:
        raise FormatError("Packed splits name no known model")
    reader = SymbolReader(data[1:], _split_contexts(data[0]))
    corners = [0] * len(layout.welded)
    position_of = []
    splits = _Splits(layout, data[0])
    for corner, _first, at, face in layout.corners():
        choice = splits.options(corner, at, face)
        index = 0
        if choice is not None:
            context, options = choice
            index = _read_choice(reader, context)
            if not 0 <= index <= len(options):
                raise FormatError("A packed corner names a missing vertex")
        if index:
            vertex = options[index - 1]
        else:
            vertex = len(position_of)
            position_of.append(layout.welded[corner])
        corners[corner] = vertex
        splits.record(corner, vertex, index if choice is not None else None)
    return corners, position_of


def _vertex_normals(starts: list, keys: list, rows: list) -> list:
    """Return each key's stored direction from the areas of faces around it.

    A key is what a corner's normal follows: its welded position or its vertex.
    """
    totals = [[0, 0, 0] for _ in rows]
    for face in range(len(starts) - 1):
        first = starts[face]
        a = keys[first]
        pa = rows[a]
        for at in range(first + 1, starts[face + 1] - 1):
            b, c = keys[at], keys[at + 1]
            pb, pc = rows[b], rows[c]
            ux, uy, uz = pb[0] - pa[0], pb[1] - pa[1], pb[2] - pa[2]
            vx, vy, vz = pc[0] - pa[0], pc[1] - pa[1], pc[2] - pa[2]
            nx, ny, nz = uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx
            for corner in (a, b, c):
                total = totals[corner]
                total[0] += nx
                total[1] += ny
                total[2] += nz
    return [encode_normal(float(x), float(y), float(z)) for x, y, z in totals]


def _normal_context(seen: list, history: int) -> int:
    offset = 1 if seen else 0
    return (
        _MAX_SEEN_CONTEXT
        + 1
        + offset * _NORMAL_HISTORY_BUCKETS
        + history // _NORMAL_HISTORY_STEP
    )


class _SeenLookup:
    """The values already seen where a corner looks: lookup 0 its welded position,
    1 its vertex, 2 its vertex if it has any, else its position. Lookup 2 keys its
    vertex choices from vertex_contexts up."""

    def __init__(self, layout: _Layout, corners: list, lookup: int, vertex_contexts):
        self.welded = layout.welded
        self.corners = corners
        self.lookup = lookup
        self.vertex_contexts = vertex_contexts
        self.by_position = {}
        self.by_vertex = {}

    def options(self, corner: int) -> tuple:
        """Return (context, the values this corner may name); the list may be empty."""
        position = self.by_position.setdefault(self.welded[corner], [])
        vertex = self.by_vertex.setdefault(self.corners[corner], [])
        if self.lookup == 1 or self.lookup == 2 and vertex:
            context = min(len(vertex), _MAX_SEEN_CONTEXT)
            return context + (self.vertex_contexts if self.lookup == 2 else 0), vertex
        return min(len(position), _MAX_SEEN_CONTEXT), position

    def record(self, corner: int, pair: tuple) -> None:
        for seen in (
            self.by_position[self.welded[corner]],
            self.by_vertex[self.corners[corner]],
        ):
            if pair not in seen:
                seen.append(pair)


def _normal_keys(layout: _Layout, corners: list, rows: list, by_vertex: int) -> tuple:
    """Return (each corner's key, each key's grid row) for this way of predicting.

    Smooth meshes predict best around a welded position, since a UV seam does not
    bend the surface; meshes with hard edges predict best around each vertex.
    """
    if not by_vertex:
        return layout.welded, rows
    vertex_rows = [None] * (max(corners) + 1 if corners else 0)
    for vertex, position in zip(corners, layout.welded, strict=True):
        vertex_rows[vertex] = rows[position]
    return corners, vertex_rows


def _encode_normals(layout: _Layout, corners: list, pairs: list, rows: list) -> bytes:
    """Return the normals each way, whichever is smaller.

    The first byte's bit 0 predicts around vertices, bit 1 uses fine numbers, bits 2
    and 3 say where a corner looks for a normal already seen, as for UVs, and bit 4
    keys a normal's second part on its first. That last is tried with the form that
    won, which keeps saving time down.
    """
    best = min(
        (
            bytes((by_vertex | fine << 1 | lookup << 2,))
            + _encode_normals_by(layout, corners, by_vertex, rows, pairs, fine, lookup)
            for by_vertex in (0, 1)
            for fine in (0, 1)
            for lookup in _SEEN_LOOKUPS
        ),
        key=len,
    )
    form = best[0]
    paired = bytes((form | 16,)) + _encode_normals_by(
        layout, corners, form & 1, rows, pairs, form >> 1 & 1, form >> 2, 1
    )
    return min(best, paired, key=len)


def _normal_contexts(lookup: int, paired: int = 0) -> int:
    base = _NORMAL_CONTEXTS + (_MAX_SEEN_CONTEXT + 1 if lookup == 2 else 0)
    return base + (_PAIR_KINDS if paired else 0)


def _pair_context(first_contexts: int, paired: int, part: int, history, plain: int):
    """Return the context of one part of a pair: the second keys on the first's size."""
    if paired and part:
        return first_contexts + min(history[0] // 2, _PAIR_KINDS - 1)
    return plain


def _encode_normals_by(
    layout, corners, by_vertex, rows, pairs, fine, lookup, paired=0
) -> bytes:
    keys, key_rows = _normal_keys(layout, corners, rows, by_vertex)
    writer = SymbolWriter(_normal_contexts(lookup, paired), fine=bool(fine))
    guesses = _vertex_normals(layout.starts, keys, key_rows)
    history = [0, 0]
    seen_normals = _SeenLookup(layout, corners, lookup, _NORMAL_CONTEXTS)
    for corner, key in enumerate(keys):
        context, seen = seen_normals.options(corner)
        pair = pairs[corner]
        if seen:
            at = seen.index(pair) + 1 if pair in seen else 0
            _write_choice(writer, context, at)
            if at:
                seen_normals.record(corner, pair)
                continue
        base = seen[-1] if seen else guesses[key]
        for part in range(2):
            difference = pair[part] - base[part]
            context = _pair_context(
                _normal_contexts(lookup),
                paired,
                part,
                history,
                _normal_context(seen, history[part]),
            )
            writer.write_number(context, difference)
            history[part] = _magnitude(difference)
        seen_normals.record(corner, pair)
    return writer.to_bytes()


def _read_normals(layout: _Layout, data: bytes, corners: list, rows: list) -> list:
    if not data or data[0] >> 2 & 3 not in _SEEN_LOOKUPS or data[0] >> 5:
        raise FormatError("Packed normals name no known prediction")
    lookup = data[0] >> 2 & 3
    paired = data[0] >> 4
    keys, key_rows = _normal_keys(layout, corners, rows, data[0] & 1)
    reader = SymbolReader(
        data[1:], _normal_contexts(lookup, paired), fine=bool(data[0] >> 1 & 1)
    )
    guesses = _vertex_normals(layout.starts, keys, key_rows)
    history = [0, 0]
    seen_normals = _SeenLookup(layout, corners, lookup, _NORMAL_CONTEXTS)
    pairs = []
    for corner, key in enumerate(keys):
        context, seen = seen_normals.options(corner)
        if seen:
            at = _read_choice(reader, context)
            if not 0 <= at <= len(seen):
                raise FormatError("A packed normal names a missing value")
            if at:
                pairs.append(seen[at - 1])
                seen_normals.record(corner, pairs[-1])
                continue
        base = seen[-1] if seen else guesses[key]
        values = []
        for part in range(2):
            context = _pair_context(
                _normal_contexts(lookup),
                paired,
                part,
                history,
                _normal_context(seen, history[part]),
            )
            difference = reader.read_number(context)
            values.append(base[part] + difference)
            history[part] = _magnitude(difference)
        pair = tuple(values)
        seen_normals.record(corner, pair)
        pairs.append(pair)
    return pairs


def _uv_candidates(layout: _Layout, rows, values, corner, a, b) -> tuple:
    """Return the two UVs the triangle's shape in space allows for this corner."""
    welded = layout.welded
    pa, pb, pv = rows[welded[a]], rows[welded[b]], rows[welded[corner]]
    ua, ub = values[a], values[b]
    ex, ey, ez = float(pb[0] - pa[0]), float(pb[1] - pa[1]), float(pb[2] - pa[2])
    qx, qy, qz = float(pv[0] - pa[0]), float(pv[1] - pa[1]), float(pv[2] - pa[2])
    length = ex * ex + ey * ey + ez * ez
    if length == 0.0:
        return (float(ua[0]), float(ua[1])), (float(ua[0]), float(ua[1]))
    du, dv = float(ub[0] - ua[0]), float(ub[1] - ua[1])
    along = (ex * qx + ey * qy + ez * qz) / length
    rx, ry, rz = qx - along * ex, qy - along * ey, qz - along * ez
    away = math.sqrt((rx * rx + ry * ry + rz * rz) / length)
    base_u, base_v = float(ua[0]) + along * du, float(ua[1]) + along * dv
    return (
        (base_u - away * dv, base_v + away * du),
        (base_u + away * dv, base_v - away * du),
    )


def _far_corner(layout, values, first, at, face):
    """Return a corner known to lie across this corner's edge, or None."""
    if at > 2:
        return first + 1
    gate = layout.gates[face]
    if gate is None:
        return None
    same_edge = (
        values[gate[0]] == values[first] and values[gate[1]] == values[first + 1]
    )
    return gate[2] if same_edge else None


def _uv_base(layout, rows, values, corner, first, at, face, last, side_of) -> tuple:
    """Return the prediction. side_of chooses when nothing else says which side."""
    if at == 0:
        return last
    if at == 1:
        return values[first]
    a, b = first + at - 1, first
    candidates = _uv_candidates(layout, rows, values, corner, a, b)
    far = _far_corner(layout, values, first, at, face)
    if far is None:
        return candidates[side_of(candidates)]
    ua, ub, uf = values[a], values[b], values[far]
    across = float(ub[0] - ua[0]) * float(uf[1] - ua[1]) - float(ub[1] - ua[1]) * float(
        uf[0] - ua[0]
    )
    return candidates[1 if across > 0.0 else 0]


def _encode_uvs(layout: _Layout, corners: list, pairs: list, rows: list) -> bytes:
    """Return the UVs in whichever form is smallest.

    The form byte's bit 0 uses fine numbers; bits 1 and 2 say where a corner looks for
    a UV already seen: 0 its welded position's, 1 its vertex's, 2 its vertex's if it
    has any, else its position's. On meshes split at UV seams a vertex has one UV, so
    the last makes repeats nearly free without repeating the seams splits hold. Bit 3
    keys a UV's second part on its first, tried with the form that won.
    """
    best = min(
        (
            bytes((fine | lookup << 1,))
            + _encode_uvs_with(layout, corners, pairs, rows, fine, lookup)
            for fine in (0, 1)
            for lookup in _SEEN_LOOKUPS
        ),
        key=len,
    )
    form = best[0]
    paired = bytes((form | 8,)) + _encode_uvs_with(
        layout, corners, pairs, rows, form & 1, form >> 1, 1
    )
    return min(best, paired, key=len)


def _uv_contexts(lookup: int, paired: int = 0) -> int:
    base = _UV_CONTEXTS + (_MAX_SEEN_CONTEXT + 1 if lookup == 2 else 0)
    return base + (
        _PAIR_KINDS + _UV_PREDICTION_KINDS * _UV_HISTORY_BUCKETS if paired else 0
    )


def _uv_value_context(lookup, paired, part, history, at) -> int:
    """Return a UV part's context. Keyed by pairs, the first part also keys on how it
    was predicted: from the last UV, from the face's first corner, or from shape."""
    bucket = history[part] // _UV_HISTORY_STEP
    if paired and not part:
        kind = min(at, _UV_PREDICTION_KINDS - 1)
        first = _uv_contexts(lookup) + _PAIR_KINDS
        return first + kind * _UV_HISTORY_BUCKETS + bucket
    return _pair_context(
        _uv_contexts(lookup), paired, part, history, _UV_VALUE_FIRST + bucket
    )


def _encode_uvs_with(
    layout: _Layout,
    corners: list,
    pairs: list,
    rows: list,
    fine: int,
    lookup: int,
    paired: int = 0,
) -> bytes:
    writer = SymbolWriter(_uv_contexts(lookup, paired), fine=bool(fine))
    values = [None] * len(layout.welded)
    history = [0, 0]
    last = (0, 0)
    seen_uvs = _SeenLookup(layout, corners, lookup, _UV_CONTEXTS)
    for corner, first, at, face in layout.corners():
        context, seen = seen_uvs.options(corner)
        pair = pairs[corner]
        values[corner] = pair
        if seen:
            choice = seen.index(pair) + 1 if pair in seen else 0
            _write_choice(writer, context, choice)
            if choice:
                seen_uvs.record(corner, pair)
                continue

        def side_of(candidates, actual=pair):
            first_miss = abs(candidates[0][0] - actual[0]) + abs(
                candidates[0][1] - actual[1]
            )
            second_miss = abs(candidates[1][0] - actual[0]) + abs(
                candidates[1][1] - actual[1]
            )
            side = 1 if second_miss < first_miss else 0
            writer.write(_UV_SIDE_CONTEXT, side)
            return side

        base = (
            seen[-1]
            if seen and at == 0
            else _uv_base(layout, rows, values, corner, first, at, face, last, side_of)
        )
        for part in range(2):
            difference = pair[part] - round(base[part])
            context = _uv_value_context(lookup, paired, part, history, at)
            writer.write_number(context, difference)
            history[part] = _magnitude(difference)
        seen_uvs.record(corner, pair)
        last = pair
    return writer.to_bytes()


def _read_uvs(layout: _Layout, data: bytes, corners: list, rows: list) -> list:
    if not data or data[0] >> 1 & 3 not in _SEEN_LOOKUPS or data[0] >> 4:
        raise FormatError("Packed UVs name no known form")
    lookup = data[0] >> 1 & 3
    paired = data[0] >> 3
    reader = SymbolReader(
        data[1:], _uv_contexts(lookup, paired), fine=bool(data[0] & 1)
    )
    values = [None] * len(layout.welded)
    history = [0, 0]
    last = (0, 0)
    seen_uvs = _SeenLookup(layout, corners, lookup, _UV_CONTEXTS)

    def side_of(_candidates):
        side = reader.read(_UV_SIDE_CONTEXT)
        if side > 1:
            raise FormatError("A packed UV has an unknown side")
        return side

    for corner, first, at, face in layout.corners():
        context, seen = seen_uvs.options(corner)
        if seen:
            choice = _read_choice(reader, context)
            if not 0 <= choice <= len(seen):
                raise FormatError("A packed UV names a missing value")
            if choice:
                values[corner] = seen[choice - 1]
                seen_uvs.record(corner, values[corner])
                continue
        base = (
            seen[-1]
            if seen and at == 0
            else _uv_base(layout, rows, values, corner, first, at, face, last, side_of)
        )
        pair = []
        for part in range(2):
            context = _uv_value_context(lookup, paired, part, history, at)
            difference = reader.read_number(context)
            pair.append(round(base[part]) + difference)
            history[part] = _magnitude(difference)
        pair = tuple(pair)
        values[corner] = pair
        seen_uvs.record(corner, pair)
        last = pair
    return values


def _uses_only_face_edges(mesh: Mesh, faces: list) -> bool:
    """Return whether edges are exactly those the faces reach, then any loose ones."""
    reached = {}
    for face in faces:
        for key in face_edges(face):
            reached.setdefault(key, len(reached))
    stored = list(zip(mesh.edges[0::2], mesh.edges[1::2], strict=True))
    if len(set(stored)) != len(stored) or any(a > b for a, b in stored):
        return False
    held = set(stored)
    return all(key in held for key in reached)


def pack_geometry(
    mesh: Mesh, step: float, exponent: int, from_faces: bool
) -> tuple | None:
    """Return (the mesh in packed order, its sections), or None if it cannot be packed.

    The mesh is already on its packing grid: step, or 2^exponent when step is 0.
    Normals are left out when readers calculate them from faces. Meshes with no faces,
    with holes or with no grid are left to the plain sections.
    """
    if not mesh.face_count or mesh.hole_starts or exponent == NO_GRID:
        return None
    source = face_lists(mesh)
    rows = _grid_rows(mesh.positions, exponent, step)
    weld = {}
    faces = [[weld.setdefault(rows[v], len(weld)) for v in face] for face in source]
    materials = list(mesh.face_materials) or [0] * mesh.face_count
    sizes = [len(face) for face in faces]
    is_uniform = len(set(sizes)) == 1
    walk = _Walk(faces, materials, is_uniform)
    walk.run()
    ordered = reorder_by(mesh, walk.order, walk.turns)
    packed_faces = face_lists(ordered)
    if not _uses_only_face_edges(ordered, packed_faces):
        return None
    corners = list(ordered.corners)
    new_rows = _grid_rows(ordered.positions, exponent, step)
    position_rows = [None] * (walk.newest + 1)
    for source_key, source_id in weld.items():
        if source_id in walk.ids:
            position_rows[walk.ids[source_id]] = source_key
    welded = [walk.ids[weld[new_rows[v]]] for v in corners]
    starts = [0]
    for face in packed_faces:
        starts.append(starts[-1] + len(face))
    connectivity = walk.to_bytes()
    gates, across = _walk_gates(connectivity, walk.is_uniform, welded, starts)
    layout = _Layout(welded, starts, gates, walk.newest + 1, across)
    used = max(corners) + 1 if corners else 0
    sections = [
        Section(SectionKind.PACKED_CONNECTIVITY, connectivity),
        Section(SectionKind.PACKED_SPLITS, _encode_splits(layout, corners)),
        Section(
            SectionKind.PACKED_POSITIONS,
            _encode_positions(layout, position_rows, new_rows[used:], step),
        ),
    ]
    if mesh.corner_normals and not from_faces:
        normals = ordered.corner_normals
        pairs = [
            encode_normal(*normals[at * 3 : at * 3 + 3]) for at in range(len(corners))
        ]
        data = _encode_normals(layout, corners, pairs, position_rows)
        sections.append(Section(SectionKind.PACKED_NORMALS, data))
    for layer, name in enumerate(sorted(ordered.uv_layers)):
        values = ordered.uv_layers[name]
        pairs = [
            (encode_uv(values[at * 2]), encode_uv(values[at * 2 + 1]))
            for at in range(len(corners))
        ]
        data = _encode_uvs(layout, corners, pairs, position_rows)
        sections.append(Section(SectionKind.PACKED_UVS, data, layer=layer))
    return ordered, sections


def _grid_rows(positions, exponent: int, step: float) -> list:
    if step:
        return [
            tuple(round(v / step) for v in positions[at : at + _AXES])
            for at in range(0, len(positions), _AXES)
        ]
    return [
        tuple(round(math.ldexp(v, -exponent)) for v in positions[at : at + _AXES])
        for at in range(0, len(positions), _AXES)
    ]


def _walk_gates(data: bytes, is_uniform: bool, welded: list, starts: list) -> list:
    return read_connectivity(
        data, len(starts) - 1, starts[1] if is_uniform else 0, len(welded)
    )[2::2]


def _derived_edges(faces: list, loose: list) -> list:
    reached = {}
    for face in faces:
        for key in face_edges(face):
            reached.setdefault(key, len(reached))
    return [vertex for key in reached for vertex in key] + loose


def read_geometry(sections: SectionReader, info: tuple, uv_names: list) -> dict:
    """Return the Mesh fields the packed sections hold, for a mesh with this info."""
    vertices, faces, corner_count, edge_count, _, arity, exponent, flags = info
    if exponent == NO_GRID:
        raise FormatError("A packed mesh has no grid")
    welded, starts, gates, position_count, across = read_connectivity(
        sections.raw(SectionKind.PACKED_CONNECTIVITY), faces, arity, corner_count
    )
    if len(welded) != corner_count:
        raise FormatError("Packed faces do not fill their corners")
    layout = _Layout(welded, starts, gates, position_count, across)
    corners, position_of = _read_splits(sections.raw(SectionKind.PACKED_SPLITS), layout)
    extra = vertices - len(position_of)
    if extra < 0:
        raise FormatError("A packed mesh has too many vertices")
    data = sections.raw(SectionKind.PACKED_POSITIONS)
    if len(data) < _STEP.size:
        raise FormatError("Packed positions are truncated")
    (step,) = _STEP.unpack_from(data)
    if not (step == 0 or math.isfinite(step) and step > 0):
        raise FormatError("Packed positions have a bad grid step")
    rows, extras = _read_positions(layout, data, extra)
    unit = step or math.ldexp(1.0, exponent)
    positions = array("f")
    for position in position_of:
        positions.extend(value * unit for value in rows[position])
    for row in extras:
        positions.extend(value * unit for value in row)
    fields = {
        "positions": positions,
        "sizes": [b - a for a, b in zip(starts, starts[1:], strict=False)],
        "corners": corners,
        "corner_normals": array("f"),
        "uv_layers": {},
    }
    if sections.has(SectionKind.PACKED_NORMALS):
        data = sections.raw(SectionKind.PACKED_NORMALS)
        pairs = _read_normals(layout, data, corners, rows)
        fields["corner_normals"] = array(
            "f", [part for pair in pairs for part in decode_normal(*pair)]
        )
    for layer, name in enumerate(uv_names):
        pairs = _read_uvs(
            layout, sections.raw(SectionKind.PACKED_UVS, layer), corners, rows
        )
        fields["uv_layers"][name] = array(
            "f", [decode_uv(part) for pair in pairs for part in pair]
        )
    faces_of_vertices = [corners[a:b] for a, b in zip(starts, starts[1:], strict=False)]
    loose = []
    if sections.has(SectionKind.PACKED_LOOSE_EDGES):
        loose = sections.column(
            SectionKind.PACKED_LOOSE_EDGES,
            sections.count_of(SectionKind.PACKED_LOOSE_EDGES),
        )
    fields["edges"] = _derived_edges(faces_of_vertices, loose)
    if len(fields["edges"]) != edge_count * 2:
        raise FormatError("Packed edges do not match their count")
    fields["normals_from_faces"] = bool(MeshFlags(flags) & MeshFlags.NORMALS_FROM_FACES)
    return fields
