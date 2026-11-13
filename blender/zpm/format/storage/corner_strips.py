# SPDX-License-Identifier: GPL-3.0-or-later

"""Face corners as strips in small blocks, for faces of up to 32 corners.

A face usually shares an edge with the one before it. Faces are turned so that shared
edge comes first, so a linked face only says which edge of the previous face it shares,
and what its new vertices are. A vertex is either the next new one, costing one bit, or
a distance back from the newest. Reading a corner decodes its block, onto the stack,
from the block's first face to that face: nothing outside the block is needed, and
nothing is allocated. Faces belong to the block their first corner falls in.

After a face v[0..p-1], the next can continue across edge e, from 1 to p - 1, as
(v[e + 1], v[e], new vertices). Triangle strips zigzag and quad strips run straight, so
the code favours that edge.

Per face, in low bits first:
    The first face of a block has no link, only its vertices.
    Bit 0: the preferred edge, then the new vertices.
    Bits 1, 0: another edge, as an index in as few bits as there are other edges, then
    the new vertices.
    Bits 1, 1: no link, then every vertex.
The preferred edge of a triangle is whichever of 1 and 2 was not used last, and of
anything larger its middle edge. Each block starts as if edge 2 was used last, and so
does any face with no link.
Per vertex: 1 bit set for the next new vertex. Otherwise a clear bit, then the distance
plus 8 as an Exp-Golomb code: as many clear bits as it has bits past its lowest four, a
set bit, then its bits below the top one. Thirteen clear bits and a set bit mean 32
whole bits follow instead.
"""

from ..container.layout import SectionKind
from ..container.sections import Section, SectionReader, column_section
from ..errors import FormatError

CORNERS_PER_BLOCK = 384
MAX_FACE_CORNERS = 32
_TRIANGLE_CORNERS = 3
_START_EDGE = 2
_DISTANCE_ORDER = 3
_ESCAPE_ZEROS = 13
_WIDE_BITS = 32
_READ_WINDOW_BYTES = 5


class _BitWriter:
    """Collects bits, moving whole bytes out so it never shifts a long number."""

    def __init__(self):
        self._bytes = bytearray()
        self._pending = 0
        self._pending_bits = 0

    @property
    def count(self) -> int:
        return len(self._bytes) * 8 + self._pending_bits

    def write(self, value: int, width: int) -> None:
        self._pending |= value << self._pending_bits
        self._pending_bits += width
        while self._pending_bits >= 8:
            self._bytes.append(self._pending & 255)
            self._pending >>= 8
            self._pending_bits -= 8

    def to_bytes(self) -> bytes:
        tail = [self._pending] if self._pending_bits else []
        return bytes(self._bytes) + bytes(tail)


class _BitReader:
    def __init__(self, data: bytes):
        self._data = data
        self._position = 0
        self._limit = len(data) * 8

    @property
    def position(self) -> int:
        return self._position

    def read(self, width: int) -> int:
        if self._position + width > self._limit:
            raise FormatError("Corner strips run past their section")
        at = self._position >> 3
        window = int.from_bytes(self._data[at : at + _READ_WINDOW_BYTES], "little")
        value = window >> (self._position & 7) & ((1 << width) - 1)
        self._position += width
        return value


def preferred_edge(size: int, last_edge: int) -> int:
    """Return the edge a strip most likely continues from, after a face of this size."""
    return _TRIANGLE_CORNERS - last_edge if size == _TRIANGLE_CORNERS else size // 2


def _other_edge_bits(size: int) -> int:
    return (size - 3).bit_length()


def _other_edge_index(size: int, preferred: int, edge: int) -> int:
    """Number the edges other than the preferred one, from 0."""
    return edge - 1 if edge < preferred else edge - 2


def _write_vertex(writer: _BitWriter, vertex: int, newest: int) -> int:
    if vertex == newest + 1:
        writer.write(1, 1)
        return vertex
    if vertex > newest:
        raise FormatError("Vertices must be numbered by first use")
    writer.write(0, 1)
    distance = newest - vertex
    shifted = distance + (1 << _DISTANCE_ORDER)
    width = shifted.bit_length() - 1
    zeros = width - _DISTANCE_ORDER
    if zeros >= _ESCAPE_ZEROS:
        writer.write(1 << _ESCAPE_ZEROS, _ESCAPE_ZEROS + 1)
        writer.write(distance, _WIDE_BITS)
    else:
        writer.write(1 << zeros, zeros + 1)
        writer.write(shifted & ((1 << width) - 1), width)
    return newest


def find_shared_edge(previous: list, face: list):
    """Return the edge a face continues from, with that edge first, or None."""
    size = len(previous)
    for edge in range(1, size):
        if face[0] == previous[(edge + 1) % size] and face[1] == previous[edge]:
            return edge
    return None


def can_use_strips(sizes: list) -> bool:
    return bool(sizes) and max(sizes) <= MAX_FACE_CORNERS


def _is_numbered_by_first_use(corners: list) -> bool:
    newest = -1
    for vertex in corners:
        if vertex > newest + 1:
            return False
        newest = max(newest, vertex)
    return True


def _write_link(writer: _BitWriter, size: int, edge, last_edge: int) -> None:
    if edge is None:
        writer.write(3, 2)
        return
    preferred = preferred_edge(size, last_edge)
    if edge == preferred:
        writer.write(0, 1)
        return
    writer.write(1, 2)
    width = _other_edge_bits(size)
    if width:
        writer.write(_other_edge_index(size, preferred, edge), width)


def encode_corner_strips(corners: list, sizes: list) -> list:
    """Return the sections for these faces, or nothing if strips cannot hold them.

    Vertices must be numbered by first use, and no face may be too large.
    """
    if not can_use_strips(sizes) or not _is_numbered_by_first_use(corners):
        return []
    writer = _BitWriter()
    starts = []
    newest_values = []
    newest = -1
    previous = None
    last_edge = _START_EDGE
    block = -1
    at = 0
    for size in sizes:
        face = corners[at : at + size]
        if at // CORNERS_PER_BLOCK != block:
            block = at // CORNERS_PER_BLOCK
            starts.append(writer.count)
            newest_values.append(newest + 1)
            previous = None
            last_edge = _START_EDGE
        edge = find_shared_edge(previous, face) if previous else None
        if previous is not None:
            _write_link(writer, len(previous), edge, last_edge)
        last_edge = edge if edge is not None and edge <= _START_EDGE else _START_EDGE
        for vertex in face[0 if edge is None else 2 :]:
            newest = _write_vertex(writer, vertex, newest)
        previous = face
        at += size
    starts.append(writer.count)
    return [
        column_section(SectionKind.CORNER_STRIP_STARTS, starts),
        column_section(SectionKind.CORNER_STRIP_NEWEST, newest_values),
        Section(SectionKind.CORNER_STRIP_BITS, writer.to_bytes()),
    ]


def _read_vertex(reader: _BitReader, newest: int) -> tuple:
    if reader.read(1):
        return newest + 1, newest + 1
    zeros = 0
    while not reader.read(1):
        zeros += 1
        if zeros > _ESCAPE_ZEROS:
            raise FormatError("A corner distance is too long")
    if zeros == _ESCAPE_ZEROS:
        distance = reader.read(_WIDE_BITS)
    else:
        width = zeros + _DISTANCE_ORDER
        distance = (1 << width | reader.read(width)) - (1 << _DISTANCE_ORDER)
    if distance > newest:
        raise FormatError("A corner reaches before the first vertex")
    return newest - distance, newest


def _read_link(reader: _BitReader, size: int, last_edge: int):
    """Return the shared edge, or None for no link."""
    preferred = preferred_edge(size, last_edge)
    if not reader.read(1):
        return preferred
    if reader.read(1):
        return None
    width = _other_edge_bits(size)
    index = reader.read(width) if width else 0
    edge = index + 1 if index + 1 < preferred else index + 2
    if edge >= size:
        raise FormatError("A corner strip names a missing edge")
    return edge


def read_corner_strips(sections: SectionReader, sizes: list) -> list:
    """Return one vertex ID per corner, proving each block starts where it says."""
    if not can_use_strips(sizes):
        raise FormatError("Corner strips hold a face that is too large")
    last_start = sum(sizes) - sizes[-1]
    blocks = last_start // CORNERS_PER_BLOCK + 1
    starts = sections.column(SectionKind.CORNER_STRIP_STARTS, blocks + 1)
    newest_values = sections.column(SectionKind.CORNER_STRIP_NEWEST, blocks)
    reader = _BitReader(sections.raw(SectionKind.CORNER_STRIP_BITS))
    corners = []
    newest = -1
    previous = None
    last_edge = _START_EDGE
    block = -1
    for size in sizes:
        at = len(corners)
        if at // CORNERS_PER_BLOCK != block:
            block = at // CORNERS_PER_BLOCK
            if starts[block] != reader.position or newest_values[block] != newest + 1:
                raise FormatError("A corner strip block does not start where it says")
            previous = None
            last_edge = _START_EDGE
        edge = _read_link(reader, len(previous), last_edge) if previous else None
        face = []
        if edge is not None:
            count = len(previous)
            face += (previous[(edge + 1) % count], previous[edge])
        last_edge = edge if edge is not None and edge <= _START_EDGE else _START_EDGE
        while len(face) < size:
            vertex, newest = _read_vertex(reader, newest)
            face.append(vertex)
        corners += face
        previous = face
    if starts[blocks] != reader.position:
        raise FormatError("Corner strips end in the wrong place")
    return corners
