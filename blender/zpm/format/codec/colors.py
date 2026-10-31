# SPDX-License-Identifier: GPL-3.0-or-later

"""Vertex colors: named RGBA layers, per vertex or per corner, RGBA8 or RGBA32F.

RGBA8 fits one stream, as (red | green << 8, blue | alpha << 8). Float channels each
need a whole value, so they take two streams: red and green, then blue and alpha.
"""

from array import array

from ..container.layout import (
    MAX_UV_LAYERS,
    ColorFormat,
    SectionKind,
    to_signed,
)
from ..container.sections import Section, SectionReader
from ..errors import FormatError
from ..mesh import ColorLayer, Mesh
from ..storage.corner_stream import (
    encode_corner_pairs,
    encode_vertex_pairs,
    read_corner_pairs,
    read_vertex_pairs,
)
from ..storage.names import encode_names, read_names
from .quantize import decode_color_channel, encode_color_channel

_BYTE_BITS = 8
_BYTE_MASK = 255
_CHANNELS = 4


def _encode_stream(pairs, layer_data, mesh, first_kind, layer) -> list:
    if layer_data.is_corner:
        return encode_corner_pairs(
            pairs, list(mesh.corners), mesh.vertex_count, first_kind, layer
        )
    return encode_vertex_pairs(pairs, first_kind, layer)


def _encode_layer(layer_data: ColorLayer, mesh: Mesh, layer: int) -> list:
    values = layer_data.values
    groups = [values[at : at + _CHANNELS] for at in range(0, len(values), _CHANNELS)]
    if not layer_data.is_float:
        pairs = [(r | g << _BYTE_BITS, b | a << _BYTE_BITS) for r, g, b, a in groups]
        return _encode_stream(
            pairs, layer_data, mesh, SectionKind.COLOR_RG_REPEAT_MASK, layer
        )
    stored = [[encode_color_channel(value) for value in group] for group in groups]
    return [
        *_encode_stream(
            [(r, g) for r, g, _, _ in stored],
            layer_data,
            mesh,
            SectionKind.COLOR_RG_REPEAT_MASK,
            layer,
        ),
        *_encode_stream(
            [(b, a) for _, _, b, a in stored],
            layer_data,
            mesh,
            SectionKind.COLOR_BA_REPEAT_MASK,
            layer,
        ),
    ]


def encode_colors(mesh: Mesh) -> list:
    """Return every color layer with the name and format tables describing them."""
    names = sorted(mesh.color_layers)
    if len(names) > MAX_UV_LAYERS:
        raise FormatError("The mesh has too many color layers")
    if not names:
        return []
    formats = array("I")
    sections = [Section(SectionKind.COLOR_NAMES, encode_names(names))]
    for layer, name in enumerate(names):
        layer_data = mesh.color_layers[name]
        stored = ColorFormat.NONE
        if layer_data.is_corner:
            stored |= ColorFormat.CORNER
        if layer_data.is_float:
            stored |= ColorFormat.FLOAT
        formats.append(stored)
        sections += _encode_layer(layer_data, mesh, layer)
    sections.append(Section(SectionKind.COLOR_FORMATS, formats.tobytes()))
    return sections


def _read_stream(sections, first_kind, stored_format, corners, layer) -> list:
    if stored_format & ColorFormat.CORNER:
        return read_corner_pairs(sections, first_kind, corners, layer)
    return read_vertex_pairs(sections, first_kind, layer)


def _read_layer(sections, stored_format, corners, layer) -> ColorLayer:
    is_corner = bool(stored_format & ColorFormat.CORNER)
    first = _read_stream(
        sections, SectionKind.COLOR_RG_REPEAT_MASK, stored_format, corners, layer
    )
    if not stored_format & ColorFormat.FLOAT:
        channels = []
        for a, b in first:
            if a >> (_BYTE_BITS * 2) or b >> (_BYTE_BITS * 2):
                raise FormatError("An 8-bit color channel is out of range")
            channels += (
                a & _BYTE_MASK,
                a >> _BYTE_BITS,
                b & _BYTE_MASK,
                b >> _BYTE_BITS,
            )
        return ColorLayer(array("B", channels), is_corner)
    second = _read_stream(
        sections, SectionKind.COLOR_BA_REPEAT_MASK, stored_format, corners, layer
    )
    return ColorLayer(
        array(
            "f",
            [
                decode_color_channel(to_signed(value))
                for rg, ba in zip(first, second, strict=True)
                for value in (*rg, *ba)
            ],
        ),
        is_corner,
    )


def read_colors(sections: SectionReader, corners: list) -> dict:
    """Return layer name to ColorLayer."""
    if not sections.has(SectionKind.COLOR_NAMES):
        return {}
    names = read_names(sections.raw(SectionKind.COLOR_NAMES))
    formats = array("I")
    formats.frombytes(sections.raw(SectionKind.COLOR_FORMATS))
    if len(formats) != len(names):
        raise FormatError("Color layer formats do not match their names")
    return {
        name: _read_layer(sections, ColorFormat(formats[layer]), corners, layer)
        for layer, name in enumerate(names)
    }
