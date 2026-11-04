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
_VERTEX_SYMBOLS = 5
_CANDIDATE_KINDS = 4
_SPLIT_CONTEXTS = 8
_POSITION_CONTEXTS = 4
_POSITION_HISTORY_STEP = 4
_POSITION_CONTEXT_MIN_ROWS = 4000
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
_CHOICE_ESCAPE = 200
_STEP = struct.Struct("<f")
_SPAN_BITS = 20
_STEP_MANTISSA_BITS = 6
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
    """

    def __init__(self):
        self.into = {}
        self.out_of = {}
        self.gates = []

    def is_open(self, u: int, v: int) -> bool:
        return u in self.into.get(v, ())

    def remove(self, u: int, v: int) -> None:
        into = self.into[v]
        del into[len(into) - 1 - into[::-1].index(u)]
        out_of = self.out_of[u]
        del out_of[len(out_of) - 1 - out_of[::-1].index(v)]

    def add_face(self, ring: list, first_corner: int) -> None:
        count = len(ring)
        for at in range(count):
            u, v = ring[at], ring[(at + 1) % count]
            if self.is_open(v, u):
                self.remove(v, u)
            else:
                self.into.setdefault(v, []).append(u)
                self.out_of.setdefault(u, []).append(v)
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
    """

    def __init__(self, model: int):
        self.model = model
        vertex_contexts = _VERTEX_SYMBOLS * 2
        if model:
            vertex_contexts *= _VERTEX_SYMBOLS * _CANDIDATE_KINDS
        self.gate = vertex_contexts
        self.far = vertex_contexts + 1
        self.arity = vertex_contexts + 2
        self.count = vertex_contexts + 3

    def vertex(self, previous: int, before: int, is_last: bool, kinds: int) -> int:
        context = previous * 2 + (1 if is_last else 0)
        if self.model:
            context = (context * _VERTEX_SYMBOLS + before) * _CANDIDATE_KINDS + kinds
        return context


_CONNECTIVITY_MODELS = (_ConnectivityModel(0), _ConnectivityModel(1))


def _candidate_kinds(open_edges: _OpenEdges, ring: list) -> int:
    """Return bit 0 set if an open edge leads into the ring's last vertex, bit 1 out
    of its first."""
    if not ring:
        return 0
    into = 1 if open_edges.into.get(ring[-1]) else 0
    return into | (2 if open_edges.out_of.get(ring[0]) else 0)


def _vertex_symbol(open_edges, vertex, ring, newest) -> int:
    if vertex == newest + 1:
        return _VERTEX_NEW
    last = ring[-1] if ring else None
    first = ring[0] if ring else None
    into_last, into_first, older = open_edges.candidates(last, first)
    for symbol, candidate in (
        (_VERTEX_INTO_LAST, into_last),
        (_VERTEX_INTO_FIRST, into_first),
        (_VERTEX_INTO_LAST_OLDER, older),
    ):
        if candidate == vertex:
            return symbol
    return _VERTEX_FAR


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
            symbol = _vertex_symbol(self.open_edges, walk_id, ring, self.newest)
            kinds = _candidate_kinds(self.open_edges, ring)
            far = self.newest - walk_id if symbol == _VERTEX_FAR else None
            self.events.append(
                ("vertex", symbol, self.previous, self.before, at == count - 1, kinds)
            )
            if far is not None:
                self.events.append(("far", far))
            if symbol == _VERTEX_NEW:
                self.newest = walk_id
            self.before, self.previous = self.previous, symbol
            if source not in self.ids:
                self.ids[source] = walk_id
                self.sources.append(source)
            ring.append(walk_id)
        self.open_edges.add_face(ring, self.corner)
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
                self.events.append(("gate", 1 if found else 0))
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
            elif event[0] == "gate":
                writer.write(model.gate, event[1])
            else:
                writer.write_number(model.arity, event[1])
        return bytes((model.model,)) + writer.to_bytes()


def read_connectivity(data: bytes, faces: int, arity: int, corner_count: int) -> tuple:
    """Return (welded position per corner, face starts, gate per face, positions)."""
    if not data or data[0] >= len(_CONNECTIVITY_MODELS):
        raise FormatError("Packed connectivity names no known model")
    model = _CONNECTIVITY_MODELS[data[0]]
    reader = SymbolReader(data[1:], model.count)
    open_edges = _OpenEdges()
    welded = []
    starts = []
    gates_of_faces = []
    newest = -1
    previous = before = _VERTEX_NEW

    def read_face(known: list, gate) -> None:
        nonlocal newest, previous, before
        count = arity or reader.read_number(model.arity)
        if count < 3 or len(welded) + count > corner_count:
            raise FormatError("A packed face has an impossible size")
        ring = list(known)
        for at in range(len(known), count):
            kinds = _candidate_kinds(open_edges, ring)
            context = model.vertex(previous, before, at == count - 1, kinds)
            vertex, newest, symbol = _read_vertex(
                reader, model, open_edges, ring, newest, context
            )
            before, previous = previous, symbol
            ring.append(vertex)
        first = len(welded)
        starts.append(first)
        gates_of_faces.append(gate)
        welded.extend(ring)
        open_edges.add_face(ring, first)

    gates = open_edges.gates
    while len(starts) < faces:
        read_face([], None)
        while gates and len(starts) < faces:
            u, v, corner_u, corner_v, corner_third = gates.pop()
            if not open_edges.is_open(u, v):
                continue
            if reader.read(model.gate):
                read_face([v, u], (corner_v, corner_u, corner_third))
            else:
                open_edges.remove(u, v)
    starts.append(len(welded))
    return welded, starts, gates_of_faces, newest + 1


class _Layout:
    """What each stream reads from connectivity, shared by writing and reading."""

    def __init__(self, welded, starts, gates, position_count):
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


def _parallelogram(layout: _Layout, first: int, at: int, face: int, rows: list):
    """Return a corner's predicted position from those before it in the walk."""
    welded = layout.welded
    if at == 0:
        return None
    if at == 1:
        return rows[welded[first]]
    a = rows[welded[first + at - 1]]
    b = rows[welded[first]]
    gate = layout.gates[face]
    if at == 2:
        if gate is None:
            return a
        c = rows[welded[gate[2]]]
    else:
        c = rows[welded[first + 1]]
    return (a[0] + b[0] - c[0], a[1] + b[1] - c[1], a[2] + b[2] - c[2])


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
    writer = SymbolWriter(_POSITION_CONTEXTS)
    previous = (0, 0, 0)
    history = [0, 0, 0]
    uses_history = layout.position_count >= _POSITION_CONTEXT_MIN_ROWS
    made = 0
    for corner, first, at, face in layout.corners():
        position = layout.welded[corner]
        if position != made:
            continue
        made += 1
        guess = _parallelogram(layout, first, at, face, rows) or previous
        row = rows[position]
        for axis in range(_AXES):
            difference = row[axis] - guess[axis]
            context = history[axis] // _POSITION_HISTORY_STEP if uses_history else 0
            writer.write_number(min(context, _POSITION_CONTEXTS - 1), difference)
            history[axis] = _magnitude(difference)
        previous = row
    for row in extra:
        for axis in range(_AXES):
            writer.write_number(0, row[axis] - previous[axis])
        previous = row
    return _STEP.pack(step) + writer.to_bytes()


def _read_positions(layout: _Layout, data: bytes, extra: int) -> tuple:
    reader = SymbolReader(data[_STEP.size :], _POSITION_CONTEXTS)
    rows = []
    previous = (0, 0, 0)
    history = [0, 0, 0]
    uses_history = layout.position_count >= _POSITION_CONTEXT_MIN_ROWS
    for corner, first, at, face in layout.corners():
        if layout.welded[corner] != len(rows):
            continue
        guess = _parallelogram(layout, first, at, face, rows) or previous
        values = []
        for axis in range(_AXES):
            context = history[axis] // _POSITION_HISTORY_STEP if uses_history else 0
            difference = reader.read_number(min(context, _POSITION_CONTEXTS - 1))
            values.append(guess[axis] + difference)
            history[axis] = _magnitude(difference)
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
    used here; then the rest, newest first. Either way 0 is a new vertex.
    """

    def __init__(self, layout: _Layout, corners: list, by_gate: int):
        self.layout = layout
        self.corners = corners
        self.by_gate = by_gate
        self.seen = {}
        self.recent = {}

    def options(self, corner: int, at: int, face: int) -> tuple | None:
        """Return (context, the vertices this corner may name), or None if none yet."""
        position = self.layout.welded[corner]
        known = self.seen.get(position)
        if not known:
            return None
        if not self.by_gate:
            return min(len(known), 3), known
        gate = self.layout.gates[face]
        from_gate = gate is not None and at < 2
        guess = self.corners[gate[at]] if from_gate else self.recent[position]
        rest = [vertex for vertex in reversed(known) if vertex != guess]
        return min(len(known), 3) + (4 if from_gate else 0), [guess, *rest]

    def record(self, corner: int, vertex: int) -> None:
        position = self.layout.welded[corner]
        known = self.seen.setdefault(position, [])
        if vertex not in known:
            known.append(vertex)
        self.recent[position] = vertex


def _encode_splits(layout: _Layout, corners: list) -> bytes:
    """Return the splits under whichever model makes them smaller."""
    written = []
    for by_gate in (0, 1):
        writer = SymbolWriter(_SPLIT_CONTEXTS)
        splits = _Splits(layout, corners, by_gate)
        for corner, _first, at, face in layout.corners():
            vertex = corners[corner]
            choice = splits.options(corner, at, face)
            if choice is not None:
                context, options = choice
                index = options.index(vertex) + 1 if vertex in options else 0
                _write_choice(writer, context, index)
            splits.record(corner, vertex)
        written.append(bytes((by_gate,)) + writer.to_bytes())
    return min(written, key=len)


def _read_splits(data: bytes, layout: _Layout) -> tuple:
    """Return (each corner's vertex, each vertex's welded position)."""
    if not data or data[0] > 1:
        raise FormatError("Packed splits name no known model")
    reader = SymbolReader(data[1:], _SPLIT_CONTEXTS)
    corners = [0] * len(layout.welded)
    position_of = []
    splits = _Splits(layout, corners, data[0])
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
        splits.record(corner, vertex)
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
    """Return the normals predicted both ways, whichever is smaller."""
    return min(
        (
            bytes((by_vertex,))
            + _encode_normals_by(
                layout.starts, *_normal_keys(layout, corners, rows, by_vertex), pairs
            )
            for by_vertex in (0, 1)
        ),
        key=len,
    )


def _encode_normals_by(starts: list, keys: list, rows: list, pairs: list) -> bytes:
    writer = SymbolWriter(_NORMAL_CONTEXTS)
    guesses = _vertex_normals(starts, keys, rows)
    history = [0, 0]
    seen_at = [[] for _ in rows]
    for corner, position in enumerate(keys):
        seen = seen_at[position]
        pair = pairs[corner]
        if seen:
            at = seen.index(pair) + 1 if pair in seen else 0
            _write_choice(writer, min(len(seen), _MAX_SEEN_CONTEXT), at)
            if at:
                continue
        base = seen[-1] if seen else guesses[position]
        for part in range(2):
            difference = pair[part] - base[part]
            writer.write_number(_normal_context(seen, history[part]), difference)
            history[part] = _magnitude(difference)
        seen.append(pair)
    return writer.to_bytes()


def _read_normals(layout: _Layout, data: bytes, corners: list, rows: list) -> list:
    if not data or data[0] > 1:
        raise FormatError("Packed normals name no known prediction")
    keys, key_rows = _normal_keys(layout, corners, rows, data[0])
    reader = SymbolReader(data[1:], _NORMAL_CONTEXTS)
    guesses = _vertex_normals(layout.starts, keys, key_rows)
    history = [0, 0]
    seen_at = [[] for _ in key_rows]
    pairs = []
    for position in keys:
        seen = seen_at[position]
        if seen:
            at = _read_choice(reader, min(len(seen), _MAX_SEEN_CONTEXT))
            if not 0 <= at <= len(seen):
                raise FormatError("A packed normal names a missing value")
            if at:
                pairs.append(seen[at - 1])
                continue
        base = seen[-1] if seen else guesses[position]
        values = []
        for part in range(2):
            difference = reader.read_number(_normal_context(seen, history[part]))
            values.append(base[part] + difference)
            history[part] = _magnitude(difference)
        pair = tuple(values)
        seen.append(pair)
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


def _encode_uvs(layout: _Layout, pairs: list, rows: list) -> bytes:
    writer = SymbolWriter(_UV_CONTEXTS)
    values = [None] * len(layout.welded)
    history = [0, 0]
    last = (0, 0)
    seen_at = [[] for _ in rows]
    for corner, first, at, face in layout.corners():
        seen = seen_at[layout.welded[corner]]
        pair = pairs[corner]
        values[corner] = pair
        if seen:
            choice = seen.index(pair) + 1 if pair in seen else 0
            _write_choice(writer, min(len(seen), _MAX_SEEN_CONTEXT), choice)
            if choice:
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
            bucket = history[part] // _UV_HISTORY_STEP
            writer.write_number(_UV_VALUE_FIRST + bucket, difference)
            history[part] = _magnitude(difference)
        seen.append(pair)
        last = pair
    return writer.to_bytes()


def _read_uvs(layout: _Layout, data: bytes, rows: list) -> list:
    reader = SymbolReader(data, _UV_CONTEXTS)
    values = [None] * len(layout.welded)
    history = [0, 0]
    last = (0, 0)
    seen_at = [[] for _ in rows]

    def side_of(_candidates):
        side = reader.read(_UV_SIDE_CONTEXT)
        if side > 1:
            raise FormatError("A packed UV has an unknown side")
        return side

    for corner, first, at, face in layout.corners():
        seen = seen_at[layout.welded[corner]]
        if seen:
            choice = _read_choice(reader, min(len(seen), _MAX_SEEN_CONTEXT))
            if not 0 <= choice <= len(seen):
                raise FormatError("A packed UV names a missing value")
            if choice:
                values[corner] = seen[choice - 1]
                continue
        base = (
            seen[-1]
            if seen and at == 0
            else _uv_base(layout, rows, values, corner, first, at, face, last, side_of)
        )
        pair = []
        for part in range(2):
            bucket = history[part] // _UV_HISTORY_STEP
            difference = reader.read_number(_UV_VALUE_FIRST + bucket)
            pair.append(round(base[part]) + difference)
            history[part] = _magnitude(difference)
        pair = tuple(pair)
        values[corner] = pair
        seen.append(pair)
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
    gates = _walk_gates(connectivity, walk.is_uniform, welded, starts)
    layout = _Layout(welded, starts, gates, walk.newest + 1)
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
        data = _encode_uvs(layout, pairs, position_rows)
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
    )[2]


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
    welded, starts, gates, position_count = read_connectivity(
        sections.raw(SectionKind.PACKED_CONNECTIVITY), faces, arity, corner_count
    )
    if len(welded) != corner_count:
        raise FormatError("Packed faces do not fill their corners")
    layout = _Layout(welded, starts, gates, position_count)
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
        pairs = _read_uvs(layout, sections.raw(SectionKind.PACKED_UVS, layer), rows)
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
