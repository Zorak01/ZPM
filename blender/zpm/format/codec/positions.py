# SPDX-License-Identifier: GPL-3.0-or-later

"""Vertex positions: repeats removed, then each axis in its smallest form."""

import math
import struct

from ..container.layout import (
    AXIS_COUNT,
    NO_GRID,
    SectionFlags,
    SectionKind,
    bits_to_ordered,
    float_bits,
    float_from_bits,
    ordered_to_bits,
    to_signed,
)
from ..container.sections import (
    Section,
    SectionReader,
    column_section,
    stored_bytes,
)
from ..errors import FormatError
from ..mesh import Mesh
from ..storage.back_references import resolve_back_references
from ..storage.rank_mask import encode_rank_mask, read_rank_mask


def _encode_axis(rows: list, axis: int, count: int, exponent: int) -> list:
    column = [rows[i * AXIS_COUNT + axis] for i in range(count)]
    if len(set(column)) == 1:
        kind = SectionKind(SectionKind.POSITION_CONSTANT_X + axis)
        return [Section(kind, struct.pack("<I", float_bits(column[0])))]
    if exponent == NO_GRID:
        ordered = [bits_to_ordered(float_bits(value)) for value in column]
        return [
            column_section(SectionKind(SectionKind.POSITION_BITS_X + axis), ordered)
        ]
    grid = [round(math.ldexp(value, -exponent)) for value in column]
    direct = [column_section(SectionKind(SectionKind.POSITION_GRID_X + axis), grid)]
    paletted = _encode_palette(grid, axis)
    return paletted if stored_bytes(paletted) < stored_bytes(direct) else direct


def _encode_palette(grid: list, axis: int) -> list:
    """Store each size once, with its sign in the index: mirrored values share."""
    negative = any(value < 0 for value in grid)
    palette = sorted({abs(value) for value in grid})
    lookup = {value: index for index, value in enumerate(palette)}
    if negative:
        indices = [lookup[abs(value)] * 2 + (value < 0) for value in grid]
    else:
        indices = [lookup[value] for value in grid]
    flag = SectionFlags.SIGNED_INDICES if negative else SectionFlags.NONE
    return [
        column_section(SectionKind(SectionKind.POSITION_PALETTE_X + axis), palette),
        column_section(
            SectionKind(SectionKind.POSITION_INDICES_X + axis), indices, flag
        ),
    ]


def _encode_axes(rows: list, count: int, exponent: int) -> list:
    return [
        section
        for axis in range(AXIS_COUNT)
        for section in _encode_axis(rows, axis, count, exponent)
    ]


def encode_positions(mesh: Mesh, exponent: int) -> tuple:
    """Return (sections, unique position count)."""
    values = list(mesh.positions)
    vertex_count = mesh.vertex_count
    plain = _encode_axes(values, vertex_count, exponent)
    raw = mesh.positions.tobytes()
    known = {}
    rows = []
    marks = [False] * vertex_count
    distances = []
    for vertex in range(vertex_count):
        key = raw[vertex * 12 : (vertex + 1) * 12]
        index = known.get(key)
        if index is None:
            known[key] = len(known)
            rows.extend(values[vertex * AXIS_COUNT : (vertex + 1) * AXIS_COUNT])
        else:
            marks[vertex] = True
            distances.append(len(known) - 1 - index)
    if not distances:
        return plain, vertex_count
    layered = [
        Section(SectionKind.POSITION_REPEAT_MASK, encode_rank_mask(marks)),
        column_section(SectionKind.POSITION_BACK_DISTANCES, distances),
        *_encode_axes(rows, len(known), exponent),
    ]
    if stored_bytes(layered) < stored_bytes(plain):
        return layered, len(known)
    return plain, vertex_count


def _read_axis(sections: SectionReader, axis: int, count: int, exponent: int) -> list:
    constant = SectionKind(SectionKind.POSITION_CONSTANT_X + axis)
    bits = SectionKind(SectionKind.POSITION_BITS_X + axis)
    grid = SectionKind(SectionKind.POSITION_GRID_X + axis)
    if sections.has(constant):
        stored = struct.unpack("<I", sections.raw(constant)[:4])[0]
        return [float_from_bits(stored)] * count
    if sections.has(bits):
        return [
            float_from_bits(ordered_to_bits(value))
            for value in sections.column(bits, count)
        ]
    if exponent == NO_GRID:
        raise FormatError("Grid positions have no grid")
    if sections.has(grid):
        stored = [to_signed(value) for value in sections.column(grid, count)]
    else:
        stored = _read_palette(sections, axis, count)
    return [math.ldexp(value, exponent) for value in stored]


def _read_palette(sections: SectionReader, axis: int, count: int) -> list:
    palette_kind = SectionKind(SectionKind.POSITION_PALETTE_X + axis)
    indices_kind = SectionKind(SectionKind.POSITION_INDICES_X + axis)
    palette = sections.column(palette_kind, sections.count_of(palette_kind))
    indices = sections.column(indices_kind, count)
    try:
        if sections.flags(indices_kind) & SectionFlags.SIGNED_INDICES:
            return [-palette[i >> 1] if i & 1 else palette[i >> 1] for i in indices]
        return [palette[i] for i in indices]
    except IndexError as error:
        raise FormatError("A position palette index is out of range") from error


def read_positions(
    sections: SectionReader, vertex_count: int, unique_count: int, exponent: int
) -> list:
    """Return flat XYZ values, one triple per vertex."""
    if not vertex_count:
        return []
    axes = [
        _read_axis(sections, axis, unique_count, exponent) for axis in range(AXIS_COUNT)
    ]
    rows = list(zip(*axes, strict=True))
    if not sections.has(SectionKind.POSITION_REPEAT_MASK):
        if unique_count != vertex_count:
            raise FormatError("The unique position count does not match the mesh")
        return [value for row in rows for value in row]
    marks = read_rank_mask(sections.raw(SectionKind.POSITION_REPEAT_MASK), vertex_count)
    distances = sections.column(
        SectionKind.POSITION_BACK_DISTANCES, vertex_count - unique_count
    )
    ids = resolve_back_references(marks, distances)
    if ids and max(ids) >= unique_count:
        raise FormatError("A repeated position ID is out of range")
    return [value for unique in ids for value in rows[unique]]
