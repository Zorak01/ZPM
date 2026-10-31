# SPDX-License-Identifier: GPL-3.0-or-later

"""File bytes to Mesh, for editing tools. Runtime readers index the file instead."""

import math
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
    NO_GRID,
    TAIL_BYTES,
    UINT32_MAX,
    VERSION,
    MeshFlags,
    SectionFlags,
    SectionKind,
    StreamRole,
    align,
    ordered_to_bits,
    to_signed,
)
from .model import FormatError, Mesh
from .rank_mask import read_rank_mask
from .select_bits import read_select_bits
from .surface_values import decode_crease, decode_normal, decode_uv
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
        previous = (0, 0)
        for index, (kind, flags, layer, offset) in enumerate(entries):
            if (kind, layer) <= previous or offset != expected:
                raise FormatError("File directory is out of order")
            end = entries[index + 1][3] if index + 1 < count else limit
            if end < offset or end > limit or end != align(end):
                raise FormatError("File section is out of bounds")
            try:
                known = SectionKind(kind)
                self._sections[known, layer] = (SectionFlags(flags), data[offset:end])
            except ValueError as error:
                raise FormatError("File has an unknown section") from error
            previous = (kind, layer)
            expected = end
        if expected != limit:
            raise FormatError("File has bytes no section claims")

    def has(self, kind: SectionKind, layer: int = 0) -> bool:
        return (kind, layer) in self._sections

    def raw(self, kind: SectionKind, layer: int = 0) -> bytes:
        if (kind, layer) not in self._sections:
            raise FormatError(f"File is missing section {kind.name}")
        return self._sections[kind, layer][1]

    def column(self, kind: SectionKind, count: int, layer: int = 0) -> list:
        if (kind, layer) not in self._sections:
            raise FormatError(f"File is missing section {kind.name}")
        flags, section = self._sections[kind, layer]
        values = read_column(section, flags)
        if len(values) != count:
            raise FormatError(f"Section {kind.name} has the wrong element count")
        return values

    def count_of(self, kind: SectionKind, layer: int = 0) -> int:
        return struct.unpack_from("<I", self.raw(kind, layer))[0]

    def flags(self, kind: SectionKind) -> SectionFlags:
        return self._sections[kind, 0][0]


def _float_from_bits(bits: int) -> float:
    return struct.unpack("<f", struct.pack("<I", bits))[0]


def _read_axis(sections: _Sections, axis: int, count: int, exponent: int) -> list:
    constant = SectionKind(SectionKind.POSITION_CONSTANT_X + axis)
    bits = SectionKind(SectionKind.POSITION_BITS_X + axis)
    grid = SectionKind(SectionKind.POSITION_GRID_X + axis)
    palette_kind = SectionKind(SectionKind.POSITION_PALETTE_X + axis)
    indices_kind = SectionKind(SectionKind.POSITION_INDICES_X + axis)
    if sections.has(constant):
        stored = struct.unpack("<I", sections.raw(constant)[:4])[0]
        return [_float_from_bits(stored)] * count
    if sections.has(bits):
        return [
            _float_from_bits(ordered_to_bits(value))
            for value in sections.column(bits, count)
        ]
    if exponent == NO_GRID:
        raise FormatError("Grid positions have no grid")
    if sections.has(grid):
        stored = [to_signed(value) for value in sections.column(grid, count)]
    else:
        palette = sections.column(palette_kind, sections.count_of(palette_kind))
        indices = sections.column(indices_kind, count)
        try:
            if sections.flags(indices_kind) & SectionFlags.SIGNED_INDICES:
                stored = [
                    -palette[i >> 1] if i & 1 else palette[i >> 1] for i in indices
                ]
            else:
                stored = [palette[i] for i in indices]
        except IndexError as error:
            raise FormatError("Position palette index is out of range") from error
    return [math.ldexp(value, exponent) for value in stored]


def _read_positions(
    sections: _Sections, vertex_count: int, unique_count: int, exponent: int
) -> list:
    if not vertex_count:
        return []
    axes = [
        _read_axis(sections, axis, unique_count, exponent) for axis in range(AXIS_COUNT)
    ]
    rows = list(zip(*axes, strict=True))
    if not sections.has(SectionKind.POSITION_REPEAT_MASK):
        if unique_count != vertex_count:
            raise FormatError("Unique position count does not match the mesh")
        return [value for row in rows for value in row]
    marks = read_rank_mask(sections.raw(SectionKind.POSITION_REPEAT_MASK), vertex_count)
    distances = sections.column(
        SectionKind.POSITION_BACK_DISTANCES, vertex_count - unique_count
    )
    ids = _resolve_back_references(marks, distances, "Position")
    if ids and max(ids) >= unique_count:
        raise FormatError("Repeated position ID is out of range")
    return [value for unique in ids for value in rows[unique]]


def _resolve_back_references(marks: list, distances: list, limit_name: str) -> list:
    """Turn a repeat mask and distances back into one ID per element."""
    references = iter(distances)
    resolved = []
    newest = 0
    try:
        for marked in marks:
            if marked:
                resolved.append(newest - 1 - next(references))
            else:
                resolved.append(newest)
                newest += 1
    except StopIteration as error:
        raise FormatError(f"{limit_name} distances do not match their mask") from error
    if resolved and min(resolved) < 0:
        raise FormatError(f"{limit_name} reaches back before the first value")
    return resolved


def _read_back_distances(sections: _Sections, corner_count: int) -> list:
    kind = SectionKind.CORNER_BACK_DISTANCES
    marks = read_rank_mask(sections.raw(SectionKind.CORNER_REPEAT_MASK), corner_count)
    distances = sections.column(kind, sections.count_of(kind))
    return _resolve_back_references(marks, distances, "Corner")


def _read_corner_stream(
    sections: _Sections, first_kind: SectionKind, layer: int, corners: list
) -> list:
    """Return one stored pair per corner, from either stream form."""

    def kind(role: StreamRole) -> SectionKind:
        return SectionKind(first_kind + role)

    if sections.has(kind(StreamRole.REPEAT_MASK), layer):
        marks = read_rank_mask(
            sections.raw(kind(StreamRole.REPEAT_MASK), layer), len(corners)
        )
        distances = sections.column(kind(StreamRole.DISTANCES), sum(marks), layer)
        ids = _resolve_back_references(marks, distances, "Corner stream")
        count = len(corners) - sum(marks)
        values_a = sections.column(kind(StreamRole.VALUES_A), count, layer)
        values_b = sections.column(kind(StreamRole.VALUES_B), count, layer)
        return [(values_a[i], values_b[i]) for i in ids]
    vertex_kind = kind(StreamRole.VERTEX_A)
    vertex_a = sections.column(
        vertex_kind, sections.count_of(vertex_kind, layer), layer
    )
    vertex_b = sections.column(kind(StreamRole.VERTEX_B), len(vertex_a), layer)
    if corners and max(corners) >= len(vertex_a):
        raise FormatError("Corner stream does not cover every vertex")
    pairs = [(vertex_a[v], vertex_b[v]) for v in corners]
    if sections.has(kind(StreamRole.EXCEPTION_MASK), layer):
        marks = read_rank_mask(
            sections.raw(kind(StreamRole.EXCEPTION_MASK), layer), len(corners)
        )
        count = sum(marks)
        except_a = iter(sections.column(kind(StreamRole.EXCEPTION_A), count, layer))
        except_b = iter(sections.column(kind(StreamRole.EXCEPTION_B), count, layer))
        for corner, marked in enumerate(marks):
            if marked:
                pairs[corner] = (next(except_a), next(except_b))
    return pairs


def _read_uv_names(section: bytes) -> list:
    try:
        count = struct.unpack_from("<I", section)[0]
        names = []
        at = 4
        for _ in range(count):
            length = struct.unpack_from("<I", section, at)[0]
            at += 4
            if at + length > len(section):
                raise FormatError("UV layer name is out of bounds")
            names.append(section[at : at + length].decode("utf-8"))
            at += length
    except (struct.error, UnicodeDecodeError) as error:
        raise FormatError("UV layer names are invalid") from error
    if any(section[at:]) or align(at) != len(section):
        raise FormatError("UV layer names have invalid padding")
    return names


def _read_flags(sections: _Sections, kind: SectionKind, count: int) -> array:
    if not sections.has(kind):
        return array("B")
    values = sections.column(kind, count)
    if values and max(values) > 1:
        raise FormatError(f"Section {kind.name} holds a value other than 0 or 1")
    return array("B", values)


def _read_creases(sections: _Sections, kind: SectionKind, count: int) -> array:
    if not sections.has(kind):
        return array("f")
    return array("f", [decode_crease(v) for v in sections.column(kind, count)])


def _read_surface(
    sections: _Sections,
    corners: list,
    edges: int,
    faces: int,
    vertices: int,
    flags: MeshFlags,
) -> dict:
    surface = {
        "has_custom_normals": bool(flags & MeshFlags.CUSTOM_NORMALS),
        "sharp_edges": _read_flags(sections, SectionKind.EDGE_SHARP, edges),
        "seam_edges": _read_flags(sections, SectionKind.EDGE_SEAM, edges),
        "flat_faces": _read_flags(sections, SectionKind.FACE_FLAT, faces),
        "edge_creases": _read_creases(sections, SectionKind.EDGE_CREASES, edges),
        "vertex_creases": _read_creases(sections, SectionKind.VERTEX_CREASES, vertices),
    }
    has_normals = sections.has(SectionKind.NORMAL_REPEAT_MASK) or sections.has(
        SectionKind.NORMAL_VERTEX_A
    )
    if has_normals:
        pairs = _read_corner_stream(
            sections, SectionKind.NORMAL_REPEAT_MASK, 0, corners
        )
        surface["corner_normals"] = array(
            "f", [part for a, b in pairs for part in decode_normal(a, b)]
        )
    layers = {}
    if sections.has(SectionKind.UV_NAMES):
        names = _read_uv_names(sections.raw(SectionKind.UV_NAMES))
        for layer, name in enumerate(names):
            pairs = _read_corner_stream(
                sections, SectionKind.UV_REPEAT_MASK, layer, corners
            )
            layers[name] = array(
                "f",
                [decode_uv(to_signed(part)) for pair in pairs for part in pair],
            )
    surface["uv_layers"] = layers
    return surface


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
    (
        vertices,
        faces,
        corner_count,
        edge_count,
        unique,
        arity,
        exponent,
        flags,
    ) = MESH_INFO.unpack(info)
    width = 2 if vertices <= _SHORT_ID_LIMIT else 4
    expanded = vertices * 12 + (corner_count + edge_count * 2) * width + faces * 4
    expanded += corner_count * 12 if sections.has(SectionKind.NORMAL_VERTEX_A) else 0
    if expanded > max_bytes:
        raise FormatError("Decoded mesh exceeds the configured size limit")
    if unique > vertices or (faces and not corner_count):
        raise FormatError("Invalid element counts")
    if arity and arity * faces != corner_count:
        raise FormatError("Invalid uniform face layout")

    position_values = _read_positions(sections, vertices, unique, exponent)
    corners = _read_corners(sections, corner_count, faces, arity)
    mixed = corner_count and not arity
    stored_sizes = _read_face_sizes(sections, corner_count, faces) if mixed else []
    sizes = [arity] * faces if arity else stored_sizes
    edges = _read_edges(sections, edge_count, corners, sizes)

    positions = array("f", position_values)
    code = "H" if width == 2 else "I"
    try:
        mesh = Mesh(
            positions,
            array("I", stored_sizes),
            array(code, corners),
            array(code, edges),
            arity=arity,
            **_read_surface(
                sections, corners, edge_count, faces, vertices, MeshFlags(flags)
            ),
        )
    except OverflowError as error:
        raise FormatError("Topology references a missing vertex") from error
    mesh.validate()
    return mesh
