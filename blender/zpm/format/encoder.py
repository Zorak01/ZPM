# SPDX-License-Identifier: GPL-3.0-or-later

"""Mesh to file bytes. Every stream takes whichever legal form is smallest."""

import struct
import sys
from array import array
from dataclasses import dataclass

from .column import encode_column
from .layout import (
    AXIS_COUNT,
    BLOCK_SIZE,
    DIRECTORY_ENTRY,
    FILE_HEADER,
    MAGIC,
    MAGNITUDE_MASK,
    MAX_RELATIVE_ARITY,
    MAX_UNIFORM_ARITY,
    MESH_INFO,
    SIGN_BIT,
    TAIL_BYTES,
    UINT32_MAX,
    VERSION,
    SectionFlags,
    SectionKind,
    align,
    bits_to_ordered,
)
from .model import FormatError, Mesh
from .optimize import reorder_mesh, snap_positions
from .rank_mask import encode_rank_mask
from .select_bits import encode_select_bits
from .topology import next_corners

_REFERENCE_PASSES = 2


@dataclass(frozen=True, slots=True)
class _Section:
    kind: SectionKind
    data: bytes
    flags: SectionFlags = SectionFlags.NONE


def _column(kind: SectionKind, values: list, extra=SectionFlags.NONE) -> _Section:
    data, flags = encode_column(values)
    return _Section(kind, data, flags | extra)


def _stored_bytes(sections: list) -> int:
    return sum(
        align(len(section.data)) + DIRECTORY_ENTRY.size for section in sections
    )


def _encode_axis(rows: list, axis: int, count: int) -> list:
    bits_kind = SectionKind.POSITION_BITS_X + axis
    column = [rows[i * AXIS_COUNT + axis] for i in range(count)]
    if len(set(column)) == 1:
        constant_kind = SectionKind(SectionKind.POSITION_CONSTANT_X + axis)
        return [_Section(constant_kind, struct.pack("<I", column[0]))]
    direct = [
        _column(SectionKind(bits_kind), [bits_to_ordered(bits) for bits in column])
    ]
    negative = any(bits & SIGN_BIT for bits in column)
    palette = sorted({bits & MAGNITUDE_MASK for bits in column})
    lookup = {value: index for index, value in enumerate(palette)}
    if negative:
        indices = [lookup[bits & MAGNITUDE_MASK] * 2 + (bits >> 31) for bits in column]
    else:
        indices = [lookup[bits & MAGNITUDE_MASK] for bits in column]
    index_flag = SectionFlags.SIGNED_INDICES if negative else SectionFlags.NONE
    palette_kind = SectionKind(SectionKind.POSITION_PALETTE_X + axis)
    indices_kind = SectionKind(SectionKind.POSITION_INDICES_X + axis)
    paletted = [
        _column(palette_kind, palette),
        _column(indices_kind, indices, index_flag),
    ]
    return paletted if _stored_bytes(paletted) < _stored_bytes(direct) else direct


def _encode_axes(rows: list, count: int) -> list:
    return [
        section
        for axis in range(AXIS_COUNT)
        for section in _encode_axis(rows, axis, count)
    ]


def _encode_positions(bits: list, vertex_count: int) -> tuple:
    """Return (sections, unique position count)."""
    plain = _encode_axes(bits, vertex_count)
    seen = {}
    rows = []
    marks = [False] * vertex_count
    repeat_ids = []
    for vertex in range(vertex_count):
        key = tuple(bits[vertex * AXIS_COUNT : (vertex + 1) * AXIS_COUNT])
        known = seen.get(key)
        if known is None:
            seen[key] = len(seen)
            rows.extend(key)
        else:
            marks[vertex] = True
            repeat_ids.append(known)
    if not repeat_ids:
        return plain, vertex_count
    layered = [
        _Section(SectionKind.POSITION_REPEAT_MASK, encode_rank_mask(marks)),
        _column(SectionKind.POSITION_REPEAT_IDS, repeat_ids),
        *_encode_axes(rows, len(seen)),
    ]
    if _stored_bytes(layered) < _stored_bytes(plain):
        return layered, len(seen)
    return plain, vertex_count


def _encode_back_distances(corners: list) -> list | None:
    """Store how far each corner reaches back, if vertices are numbered by first use.

    A corner either uses the next new vertex or an earlier one. Marked corners store
    their distance back from the newest vertex, so nearby faces cost few bits, and the
    newest vertex at any corner is just its rank in the mask.
    """
    newest = 0
    marks = []
    distances = []
    for vertex in corners:
        if vertex > newest:
            return None
        if vertex == newest:
            newest += 1
            marks.append(False)
        else:
            distances.append(newest - 1 - vertex)
            marks.append(True)
    return [
        _Section(SectionKind.CORNER_REPEAT_MASK, encode_rank_mask(marks)),
        _column(SectionKind.CORNER_BACK_DISTANCES, distances),
    ]


def _encode_corners(corners: list, face_count: int, arity: int) -> list:
    best = [_column(SectionKind.CORNER_IDS, corners)]
    if 3 <= arity <= MAX_RELATIVE_ARITY:
        first = corners[0::arity]
        relative = [_column(SectionKind.CORNER_FIRST, first)]
        for position in range(1, arity):
            offsets = [
                corners[face * arity + position] - first[face]
                for face in range(face_count)
            ]
            kind = SectionKind(SectionKind.CORNER_FIRST + position)
            relative.append(_column(kind, offsets))
        if _stored_bytes(relative) < _stored_bytes(best):
            best = relative
    distances = _encode_back_distances(corners)
    if distances and _stored_bytes(distances) < _stored_bytes(best):
        best = distances
    return best


def _encode_face_starts(sizes: list, corner_count: int) -> _Section:
    starts = []
    at = 0
    for size in sizes:
        starts.append(at)
        at += size
    return _Section(SectionKind.FACE_STARTS, encode_select_bits(starts, corner_count))


def _nearest_references(candidates: list) -> list:
    """An edge has a corner on each side; pick whichever sits nearest its block."""
    picked = [options[0] for options in candidates]
    for start in range(0, len(picked), BLOCK_SIZE):
        end = min(start + BLOCK_SIZE, len(picked))
        for _ in range(_REFERENCE_PASSES):
            middle = sorted(picked[start:end])[(end - start) // 2]
            for i in range(start, end):
                picked[i] = min(
                    candidates[i],
                    key=lambda corner: (abs(corner - middle), corner != picked[i]),
                )
    return picked


def _encode_edge_first_use(pairs: list, corners: list, following: list):
    """Mark the corner that first reaches each edge, if edges are stored in that order.

    Costs one bit per corner. Returns None unless every pair is lower ID first and in
    exactly the order the corners first reach them.
    """
    reached = {}
    for corner, vertex in enumerate(corners):
        other = corners[following[corner]]
        reached.setdefault((min(vertex, other), max(vertex, other)), corner)
    if pairs != list(reached):
        return None
    data = encode_select_bits(list(reached.values()), len(corners))
    return _Section(SectionKind.EDGE_FIRST_USE, data)


def _encode_edge_references(edges: list, corners: list, sizes: list) -> list:
    following = next_corners(sizes, len(corners))
    corners_of_edge = {}
    for corner, vertex in enumerate(corners):
        other = corners[following[corner]]
        key = (vertex, other) if vertex < other else (other, vertex)
        corners_of_edge.setdefault(key, []).append(corner)
    edge_count = len(edges) // 2
    candidates = []
    first_ids = []
    attached_pairs = []
    loose_marks = [False] * edge_count
    loose_first = []
    loose_second = []
    for edge in range(edge_count):
        a, b = edges[edge * 2], edges[edge * 2 + 1]
        options = corners_of_edge.get((a, b) if a < b else (b, a))
        if options is None:
            loose_marks[edge] = True
            loose_first.append(a)
            loose_second.append(b)
        else:
            candidates.append(options)
            first_ids.append(a)
            attached_pairs.append((a, b))
    sections = []
    if loose_first:
        sections += [
            _Section(SectionKind.EDGE_LOOSE_MASK, encode_rank_mask(loose_marks)),
            _column(SectionKind.EDGE_LOOSE_FIRST, loose_first),
            _column(SectionKind.EDGE_LOOSE_SECOND, loose_second),
        ]
    if not candidates:
        return sections
    by_first = [options[0] for options in candidates]
    by_block = _nearest_references(candidates)
    picked = min(
        (by_first, by_block), key=lambda values: len(encode_column(values)[0])
    )
    first_use = _encode_edge_first_use(attached_pairs, corners, following)
    if first_use is not None:
        return [*sections, first_use]
    sections.append(_column(SectionKind.EDGE_CORNERS, picked))
    flips = [0 if corners[ref] == a else 1 for ref, a in zip(picked, first_ids)]
    if flips != [
        1 if corners[ref] > corners[following[ref]] else 0 for ref in picked
    ]:
        sections.append(_column(SectionKind.EDGE_FLIPS, flips))
    return sections


def _encode_edges(edges: list, corners: list, sizes: list) -> list:
    spans = [second - first for first, second in zip(edges[0::2], edges[1::2])]
    endpoints = [
        _column(SectionKind.EDGE_FIRST, edges[0::2]),
        _column(SectionKind.EDGE_SPAN, spans),
    ]
    if not corners:
        return endpoints
    referenced = _encode_edge_references(edges, corners, sizes)
    if _stored_bytes(referenced) < _stored_bytes(endpoints):
        return referenced
    return endpoints


def _position_bits(mesh: Mesh) -> list:
    bits = array("I")
    bits.frombytes(mesh.positions.tobytes())
    if sys.byteorder != "little":
        bits.byteswap()
    return bits.tolist()


def _write_file(sections: list) -> bytes:
    """Join sections in rising kind order behind the directory that names them."""
    sections = sorted(sections, key=lambda section: section.kind)
    offset = FILE_HEADER.size + len(sections) * DIRECTORY_ENTRY.size
    directory = bytearray()
    body = bytearray()
    for section in sections:
        directory += DIRECTORY_ENTRY.pack(section.kind, section.flags, offset)
        padded = align(len(section.data))
        body += section.data + bytes(padded - len(section.data))
        offset += padded
    header = FILE_HEADER.pack(MAGIC, VERSION, len(sections), offset + TAIL_BYTES)
    return header + bytes(directory) + bytes(body) + bytes(TAIL_BYTES)


def encode(mesh: Mesh) -> bytes:
    """Return the smallest file bytes for a validated mesh.

    Positions snap to a grid of at most 1 / 2^20 of the model's size. Faces, vertices
    and edges are reordered only when that makes the file smaller. Costs seconds on
    large meshes; reading the result costs nothing extra.
    """
    mesh.validate()
    snapped = snap_positions(mesh)
    as_stored = _encode_mesh(snapped)
    reordered = _encode_mesh(reorder_mesh(snapped))
    return reordered if len(reordered) < len(as_stored) else as_stored


def _encode_mesh(mesh: Mesh) -> bytes:
    corners = list(mesh.corners)
    edges = list(mesh.edges)
    face_count = mesh.face_count
    sizes = [mesh.arity] * face_count if mesh.arity else list(mesh.face_sizes)
    vertex_count = mesh.vertex_count
    if max(vertex_count, face_count, len(corners), mesh.edge_count) > UINT32_MAX:
        raise FormatError("Mesh exceeds the file's 32-bit size limit")

    sections = []
    unique_count = 0
    if vertex_count:
        positions, unique_count = _encode_positions(_position_bits(mesh), vertex_count)
        sections += positions
    arity = 0
    if corners:
        if sizes[0] <= MAX_UNIFORM_ARITY and all(size == sizes[0] for size in sizes):
            arity = sizes[0]
        sections += _encode_corners(corners, face_count, arity)
        if not arity:
            sections.append(_encode_face_starts(sizes, len(corners)))
    if edges:
        sections += _encode_edges(edges, corners, sizes)
    info = MESH_INFO.pack(
        vertex_count, face_count, len(corners), mesh.edge_count, unique_count, arity
    )
    sections.append(_Section(SectionKind.MESH_INFO, info))
    return _write_file(sections)
