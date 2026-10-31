# SPDX-License-Identifier: GPL-3.0-or-later

"""Curves and lattices: exact control points in columns, readable by index."""

from ..container.layout import (
    AXIS_COUNT,
    CURVE_INFO,
    LATTICE_INFO,
    NO_INDEX,
    SPLINE_INFO,
    SectionKind,
    SplineFlags,
    SplineType,
)
from ..container.sections import Section, SectionReader, column_section
from ..errors import FormatError
from ..scene import Curve, Lattice, Scene, Spline
from .scene_tables import (
    checked_enum,
    float_column,
    names_section,
    read_float_column,
    read_name_list,
    read_records,
)

_POSITION_KINDS = (
    SectionKind.CURVE_POINT_X,
    SectionKind.CURVE_POINT_Y,
    SectionKind.CURVE_POINT_Z,
)
_LEFT_KINDS = (
    SectionKind.CURVE_HANDLE_LEFT_X,
    SectionKind.CURVE_HANDLE_LEFT_Y,
    SectionKind.CURVE_HANDLE_LEFT_Z,
)
_RIGHT_KINDS = (
    SectionKind.CURVE_HANDLE_RIGHT_X,
    SectionKind.CURVE_HANDLE_RIGHT_Y,
    SectionKind.CURVE_HANDLE_RIGHT_Z,
)
_REST_KINDS = (
    SectionKind.LATTICE_REST_X,
    SectionKind.LATTICE_REST_Y,
    SectionKind.LATTICE_REST_Z,
)
_DEFORM_KINDS = (
    SectionKind.LATTICE_DEFORM_X,
    SectionKind.LATTICE_DEFORM_Y,
    SectionKind.LATTICE_DEFORM_Z,
)


def _axis_columns(kinds: tuple, values: list) -> list:
    return [
        float_column(kind, values[axis::AXIS_COUNT]) for axis, kind in enumerate(kinds)
    ]


def _read_axes(sections: SectionReader, kinds: tuple, count: int) -> list:
    axes = [read_float_column(sections, kind, count) for kind in kinds]
    return [value for row in zip(*axes, strict=True) for value in row]


def _handles_of(spline: Spline, name: str) -> list:
    """Return handles for the shared columns: zero for splines that have none."""
    return getattr(spline, name) or [0.0] * (spline.point_count * AXIS_COUNT)


def encode_curves(curves: list) -> list:
    if not curves:
        return []
    infos = bytearray()
    splines = bytearray()
    first_spline = first_point = first_knot = 0
    for curve in curves:
        infos += CURVE_INFO.pack(first_spline, len(curve.splines))
        first_spline += len(curve.splines)
        for spline in curve.splines:
            splines += SPLINE_INFO.pack(
                spline.spline_type,
                spline.flags,
                spline.order,
                spline.resolution,
                first_point,
                spline.point_count,
                first_knot if spline.knots else NO_INDEX,
                len(spline.knots),
            )
            first_point += spline.point_count
            first_knot += len(spline.knots)
    every = [spline for curve in curves for spline in curve.splines]
    sections = [
        *names_section(SectionKind.CURVE_NAMES, [c.name for c in curves]),
        Section(SectionKind.CURVE_INFO, bytes(infos)),
    ]
    if not every:
        return sections

    def joined(name: str) -> list:
        return [value for spline in every for value in getattr(spline, name)]

    sections += [
        Section(SectionKind.SPLINE_INFO, bytes(splines)),
        *_axis_columns(_POSITION_KINDS, joined("positions")),
        float_column(SectionKind.CURVE_POINT_W, joined("weights")),
        float_column(SectionKind.CURVE_POINT_RADIUS, joined("radii")),
        float_column(SectionKind.CURVE_POINT_TILT, joined("tilts")),
    ]
    if any(spline.left_handles for spline in every):
        left = [v for s in every for v in _handles_of(s, "left_handles")]
        right = [v for s in every for v in _handles_of(s, "right_handles")]
        types = [
            value
            for spline in every
            for value in (spline.handle_types or [0] * (spline.point_count * 2))
        ]
        sections += [
            *_axis_columns(_LEFT_KINDS, left),
            *_axis_columns(_RIGHT_KINDS, right),
            column_section(SectionKind.CURVE_HANDLE_TYPES, types),
        ]
    knots = joined("knots")
    if knots:
        sections.append(float_column(SectionKind.CURVE_KNOTS, knots))
    return sections


def _read_spline(values: tuple, columns: dict) -> Spline:
    raw_type, flags, order, resolution, first, count, first_knot, knot_count = values
    end = first + count
    if end > columns["count"]:
        raise FormatError("A spline runs past the curve points")
    spline_type = checked_enum(SplineType, raw_type)
    spline = Spline(
        spline_type,
        SplineFlags(flags),
        order,
        resolution,
        columns["positions"][first * AXIS_COUNT : end * AXIS_COUNT],
        columns["weights"][first:end],
        columns["radii"][first:end],
        columns["tilts"][first:end],
    )
    if spline_type == SplineType.BEZIER:
        if not columns["left"]:
            raise FormatError("A Bezier spline has no handles")
        spline.left_handles = columns["left"][first * AXIS_COUNT : end * AXIS_COUNT]
        spline.right_handles = columns["right"][first * AXIS_COUNT : end * AXIS_COUNT]
        spline.handle_types = columns["types"][first * 2 : end * 2]
    if first_knot != NO_INDEX:
        if first_knot + knot_count > len(columns["knots"]):
            raise FormatError("A spline runs past the knots")
        spline.knots = columns["knots"][first_knot : first_knot + knot_count]
    return spline


def read_curves(sections: SectionReader) -> list:
    names = read_name_list(sections, SectionKind.CURVE_NAMES)
    infos = read_records(sections, SectionKind.CURVE_INFO, CURVE_INFO, len(names))
    kind = SectionKind.SPLINE_INFO
    spline_count = (
        len(sections.raw(kind)) // SPLINE_INFO.size if sections.has(kind) else 0
    )
    records = read_records(sections, kind, SPLINE_INFO, spline_count)
    if not records:
        columns = {}
    else:
        count = sections.count_of(SectionKind.CURVE_POINT_X)
        has_handles = sections.has(SectionKind.CURVE_HANDLE_TYPES)
        knots = SectionKind.CURVE_KNOTS
        columns = {
            "count": count,
            "positions": _read_axes(sections, _POSITION_KINDS, count),
            "weights": read_float_column(sections, SectionKind.CURVE_POINT_W, count),
            "radii": read_float_column(sections, SectionKind.CURVE_POINT_RADIUS, count),
            "tilts": read_float_column(sections, SectionKind.CURVE_POINT_TILT, count),
            "left": _read_axes(sections, _LEFT_KINDS, count) if has_handles else [],
            "right": _read_axes(sections, _RIGHT_KINDS, count) if has_handles else [],
            "types": sections.column(SectionKind.CURVE_HANDLE_TYPES, count * 2)
            if has_handles
            else [],
            "knots": read_float_column(sections, knots, sections.count_of(knots))
            if sections.has(knots)
            else [],
        }
    splines = [_read_spline(values, columns) for values in records]
    curves = []
    for name, (first, count) in zip(names, infos, strict=True):
        if first + count > len(splines):
            raise FormatError(f"Curve {name} has missing splines")
        curves.append(Curve(name, splines[first : first + count]))
    return curves


def encode_lattices(lattices: list) -> list:
    if not lattices:
        return []
    infos = bytearray()
    first = 0
    for lattice in lattices:
        count = len(lattice.rest) // AXIS_COUNT
        infos += LATTICE_INFO.pack(
            lattice.points_u, lattice.points_v, lattice.points_w, first, count
        )
        first += count
    rest = [v for lattice in lattices for v in lattice.rest]
    deformed = [v for lattice in lattices for v in lattice.deformed]
    weights = [v for lattice in lattices for v in lattice.softbody_weights]
    return [
        *names_section(SectionKind.LATTICE_NAMES, [lat.name for lat in lattices]),
        Section(SectionKind.LATTICE_INFO, bytes(infos)),
        *_axis_columns(_REST_KINDS, rest),
        *_axis_columns(_DEFORM_KINDS, deformed),
        float_column(SectionKind.LATTICE_WEIGHTS, weights),
    ]


def read_lattices(sections: SectionReader) -> list:
    names = read_name_list(sections, SectionKind.LATTICE_NAMES)
    if not names:
        return []
    infos = read_records(sections, SectionKind.LATTICE_INFO, LATTICE_INFO, len(names))
    total = sections.count_of(SectionKind.LATTICE_REST_X)
    rest = _read_axes(sections, _REST_KINDS, total)
    deformed = _read_axes(sections, _DEFORM_KINDS, total)
    weights = read_float_column(sections, SectionKind.LATTICE_WEIGHTS, total)
    lattices = []
    for name, (u, v, w, first, count) in zip(names, infos, strict=True):
        if first + count > total:
            raise FormatError(f"Lattice {name} runs past its points")
        lattices.append(
            Lattice(
                name,
                u,
                v,
                w,
                rest[first * AXIS_COUNT : (first + count) * AXIS_COUNT],
                deformed[first * AXIS_COUNT : (first + count) * AXIS_COUNT],
                weights[first : first + count],
            )
        )
    return lattices


def encode_curves_and_lattices(scene: Scene) -> list:
    return [*encode_curves(scene.curves), *encode_lattices(scene.lattices)]
