# SPDX-License-Identifier: GPL-3.0-or-later

"""Vertex groups and weights: any number of weights per vertex, none padded.

Vertices with exactly the same weights can share one row, found through one more
column, whenever that is smaller. A row is one lookup further than a vertex.

Weights adding up to exactly 1 on the 12-bit grid store no last value in each row, since
the others say what it is. A row's values then start at its group offset minus its row
number. WEIGHT_SUM on the values column says so.

A packed mesh may instead hold such weights in PACKED_WEIGHTS: see packed/weights.py.
"""

from array import array

from ..container.layout import WEIGHT_TOTAL, SectionFlags, SectionKind
from ..container.sections import Section, SectionReader, column_section, stored_bytes
from ..errors import FormatError
from ..mesh import Mesh
from ..storage.names import encode_names, read_names
from .packed.weights import WeightRows
from .packed.weights import encode_weights as encode_weight_stream
from .packed.weights import read_weights as read_weight_stream
from .quantize import decode_unit, encode_unit

_ON_GRID_TOLERANCE = 1e-6


def _row_columns(offsets: list, groups: list, values: list, *, sums: bool) -> list:
    extra = SectionFlags.WEIGHT_SUM if sums else SectionFlags.NONE
    if sums:
        values = [
            value
            for start, end in zip(offsets, offsets[1:], strict=False)
            for value in values[start : end - 1]
        ]
    return [
        column_section(SectionKind.WEIGHT_OFFSETS, offsets),
        column_section(SectionKind.WEIGHT_GROUPS, groups),
        column_section(SectionKind.WEIGHT_VALUES, values, extra),
    ]


def sum_steps(mesh: Mesh) -> list | None:
    """Return weights as whole steps, if every row adds up to exactly 1."""
    offsets = mesh.weight_offsets
    steps = [round(value * WEIGHT_TOTAL) for value in mesh.weight_values]
    for step, value in zip(steps, mesh.weight_values, strict=True):
        if abs(step / WEIGHT_TOTAL - value) > _ON_GRID_TOLERANCE:
            return None
    for start, end in zip(offsets, offsets[1:], strict=False):
        if end == start or sum(steps[start:end]) != WEIGHT_TOTAL:
            return None
    return steps


def _shared_rows(mesh: Mesh, stored: list, *, sums: bool) -> list:
    """Return the columns with each distinct set of weights stored once."""
    rows = {}
    row_of_vertex = []
    offsets, groups, values = [0], [], []
    for vertex in range(mesh.vertex_count):
        start, end = mesh.weight_offsets[vertex], mesh.weight_offsets[vertex + 1]
        key = (tuple(mesh.weight_groups[start:end]), tuple(stored[start:end]))
        if key not in rows:
            rows[key] = len(rows)
            groups += key[0]
            values += key[1]
            offsets.append(len(values))
        row_of_vertex.append(rows[key])
    return [
        column_section(SectionKind.WEIGHT_SET_OF_VERTEX, row_of_vertex),
        *_row_columns(offsets, groups, values, sums=sums),
    ]


def encode_weights(mesh: Mesh, *, packed: bool = False) -> list:
    """Return group names, and per-vertex weights when any vertex has them.

    packed also tries one stream, for rows adding up to exactly 1, and keeps whichever
    form is smallest; it is read vertex by vertex, not by index.
    """
    sections = []
    if mesh.vertex_groups:
        names = encode_names(mesh.vertex_groups)
        sections.append(Section(SectionKind.GROUP_NAMES, names))
    if mesh.weight_values:
        steps = sum_steps(mesh)
        sums = steps is not None
        stored = steps if sums else [encode_unit(v) for v in mesh.weight_values]
        plain = _row_columns(
            list(mesh.weight_offsets), list(mesh.weight_groups), stored, sums=sums
        )
        shared = _shared_rows(mesh, stored, sums=sums)
        forms = [plain, shared]
        if packed and sums:
            rows = WeightRows(
                list(mesh.weight_offsets), list(mesh.weight_groups), steps
            )
            data = encode_weight_stream(rows)
            forms.append([Section(SectionKind.PACKED_WEIGHTS, data)])
        sections += min(forms, key=stored_bytes)
    return sections


def read_weights(sections: SectionReader, vertex_count: int) -> dict:
    """Return Mesh fields for groups and weights; absent ones come back empty."""
    fields = {
        "vertex_groups": [],
        "weight_offsets": array("I"),
        "weight_groups": array("I"),
        "weight_values": array("f"),
    }
    if sections.has(SectionKind.GROUP_NAMES):
        fields["vertex_groups"] = read_names(sections.raw(SectionKind.GROUP_NAMES))
    if sections.has(SectionKind.PACKED_WEIGHTS):
        data = sections.raw(SectionKind.PACKED_WEIGHTS)
        rows = read_weight_stream(data, vertex_count, len(fields["vertex_groups"]))
        fields["weight_offsets"] = array("I", rows.offsets)
        fields["weight_groups"] = array("I", rows.groups)
        fields["weight_values"] = array("f", [s / WEIGHT_TOTAL for s in rows.steps])
        return fields
    if not sections.has(SectionKind.WEIGHT_OFFSETS):
        return fields
    has_rows = sections.has(SectionKind.WEIGHT_SET_OF_VERTEX)
    count = (
        sections.count_of(SectionKind.WEIGHT_OFFSETS) if has_rows else vertex_count + 1
    )
    offsets = sections.column(SectionKind.WEIGHT_OFFSETS, count)
    total = offsets[-1] if offsets else 0
    if any(a > b for a, b in zip(offsets, offsets[1:], strict=False)):
        raise FormatError("Weight offsets must rise")
    groups = sections.column(SectionKind.WEIGHT_GROUPS, total)
    if sections.flags(SectionKind.WEIGHT_VALUES) & SectionFlags.WEIGHT_SUM:
        values = _read_sum_values(sections, offsets, total)
    else:
        values = [
            decode_unit(v) for v in sections.column(SectionKind.WEIGHT_VALUES, total)
        ]
    if has_rows:
        rows = sections.column(SectionKind.WEIGHT_SET_OF_VERTEX, vertex_count)
        if rows and max(rows) + 1 >= count:
            raise FormatError("A vertex uses a missing weight row")
        offsets, groups, values = _expand_rows(rows, offsets, groups, values)
    fields["weight_offsets"] = array("I", offsets)
    fields["weight_groups"] = array("I", groups)
    fields["weight_values"] = array("f", values)
    return fields


def _read_sum_values(sections: SectionReader, offsets: list, total: int) -> list:
    rows = len(offsets) - 1
    stored = iter(sections.column(SectionKind.WEIGHT_VALUES, total - rows))
    values = []
    for start, end in zip(offsets, offsets[1:], strict=False):
        if end == start:
            raise FormatError("A row with a weight sum has no weights")
        row = [next(stored) for _ in range(end - start - 1)]
        if sum(row) > WEIGHT_TOTAL:
            raise FormatError("A row's weights add up to more than 1")
        row.append(WEIGHT_TOTAL - sum(row))
        values += [step / WEIGHT_TOTAL for step in row]
    return values


def _expand_rows(rows: list, offsets: list, groups: list, values: list) -> tuple:
    expanded_offsets, expanded_groups, expanded_values = [0], [], []
    for row in rows:
        start, end = offsets[row], offsets[row + 1]
        expanded_groups += groups[start:end]
        expanded_values += values[start:end]
        expanded_offsets.append(len(expanded_values))
    return expanded_offsets, expanded_groups, expanded_values
