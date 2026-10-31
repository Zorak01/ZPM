# SPDX-License-Identifier: GPL-3.0-or-later

"""Per-corner surface data: normals, tangents and UV layers; and marks and creases."""

from array import array

from ..container.layout import AXIS_COUNT, MAX_UV_LAYERS, SectionKind, to_signed
from ..container.sections import Section, SectionReader, column_section
from ..errors import FormatError
from ..mesh import Mesh
from ..storage.corner_stream import (
    encode_corner_pairs,
    has_stream,
    read_corner_pairs,
)
from ..storage.names import encode_names, read_names
from .quantize import (
    decode_normal,
    decode_tangent,
    decode_unit,
    decode_uv,
    encode_normal,
    encode_tangent,
    encode_unit,
    encode_uv,
)

_TANGENT_WIDTH = 4
_UV_WIDTH = 2
_FLAG_KINDS = (
    ("sharp_edges", SectionKind.EDGE_SHARP),
    ("seam_edges", SectionKind.EDGE_SEAM),
    ("flat_faces", SectionKind.FACE_FLAT),
)
_CREASE_KINDS = (
    ("edge_creases", SectionKind.EDGE_CREASES),
    ("vertex_creases", SectionKind.VERTEX_CREASES),
)


def _groups(values: array, width: int):
    for at in range(0, len(values), width):
        yield values[at : at + width]


def encode_normals(mesh: Mesh) -> list:
    pairs = [
        encode_normal(*group) for group in _groups(mesh.corner_normals, AXIS_COUNT)
    ]
    return encode_corner_pairs(
        pairs, list(mesh.corners), mesh.vertex_count, SectionKind.NORMAL_REPEAT_MASK
    )


def encode_tangents(mesh: Mesh) -> list:
    if not mesh.corner_tangents:
        return []
    pairs = [
        encode_tangent(*group)
        for group in _groups(mesh.corner_tangents, _TANGENT_WIDTH)
    ]
    return encode_corner_pairs(
        pairs, list(mesh.corners), mesh.vertex_count, SectionKind.TANGENT_REPEAT_MASK
    )


def encode_uv_layers(mesh: Mesh) -> list:
    names = sorted(mesh.uv_layers)
    if len(names) > MAX_UV_LAYERS:
        raise FormatError("The mesh has too many UV layers")
    if not names:
        return []
    sections = [Section(SectionKind.UV_NAMES, encode_names(names))]
    corners = list(mesh.corners)
    for layer, name in enumerate(names):
        pairs = [
            (encode_uv(u), encode_uv(v))
            for u, v in _groups(mesh.uv_layers[name], _UV_WIDTH)
        ]
        sections += encode_corner_pairs(
            pairs, corners, mesh.vertex_count, SectionKind.UV_REPEAT_MASK, layer
        )
    return sections


def encode_marks(mesh: Mesh) -> list:
    """Return sharp, seam and flat marks and creases, each only if any value is set."""
    sections = []
    for name, kind in _FLAG_KINDS:
        flags = getattr(mesh, name)
        if any(flags):
            sections.append(column_section(kind, [1 if flag else 0 for flag in flags]))
    for name, kind in _CREASE_KINDS:
        stored = [encode_unit(value) for value in getattr(mesh, name)]
        if any(stored):
            sections.append(column_section(kind, stored))
    return sections


def read_normals(sections: SectionReader, corners: list) -> array:
    if not has_stream(sections, SectionKind.NORMAL_REPEAT_MASK):
        return array("f")
    pairs = read_corner_pairs(sections, SectionKind.NORMAL_REPEAT_MASK, corners)
    return array("f", [part for a, b in pairs for part in decode_normal(a, b)])


def read_tangents(sections: SectionReader, corners: list) -> array:
    if not has_stream(sections, SectionKind.TANGENT_REPEAT_MASK):
        return array("f")
    pairs = read_corner_pairs(sections, SectionKind.TANGENT_REPEAT_MASK, corners)
    return array("f", [part for a, b in pairs for part in decode_tangent(a, b)])


def read_uv_layers(sections: SectionReader, corners: list) -> dict:
    if not sections.has(SectionKind.UV_NAMES):
        return {}
    layers = {}
    for layer, name in enumerate(read_names(sections.raw(SectionKind.UV_NAMES))):
        pairs = read_corner_pairs(sections, SectionKind.UV_REPEAT_MASK, corners, layer)
        layers[name] = array(
            "f", [decode_uv(to_signed(part)) for pair in pairs for part in pair]
        )
    return layers


def read_marks(
    sections: SectionReader, edge_count: int, face_count: int, vertex_count: int
) -> dict:
    """Return Mesh fields for marks and creases; absent ones come back empty."""
    owners = {
        "sharp_edges": edge_count,
        "seam_edges": edge_count,
        "flat_faces": face_count,
        "edge_creases": edge_count,
        "vertex_creases": vertex_count,
    }
    fields = {}
    for name, kind in _FLAG_KINDS:
        values = sections.column(kind, owners[name]) if sections.has(kind) else []
        if values and max(values) > 1:
            raise FormatError(f"Section {kind.name} holds a value other than 0 or 1")
        fields[name] = array("B", values)
    for name, kind in _CREASE_KINDS:
        values = sections.column(kind, owners[name]) if sections.has(kind) else []
        fields[name] = array("f", [decode_unit(value) for value in values])
    return fields
