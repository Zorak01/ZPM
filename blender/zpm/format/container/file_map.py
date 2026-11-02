# SPDX-License-Identifier: GPL-3.0-or-later

"""Names every byte of a ZPM file, so a file can be inspected and its layout proven."""

from dataclasses import dataclass

from ..errors import FormatError
from ..storage.column import column_length
from ..storage.names import names_length
from .layout import (
    DIRECTORY_ENTRY,
    FILE_HEADER,
    LARGEST_NARROW_FILE,
    MESH_INFO,
    OPTIONAL_KIND_START,
    SPACE,
    TAIL_BYTES,
    WIDE_DIRECTORY_ENTRY,
    SectionFlags,
    SectionKind,
    align,
)

_CONSTANT_BYTES = 4
_FIXED_LENGTHS = {
    SectionKind.MESH_INFO: MESH_INFO.size,
    SectionKind.SPACE: SPACE.size,
    SectionKind.POSITION_CONSTANT_X: _CONSTANT_BYTES,
    SectionKind.POSITION_CONSTANT_Y: _CONSTANT_BYTES,
    SectionKind.POSITION_CONSTANT_Z: _CONSTANT_BYTES,
}
_RAW_KINDS = {
    SectionKind.POSITION_REPEAT_MASK,
    SectionKind.EDGE_LOOSE_MASK,
    SectionKind.FACE_STARTS,
    SectionKind.CORNER_REPEAT_MASK,
    SectionKind.EDGE_FIRST_USE,
    SectionKind.NORMAL_REPEAT_MASK,
    SectionKind.NORMAL_EXCEPTION_MASK,
    SectionKind.UV_REPEAT_MASK,
    SectionKind.UV_EXCEPTION_MASK,
    SectionKind.TANGENT_REPEAT_MASK,
    SectionKind.TANGENT_EXCEPTION_MASK,
    SectionKind.COLOR_FORMATS,
    SectionKind.ATTRIBUTE_FORMATS,
    SectionKind.SHAPE_INFO,
    SectionKind.SCENE_MESH_STARTS,
    SectionKind.SCENE_MESHES,
    SectionKind.OBJECT_INFO,
    SectionKind.POSE_CHANNELS,
    SectionKind.COLLECTION_PARENTS,
    SectionKind.COLLECTION_MEMBER_STARTS,
    SectionKind.COLLECTION_MEMBERS,
    SectionKind.MATERIAL_INFO,
    SectionKind.TEXTURE_SLOTS,
    SectionKind.SAMPLERS,
    SectionKind.IMAGE_INFO,
    SectionKind.IMAGE_TILES,
    SectionKind.IMAGE_PAYLOAD_STARTS,
    SectionKind.IMAGE_PAYLOADS,
    SectionKind.LIGHT_INFO,
    SectionKind.ARMATURE_BONE_STARTS,
    SectionKind.BONE_INFO,
    SectionKind.SKIN_STARTS,
    SectionKind.SKIN_BONES,
    SectionKind.CORNER_STRIP_BITS,
    SectionKind.VALUE_NODES,
    SectionKind.VALUE_OWNERS,
    SectionKind.CURVE_INFO,
    SectionKind.SPLINE_INFO,
    SectionKind.LATTICE_INFO,
    SectionKind.CLIP_INFO,
    SectionKind.TRACK_INFO,
    SectionKind.TIME_STARTS,
    SectionKind.EXTENSION_INFO,
    SectionKind.EXTENSION_PAYLOADS,
    SectionKind.OBJECT_ORDER,
    SectionKind.OBJECT_CHILD_STARTS,
    SectionKind.OBJECT_CHILDREN,
    SectionKind.BONE_ORDER,
    SectionKind.BONE_CHILD_STARTS,
    SectionKind.BONE_CHILDREN,
    SectionKind.COLOR_RG_REPEAT_MASK,
    SectionKind.COLOR_RG_EXCEPTION_MASK,
    SectionKind.COLOR_BA_REPEAT_MASK,
    SectionKind.COLOR_BA_EXCEPTION_MASK,
}
_NAME_KINDS = {
    SectionKind.UV_NAMES,
    SectionKind.ATTRIBUTE_STRINGS,
    SectionKind.COLOR_NAMES,
    SectionKind.GROUP_NAMES,
    SectionKind.MATERIAL_SLOT_NAMES,
    SectionKind.TANGENT_METHOD,
    SectionKind.ATTRIBUTE_NAMES,
    SectionKind.SHAPE_NAMES,
    SectionKind.SHAPE_GROUP_NAMES,
    SectionKind.OBJECT_NAMES,
    SectionKind.COLLECTION_NAMES,
    SectionKind.MATERIAL_NAMES,
    SectionKind.TEXTURE_UV_NAMES,
    SectionKind.IMAGE_NAMES,
    SectionKind.IMAGE_COLOR_SPACES,
    SectionKind.IMAGE_PATHS,
    SectionKind.LIGHT_NAMES,
    SectionKind.ARMATURE_NAMES,
    SectionKind.BONE_NAMES,
    SectionKind.TEXTURE_ROLES,
    SectionKind.VALUE_STRINGS,
    SectionKind.CAMERA_NAMES,
    SectionKind.CURVE_NAMES,
    SectionKind.LATTICE_NAMES,
    SectionKind.CLIP_NAMES,
    SectionKind.EXTENSION_NAMES,
}


@dataclass(frozen=True, slots=True)
class FileRegion:
    """One named stretch of a file: offset, length, and what it holds."""

    name: str
    offset: int
    length: int
    flags: SectionFlags = SectionFlags.NONE


def _used_length(kind: SectionKind, flags: SectionFlags, section: bytes) -> int:
    if kind in _RAW_KINDS:
        return len(section)
    if kind in _NAME_KINDS:
        return names_length(section)
    if kind in _FIXED_LENGTHS:
        return _FIXED_LENGTHS[kind]
    return column_length(section, flags)


def map_file(data: bytes) -> list:
    """Return regions in file order, covering every byte exactly once.

    Lengths come from each section's own contents. Raises FormatError when
    padding is not zero or is longer than alignment needs.
    """
    count = FILE_HEADER.unpack_from(data)[2]
    limit = len(data) - TAIL_BYTES
    entry = (
        DIRECTORY_ENTRY if len(data) <= LARGEST_NARROW_FILE else WIDE_DIRECTORY_ENTRY
    )
    regions = [
        FileRegion("FILE_HEADER", 0, FILE_HEADER.size),
        FileRegion("DIRECTORY", FILE_HEADER.size, count * entry.size),
    ]
    entries = [
        entry.unpack_from(data, FILE_HEADER.size + i * entry.size) for i in range(count)
    ]
    for index, (raw_kind, raw_flags, layer, offset) in enumerate(entries):
        end = entries[index + 1][3] if index + 1 < count else limit
        if raw_kind >= OPTIONAL_KIND_START:
            regions.append(FileRegion(f"OPTIONAL_{raw_kind}", offset, end - offset))
            continue
        kind = SectionKind(raw_kind)
        flags = SectionFlags(raw_flags)
        used = _used_length(kind, flags, data[offset:end])
        name = f"{kind.name}[{layer}]" if layer else kind.name
        regions.append(FileRegion(name, offset, used, flags))
        if align(used) != end - offset or any(data[offset + used : end]):
            raise FormatError(f"Section {kind.name} has invalid padding")
        if used != end - offset:
            regions.append(FileRegion("PADDING", offset + used, end - offset - used))
    regions.append(FileRegion("TAIL", limit, TAIL_BYTES))
    return regions
