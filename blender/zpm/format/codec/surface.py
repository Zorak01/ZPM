# SPDX-License-Identifier: GPL-3.0-or-later

"""Per-corner surface data: normals, tangents and UV layers; and marks and creases.

Tangents and seams belong to a UV layer, so they use that layer's number.
"""

from array import array

from ..container.layout import AXIS_COUNT, MAX_LAYERS, SectionKind, to_signed
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
    ("sharp_corners", SectionKind.CORNER_SHARP),
)
_CHAIN_KINDS = (
    ("crease_chain_offsets", SectionKind.CREASE_CHAIN_OFFSETS),
    ("crease_chain_vertices", SectionKind.CREASE_CHAIN_VERTICES),
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
    sections = []
    corners = list(mesh.corners)
    for layer, name in enumerate(sorted(mesh.uv_layers)):
        if name not in mesh.tangent_layers:
            continue
        pairs = [
            encode_tangent(*group)
            for group in _groups(mesh.tangent_layers[name], _TANGENT_WIDTH)
        ]
        sections += encode_corner_pairs(
            pairs, corners, mesh.vertex_count, SectionKind.TANGENT_REPEAT_MASK, layer
        )
    return sections


def encode_uv_seams(mesh: Mesh) -> list:
    """Return seams for each UV layer whose seams differ from the mesh's own."""
    return [
        column_section(
            SectionKind.UV_SEAMS,
            [1 if flag else 0 for flag in mesh.uv_seams[name]],
            layer=layer,
        )
        for layer, name in enumerate(sorted(mesh.uv_layers))
        if name in mesh.uv_seams
    ]


def encode_uv_layers(mesh: Mesh) -> list:
    names = sorted(mesh.uv_layers)
    if len(names) > MAX_LAYERS:
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
    if any(mesh.smoothing_groups):
        sections.append(
            column_section(
                SectionKind.CORNER_SMOOTHING_GROUPS, list(mesh.smoothing_groups)
            )
        )
    if mesh.crease_chain_offsets:
        sections += [
            column_section(kind, list(getattr(mesh, name)))
            for name, kind in _CHAIN_KINDS
        ]
    return sections


def read_normals(sections: SectionReader, corners: list) -> array:
    if not has_stream(sections, SectionKind.NORMAL_REPEAT_MASK):
        return array("f")
    pairs = read_corner_pairs(sections, SectionKind.NORMAL_REPEAT_MASK, corners)
    return array("f", [part for a, b in pairs for part in decode_normal(a, b)])


def encode_tangent_method(mesh: Mesh) -> list:
    """Return the name of what made the tangents, when the mesh names one."""
    if not mesh.tangent_method:
        return []
    names = encode_names([mesh.tangent_method])
    return [Section(SectionKind.TANGENT_METHOD, names)]


def read_tangent_method(sections: SectionReader) -> str:
    if not sections.has(SectionKind.TANGENT_METHOD):
        return ""
    names = read_names(sections.raw(SectionKind.TANGENT_METHOD))
    if len(names) != 1:
        raise FormatError("A file names one tangent method")
    return names[0]


def _uv_names(sections: SectionReader) -> list:
    if not sections.has(SectionKind.UV_NAMES):
        return []
    return read_names(sections.raw(SectionKind.UV_NAMES))


def read_tangents(sections: SectionReader, corners: list) -> dict:
    """Return UV layer name to tangents, for layers that have them."""
    kind = SectionKind.TANGENT_REPEAT_MASK
    return {
        name: array(
            "f",
            [
                part
                for a, b in read_corner_pairs(sections, kind, corners, layer)
                for part in decode_tangent(a, b)
            ],
        )
        for layer, name in enumerate(_uv_names(sections))
        if has_stream(sections, kind, layer)
    }


def read_uv_seams(sections: SectionReader, edge_count: int) -> dict:
    """Return UV layer name to seams, for layers whose seams differ from the mesh's."""
    seams = {}
    for layer, name in enumerate(_uv_names(sections)):
        if sections.has(SectionKind.UV_SEAMS, layer):
            values = sections.column(SectionKind.UV_SEAMS, edge_count, layer)
            if values and max(values) > 1:
                raise FormatError("UV seams hold a value other than 0 or 1")
            seams[name] = array("B", values)
    return seams


def read_uv_layers(sections: SectionReader, corners: list) -> dict:
    layers = {}
    for layer, name in enumerate(_uv_names(sections)):
        pairs = read_corner_pairs(sections, SectionKind.UV_REPEAT_MASK, corners, layer)
        layers[name] = array(
            "f", [decode_uv(to_signed(part)) for pair in pairs for part in pair]
        )
    return layers


def read_marks(
    sections: SectionReader,
    edge_count: int,
    face_count: int,
    vertex_count: int,
    corner_count: int,
) -> dict:
    """Return Mesh fields for marks and creases; absent ones come back empty."""
    owners = {
        "sharp_edges": edge_count,
        "seam_edges": edge_count,
        "flat_faces": face_count,
        "sharp_corners": corner_count,
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
    kind = SectionKind.CORNER_SMOOTHING_GROUPS
    groups = sections.column(kind, corner_count) if sections.has(kind) else []
    fields["smoothing_groups"] = array("I", groups)
    for name, kind in _CHAIN_KINDS:
        count = sections.count_of(kind) if sections.has(kind) else 0
        fields[name] = array("I", sections.column(kind, count) if count else [])
    return fields
