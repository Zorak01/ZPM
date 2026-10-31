# SPDX-License-Identifier: GPL-3.0-or-later

"""Objects, poses, collections, lights, armatures and skin links."""

from ..container.layout import (
    BONE_INFO,
    LIGHT_INFO,
    OBJECT_INFO,
    POSE_CHANNEL,
    AreaShape,
    BoneFlags,
    LightType,
    LightUnit,
    ObjectFlags,
    ObjectType,
    RotationMode,
    SectionKind,
    SkinFlags,
)
from ..container.sections import Section, SectionReader
from ..errors import FormatError
from ..scene import (
    Armature,
    Bone,
    Collection,
    Light,
    PoseChannel,
    Scene,
    SceneObject,
)
from .scene_tables import (
    checked_enum,
    indices_section,
    names_section,
    read_indices,
    read_name_list,
    read_records,
    read_starts,
    starts_of,
)

_CASTS_SHADOW = 1


def _encode_objects(objects: list) -> list:
    if not objects:
        return []
    infos = bytearray()
    channels = bytearray()
    first_channel = 0
    for item in objects:
        for channel in item.pose:
            channels += POSE_CHANNEL.pack(
                channel.bone,
                *channel.location,
                *channel.rotation,
                *channel.scale,
                channel.rotation_mode,
            )
        infos += OBJECT_INFO.pack(
            item.object_type,
            item.data,
            item.parent,
            item.parent_bone,
            item.rotation_mode,
            item.flags,
            item.instance_collection,
            item.skin_armature,
            item.skin_flags,
            *item.location,
            *item.rotation,
            *item.scale,
            *item.parent_inverse,
            first_channel,
            len(item.pose),
        )
        first_channel += len(item.pose)
    sections = [
        *names_section(SectionKind.OBJECT_NAMES, [item.name for item in objects]),
        Section(SectionKind.OBJECT_INFO, bytes(infos)),
    ]
    if channels:
        sections.append(Section(SectionKind.POSE_CHANNELS, bytes(channels)))
    if any(item.skin_bones for item in objects):
        groups = [item.skin_bones for item in objects]
        sections += indices_section(SectionKind.SKIN_STARTS, starts_of(groups))
        joined = [bone for group in groups for bone in group]
        sections += indices_section(SectionKind.SKIN_BONES, joined)
    return sections


def _encode_collections(collections: list) -> list:
    if not collections:
        return []
    members = [collection.members for collection in collections]
    joined = [member for group in members for member in group]
    return [
        *names_section(SectionKind.COLLECTION_NAMES, [c.name for c in collections]),
        *indices_section(
            SectionKind.COLLECTION_PARENTS, [c.parent for c in collections]
        ),
        *indices_section(SectionKind.COLLECTION_MEMBER_STARTS, starts_of(members)),
        *indices_section(SectionKind.COLLECTION_MEMBERS, joined),
    ]


def _encode_lights(lights: list) -> list:
    if not lights:
        return []
    records = b"".join(
        LIGHT_INFO.pack(
            light.light_type,
            light.unit,
            *light.color,
            light.power,
            _CASTS_SHADOW if light.casts_shadow else 0,
            light.area_shape,
            light.radius,
            light.spot_angle,
            light.spot_blend,
            light.sun_angle,
            light.area_width,
            light.area_height,
        )
        for light in lights
    )
    return [
        *names_section(SectionKind.LIGHT_NAMES, [light.name for light in lights]),
        Section(SectionKind.LIGHT_INFO, records),
    ]


def _bone_flags(bone: Bone) -> BoneFlags:
    flags = BoneFlags.NONE
    if bone.is_connected:
        flags |= BoneFlags.CONNECTED
    if bone.is_deform:
        flags |= BoneFlags.DEFORM
    if bone.inherits_rotation:
        flags |= BoneFlags.INHERIT_ROTATION
    if bone.inherits_scale:
        flags |= BoneFlags.INHERIT_SCALE
    return flags


def _encode_armatures(armatures: list) -> list:
    if not armatures:
        return []
    groups = [armature.bones for armature in armatures]
    bones = [bone for group in groups for bone in group]
    records = b"".join(
        BONE_INFO.pack(
            bone.parent,
            _bone_flags(bone),
            *bone.head,
            *bone.tail,
            bone.roll,
            bone.envelope_distance,
            bone.head_radius,
            bone.tail_radius,
        )
        for bone in bones
    )
    sections = [
        *names_section(SectionKind.ARMATURE_NAMES, [a.name for a in armatures]),
        *indices_section(SectionKind.ARMATURE_BONE_STARTS, starts_of(groups)),
    ]
    if bones:
        sections += names_section(SectionKind.BONE_NAMES, [b.name for b in bones])
        sections.append(Section(SectionKind.BONE_INFO, records))
    return sections


def encode_objects_and_rigs(scene: Scene) -> list:
    return [
        *_encode_objects(scene.objects),
        *_encode_collections(scene.collections),
        *_encode_lights(scene.lights),
        *_encode_armatures(scene.armatures),
    ]


def _read_pose(sections: SectionReader, first: int, count: int, records: list) -> list:
    if first + count > len(records):
        raise FormatError("An object has missing pose channels")
    return [
        PoseChannel(
            values[0],
            values[1:4],
            values[4:8],
            values[8:11],
            checked_enum(RotationMode, values[11]),
        )
        for values in records[first : first + count]
    ]


def read_objects(sections: SectionReader) -> list:
    names = read_name_list(sections, SectionKind.OBJECT_NAMES)
    records = read_records(sections, SectionKind.OBJECT_INFO, OBJECT_INFO, len(names))
    channels = []
    if sections.has(SectionKind.POSE_CHANNELS):
        section = sections.raw(SectionKind.POSE_CHANNELS)
        channels = read_records(
            sections,
            SectionKind.POSE_CHANNELS,
            POSE_CHANNEL,
            len(section) // POSE_CHANNEL.size,
        )
    starts = [0] * (len(names) + 1)
    skin = []
    if sections.has(SectionKind.SKIN_STARTS):
        starts = read_starts(sections, SectionKind.SKIN_STARTS, len(names))
        skin = read_indices(sections, SectionKind.SKIN_BONES, starts[-1])
    objects = []
    for index, (name, values) in enumerate(zip(names, records, strict=True)):
        objects.append(
            SceneObject(
                name,
                checked_enum(ObjectType, values[0]),
                values[1],
                values[2],
                values[3],
                checked_enum(RotationMode, values[4]),
                ObjectFlags(values[5]),
                values[6],
                values[9:12],
                values[12:16],
                values[16:19],
                values[19:35],
                values[7],
                SkinFlags(values[8]),
                skin[starts[index] : starts[index + 1]],
                _read_pose(sections, values[35], values[36], channels),
            )
        )
    return objects


def read_collections(sections: SectionReader) -> list:
    names = read_name_list(sections, SectionKind.COLLECTION_NAMES)
    parents = read_indices(sections, SectionKind.COLLECTION_PARENTS, len(names))
    starts = read_starts(sections, SectionKind.COLLECTION_MEMBER_STARTS, len(names))
    members = read_indices(sections, SectionKind.COLLECTION_MEMBERS, starts[-1])
    return [
        Collection(name, parents[index], members[starts[index] : starts[index + 1]])
        for index, name in enumerate(names)
    ]


def read_lights(sections: SectionReader) -> list:
    names = read_name_list(sections, SectionKind.LIGHT_NAMES)
    records = read_records(sections, SectionKind.LIGHT_INFO, LIGHT_INFO, len(names))
    return [
        Light(
            name,
            checked_enum(LightType, values[0]),
            values[2:5],
            values[5],
            checked_enum(LightUnit, values[1]),
            bool(values[6] & _CASTS_SHADOW),
            values[8],
            values[9],
            values[10],
            values[11],
            checked_enum(AreaShape, values[7]),
            values[12],
            values[13],
        )
        for name, values in zip(names, records, strict=True)
    ]


def _unpack_bone(name: str, values: tuple) -> Bone:
    flags = BoneFlags(values[1])
    return Bone(
        name,
        values[0],
        values[2:5],
        values[5:8],
        values[8],
        values[9],
        values[10],
        values[11],
        bool(flags & BoneFlags.CONNECTED),
        bool(flags & BoneFlags.DEFORM),
        bool(flags & BoneFlags.INHERIT_ROTATION),
        bool(flags & BoneFlags.INHERIT_SCALE),
    )


def read_armatures(sections: SectionReader) -> list:
    names = read_name_list(sections, SectionKind.ARMATURE_NAMES)
    starts = read_starts(sections, SectionKind.ARMATURE_BONE_STARTS, len(names))
    bone_names = read_name_list(sections, SectionKind.BONE_NAMES)
    if len(bone_names) != starts[-1]:
        raise FormatError("Bone names do not match the armatures")
    records = read_records(sections, SectionKind.BONE_INFO, BONE_INFO, len(bone_names))
    bones = [
        _unpack_bone(name, values)
        for name, values in zip(bone_names, records, strict=True)
    ]
    return [
        Armature(name, bones[starts[index] : starts[index + 1]])
        for index, name in enumerate(names)
    ]
