# SPDX-License-Identifier: GPL-3.0-or-later

"""Vertex groups and weights: any number of weights per vertex, none padded."""

from array import array

from ..container.layout import SectionKind
from ..container.sections import Section, SectionReader, column_section
from ..mesh import Mesh
from ..storage.names import encode_names, read_names
from .quantize import decode_unit, encode_unit


def encode_weights(mesh: Mesh) -> list:
    """Return group names, and per-vertex weights when any vertex has them."""
    sections = []
    if mesh.vertex_groups:
        names = encode_names(mesh.vertex_groups)
        sections.append(Section(SectionKind.GROUP_NAMES, names))
    if mesh.weight_values:
        sections += [
            column_section(SectionKind.WEIGHT_OFFSETS, list(mesh.weight_offsets)),
            column_section(SectionKind.WEIGHT_GROUPS, list(mesh.weight_groups)),
            column_section(
                SectionKind.WEIGHT_VALUES,
                [encode_unit(value) for value in mesh.weight_values],
            ),
        ]
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
    if not sections.has(SectionKind.WEIGHT_OFFSETS):
        return fields
    offsets = sections.column(SectionKind.WEIGHT_OFFSETS, vertex_count + 1)
    total = offsets[-1]
    fields["weight_offsets"] = array("I", offsets)
    fields["weight_groups"] = array(
        "I", sections.column(SectionKind.WEIGHT_GROUPS, total)
    )
    fields["weight_values"] = array(
        "f",
        [decode_unit(v) for v in sections.column(SectionKind.WEIGHT_VALUES, total)],
    )
    return fields
