# SPDX-License-Identifier: GPL-3.0-or-later

"""Whole files to and from Scene.

Each mesh is a complete mesh file, kept whole inside the scene file, so one mesh opens
with no copy using the same reader, and costs only its own short directory and tail.
"""

from dataclasses import replace

from ..container.layout import (
    DEFAULT_MAX_BYTES,
    FILE_HEADER,
    NO_INDEX,
    ObjectType,
    ParentType,
    ResourceKind,
    SectionKind,
    TrackValue,
)
from ..container.sections import Section, SectionReader, write_file
from ..errors import FormatError
from ..scene import Camera, Scene
from .mesh_codec import decode, encode
from .scene_animation import encode_animation, read_animation
from .scene_curves import encode_curves_and_lattices, read_curves, read_lattices
from .scene_extensions import encode_extensions, read_extensions
from .scene_materials import (
    encode_materials_and_images,
    read_images,
    read_materials_table,
    read_samplers,
)
from .scene_objects import (
    encode_objects_and_rigs,
    read_armatures,
    read_collections,
    read_lights,
    read_objects,
)
from .scene_tables import (
    byte_offsets_section,
    names_section,
    read_byte_offsets,
    read_name_list,
)
from .scene_values import StringTable, encode_values, read_strings, read_values
from .space import encode_space, read_space


def _without_repeated_meshes(scene: Scene) -> Scene:
    """Return the scene with identical meshes stored once, and objects sharing them.

    Compares what would be stored. An animated mesh keeps its own copy, because tracks
    address meshes by index. Keys naming a mesh follow it. Costs one encode of every
    mesh when any two have equal size.
    """
    animated = {
        track.owner
        for clip in scene.clips
        for track in clip.tracks
        if track.owner_kind == ResourceKind.MESH
    }
    sizes = {}
    for index, mesh in enumerate(scene.meshes):
        key = (mesh.vertex_count, mesh.face_count, len(mesh.corners))
        sizes.setdefault(key, []).append(index)
    if all(len(group) == 1 for group in sizes.values()):
        return scene
    first_of = {}
    remap = {}
    kept = []
    for index, mesh in enumerate(scene.meshes):
        key = (encode(mesh), repr((mesh.properties, mesh.drivers)))
        if index in animated or key not in first_of:
            first_of.setdefault(key, len(kept))
            remap[index] = len(kept)
            kept.append(mesh)
        else:
            remap[index] = first_of[key]
    objects = [
        replace(item, data=remap[item.data])
        if item.object_type == ObjectType.MESH and item.data != NO_INDEX
        else item
        for item in scene.objects
    ]
    return replace(
        scene,
        meshes=kept,
        objects=objects,
        clips=[
            replace(clip, tracks=[_with_meshes_moved(t, remap) for t in clip.tracks])
            for clip in scene.clips
        ],
    )


def _with_meshes_moved(track, remap: dict):
    names_mesh = (
        track.value_type == TrackValue.REFERENCE
        and track.target_kind == ResourceKind.MESH
    )
    if not names_mesh:
        return track
    return replace(track, values=[remap[value] for value in track.values])


def _meshes_with_named_vertices(scene: Scene) -> set:
    """Return meshes whose vertices objects are parented to by number."""
    return {
        scene.objects[item.parent].data
        for item in scene.objects
        if item.parent != NO_INDEX
        and item.parent_type in (ParentType.VERTEX, ParentType.VERTEX_3)
        and scene.objects[item.parent].object_type == ObjectType.MESH
    }


def encode_scene(scene: Scene) -> bytes:
    """Return file bytes for a validated scene. Meshes encode as encode describes."""
    scene.validate()
    deduplicated = _without_repeated_meshes(scene)
    named_vertices = _meshes_with_named_vertices(deduplicated)
    meshes = [
        encode(mesh, keep_vertex_order=index in named_vertices)
        for index, mesh in enumerate(deduplicated.meshes)
    ]
    strings = StringTable()
    sections = [
        *encode_space(scene.space),
        *byte_offsets_section(SectionKind.SCENE_MESH_STARTS, meshes),
        *encode_materials_and_images(deduplicated),
        *encode_objects_and_rigs(deduplicated),
        *names_section(SectionKind.CAMERA_NAMES, [c.name for c in scene.cameras]),
        *encode_curves_and_lattices(deduplicated),
        *encode_extensions(scene.extensions),
        *encode_animation(deduplicated, strings),
        *encode_values(deduplicated, strings),
    ]
    sections += strings.section()
    if meshes:
        sections.append(Section(SectionKind.SCENE_MESHES, b"".join(meshes)))
    return write_file(sections)


def _read_meshes(sections: SectionReader, max_bytes: int) -> list:
    starts = read_byte_offsets(sections, SectionKind.SCENE_MESH_STARTS)
    if len(starts) == 1:
        return []
    blob = sections.raw(SectionKind.SCENE_MESHES)
    if starts[-1] != len(blob):
        raise FormatError("Meshes do not fill their section")
    return [
        decode(blob[a:b], max_bytes=max_bytes)
        for a, b in zip(starts, starts[1:], strict=False)
    ]


def decode_scene(data: bytes, *, max_bytes: int = DEFAULT_MAX_BYTES) -> Scene:
    """Expand one file into a Scene, for editing tools. Raises FormatError if malformed.

    Runtime readers index the file instead. max_bytes limits the file and each mesh.
    """
    if len(data) > max_bytes:
        raise FormatError("File exceeds the configured size limit")
    if len(data) < FILE_HEADER.size:
        raise FormatError("File is truncated")
    sections = SectionReader(data)
    if not sections.has(SectionKind.SCENE_MESH_STARTS):
        raise FormatError("File holds no scene")
    strings = read_strings(sections)
    scene = Scene(
        read_space(sections),
        _read_meshes(sections, max_bytes),
        read_materials_table(sections),
        read_samplers(sections),
        read_images(sections),
        read_objects(sections),
        read_collections(sections),
        read_lights(sections),
        read_armatures(sections),
        read_curves(sections),
        read_lattices(sections),
        [Camera(name) for name in read_name_list(sections, SectionKind.CAMERA_NAMES)],
        read_animation(sections, strings),
        read_extensions(sections),
    )
    read_values(sections, scene, strings)
    scene.validate()
    return scene
