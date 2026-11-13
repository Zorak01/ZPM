# SPDX-License-Identifier: GPL-3.0-or-later

"""Connectivity: faces walked across open edges over welded positions.

The walk keeps open edges, directed edges with a face on one side so far, and a stack
of them, newest last. Each popped edge still open costs a gate symbol saying whether a
face lies across it; that face starts with the edge's two positions. Each later corner
is a symbol: a new position, one closing a nearby open edge, a few steps along the
boundary, or, rarely, a distance back from the newest position.
"""

from enum import IntEnum
from typing import NamedTuple

from ...errors import FormatError
from ...storage.rans import SymbolReader, SymbolWriter
from .layout import Gate, GateCorners, Layout


class Symbol(IntEnum):
    """What names a corner's position."""

    NEW = 0
    INTO_LAST = 1
    OUT_OF_FIRST = 2
    OLDER_INTO_LAST = 3
    FAR = 4
    FORWARD = 5
    BACK = 6


# Contexts tell apart new, the three candidates and anything further.
_CONTEXT_SYMBOLS = 5
# The furthest a corner may name a position along the open boundary.
_MAX_STEPS = 64
_CANDIDATE_KINDS = 4
_LEFT_KINDS = 5
_GATE_LEFT_KINDS = 3
_COUNT_KINDS = 10
_TYPICAL_FACE_COUNT = 6
_KNOWN_AT_GATE = 2


class ConnectivityModel(IntEnum):
    """How connectivity symbols take contexts. The stream's first byte names one."""

    # The symbol before, and whether a corner ends its face.
    LAST_SYMBOL = 0
    # Also the symbol before that, and which candidates exist: pays on larger meshes.
    LAST_TWO = 1
    # Each new position's face count, then how many faces a face's first and last
    # positions still need: on regular meshes that settles most corners.
    FACE_COUNTS = 2


class _Contexts:
    """Every context a connectivity model uses, numbered in one table."""

    def __init__(self, model: ConnectivityModel):
        self.model = model
        if model == ConnectivityModel.FACE_COUNTS:
            vertex_contexts = _LEFT_KINDS * _LEFT_KINDS * 2 * _CANDIDATE_KINDS
            gate_contexts = _GATE_LEFT_KINDS * _GATE_LEFT_KINDS
        else:
            vertex_contexts = _CONTEXT_SYMBOLS * 2
            if model == ConnectivityModel.LAST_TWO:
                vertex_contexts *= _CONTEXT_SYMBOLS * _CANDIDATE_KINDS
            gate_contexts = 1
        self.writes_counts = model == ConnectivityModel.FACE_COUNTS
        self._gate_first = vertex_contexts
        self.far = vertex_contexts + gate_contexts
        self.size = self.far + 1
        self.steps = self.far + 2
        self._count_first = self.far + 3
        counts = _COUNT_KINDS * (_COUNT_KINDS + 1) if self.writes_counts else 0
        self.count = self._count_first + counts

    def vertex(self, previous, before, is_last, kinds, first_left, last_left) -> int:
        """Return the context of a corner's symbol."""
        if self.model == ConnectivityModel.FACE_COUNTS:
            context = _left_kind(first_left) * _LEFT_KINDS + _left_kind(last_left)
            return (context * 2 + (1 if is_last else 0)) * _CANDIDATE_KINDS + kinds
        context = min(previous, _CONTEXT_SYMBOLS - 1) * 2 + (1 if is_last else 0)
        if self.model == ConnectivityModel.LAST_TWO:
            before = min(before, _CONTEXT_SYMBOLS - 1)
            context = (context * _CONTEXT_SYMBOLS + before) * _CANDIDATE_KINDS + kinds
        return context

    def gate(self, u_left: int, v_left: int) -> int:
        """Return the context of a gate symbol."""
        if self.model != ConnectivityModel.FACE_COUNTS:
            return self._gate_first
        u_kind = min(u_left, _GATE_LEFT_KINDS - 1)
        return (
            self._gate_first
            + u_kind * _GATE_LEFT_KINDS
            + min(v_left, _GATE_LEFT_KINDS - 1)
        )

    def face_count(self, previous_count: int, first_count: int | None) -> int:
        """Return a face count's context: the count before, and the ring's first's."""
        first = 0 if first_count is None else 1 + min(first_count, _COUNT_KINDS - 1)
        return (
            self._count_first
            + min(previous_count, _COUNT_KINDS - 1) * (_COUNT_KINDS + 1)
            + first
        )


_CONTEXTS = {model: _Contexts(model) for model in ConnectivityModel}


def _left_kind(left: int | None) -> int:
    """Return 0 for no position, else 1 + the faces it still needs, up to 3."""
    return 0 if left is None else 1 + min(max(left, 0), _LEFT_KINDS - 2)


class _OpenEdges:
    """Directed edges with a face on one side so far: a walk's only working state.

    When a face closes an open edge, each of its two corners there gets the corner
    across: the earlier face's corner at the same position.
    """

    def __init__(self):
        self.into = {}
        self.out_of = {}
        self.gates = []
        self.across = {}
        self._edge_corners = {}

    def is_open(self, u: int, v: int) -> bool:
        return u in self.into.get(v, ())

    def remove(self, u: int, v: int) -> tuple:
        """Close the newest open edge from u to v; return the corners holding u, v."""
        into = self.into[v]
        del into[len(into) - 1 - into[::-1].index(u)]
        out_of = self.out_of[u]
        del out_of[len(out_of) - 1 - out_of[::-1].index(v)]
        return self._edge_corners[(u, v)].pop()

    def add_face(self, ring: list, first_corner: int) -> None:
        """Close each edge the face shares with an open one, and open the rest."""
        count = len(ring)
        for at in range(count):
            u, v = ring[at], ring[(at + 1) % count]
            corner_u, corner_v = first_corner + at, first_corner + (at + 1) % count
            if self.is_open(v, u):
                earlier_v, earlier_u = self.remove(v, u)
                self.across.setdefault(corner_u, earlier_u)
                self.across.setdefault(corner_v, earlier_v)
                continue
            self.into.setdefault(v, []).append(u)
            self.out_of.setdefault(u, []).append(v)
            self._edge_corners.setdefault((u, v), []).append((corner_u, corner_v))
            after = first_corner + (at + 2) % count
            self.gates.append(Gate(u, v, corner_u, corner_v, after))

    def candidates(self, ring: list) -> tuple:
        """Return (the position closing into the ring's last, out of its first, an
        older one into its last), each None if there is none."""
        into_last = self.into.get(ring[-1], ()) if ring else ()
        out_of_first = self.out_of.get(ring[0], ()) if ring else ()
        return (
            into_last[-1] if into_last else None,
            out_of_first[-1] if out_of_first else None,
            into_last[-2] if len(into_last) > 1 else None,
        )

    def candidate_kinds(self, ring: list) -> int:
        """Return bit 0 set if an open edge leads into the ring's last position, bit 1
        if one leads out of its first."""
        if not ring:
            return 0
        into = 1 if self.into.get(ring[-1]) else 0
        return into | (2 if self.out_of.get(ring[0]) else 0)

    def along(self, start: int, steps: int, forward: bool) -> int | None:
        """Return the position this many open edges on from start, or None."""
        links = self.out_of if forward else self.into
        at = start
        for _ in range(steps):
            following = links.get(at)
            if not following:
                return None
            at = following[-1]
        return at

    def steps_to(self, start: int, target: int, forward: bool) -> int | None:
        """Return how many open edges on from start the target is, within reach."""
        links = self.out_of if forward else self.into
        at = start
        for steps in range(1, _MAX_STEPS + 1):
            following = links.get(at)
            if not following:
                return None
            at = following[-1]
            if at == target:
                return steps
        return None


class _FaceCounts:
    """How many faces each position has in all, and how many are placed so far."""

    def __init__(self):
        self.total = {}
        self._placed = {}

    def left(self, position: int | None) -> int | None:
        if position is None:
            return None
        return self.total.get(position, 0) - self._placed.get(position, 0)

    def place(self, ring: list) -> None:
        for position in ring:
            self._placed[position] = self._placed.get(position, 0) + 1


class _VertexEvent(NamedTuple):
    symbol: int
    previous: int
    before: int
    is_last: bool
    kinds: int
    first_left: int | None
    last_left: int | None

    def write(self, writer: SymbolWriter, contexts: _Contexts) -> None:
        context = contexts.vertex(*self[1:])
        writer.write(context, self.symbol)


class _FarEvent(NamedTuple):
    distance: int

    def write(self, writer: SymbolWriter, contexts: _Contexts) -> None:
        writer.write_number(contexts.far, self.distance)


class _StepsEvent(NamedTuple):
    # Steps along the boundary, less one.
    steps: int

    def write(self, writer: SymbolWriter, contexts: _Contexts) -> None:
        writer.write_choice(contexts.steps, self.steps)


class _GateEvent(NamedTuple):
    found: int
    u_left: int
    v_left: int

    def write(self, writer: SymbolWriter, contexts: _Contexts) -> None:
        writer.write(contexts.gate(self.u_left, self.v_left), self.found)


class _CountEvent(NamedTuple):
    total: int
    previous_count: int
    first_count: int | None

    def write(self, writer: SymbolWriter, contexts: _Contexts) -> None:
        if contexts.writes_counts:
            context = contexts.face_count(self.previous_count, self.first_count)
            writer.write_choice(context, self.total)


class _SizeEvent(NamedTuple):
    size: int

    def write(self, writer: SymbolWriter, contexts: _Contexts) -> None:
        writer.write_choice(contexts.size, self.size)


def _vertex_symbol(open_edges: _OpenEdges, vertex: int, ring: list, newest: int):
    """Return (the symbol naming this corner's position, then its event or None)."""
    if vertex == newest + 1:
        return Symbol.NEW, None
    candidates = open_edges.candidates(ring)
    for symbol, candidate in zip(
        (Symbol.INTO_LAST, Symbol.OUT_OF_FIRST, Symbol.OLDER_INTO_LAST),
        candidates,
        strict=True,
    ):
        if candidate == vertex:
            return symbol, None
    if ring:
        forward = open_edges.steps_to(ring[-1], vertex, True)
        back = open_edges.steps_to(ring[0], vertex, False)
        if forward is not None and (back is None or forward <= back):
            return Symbol.FORWARD, _StepsEvent(forward - 1)
        if back is not None:
            return Symbol.BACK, _StepsEvent(back - 1)
    return Symbol.FAR, _FarEvent(newest - vertex)


class FaceWalk:
    """The encoder's walk over faces, choosing their order and starting corners.

    faces hold welded positions. Faces of one material stay together. After run,
    order and turns say how to reorder the mesh, ids map welded positions to walk
    order, and to_bytes writes the walk.
    """

    def __init__(self, faces: list, materials: list):
        self.faces = faces
        self.materials = materials
        self.is_uniform = len({len(face) for face in faces}) == 1
        self.ids = {}
        self.newest = -1
        self.order = []
        self.turns = {}
        self._events = []
        self._sources = []
        self._previous = Symbol.NEW
        self._before = Symbol.NEW
        self._open_edges = _OpenEdges()
        self._counts = _FaceCounts()
        self._source_counts = {}
        for face in faces:
            for source in face:
                self._source_counts[source] = self._source_counts.get(source, 0) + 1
        self._previous_count = _TYPICAL_FACE_COUNT
        self._used = [False] * len(faces)
        self._corner = 0
        self._by_edge = {}
        for index, face in enumerate(faces):
            for at, u in enumerate(face):
                edge = (u, face[(at + 1) % len(face)])
                self._by_edge.setdefault(edge, []).append((index, at))

    def run(self) -> None:
        """Walk every face once."""
        starts = sorted(range(len(self.faces)), key=self.materials.__getitem__)
        placed = 0
        total = len(self.faces)
        gates = self._open_edges.gates
        for start in starts:
            if self._used[start]:
                continue
            self._place(start, 0, 0)
            placed += 1
            material = self.materials[start]
            while gates and placed < total:
                gate = gates.pop()
                if not self._open_edges.is_open(gate.u, gate.v):
                    continue
                found = self._next_across(gate.u, gate.v, material)
                ends = (self._counts.left(gate.u), self._counts.left(gate.v))
                self._events.append(_GateEvent(1 if found else 0, *ends))
                if not found:
                    self._open_edges.remove(gate.u, gate.v)
                    continue
                self._place(found[0], found[1], _KNOWN_AT_GATE)
                placed += 1
            if placed == total:
                return

    def to_bytes(self) -> bytes:
        """Return the walk under whichever model makes it smaller."""
        return min((self._written(model) for model in ConnectivityModel), key=len)

    def _written(self, model: ConnectivityModel) -> bytes:
        contexts = _CONTEXTS[model]
        writer = SymbolWriter(contexts.count)
        for event in self._events:
            event.write(writer, contexts)
        return bytes((model,)) + writer.to_bytes()

    def _place(self, index: int, turn: int, known: int) -> None:
        """Note a face starting at this corner, its first known corners implied."""
        face = self.faces[index]
        count = len(face)
        turned = face[turn:] + face[:turn]
        if not self.is_uniform:
            self._events.append(_SizeEvent(count))
        ring = [self.ids[source] for source in turned[:known]]
        for at, source in enumerate(turned[known:], known):
            walk_id = self.ids.get(source, self.newest + 1)
            self._note_corner(walk_id, source, ring, at == count - 1)
            ring.append(walk_id)
        self._open_edges.add_face(ring, self._corner)
        self._counts.place(ring)
        self._corner += count
        self._used[index] = True
        self.order.append(index)
        self.turns[index] = turn

    def _note_corner(
        self, walk_id: int, source: int, ring: list, is_last: bool
    ) -> None:
        symbol, number = _vertex_symbol(self._open_edges, walk_id, ring, self.newest)
        self._events.append(
            _VertexEvent(
                symbol,
                self._previous,
                self._before,
                is_last,
                self._open_edges.candidate_kinds(ring),
                self._counts.left(ring[0] if ring else None),
                self._counts.left(ring[-1] if ring else None),
            )
        )
        if number is not None:
            self._events.append(number)
        if symbol == Symbol.NEW:
            self.newest = walk_id
            total = self._source_counts[source]
            self._counts.total[walk_id] = total
            first_count = self._counts.total.get(ring[0]) if ring else None
            self._events.append(_CountEvent(total, self._previous_count, first_count))
            self._previous_count = total
        self._before, self._previous = self._previous, symbol
        if source not in self.ids:
            self.ids[source] = walk_id
            self._sources.append(source)

    def _next_across(self, u: int, v: int, material: int):
        for index, at in self._by_edge.get((self._sources[v], self._sources[u]), ()):
            if not self._used[index] and self.materials[index] == material:
                return index, at
        return None


def read_connectivity(data: bytes, faces: int, size: int, corner_count: int) -> Layout:
    """Return the walk's Layout. size is the shared face size, or 0 if each face says.

    Raises FormatError for a model, symbol, size or reference the walk cannot have.
    """
    if not data or data[0] >= len(ConnectivityModel):
        raise FormatError("Packed connectivity names no known model")
    return _ConnectivityReader(data, size, corner_count).read(faces)


class _ConnectivityReader:
    """Reads a walk back, face by face, in the order it was written."""

    def __init__(self, data: bytes, size: int, corner_count: int):
        self._contexts = _CONTEXTS[ConnectivityModel(data[0])]
        self._reader = SymbolReader(data[1:], self._contexts.count)
        self._size = size
        self._corner_count = corner_count
        self._open_edges = _OpenEdges()
        self._counts = _FaceCounts()
        self._welded = []
        self._starts = []
        self._gates = []
        self._newest = -1
        self._previous = self._before = Symbol.NEW
        self._previous_count = _TYPICAL_FACE_COUNT

    def read(self, faces: int) -> Layout:
        gates = self._open_edges.gates
        while len(self._starts) < faces:
            self._read_face([], None)
            while gates and len(self._starts) < faces:
                gate = gates.pop()
                if not self._open_edges.is_open(gate.u, gate.v):
                    continue
                left = (self._counts.left(gate.u), self._counts.left(gate.v))
                if self._reader.read(self._contexts.gate(*left)):
                    corners = GateCorners(
                        gate.corner_v, gate.corner_u, gate.corner_after
                    )
                    self._read_face([gate.v, gate.u], corners)
                else:
                    self._open_edges.remove(gate.u, gate.v)
        self._starts.append(len(self._welded))
        across = self._open_edges.across
        corners = range(len(self._welded))
        return Layout(
            self._welded,
            self._starts,
            self._gates,
            self._newest + 1,
            [across.get(corner) for corner in corners],
        )

    def _read_face(self, known: list, gate: GateCorners | None) -> None:
        count = self._size or self._reader.read_choice(self._contexts.size)
        if count < 3 or len(self._welded) + count > self._corner_count:
            raise FormatError("A packed face has an impossible size")
        ring = list(known)
        for at in range(len(known), count):
            ring.append(self._read_corner(ring, at == count - 1))
        first = len(self._welded)
        self._starts.append(first)
        self._gates.append(gate)
        self._welded.extend(ring)
        self._open_edges.add_face(ring, first)
        self._counts.place(ring)

    def _read_corner(self, ring: list, is_last: bool) -> int:
        context = self._contexts.vertex(
            self._previous,
            self._before,
            is_last,
            self._open_edges.candidate_kinds(ring),
            self._counts.left(ring[0] if ring else None),
            self._counts.left(ring[-1] if ring else None),
        )
        symbol = self._reader.read(context)
        vertex = self._position(symbol, ring)
        if symbol == Symbol.NEW and self._contexts.writes_counts:
            self._read_count(vertex, ring)
        self._before, self._previous = self._previous, symbol
        return vertex

    def _read_count(self, vertex: int, ring: list) -> None:
        first_count = self._counts.total.get(ring[0]) if ring else None
        context = self._contexts.face_count(self._previous_count, first_count)
        total = self._reader.read_choice(context)
        if not 1 <= total <= self._corner_count:
            raise FormatError("A packed position has an impossible face count")
        self._counts.total[vertex] = total
        self._previous_count = total

    def _position(self, symbol: int, ring: list) -> int:
        """Return the position a corner's symbol names."""
        if symbol == Symbol.NEW:
            self._newest += 1
            return self._newest
        if symbol == Symbol.FAR:
            vertex = self._newest - self._reader.read_number(self._contexts.far)
            if not 0 <= vertex <= self._newest:
                raise FormatError("A packed corner names a missing position")
            return vertex
        if symbol in (Symbol.FORWARD, Symbol.BACK):
            return self._along(symbol, ring)
        if symbol >= len(Symbol):
            raise FormatError("A packed corner has an unknown symbol")
        vertex = self._open_edges.candidates(ring)[symbol - 1]
        if vertex is None:
            raise FormatError("A packed corner names a missing open edge")
        return vertex

    def _along(self, symbol: int, ring: list) -> int:
        steps = self._reader.read_choice(self._contexts.steps) + 1
        if not ring or not 1 <= steps <= _MAX_STEPS:
            raise FormatError("A packed corner reaches too far along the boundary")
        forward = symbol == Symbol.FORWARD
        start = ring[-1] if forward else ring[0]
        vertex = self._open_edges.along(start, steps, forward)
        if vertex is None:
            raise FormatError("A packed corner names a missing open edge")
        return vertex
