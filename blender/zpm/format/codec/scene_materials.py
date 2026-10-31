# SPDX-License-Identifier: GPL-3.0-or-later

"""Materials, texture slots, samplers and images.

An image's encoded bytes are kept as they are, stored once however many tiles or
images share them, and readable straight from the file.
"""

from ..container.layout import (
    IMAGE_INFO,
    IMAGE_TILE,
    MATERIAL_INFO,
    NO_INDEX,
    SAMPLER,
    TEXTURE_SLOT,
    AlphaMode,
    SamplerFilter,
    SamplerWrap,
    SectionKind,
)
from ..container.sections import Section, SectionReader
from ..errors import FormatError
from ..scene import Image, ImageTile, Material, Sampler, Scene, TextureSlot
from .scene_tables import (
    byte_offsets_section,
    checked_enum,
    names_section,
    read_byte_offsets,
    read_name_list,
    read_records,
)

_DOUBLE_SIDED = 1
_HAS_ALPHA = 1


def _encode_material_tables(materials: list) -> list:
    if not materials:
        return []
    infos = bytearray()
    slots = bytearray()
    uv_names = []
    roles = []
    for material in materials:
        first = len(uv_names)
        for slot in material.slots:
            uv_names.append(slot.uv_layer)
            roles.append(slot.role)
            slots += TEXTURE_SLOT.pack(
                slot.image,
                slot.sampler,
                *slot.offset,
                slot.rotation,
                *slot.scale,
            )
        infos += MATERIAL_INFO.pack(
            *material.base_color,
            material.metallic,
            material.roughness,
            *material.emission,
            material.emission_strength,
            material.normal_scale,
            material.alpha_cutoff,
            material.alpha_mode,
            _DOUBLE_SIDED if material.is_double_sided else 0,
            first,
            len(material.slots),
        )
    sections = [
        *names_section(SectionKind.MATERIAL_NAMES, [m.name for m in materials]),
        Section(SectionKind.MATERIAL_INFO, bytes(infos)),
    ]
    if slots:
        sections.append(Section(SectionKind.TEXTURE_SLOTS, bytes(slots)))
        sections += names_section(SectionKind.TEXTURE_UV_NAMES, uv_names)
        sections += names_section(SectionKind.TEXTURE_ROLES, roles)
    return sections


def _encode_images(images: list) -> list:
    if not images:
        return []
    infos = bytearray()
    tiles = bytearray()
    paths = []
    payloads = {}
    first_tile = 0
    for image in images:
        for tile in image.tiles:
            payload = NO_INDEX
            if tile.payload:
                payload = payloads.setdefault(tile.payload, len(payloads))
            tiles += IMAGE_TILE.pack(tile.number, payload)
            paths.append(tile.path)
        infos += IMAGE_INFO.pack(
            image.width,
            image.height,
            image.channels,
            _HAS_ALPHA if image.has_alpha else 0,
            first_tile,
            len(image.tiles),
        )
        first_tile += len(image.tiles)
    sections = [
        *names_section(SectionKind.IMAGE_NAMES, [i.name for i in images]),
        *names_section(SectionKind.IMAGE_COLOR_SPACES, [i.color_space for i in images]),
        Section(SectionKind.IMAGE_INFO, bytes(infos)),
    ]
    if tiles:
        sections.append(Section(SectionKind.IMAGE_TILES, bytes(tiles)))
        sections += names_section(SectionKind.IMAGE_PATHS, paths)
    if payloads:
        blobs = list(payloads)
        sections += byte_offsets_section(SectionKind.IMAGE_PAYLOAD_STARTS, blobs)
        sections.append(Section(SectionKind.IMAGE_PAYLOADS, b"".join(blobs)))
    return sections


def encode_materials_and_images(scene: Scene) -> list:
    samplers = b"".join(
        SAMPLER.pack(sampler.filter, sampler.wrap_u, sampler.wrap_v)
        for sampler in scene.samplers
    )
    return [
        *_encode_material_tables(scene.materials),
        *([Section(SectionKind.SAMPLERS, samplers)] if samplers else []),
        *_encode_images(scene.images),
    ]


def _read_slots(sections: SectionReader) -> list:
    uv_names = read_name_list(sections, SectionKind.TEXTURE_UV_NAMES)
    records = read_records(
        sections, SectionKind.TEXTURE_SLOTS, TEXTURE_SLOT, len(uv_names)
    )
    roles = read_name_list(sections, SectionKind.TEXTURE_ROLES)
    if len(roles) != len(uv_names):
        raise FormatError("Texture roles do not match their slots")
    return [
        TextureSlot(
            role,
            image,
            sampler,
            uv_name,
            (ox, oy),
            rotation,
            (sx, sy),
        )
        for role, uv_name, (image, sampler, ox, oy, rotation, sx, sy) in zip(
            roles, uv_names, records, strict=True
        )
    ]


def read_materials_table(sections: SectionReader) -> list:
    names = read_name_list(sections, SectionKind.MATERIAL_NAMES)
    records = read_records(
        sections, SectionKind.MATERIAL_INFO, MATERIAL_INFO, len(names)
    )
    slots = _read_slots(sections)
    materials = []
    for name, values in zip(names, records, strict=True):
        alpha_mode, flags, first, count = values[12:]
        if first + count > len(slots):
            raise FormatError(f"Material {name} has missing texture slots")
        materials.append(
            Material(
                name,
                values[:4],
                values[4],
                values[5],
                values[6:9],
                values[9],
                values[10],
                values[11],
                checked_enum(AlphaMode, alpha_mode),
                bool(flags & _DOUBLE_SIDED),
                slots[first : first + count],
            )
        )
    return materials


def read_samplers(sections: SectionReader) -> list:
    kind = SectionKind.SAMPLERS
    if not sections.has(kind):
        return []
    section = sections.raw(kind)
    if len(section) % SAMPLER.size:
        raise FormatError("Samplers have the wrong size")
    return [
        Sampler(
            checked_enum(SamplerFilter, a),
            checked_enum(SamplerWrap, b),
            checked_enum(SamplerWrap, c),
        )
        for a, b, c in SAMPLER.iter_unpack(section)
    ]


def _read_payloads(sections: SectionReader) -> list:
    kind = SectionKind.IMAGE_PAYLOAD_STARTS
    if not sections.has(kind):
        return []
    starts = read_byte_offsets(sections, kind)
    blob = sections.raw(SectionKind.IMAGE_PAYLOADS)
    if starts[-1] > len(blob):
        raise FormatError("Image payloads run past their section")
    return [blob[a:b] for a, b in zip(starts, starts[1:], strict=False)]


def read_images(sections: SectionReader) -> list:
    names = read_name_list(sections, SectionKind.IMAGE_NAMES)
    spaces = read_name_list(sections, SectionKind.IMAGE_COLOR_SPACES)
    records = read_records(sections, SectionKind.IMAGE_INFO, IMAGE_INFO, len(names))
    paths = read_name_list(sections, SectionKind.IMAGE_PATHS)
    tile_records = read_records(
        sections, SectionKind.IMAGE_TILES, IMAGE_TILE, len(paths)
    )
    payloads = _read_payloads(sections)
    if len(spaces) != len(names):
        raise FormatError("Image color spaces do not match their names")
    images = []
    for name, space, record in zip(names, spaces, records, strict=True):
        width, height, channels, flags, first, count = record
        if first + count > len(paths):
            raise FormatError(f"Image {name} has missing tiles")
        tiles = []
        for at in range(first, first + count):
            number, payload = tile_records[at]
            if payload != NO_INDEX and payload >= len(payloads):
                raise FormatError(f"Image {name} has a missing payload")
            data = payloads[payload] if payload != NO_INDEX else b""
            tiles.append(ImageTile(number, paths[at], data))
        images.append(
            Image(name, space, width, height, channels, bool(flags & _HAS_ALPHA), tiles)
        )
    return images
