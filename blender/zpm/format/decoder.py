# SPDX-License-Identifier: GPL-3.0-or-later

"""File bytes to Mesh, for editing tools. Runtime readers index the file instead."""

import struct
from array import array

from .column import read_column
from .layout import (
    AXIS_COUNT,
    DEFAULT_MAX_BYTES,
    DIRECTORY_ENTRY,
    FILE_HEADER,
    MAGIC,
    MAX_RELATIVE_ARITY,
    MESH_INFO,
    TAIL_BYTES,
    UINT32_MAX,
    VERSION,
    SectionFlags,
    SectionKind,
    align,
    ordered_to_bits,
)
from .model import FormatError, Mesh
from .rank_mask import read_rank_mask
from .select_bits import read_select_bits
from .topology import next_corners

_SHORT_ID_LIMIT = 65536


class _Sections:
    """The directory of one file: each kind maps to its flags and bytes."""

    def __init__(self, data: bytes):
        if len(data) < FILE_HEADER.size + TAIL_BYTES:
            raise FormatError("File is truncated")
        magic, version, count, file_bytes = FILE_HEADER.unpack_from(data)
        if magic != MAGIC or version != VERSION:
            raise FormatError("Unsupported ZPM file or version")
        if file_bytes != len(data):
            raise FormatError("File length does not match its header")
        limit = file_bytes - TAIL_BYTES
        expected = FILE_HEADER.size + count * DIRECTORY_ENTRY.size
        if expected > limit:
            raise FormatError("File directory is truncated")
        if any(data[limit:]):
            raise FormatError("File tail is not zero")
        entries = [
            DIRECTORY_ENTRY.unpack_from(
                data, FILE_HEADER.size + index * DIRECTORY_ENTRY.size
            )
            for index in range(count)
        ]
        self._sections = {}
        previous_kind = 0
        for index, (kind, flags, offset) in enumerate(entries):
            if kind <= previous_kind or offset != expected:
                raise FormatError("File directory is out of order")
            end = entries[index + 1][2] if index + 1 < count else limit
            if end < offset or end > limit or end != align(end):
                raise FormatError("File section is out of bounds")
            try:
                known = SectionKind(kind)
                self._sections[known] = (SectionFlags(flags), data[offset:end])
            except ValueError as error:
                raise FormatError("File has an unknown section") from error
            previous_kind = kind
            expected = end
        if expected != limit:
            raise FormatError("File has bytes no section claims")

    def has(self, kind: SectionKind) -> bool:
        return kind in self._sections

    def raw(self, kind: SectionKind) -> bytes:
        if kind not in self._sections:
            raise FormatError(f"File is missing section {kind.name}")
        return self._sections[kind][1]

    def column(self, kind: SectionKind, count: int) -> list:
        if kind not in self._sections:
            raise FormatError(f"File is missing section {kind.name}")
        flags, section = self._sections[kind]
        values = read_column(section, flags)
        if len(values) != count:
            raise FormatError(f"Section {kind.name} has the wrong element count")
        return values

    def count_of(self, kind: SectionKind) -> int:
        return struct.unpack_from("<I", self.raw(kind))[0]

    def flags(self, kind: SectionKind) -> SectionFlags:
        return self._sections[kind][0]


def _read_axis(sections: _Sections, axis: int, count: int) -> list:
    constant = SectionKind(SectionKind.POSITION_CONSTANT_X + axis)
    bits = SectionKind(SectionKind.POSITION_BITS_X + axis)
    palette_kind = SectionKind(SectionKind.POSITION_PALETTE_X + axis)
    indices_kind = SectionKind(SectionKind.POSITION_INDICES_X + axis)
    if sections.has(constant):
        return [struct.unpack("<I", sections.raw(constant))[0]] * count
    if sections.has(bits):
        return [ordered_to_bits(value) for value in sections.column(bits, count)]
    palette = sections.column(palette_kind, sections.count_of(palette_kind))
    indices = sections.column(indices_kind, count)
    try:
        if sections.flags(indices_kind) & SectionFlags.SIGNED_INDICES:
            return [palette[i >> 1] | ((i & 1) << 31) for i in indices]
        return [palette[i] for i in indices]
    except IndexError as error:
        raise FormatError("Position palette index is out of range") from error


def _read_positions(sections: _Sections, vertex_count: int, unique_count: int) -> list:
    if not vertex_count:
        return []
    axes = [_read_axis(sections, axis, unique_count) for axis in range(AXIS_COUNT)]
    rows = list(zip(*axes, strict=True))
    if not sections.has(SectionKind.POSITION_REPEAT_MASK):
        if unique_count != vertex_count:
            raise FormatError("Unique position count does not match the mesh")
        return [value for row in rows for value in row]
    marks = read_rank_mask(
        sections.raw(SectionKind.POSITION_REPEAT_MASK), vertex_count
    )
    repeat_ids = iter(
        sections.column(SectionKind.POSITION_REPEAT_IDS, vertex_count - unique_count)
    )
    bits = []
    next_unique = 0
    try:
        for marked in marks:
            if marked:
                bits.extend(rows[next(repeat_ids)])
            else:
                bits.extend(rows[next_unique])
                next_unique += 1
    except (IndexError, StopIteration) as error:
        raise FormatError("Repeated position ID is out of range") from error
    return bits


def _read_back_distances(sections: _Sections, corner_count: int) -> list:
    kind = SectionKind.CORNER_BACK_DISTANCES
    distances = iter(sections.column(kind, sections.count_of(kind)))
    marks = read_rank_mask(sections.raw(SectionKind.CORNER_REPEAT_MASK), corner_count)
    corners = []
    newest = 0
    try:
        for marked in marks:
            if marked:
                corners.append(newest - 1 - next(distances))
            else:
                corners.append(newest)
                newest += 1
    except StopIteration as error:
        raise FormatError("Corner distances do not match their mask") from error
    if corners and min(corners) < 0:
        raise FormatError("Corner reaches back before the first vertex")
    return corners


def _read_corners(sections: _Sections, corner_count: int, face_count: int, arity: int):
    if not corner_count:
        return []
    if sections.has(SectionKind.CORNER_REPEAT_MASK):
        return _read_back_distances(sections, corner_count)
    if not sections.has(SectionKind.CORNER_FIRST):
        return sections.column(SectionKind.CORNER_IDS, corner_count)
    if not 3 <= arity <= MAX_RELATIVE_ARITY:
        raise FormatError("Invalid uniform face layout")
    first = sections.column(SectionKind.CORNER_FIRST, face_count)
    corners = [0] * corner_count
    corners[0::arity] = first
    for position in range(1, arity):
        kind = SectionKind(SectionKind.CORNER_FIRST + position)
        corners[position::arity] = [
            (start + offset) & UINT32_MAX
            for start, offset in zip(
                first, sections.column(kind, face_count), strict=True
            )
        ]
    return corners


def _read_face_sizes(sections: _Sections, corner_count: int, face_count: int) -> list:
    data = sections.raw(SectionKind.FACE_STARTS)
    words = (corner_count + 63) // 64
    selects = (face_count + 63) // 64
    starts = read_select_bits(data[: words * 8 + selects * 4], corner_count, face_count)
    if not starts or starts[0] != 0:
        raise FormatError("Face starts are invalid")
    ends = starts[1:] + [corner_count]
    return [end - start for start, end in zip(starts, ends, strict=True)]


def _read_edges(sections: _Sections, edge_count: int, corners: list, sizes: list):
    if not edge_count:
        return []
    if sections.has(SectionKind.EDGE_FIRST):
        first = sections.column(SectionKind.EDGE_FIRST, edge_count)
        spans = sections.column(SectionKind.EDGE_SPAN, edge_count)
        edges = [0] * (edge_count * 2)
        edges[0::2] = first
        edges[1::2] = [
            (start + span) & UINT32_MAX
            for start, span in zip(first, spans, strict=True)
        ]
        return edges
    marks = [False] * edge_count
    loose_first = loose_second = []
    if sections.has(SectionKind.EDGE_LOOSE_MASK):
        marks = read_rank_mask(sections.raw(SectionKind.EDGE_LOOSE_MASK), edge_count)
        loose_first = sections.column(SectionKind.EDGE_LOOSE_FIRST, sum(marks))
        loose_second = sections.column(SectionKind.EDGE_LOOSE_SECOND, sum(marks))
    attached = edge_count - sum(marks)
    if sections.has(SectionKind.EDGE_FIRST_USE):
        words = (len(corners) + 63) // 64
        selects = (attached + 63) // 64
        data = sections.raw(SectionKind.EDGE_FIRST_USE)
        references = read_select_bits(
            data[: words * 8 + selects * 4],
            len(corners),
            attached,
        )
    elif attached:
        references = sections.column(SectionKind.EDGE_CORNERS, attached)
    else:
        references = []
    flips = (
        sections.column(SectionKind.EDGE_FLIPS, attached)
        if sections.has(SectionKind.EDGE_FLIPS)
        else None
    )
    if references and max(references) >= len(corners):
        raise FormatError("Edge references a missing corner")
    following = next_corners(sizes, len(corners))
    edges = []
    next_attached = 0
    next_loose = 0
    for marked in marks:
        if marked:
            edges += (loose_first[next_loose], loose_second[next_loose])
            next_loose += 1
            continue
        reference = references[next_attached]
        a, b = corners[reference], corners[following[reference]]
        flipped = a > b if flips is None else flips[next_attached] != 0
        edges += (b, a) if flipped else (a, b)
        next_attached += 1
    return edges


def decode(data: bytes, *, max_bytes: int = DEFAULT_MAX_BYTES) -> Mesh:
    """Expand one file into a Mesh, with strict length, range and size checks.

    max_bytes limits both file size and retained expanded buffers. It is not
    a peak process-memory limit: expanding also retains the input bytes.
    """
    if len(data) > max_bytes:
        raise FormatError("File exceeds the configured size limit")
    sections = _Sections(data)
    info = sections.raw(SectionKind.MESH_INFO)
    if len(info) != MESH_INFO.size:
        raise FormatError("Mesh info has the wrong size")
    vertices, faces, corner_count, edge_count, unique, arity = MESH_INFO.unpack(info)
    width = 2 if vertices <= _SHORT_ID_LIMIT else 4
    expanded = vertices * 12 + (corner_count + edge_count * 2) * width + faces * 4
    if expanded > max_bytes:
        raise FormatError("Decoded mesh exceeds the configured size limit")
    if unique > vertices or (faces and not corner_count):
        raise FormatError("Invalid element counts")
    if arity and arity * faces != corner_count:
        raise FormatError("Invalid uniform face layout")

    bits = _read_positions(sections, vertices, unique)
    corners = _read_corners(sections, corner_count, faces, arity)
    mixed = corner_count and not arity
    stored_sizes = _read_face_sizes(sections, corner_count, faces) if mixed else []
    sizes = [arity] * faces if arity else stored_sizes
    edges = _read_edges(sections, edge_count, corners, sizes)

    positions = array("f")
    positions.frombytes(array("I", bits).tobytes())
    code = "H" if width == 2 else "I"
    try:
        mesh = Mesh(
            positions,
            array("I", stored_sizes),
            array(code, corners),
            array(code, edges),
            arity=arity,
        )
    except OverflowError as error:
        raise FormatError("Topology references a missing vertex") from error
    mesh.validate()
    return mesh
