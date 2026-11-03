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
from array import array

from ..container.layout import NO_GRID, MeshFlags, SectionKind
from ..container.sections import Section, SectionReader
from ..errors import FormatError
from ..mesh import Mesh
from ..processing.faces import face_edges, face_lists
from ..processing.reordering import reorder_by
from ..processing.snapping import grid_exponent, normals_match_faces
from ..storage.rans import SymbolReader, SymbolWriter
from .quantize import decode_normal, decode_uv, encode_normal, encode_uv

_VERTEX_NEW = 0
_VERTEX_INTO_LAST = 1
_VERTEX_INTO_FIRST = 2
_VERTEX_INTO_LAST_OLDER = 3
_VERTEX_FAR = 4
_VERTEX_SYMBOLS = 5
_CONTEXT_GATE = _VERTEX_SYMBOLS * 2
_CONTEXT_FAR = _CONTEXT_GATE + 1
_CONTEXT_ARITY = _CONTEXT_GATE + 2
_CONNECTIVITY_CONTEXTS = _CONTEXT_GATE + 3
_SPLIT_CONTEXTS = 4
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


def _write_vertex(writer, open_edges, vertex, ring, newest, previous, is_last) -> tuple:
    """Write one corner's vertex. Returns (newest vertex, the symbol used)."""
    context = previous * 2 + (1 if is_last else 0)
    if vertex == newest + 1:
        writer.write(context, _VERTEX_NEW)
        return vertex, _VERTEX_NEW
    last = ring[-1] if ring else None
    first = ring[0] if ring else None
    into_last, into_first, older = open_edges.candidates(last, first)
    for symbol, candidate in (
        (_VERTEX_INTO_LAST, into_last),
        (_VERTEX_INTO_FIRST, into_first),
        (_VERTEX_INTO_LAST_OLDER, older),
    ):
        if candidate == vertex:
            writer.write(context, symbol)
            return newest, symbol
    writer.write(context, _VERTEX_FAR)
    writer.write_number(_CONTEXT_FAR, newest - vertex)
    return newest, _VERTEX_FAR


def _read_vertex(reader, open_edges, ring, newest, previous, is_last) -> tuple:
    """Return (vertex, newest vertex, the symbol used)."""
    symbol = reader.read(previous * 2 + (1 if is_last else 0))
    if symbol == _VERTEX_NEW:
        return newest + 1, newest + 1, symbol
    if symbol == _VERTEX_FAR:
        vertex = newest - reader.read_number(_CONTEXT_FAR)
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
        self.writer = SymbolWriter(_CONNECTIVITY_CONTEXTS)
        self.ids = {}
        self.sources = []
        self.newest = -1
        self.previous = _VERTEX_NEW
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
            self.writer.write_number(_CONTEXT_ARITY, count)
        ring = [self.ids[source] for source in turned[:known]]
        for at, source in enumerate(turned[known:], known):
            walk_id = self.ids.get(source, self.newest + 1)
            self.newest, self.previous = _write_vertex(
                self.writer,
                self.open_edges,
                walk_id,
                ring,
                self.newest,
                self.previous,
                at == count - 1,
            )
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
                self.writer.write(_CONTEXT_GATE, 1 if found else 0)
                if not found:
                    self.open_edges.remove(u, v)
                    continue
                self._place(found[0], found[1], 2)
                placed += 1
            if placed == total:
                return


def read_connectivity(data: bytes, faces: int, arity: int, corner_count: int) -> tuple:
    """Return (welded position per corner, face starts, gate per face, positions)."""
    reader = SymbolReader(data, _CONNECTIVITY_CONTEXTS)
    open_edges = _OpenEdges()
    welded = []
    starts = []
    gates_of_faces = []
    newest = -1
    previous = _VERTEX_NEW

    def read_face(known: list, gate) -> None:
        nonlocal newest, previous
        count = arity or reader.read_number(_CONTEXT_ARITY)
        if count < 3 or len(welded) + count > corner_count:
            raise FormatError("A packed face has an impossible size")
        ring = list(known)
        for at in range(len(known), count):
            vertex, newest, previous = _read_vertex(
                reader, open_edges, ring, newest, previous, at == count - 1
            )
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
            if reader.read(_CONTEXT_GATE):
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


def _encode_positions(layout: _Layout, rows: list, extra: list) -> bytes:
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
    return writer.to_bytes()


def _read_positions(layout: _Layout, data: bytes, extra: int) -> tuple:
    reader = SymbolReader(data, _POSITION_CONTEXTS)
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


def _encode_splits(corners: list, welded: list) -> bytes:
    writer = SymbolWriter(_SPLIT_CONTEXTS)
    seen = {}
    for vertex, position in zip(corners, welded, strict=True):
        known = seen.setdefault(position, [])
        if known:
            at = known.index(vertex) + 1 if vertex in known else 0
            _write_choice(writer, min(len(known), _SPLIT_CONTEXTS - 1), at)
        if vertex not in known:
            known.append(vertex)
    return writer.to_bytes()


def _read_splits(data: bytes, welded: list) -> tuple:
    """Return (each corner's vertex, each vertex's welded position)."""
    reader = SymbolReader(data, _SPLIT_CONTEXTS)
    seen = {}
    corners = []
    position_of = []
    for position in welded:
        known = seen.setdefault(position, [])
        at = 0
        if known:
            at = _read_choice(reader, min(len(known), _SPLIT_CONTEXTS - 1))
            if not 0 <= at <= len(known):
                raise FormatError("A packed corner names a missing vertex")
        if at:
            corners.append(known[at - 1])
            continue
        vertex = len(position_of)
        position_of.append(position)
        known.append(vertex)
        corners.append(vertex)
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


def pack_geometry(mesh: Mesh) -> tuple | None:
    """Return (the mesh in packed order, its sections), or None if it cannot be packed.

    Meshes with no faces, with holes or with no grid are left to the plain sections.
    """
    exponent = grid_exponent(mesh)
    if not mesh.face_count or mesh.hole_starts or exponent == NO_GRID:
        return None
    source = face_lists(mesh)
    rows = _grid_rows(mesh.positions, exponent)
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
    new_rows = _grid_rows(ordered.positions, exponent)
    position_rows = [None] * (walk.newest + 1)
    for source_key, source_id in weld.items():
        if source_id in walk.ids:
            position_rows[walk.ids[source_id]] = source_key
    welded = [walk.ids[weld[new_rows[v]]] for v in corners]
    starts = [0]
    for face in packed_faces:
        starts.append(starts[-1] + len(face))
    layout = _Layout(welded, starts, _walk_gates(walk, welded, starts), walk.newest + 1)
    used = max(corners) + 1 if corners else 0
    sections = [
        Section(SectionKind.PACKED_CONNECTIVITY, walk.writer.to_bytes()),
        Section(SectionKind.PACKED_SPLITS, _encode_splits(corners, welded)),
        Section(
            SectionKind.PACKED_POSITIONS,
            _encode_positions(layout, position_rows, new_rows[used:]),
        ),
    ]
    if mesh.corner_normals and not normals_match_faces(mesh):
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


def _grid_rows(positions, exponent: int) -> list:
    return [
        tuple(round(math.ldexp(v, -exponent)) for v in positions[at : at + _AXES])
        for at in range(0, len(positions), _AXES)
    ]


def _walk_gates(walk: _Walk, welded: list, starts: list) -> list:
    return read_connectivity(
        walk.writer.to_bytes(),
        len(starts) - 1,
        0 if not walk.is_uniform else starts[1],
        len(welded),
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
    corners, position_of = _read_splits(sections.raw(SectionKind.PACKED_SPLITS), welded)
    extra = vertices - len(position_of)
    if extra < 0:
        raise FormatError("A packed mesh has too many vertices")
    rows, extras = _read_positions(
        layout, sections.raw(SectionKind.PACKED_POSITIONS), extra
    )
    positions = array("f")
    for position in position_of:
        positions.extend(math.ldexp(value, exponent) for value in rows[position])
    for row in extras:
        positions.extend(math.ldexp(value, exponent) for value in row)
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
