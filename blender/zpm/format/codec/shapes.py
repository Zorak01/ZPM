# SPDX-License-Identifier: GPL-3.0-or-later

"""Blendshapes: only the vertices each target moves, on the mesh's own position grid.

All targets share one list of rising vertex IDs with one start per target, so a face
rig of many small targets pays almost nothing per target, and a target moving every
vertex costs no IDs: rising runs fit a slope. Offsets are whole grid steps from the
base shape, so adding one back is exact: no target drifts, and absolute and relative
targets store the same way. A mesh with no grid stores each target's ordered bits.
"""

import math
from array import array

from ..container.layout import (
    AXIS_COUNT,
    NO_GRID,
    SHAPE_INFO,
    SHAPE_INTERPOLATION_SHIFT,
    SectionKind,
    ShapeFlags,
    ShapeInterpolation,
    bits_to_ordered,
    ordered_to_bits,
    to_signed,
)
from ..container.sections import Section, SectionReader, column_section
from ..errors import FormatError
from ..mesh import Mesh, ShapeTarget
from ..storage.names import encode_names, read_names
from .quantize import decode_offset, encode_offset

_POSITION_BYTES = 4 * AXIS_COUNT
_FLAG_BITS = (1 << SHAPE_INTERPOLATION_SHIFT) - 1
_OFFSET_CHANNELS = (
    ("normal_offsets", SectionKind.SHAPE_NORMAL_X),
    ("tangent_offsets", SectionKind.SHAPE_TANGENT_X),
)


def _affected_vertices(target: ShapeTarget, base: bytes) -> list:
    moved = target.positions.tobytes()
    size = _POSITION_BYTES
    offsets = (target.normal_offsets, target.tangent_offsets)
    return [
        vertex
        for vertex in range(len(base) // size)
        if moved[vertex * size : (vertex + 1) * size]
        != base[vertex * size : (vertex + 1) * size]
        or any(
            any(buffer[vertex * AXIS_COUNT : (vertex + 1) * AXIS_COUNT])
            for buffer in offsets
            if buffer
        )
    ]


def _stored_position_axis(mesh, target, vertices, axis, exponent) -> list:
    moved = target.positions
    if exponent != NO_GRID:
        base = mesh.positions
        return [
            round(math.ldexp(moved[v * AXIS_COUNT + axis], -exponent))
            - round(math.ldexp(base[v * AXIS_COUNT + axis], -exponent))
            for v in vertices
        ]
    bits = array("I")
    bits.frombytes(moved.tobytes())
    return [bits_to_ordered(bits[v * AXIS_COUNT + axis]) for v in vertices]


def _pack_info(target: ShapeTarget) -> bytes:
    flags = ShapeFlags.NONE
    if target.is_relative:
        flags |= ShapeFlags.RELATIVE
    if target.is_muted:
        flags |= ShapeFlags.MUTED
    return SHAPE_INFO.pack(
        target.reference,
        target.in_between_of,
        target.value,
        target.minimum,
        target.maximum,
        target.frame,
        target.in_between_weight,
        flags | target.interpolation << SHAPE_INTERPOLATION_SHIFT,
    )


def _offset_sections(mesh, name, first_kind, affected) -> list:
    if not any(getattr(target, name) for target in mesh.shape_targets):
        return []
    return [
        column_section(
            SectionKind(first_kind + axis),
            [
                encode_offset(getattr(target, name)[vertex * AXIS_COUNT + axis])
                for target, vertices in zip(mesh.shape_targets, affected, strict=True)
                for vertex in vertices
            ],
        )
        for axis in range(AXIS_COUNT)
    ]


def _vertex_list_sections(affected: list) -> list:
    """Return vertex lists, each distinct one once when any targets share a list."""
    distinct = {}
    joined = []
    list_starts = []
    for vertices in affected:
        key = tuple(vertices)
        if key not in distinct:
            distinct[key] = len(joined)
            joined += vertices
        list_starts.append(distinct[key])
    if len(joined) == sum(len(vertices) for vertices in affected):
        return [column_section(SectionKind.SHAPE_VERTICES, joined)]
    return [
        column_section(SectionKind.SHAPE_LIST_STARTS, list_starts),
        column_section(SectionKind.SHAPE_VERTICES, joined),
    ]


def encode_shapes(mesh: Mesh, exponent: int) -> list:
    """Return every section describing the mesh's shape targets, if it has any."""
    targets = mesh.shape_targets
    if not targets:
        return []
    base = mesh.positions.tobytes()
    affected = [_affected_vertices(target, base) for target in targets]
    starts = [0]
    for vertices in affected:
        starts.append(starts[-1] + len(vertices))
    sections = [
        Section(SectionKind.SHAPE_NAMES, encode_names([t.name for t in targets])),
        Section(SectionKind.SHAPE_INFO, b"".join(_pack_info(t) for t in targets)),
        column_section(SectionKind.SHAPE_STARTS, starts),
        *_vertex_list_sections(affected),
    ]
    if any(target.vertex_group for target in targets):
        names = encode_names([target.vertex_group for target in targets])
        sections.append(Section(SectionKind.SHAPE_GROUP_NAMES, names))
    for axis in range(AXIS_COUNT):
        stored = [
            value
            for target, vertices in zip(targets, affected, strict=True)
            for value in _stored_position_axis(mesh, target, vertices, axis, exponent)
        ]
        kind = SectionKind(SectionKind.SHAPE_POSITION_X + axis)
        sections.append(column_section(kind, stored))
    for name, first_kind in _OFFSET_CHANNELS:
        sections += _offset_sections(mesh, name, first_kind, affected)
    return sections


def _unpack_target(name, group, raw) -> ShapeTarget:
    reference, in_between_of, value, low, high, frame, weight, flags = raw
    try:
        interpolation = ShapeInterpolation(flags >> SHAPE_INTERPOLATION_SHIFT)
    except ValueError as error:
        raise FormatError("A shape target has an unknown interpolation") from error
    flags = ShapeFlags(flags & _FLAG_BITS)
    return ShapeTarget(
        name,
        array("f"),
        reference=reference,
        is_relative=bool(flags & ShapeFlags.RELATIVE),
        value=value,
        minimum=low,
        maximum=high,
        frame=frame,
        vertex_group=group,
        is_muted=bool(flags & ShapeFlags.MUTED),
        interpolation=interpolation,
        in_between_of=in_between_of,
        in_between_weight=weight,
    )


def _read_ids(sections: SectionReader, count: int) -> tuple:
    """Return (starts, each target's vertex list), proving each list rises."""
    starts = sections.column(SectionKind.SHAPE_STARTS, count + 1)
    if starts[0] != 0 or any(a > b for a, b in zip(starts, starts[1:], strict=False)):
        raise FormatError("Shape target starts must rise")
    kind = SectionKind.SHAPE_VERTICES
    if sections.has(SectionKind.SHAPE_LIST_STARTS):
        vertices = sections.column(kind, sections.count_of(kind))
        firsts = sections.column(SectionKind.SHAPE_LIST_STARTS, count)
    else:
        vertices = sections.column(kind, starts[-1])
        firsts = starts[:-1]
    runs = []
    for index, first in enumerate(firsts):
        length = starts[index + 1] - starts[index]
        run = vertices[first : first + length]
        if len(run) != length or any(
            a >= b for a, b in zip(run, run[1:], strict=False)
        ):
            raise FormatError("A shape target's vertices must rise")
        runs.append(run)
    return starts, runs


def _apply_positions(moved, base, vertices, stored, axis, exponent) -> None:
    for at, vertex in enumerate(vertices):
        slot = vertex * AXIS_COUNT + axis
        if exponent != NO_GRID:
            grid = round(math.ldexp(base[slot], -exponent)) + to_signed(stored[at])
            moved[slot] = math.ldexp(grid, exponent)
        else:
            bits = array("I", [ordered_to_bits(stored[at])])
            moved[slot : slot + 1] = array("f", bits.tobytes())


def _read_offsets(sections, first_kind, total) -> list | None:
    if not sections.has(first_kind):
        return None
    return [
        sections.column(SectionKind(first_kind + axis), total)
        for axis in range(AXIS_COUNT)
    ]


def _fill_offsets(columns, start, run, length) -> array:
    if columns is None:
        return array("f")
    offsets = array("f", [0.0]) * length
    for axis, column in enumerate(columns):
        for at, vertex in enumerate(run):
            slot = vertex * AXIS_COUNT + axis
            offsets[slot] = decode_offset(to_signed(column[start + at]))
    return offsets


def _read_targets(sections: SectionReader) -> list:
    names = read_names(sections.raw(SectionKind.SHAPE_NAMES))
    groups = [""] * len(names)
    if sections.has(SectionKind.SHAPE_GROUP_NAMES):
        groups = read_names(sections.raw(SectionKind.SHAPE_GROUP_NAMES))
    info = sections.raw(SectionKind.SHAPE_INFO)
    if len(groups) != len(names) or len(info) != len(names) * SHAPE_INFO.size:
        raise FormatError("Shape target tables do not match their names")
    return [
        _unpack_target(name, group, raw)
        for name, group, raw in zip(
            names, groups, SHAPE_INFO.iter_unpack(info), strict=True
        )
    ]


def read_shapes(sections: SectionReader, positions: array, exponent: int) -> list:
    """Return ShapeTargets holding whole positions, for editing tools."""
    if not sections.has(SectionKind.SHAPE_NAMES):
        return []
    targets = _read_targets(sections)
    starts, runs = _read_ids(sections, len(targets))
    if any(run and run[-1] * AXIS_COUNT >= len(positions) for run in runs):
        raise FormatError("A shape target references a missing vertex")
    axes = [
        sections.column(SectionKind(SectionKind.SHAPE_POSITION_X + axis), starts[-1])
        for axis in range(AXIS_COUNT)
    ]
    normals = _read_offsets(sections, SectionKind.SHAPE_NORMAL_X, starts[-1])
    tangents = _read_offsets(sections, SectionKind.SHAPE_TANGENT_X, starts[-1])
    for index, target in enumerate(targets):
        start, end = starts[index], starts[index + 1]
        target.positions = array("f", positions)
        for axis, stored in enumerate(axes):
            _apply_positions(
                target.positions,
                positions,
                runs[index],
                stored[start:end],
                axis,
                exponent,
            )
        target.normal_offsets = _fill_offsets(
            normals, start, runs[index], len(positions)
        )
        target.tangent_offsets = _fill_offsets(
            tangents, start, runs[index], len(positions)
        )
    return targets
