# SPDX-License-Identifier: GPL-3.0-or-later

"""Blender materials and images to and from the format's tables."""

import json
from pathlib import Path

import bpy

from ..format import Extension, Image, ImageTile, Material, Sampler, TextureSlot
from ..format.codec.scene_extensions import BLENDER_NODE_GRAPHS
from ..format.container.layout import AlphaMode, SamplerFilter, SamplerWrap
from ..format.scene import (
    ROLE_ALPHA,
    ROLE_BASE_COLOR,
    ROLE_EMISSION,
    ROLE_METALLIC,
    ROLE_NORMAL,
    ROLE_ROUGHNESS,
)
from . import rna_values

_PRINCIPLED = "BSDF_PRINCIPLED"
_IMAGE_NODE = "TEX_IMAGE"
_NORMAL_MAP_NODE = "NORMAL_MAP"
_MAPPING_NODE = "MAPPING"
_UV_MAP_NODE = "UVMAP"
_UDIM_TOKEN = "<UDIM>"
_MAX_WALK = 8
_ROLE_INPUTS = {
    ROLE_BASE_COLOR: "Base Color",
    ROLE_METALLIC: "Metallic",
    ROLE_ROUGHNESS: "Roughness",
    ROLE_NORMAL: "Normal",
    ROLE_EMISSION: "Emission Color",
    ROLE_ALPHA: "Alpha",
}
_FIELD_INPUTS = {*_ROLE_INPUTS.values(), "Emission Strength"}
_DATA_ROLES = (ROLE_METALLIC, ROLE_ROUGHNESS, ROLE_NORMAL, ROLE_ALPHA)
_STANDARD_NODES = {
    _PRINCIPLED,
    "OUTPUT_MATERIAL",
    _IMAGE_NODE,
    _NORMAL_MAP_NODE,
    _MAPPING_NODE,
    _UV_MAP_NODE,
    "SEPCOLOR",
}
_FILTERS = {
    "Linear": SamplerFilter.LINEAR,
    "Closest": SamplerFilter.NEAREST,
    "Cubic": SamplerFilter.CUBIC,
    "Smart": SamplerFilter.CUBIC,
}
_FILTER_NAMES = {
    SamplerFilter.LINEAR: "Linear",
    SamplerFilter.NEAREST: "Closest",
    SamplerFilter.CUBIC: "Cubic",
}
_WRAPS = {
    "REPEAT": SamplerWrap.REPEAT,
    "EXTEND": SamplerWrap.EXTEND,
    "CLIP": SamplerWrap.CLIP,
    "MIRROR": SamplerWrap.MIRROR,
}
_ALPHA_MODES = {
    "OPAQUE": AlphaMode.OPAQUE,
    "CLIP": AlphaMode.CUTOFF,
    "HASHED": AlphaMode.BLEND,
    "BLEND": AlphaMode.BLEND,
}
_ALPHA_MODE_NAMES = {
    AlphaMode.OPAQUE: "OPAQUE",
    AlphaMode.CUTOFF: "CLIP",
    AlphaMode.BLEND: "BLEND",
}


class MaterialCapture:
    """Collects materials, sharing samplers and images among them."""

    def __init__(self, embed_textures: bool):
        self._embed_textures = embed_textures
        self._images = {}
        self._samplers = {}
        self._materials = {}
        self._graphs = {}
        self._defaults = _principled_defaults()

    @property
    def extensions(self) -> list:
        """Return node graphs plain fields cannot describe, for Blender to rebuild."""
        if not self._graphs:
            return []
        payload = json.dumps(self._graphs, separators=(",", ":")).encode("utf-8")
        return [Extension(BLENDER_NODE_GRAPHS, payload=payload)]

    @property
    def materials(self) -> list:
        return list(self._materials.values())

    @property
    def samplers(self) -> list:
        return list(self._samplers)

    @property
    def images(self) -> list:
        return [image for image, _ in self._images.values()]

    def add_mesh_slots(self, mesh: bpy.types.Mesh) -> None:
        for material in mesh.materials:
            if material is not None and material.name not in self._materials:
                self._materials[material.name] = self._capture(material)

    def _capture(self, material: bpy.types.Material) -> Material:
        result = Material(
            material.name,
            properties=rna_values.capture_properties(material),
            alpha_cutoff=material.alpha_threshold,
            alpha_mode=_ALPHA_MODES.get(material.blend_method, AlphaMode.OPAQUE),
            is_double_sided=not material.use_backface_culling,
        )
        shader = _find_principled(material)
        if shader is None:
            result.base_color = tuple(material.diffuse_color)
            result.metallic = material.metallic
            result.roughness = material.roughness
            return result
        inputs = shader.inputs
        result.base_color = (
            *inputs["Base Color"].default_value[:3],
            inputs["Alpha"].default_value,
        )
        result.metallic = inputs["Metallic"].default_value
        result.roughness = inputs["Roughness"].default_value
        result.emission = tuple(inputs["Emission Color"].default_value[:3])
        result.emission_strength = inputs["Emission Strength"].default_value
        for role, name in _ROLE_INPUTS.items():
            self._add_slot(result, role, inputs[name])
        for socket in inputs:
            if socket.name not in _FIELD_INPUTS:
                self._capture_extra_input(result, socket)
        graph = _capture_graph(material)
        if graph:
            self._graphs[material.name] = graph
        return result

    def _capture_extra_input(self, result: Material, socket) -> None:
        """Keep what only some renderers have, like clearcoat, under its own name."""
        if socket.links:
            self._add_slot(result, socket.name, socket)
            return
        value = _socket_value(socket)
        if value is not None and value != self._defaults.get(socket.name):
            result.parameters[socket.name] = value

    def _add_slot(self, result: Material, role: str, socket) -> None:
        if role == ROLE_NORMAL and socket.links:
            normal_map = socket.links[0].from_node
            if normal_map.type == _NORMAL_MAP_NODE:
                result.normal_scale = normal_map.inputs["Strength"].default_value
        node = _find_image_node(socket)
        if node is None or node.image is None:
            return
        slot = TextureSlot(
            role,
            self._image_index(node.image),
            self._sampler_index(node),
        )
        _read_mapping(node, slot)
        result.slots.append(slot)

    def _sampler_index(self, node) -> int:
        wrap = _WRAPS.get(node.extension, SamplerWrap.REPEAT)
        sampler = Sampler(
            _FILTERS.get(node.interpolation, SamplerFilter.LINEAR), wrap, wrap
        )
        return self._samplers.setdefault(sampler, len(self._samplers))

    def _image_index(self, image: bpy.types.Image) -> int:
        if image.name not in self._images:
            captured = _capture_image(image, self._embed_textures)
            self._images[image.name] = (captured, len(self._images))
        return self._images[image.name][1]


def _find_principled(material: bpy.types.Material):
    if material.node_tree is None:
        return None
    for node in material.node_tree.nodes:
        if node.type == _PRINCIPLED:
            return node
    return None


def _socket_value(socket):
    if not hasattr(socket, "default_value"):
        return None
    value = socket.default_value
    if isinstance(value, (bool, int, float, str)):
        return value
    return list(value)


def _principled_defaults() -> dict:
    """Return a new Principled node's inputs, so only changed ones need storing."""
    material = bpy.data.materials.new("zpm-defaults")
    material.use_nodes = True
    shader = _find_principled(material)
    defaults = {socket.name: _socket_value(socket) for socket in shader.inputs}
    bpy.data.materials.remove(material)
    return defaults


def _capture_graph(material: bpy.types.Material) -> dict:
    """Return the node graph when it holds more than the plain fields describe."""
    tree = material.node_tree
    if tree is None or all(node.type in _STANDARD_NODES for node in tree.nodes):
        return {}
    nodes = []
    for node in tree.nodes:
        settings = rna_values.capture_rna(
            node, {}, skip={"location", "width", "height"}
        )
        nodes.append(
            {
                "type": node.bl_idname,
                "name": node.name,
                "location": list(node.location),
                "settings": settings,
                "image": node.image.name if getattr(node, "image", None) else "",
                "inputs": [_socket_value(socket) for socket in node.inputs],
            }
        )
    links = [
        [
            link.from_node.name,
            list(link.from_node.outputs).index(link.from_socket),
            link.to_node.name,
            list(link.to_node.inputs).index(link.to_socket),
        ]
        for link in tree.links
    ]
    return {"nodes": nodes, "links": links}


def _apply_graph(material, graph: dict, images: dict) -> None:
    tree = material.node_tree
    tree.nodes.clear()
    made = {}
    for stored in graph["nodes"]:
        node = tree.nodes.new(stored["type"])
        node.name = stored["name"]
        node.location = stored["location"]
        rna_values.apply_rna(node, stored["settings"], [])
        if stored["image"] in images:
            node.image = images[stored["image"]]
        for socket, value in zip(node.inputs, stored["inputs"], strict=False):
            if value is not None and hasattr(socket, "default_value"):
                socket.default_value = value
        made[stored["name"]] = node
    for source, output, target, target_input in graph["links"]:
        tree.links.new(made[source].outputs[output], made[target].inputs[target_input])


def read_graphs(extensions: list) -> dict:
    """Return material name to node graph, from the file's extensions."""
    for extension in extensions:
        if extension.name == BLENDER_NODE_GRAPHS:
            return json.loads(extension.payload)
    return {}


def _find_image_node(socket):
    """Walk up a socket's first links, through color splitters and normal maps."""
    for _ in range(_MAX_WALK):
        if not socket.links:
            return None
        node = socket.links[0].from_node
        if node.type == _IMAGE_NODE:
            return node
        linked = [item for item in node.inputs if item.links]
        if not linked:
            return None
        socket = linked[0]
    return None


def _read_mapping(node, slot: TextureSlot) -> None:
    vector = node.inputs["Vector"]
    upstream = vector.links[0].from_node if vector.links else None
    if upstream is not None and upstream.type == _MAPPING_NODE:
        location = upstream.inputs["Location"].default_value
        slot.offset = (location[0], location[1])
        slot.rotation = upstream.inputs["Rotation"].default_value[2]
        scale = upstream.inputs["Scale"].default_value
        slot.scale = (scale[0], scale[1])
        vector = upstream.inputs["Vector"]
        upstream = vector.links[0].from_node if vector.links else None
    if upstream is not None and upstream.type == _UV_MAP_NODE:
        slot.uv_layer = upstream.uv_map


def _read_file(path: str) -> bytes:
    file = Path(bpy.path.abspath(path))
    return file.read_bytes() if file.is_file() else b""


def _capture_tiles(image: bpy.types.Image, embed: bool) -> list:
    if image.source != "TILED":
        payload = b""
        if embed:
            packed = image.packed_file
            payload = bytes(packed.data) if packed else _read_file(image.filepath)
        return [ImageTile(0, image.filepath, payload)]
    tiles = []
    for tile in image.tiles:
        path = image.filepath.replace(_UDIM_TOKEN, str(tile.number))
        tiles.append(ImageTile(tile.number, path, _read_file(path) if embed else b""))
    return tiles


def _capture_image(image: bpy.types.Image, embed: bool) -> Image:
    return Image(
        image.name,
        image.colorspace_settings.name,
        image.size[0],
        image.size[1],
        image.channels,
        image.alpha_mode != "NONE",
        _capture_tiles(image, embed),
    )


def _create_image(source: Image) -> bpy.types.Image:
    is_tiled = any(tile.number for tile in source.tiles)
    image = bpy.data.images.new(
        source.name,
        max(source.width, 1),
        max(source.height, 1),
        alpha=source.has_alpha,
        tiled=is_tiled,
    )
    first = source.tiles[0] if source.tiles else ImageTile()
    if is_tiled:
        for tile in source.tiles[1:]:
            image.tiles.new(tile_number=tile.number)
        image.tiles[0].number = first.number
    elif first.payload:
        image.source = "FILE"
        image.filepath = first.path
        image.pack(data=first.payload, data_len=len(first.payload))
    elif first.path:
        image.source = "FILE"
        image.filepath = first.path
    if source.color_space:
        try:
            image.colorspace_settings.name = source.color_space
        except TypeError:
            pass
    return image


def create_images(images: list) -> list:
    """Create every image, in order. An unknown color space stays at its default."""
    return [_create_image(image) for image in images]


def _link_slot(material, shader, slot, images, samplers) -> None:
    nodes, links = material.node_tree.nodes, material.node_tree.links
    node = nodes.new("ShaderNodeTexImage")
    node.image = images[slot.image]
    sampler = samplers[slot.sampler]
    node.interpolation = _FILTER_NAMES[sampler.filter]
    node.extension = next(k for k, v in _WRAPS.items() if v == sampler.wrap_u)
    if slot.role in _DATA_ROLES and node.image is not None:
        try:
            node.image.colorspace_settings.name = "Non-Color"
        except TypeError:
            pass
    socket = shader.inputs.get(_ROLE_INPUTS.get(slot.role, slot.role))
    if socket is None:
        return
    if slot.role == ROLE_NORMAL:
        normal_map = nodes.new("ShaderNodeNormalMap")
        normal_map.uv_map = slot.uv_layer
        links.new(normal_map.outputs["Normal"], socket)
        socket = normal_map.inputs["Color"]
    links.new(node.outputs["Color"], socket)
    vector = node.inputs["Vector"]
    if slot.offset != (0.0, 0.0) or slot.rotation or slot.scale != (1.0, 1.0):
        mapping = nodes.new("ShaderNodeMapping")
        mapping.inputs["Location"].default_value = (*slot.offset, 0.0)
        mapping.inputs["Rotation"].default_value = (0.0, 0.0, slot.rotation)
        mapping.inputs["Scale"].default_value = (*slot.scale, 1.0)
        links.new(mapping.outputs["Vector"], vector)
        vector = mapping.inputs["Vector"]
    if slot.uv_layer:
        uv_map = nodes.new("ShaderNodeUVMap")
        uv_map.uv_map = slot.uv_layer
        links.new(uv_map.outputs["UV"], vector)


def create_material(source: Material, images: list, samplers: list, graphs: dict):
    """Create a material whose Principled node holds these values.

    A stored node graph replaces that node, since it says more.
    """
    material = bpy.data.materials.new(source.name)
    rna_values.apply_properties(material, source.properties)
    material.use_nodes = True
    material.blend_method = _ALPHA_MODE_NAMES[source.alpha_mode]
    material.alpha_threshold = source.alpha_cutoff
    material.use_backface_culling = not source.is_double_sided
    shader = _find_principled(material)
    inputs = shader.inputs
    inputs["Base Color"].default_value = (*source.base_color[:3], 1.0)
    inputs["Alpha"].default_value = source.base_color[3]
    inputs["Metallic"].default_value = source.metallic
    inputs["Roughness"].default_value = source.roughness
    inputs["Emission Color"].default_value = (*source.emission, 1.0)
    inputs["Emission Strength"].default_value = source.emission_strength
    for name, value in source.parameters.items():
        if name in inputs:
            inputs[name].default_value = value
    if source.name in graphs:
        by_name = {image.name: image for image in images}
        _apply_graph(material, graphs[source.name], by_name)
        return material
    for slot in source.slots:
        _link_slot(material, shader, slot, images, samplers)
    for node in material.node_tree.nodes:
        if node.type == _NORMAL_MAP_NODE:
            node.inputs["Strength"].default_value = source.normal_scale
    return material
