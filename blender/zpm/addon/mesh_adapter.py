# SPDX-License-Identifier: GPL-3.0-or-later

"""Blender mesh adapter. No UI, filesystem access or operator calls."""

import math
from array import array
from typing import NamedTuple

import bpy

from ..format import (
    Attribute,
    AttributeDomain,
    AttributeMeaning,
    AttributeType,
    ColorLayer,
    FormatError,
    Mesh,
    ShapeInterpolation,
    ShapeTarget,
)
from ..format.container.layout import NO_SHAPE_TARGET
from ..format.mesh import ATTRIBUTE_TYPECODES

_MAX_TANGENT_FACE_SIZE = 4
_BYTE_MAX = 255
# Blender has no home for these, so they live under these attribute and property names.
_SHARP_CORNER_ATTRIBUTE = "zpm_sharp_corner"
_SMOOTHING_GROUP_ATTRIBUTE = "zpm_smoothing_group"
_UV_SEAM_ATTRIBUTE_PREFIX = "zpm_seam:"
_CHAIN_OFFSETS_PROPERTY = "zpm_crease_chain_offsets"
_CHAIN_VERTICES_PROPERTY = "zpm_crease_chain_vertices"
_IN_BETWEEN_PROPERTY_PREFIX = "zpm_in_between:"
_ZPM_NAME_PREFIX = "zpm_"
_HIDDEN_NAME_PREFIX = "."
_STORED_ATTRIBUTES = {
    "position",
    "sharp_edge",
    "sharp_face",
    "uv_seam",
    "material_index",
    "crease_edge",
    "crease_vert",
    "custom_normal",
}
_DOMAINS = {
    "POINT": AttributeDomain.POINT,
    "EDGE": AttributeDomain.EDGE,
    "FACE": AttributeDomain.FACE,
    "CORNER": AttributeDomain.CORNER,
}
_SHAPE_INTERPOLATIONS = {
    "KEY_LINEAR": ShapeInterpolation.LINEAR,
    "KEY_CARDINAL": ShapeInterpolation.CARDINAL,
    "KEY_CATMULL_ROM": ShapeInterpolation.CATMULL_ROM,
    "KEY_BSPLINE": ShapeInterpolation.BSPLINE,
}


class _BlenderAttribute(NamedTuple):
    """How one Blender attribute type maps to a file format and a bulk buffer."""

    value_type: AttributeType
    components: int
    meaning: AttributeMeaning
    field: str
    buffer_code: str


_ATTRIBUTE_TYPES = {
    "BOOLEAN": _BlenderAttribute(
        AttributeType.BOOL, 1, AttributeMeaning.NONE, "value", "b"
    ),
    "INT8": _BlenderAttribute(
        AttributeType.INT8, 1, AttributeMeaning.NONE, "value", "i"
    ),
    "INT": _BlenderAttribute(
        AttributeType.INT32, 1, AttributeMeaning.NONE, "value", "i"
    ),
    "INT16_2D": _BlenderAttribute(
        AttributeType.INT16, 2, AttributeMeaning.NONE, "value", "i"
    ),
    "INT32_2D": _BlenderAttribute(
        AttributeType.INT32, 2, AttributeMeaning.NONE, "value", "i"
    ),
    "FLOAT": _BlenderAttribute(
        AttributeType.FLOAT32, 1, AttributeMeaning.NONE, "value", "f"
    ),
    "FLOAT2": _BlenderAttribute(
        AttributeType.FLOAT32, 2, AttributeMeaning.VECTOR, "vector", "f"
    ),
    "FLOAT_VECTOR": _BlenderAttribute(
        AttributeType.FLOAT32, 3, AttributeMeaning.VECTOR, "vector", "f"
    ),
    "FLOAT_COLOR": _BlenderAttribute(
        AttributeType.FLOAT32, 4, AttributeMeaning.COLOR, "color", "f"
    ),
    "QUATERNION": _BlenderAttribute(
        AttributeType.FLOAT32, 4, AttributeMeaning.QUATERNION, "value", "f"
    ),
    "FLOAT4X4": _BlenderAttribute(
        AttributeType.FLOAT32, 16, AttributeMeaning.MATRIX, "value", "f"
    ),
}
_STRING_ATTRIBUTE = "STRING"
_TANGENT_METHOD = "MikkTSpace, Blender {version}"


def capture_object(obj: bpy.types.Object) -> Mesh:
    """Copy an object's base mesh with its vertex groups and weights."""
    result = capture_mesh(obj.data)
    result.vertex_groups = [group.name for group in obj.vertex_groups]
    if result.vertex_groups:
        offsets = array("I", [0])
        groups = array("I")
        values = array("f")
        for vertex in obj.data.vertices:
            for element in vertex.groups:
                groups.append(element.group)
                values.append(element.weight)
            offsets.append(len(values))
        result.weight_offsets = offsets
        result.weight_groups = groups
        result.weight_values = values
    result.validate()
    return result


def capture_mesh(mesh: bpy.types.Mesh) -> Mesh:
    """Copy the authored mesh with Blender's bulk buffer API.

    Object transforms and modifiers are intentionally outside the mesh format.
    This does not triangulate, weld, reorder or evaluate the source geometry.
    """
    positions = array("f", [0]) * (len(mesh.vertices) * 3)
    sizes = array("I", [0]) * len(mesh.polygons)
    corners = array("I", [0]) * len(mesh.loops)
    edges = array("I", [0]) * (len(mesh.edges) * 2)
    mesh.vertices.foreach_get("co", positions)
    mesh.polygons.foreach_get("loop_total", sizes)
    mesh.loops.foreach_get("vertex_index", corners)
    mesh.edges.foreach_get("vertices", edges)
    result = Mesh(positions, sizes, corners, edges, **_capture_surface(mesh))
    result.validate()
    _validate_geometry(result)
    return result


def _capture_flags(collection, name: str, count: int) -> array:
    flags = array("b", [0]) * count
    if count:
        collection.foreach_get(name, flags)
    return array("B", [1 if flag else 0 for flag in flags])


def _capture_attribute_flags(mesh: bpy.types.Mesh, name: str) -> array:
    attribute = mesh.attributes.get(name)
    if attribute is None:
        return array("B")
    return _capture_flags(attribute.data, "value", len(attribute.data))


def _capture_uv_seams(mesh: bpy.types.Mesh) -> dict:
    seams = {}
    for layer in mesh.uv_layers:
        name = _UV_SEAM_ATTRIBUTE_PREFIX + layer.name
        if name in mesh.attributes:
            seams[layer.name] = _capture_attribute_flags(mesh, name)
    return seams


def _capture_smoothing_groups(mesh: bpy.types.Mesh) -> array:
    attribute = mesh.attributes.get(_SMOOTHING_GROUP_ATTRIBUTE)
    if attribute is None:
        return array("I")
    groups = array("i", [0]) * len(attribute.data)
    attribute.data.foreach_get("value", groups)
    return array("I", groups)


def _capture_materials(mesh: bpy.types.Mesh) -> dict:
    """Return slots and face slots, clamping a stale index as Blender itself does."""
    slots = [material.name if material else "" for material in mesh.materials]
    if not slots:
        return {}
    indices = array("i", [0]) * len(mesh.polygons)
    mesh.polygons.foreach_get("material_index", indices)
    last = len(slots) - 1
    return {
        "material_slots": slots,
        "face_materials": array("I", [min(max(i, 0), last) for i in indices]),
    }


def _capture_string_attribute(attribute) -> Attribute:
    known = {}
    values = array("I")
    for element in attribute.data:
        text = element.value.decode("utf-8", "replace")
        values.append(known.setdefault(text, len(known)))
    return Attribute(
        _DOMAINS[attribute.domain],
        AttributeType.STRING,
        1,
        values,
        strings=list(known),
    )


def attribute_buffer(data_type: str) -> tuple:
    """Return the field and array type Blender's bulk API uses for an attribute type."""
    stored = _ATTRIBUTE_TYPES[data_type]
    return stored.field, stored.buffer_code


def apply_attribute(attributes, name: str, attribute: Attribute) -> None:
    """Fill a Blender attribute of this name, adding it if the data lacks it."""
    domain = {value: key for key, value in _DOMAINS.items()}[attribute.domain]
    if attribute.value_type == AttributeType.STRING:
        new = attributes.get(name) or attributes.new(name, _STRING_ATTRIBUTE, domain)
        for element, index in zip(new.data, attribute.values, strict=True):
            element.value = attribute.strings[index].encode("utf-8")
        return
    key = (attribute.value_type, attribute.components, attribute.meaning)
    data_type, stored = next(
        (blender_type, stored)
        for blender_type, stored in _ATTRIBUTE_TYPES.items()
        if (stored.value_type, stored.components, stored.meaning) == key
    )
    new = attributes.get(name) or attributes.new(name, data_type, domain)
    new.data.foreach_set(stored.field, array(stored.buffer_code, attribute.values))


def capture_attribute(attribute, *, keep_domain: bool = True) -> Attribute:
    """Copy one attribute. keep_domain is False for a domain a mesh does not have."""
    if attribute.data_type == _STRING_ATTRIBUTE:
        return _capture_string_attribute(attribute)
    if attribute.data_type not in _ATTRIBUTE_TYPES:
        raise FormatError(
            f"Attribute {attribute.name} has a type ZPM cannot store yet: "
            f"{attribute.data_type}"
        )
    stored = _ATTRIBUTE_TYPES[attribute.data_type]
    buffer = array(stored.buffer_code, [0]) * (len(attribute.data) * stored.components)
    if buffer:
        attribute.data.foreach_get(stored.field, buffer)
    typecode = ATTRIBUTE_TYPECODES[stored.value_type]
    values = (
        array(typecode, [1 if v else 0 for v in buffer])
        if (stored.value_type == AttributeType.BOOL)
        else array(typecode, buffer)
    )
    return Attribute(
        _DOMAINS[attribute.domain] if keep_domain else AttributeDomain.MESH,
        stored.value_type,
        stored.components,
        values,
        stored.meaning,
    )


def _capture_attributes(mesh: bpy.types.Mesh) -> dict:
    """Return attributes nothing else in the file holds. Hidden ones are UI state."""
    taken = _STORED_ATTRIBUTES | {layer.name for layer in mesh.uv_layers}
    taken |= {layer.name for layer in mesh.color_attributes}
    return {
        attribute.name: capture_attribute(attribute)
        for attribute in mesh.attributes
        if attribute.name not in taken
        and not attribute.name.startswith((_ZPM_NAME_PREFIX, _HIDDEN_NAME_PREFIX))
    }


def _capture_shape_target(mesh, block, blocks, is_relative) -> ShapeTarget:
    positions = array("f", [0.0]) * (len(mesh.vertices) * 3)
    if positions:
        block.data.foreach_get("co", positions)
    between = mesh.get(_IN_BETWEEN_PROPERTY_PREFIX + block.name)
    return ShapeTarget(
        block.name,
        positions,
        reference=blocks.find(block.relative_key.name),
        is_relative=is_relative,
        value=block.value,
        minimum=block.slider_min,
        maximum=block.slider_max,
        frame=block.frame,
        vertex_group=block.vertex_group,
        is_muted=block.mute,
        interpolation=_SHAPE_INTERPOLATIONS[block.interpolation],
        in_between_of=int(between[0]) if between else NO_SHAPE_TARGET,
        in_between_weight=float(between[1]) if between else 0.0,
    )


def _capture_shape_targets(mesh: bpy.types.Mesh) -> list:
    if mesh.shape_keys is None:
        return []
    blocks = mesh.shape_keys.key_blocks
    return [
        _capture_shape_target(mesh, block, blocks, mesh.shape_keys.use_relative)
        for block in blocks
    ]


def _capture_crease(mesh: bpy.types.Mesh, name: str) -> array:
    attribute = mesh.attributes.get(name)
    if attribute is None:
        return array("f")
    values = array("f", [0.0]) * len(attribute.data)
    attribute.data.foreach_get("value", values)
    return values


def _capture_surface(mesh: bpy.types.Mesh) -> dict:
    corner_count = len(mesh.loops)
    normals = array("f", [0.0]) * (corner_count * 3)
    if corner_count:
        mesh.corner_normals.foreach_get("vector", normals)
    layers = {}
    for layer in mesh.uv_layers:
        values = array("f", [0.0]) * (corner_count * 2)
        if corner_count:
            layer.uv.foreach_get("vector", values)
        layers[layer.name] = values
    smooth = _capture_flags(mesh.polygons, "use_smooth", len(mesh.polygons))
    tangents = _capture_tangents(mesh)
    return {
        **_capture_materials(mesh),
        "attributes": _capture_attributes(mesh),
        "shape_targets": _capture_shape_targets(mesh),
        "tangent_method": _TANGENT_METHOD.format(version=bpy.app.version_string)
        if tangents
        else "",
        "uv_seams": _capture_uv_seams(mesh),
        "sharp_corners": _capture_attribute_flags(mesh, _SHARP_CORNER_ATTRIBUTE),
        "smoothing_groups": _capture_smoothing_groups(mesh),
        "crease_chain_offsets": array("I", mesh.get(_CHAIN_OFFSETS_PROPERTY, [])),
        "crease_chain_vertices": array("I", mesh.get(_CHAIN_VERTICES_PROPERTY, [])),
        "corner_normals": normals if corner_count else array("f"),
        "has_custom_normals": mesh.has_custom_normals,
        "uv_layers": layers,
        "sharp_edges": _capture_flags(mesh.edges, "use_edge_sharp", len(mesh.edges)),
        "seam_edges": _capture_flags(mesh.edges, "use_seam", len(mesh.edges)),
        "flat_faces": array("B", [0 if flag else 1 for flag in smooth]),
        "edge_creases": _capture_crease(mesh, "crease_edge"),
        "vertex_creases": _capture_crease(mesh, "crease_vert"),
        "tangent_layers": tangents,
        "color_layers": _capture_colors(mesh),
    }


def _capture_tangents(mesh: bpy.types.Mesh) -> dict:
    """Return tangents for every UV layer. Blender cannot do this for n-gons."""
    if not mesh.uv_layers or not len(mesh.loops):
        return {}
    sizes = array("I", [0]) * len(mesh.polygons)
    mesh.polygons.foreach_get("loop_total", sizes)
    if max(sizes) > _MAX_TANGENT_FACE_SIZE:
        return {}
    return {
        layer.name: _capture_layer_tangents(mesh, layer.name)
        for layer in mesh.uv_layers
    }


def _capture_layer_tangents(mesh: bpy.types.Mesh, layer_name: str) -> array:
    mesh.calc_tangents(uvmap=layer_name)
    tangents = array("f", [0.0]) * (len(mesh.loops) * 3)
    signs = array("f", [0.0]) * len(mesh.loops)
    mesh.loops.foreach_get("tangent", tangents)
    mesh.loops.foreach_get("bitangent_sign", signs)
    mesh.free_tangents()
    combined = array("f")
    for corner, sign in enumerate(signs):
        combined.extend(tangents[corner * 3 : corner * 3 + 3])
        combined.append(sign)
    return combined


def _capture_colors(mesh: bpy.types.Mesh) -> dict:
    layers = {}
    for attribute in mesh.color_attributes:
        is_corner = attribute.domain == "CORNER"
        owners = len(mesh.loops) if is_corner else len(mesh.vertices)
        if attribute.data_type == "BYTE_COLOR":
            values = array("f", [0.0]) * (owners * 4)
            attribute.data.foreach_get("color_srgb", values)
            stored = array("B", [round(value * _BYTE_MAX) for value in values])
        else:
            stored = array("f", [0.0]) * (owners * 4)
            attribute.data.foreach_get("color", stored)
        is_srgb = attribute.data_type == "BYTE_COLOR"
        layers[attribute.name] = ColorLayer(stored, is_corner, is_srgb)
    return layers


def _apply_authored_marks(mesh: Mesh, result: bpy.types.Mesh) -> None:
    if mesh.sharp_corners:
        attribute = result.attributes.new(_SHARP_CORNER_ATTRIBUTE, "BOOLEAN", "CORNER")
        attribute.data.foreach_set("value", _bools(mesh.sharp_corners))
    if mesh.smoothing_groups:
        attribute = result.attributes.new(_SMOOTHING_GROUP_ATTRIBUTE, "INT", "CORNER")
        attribute.data.foreach_set("value", array("i", mesh.smoothing_groups))
    for name, flags in mesh.uv_seams.items():
        attribute = result.attributes.new(
            _UV_SEAM_ATTRIBUTE_PREFIX + name, "BOOLEAN", "EDGE"
        )
        attribute.data.foreach_set("value", _bools(flags))
    if mesh.crease_chain_offsets:
        result[_CHAIN_OFFSETS_PROPERTY] = list(mesh.crease_chain_offsets)
        result[_CHAIN_VERTICES_PROPERTY] = list(mesh.crease_chain_vertices)


def _apply_materials(mesh: Mesh, result: bpy.types.Mesh) -> None:
    """Fill slots with materials of those names, making any that are missing."""
    if mesh.face_materials:
        indices = array("i", mesh.face_materials)
        result.polygons.foreach_set("material_index", indices)
    for name in mesh.material_slots:
        material = None
        if name:
            material = bpy.data.materials.get(name) or bpy.data.materials.new(name)
        result.materials.append(material)


def _apply_attributes(mesh: Mesh, result: bpy.types.Mesh) -> None:
    for name, attribute in mesh.attributes.items():
        apply_attribute(result.attributes, name, attribute)


def _validate_attributes_fit(mesh: Mesh) -> None:
    known = {
        (stored.value_type, stored.components, stored.meaning)
        for stored in _ATTRIBUTE_TYPES.values()
    }
    for name, attribute in mesh.attributes.items():
        key = (attribute.value_type, attribute.components, attribute.meaning)
        is_string = attribute.value_type == AttributeType.STRING
        if attribute.domain == AttributeDomain.MESH or (
            key not in known and not (is_string and attribute.components == 1)
        ):
            raise FormatError(f"Blender has no attribute like {name}")


def apply_shape_keys(mesh: Mesh, obj: bpy.types.Object) -> None:
    """Add a mesh's shape targets to the object. Normal changes have no home here."""
    if not mesh.shape_targets:
        return
    blocks = [
        obj.shape_key_add(name=t.name, from_mix=False) for t in mesh.shape_targets
    ]
    data = obj.data
    data.shape_keys.use_relative = mesh.shape_targets[0].is_relative
    names = {v: k for k, v in _SHAPE_INTERPOLATIONS.items()}
    for block, target in zip(blocks, mesh.shape_targets, strict=True):
        if target.positions:
            block.data.foreach_set("co", target.positions)
        block.relative_key = blocks[target.reference]
        block.slider_min = target.minimum
        block.slider_max = target.maximum
        block.value = target.value
        block.vertex_group = target.vertex_group
        block.mute = target.is_muted
        block.interpolation = names[target.interpolation]
        if target.in_between_of != NO_SHAPE_TARGET:
            data[_IN_BETWEEN_PROPERTY_PREFIX + target.name] = [
                target.in_between_of,
                target.in_between_weight,
            ]


def _apply_crease(result: bpy.types.Mesh, name: str, domain: str, values: array):
    if values:
        attribute = result.attributes.new(name, "FLOAT", domain)
        attribute.data.foreach_set("value", values)


def _apply_surface(mesh: Mesh, result: bpy.types.Mesh) -> None:
    """Set surface data on a new datablock. Normals stay automatic unless authored."""
    for name, values in mesh.uv_layers.items():
        result.uv_layers.new(name=name).uv.foreach_set("vector", values)
    if mesh.sharp_edges:
        result.edges.foreach_set("use_edge_sharp", _bools(mesh.sharp_edges))
    if mesh.seam_edges:
        result.edges.foreach_set("use_seam", _bools(mesh.seam_edges))
    if mesh.flat_faces:
        smooth = array("b", [0 if flag else 1 for flag in mesh.flat_faces])
        result.polygons.foreach_set("use_smooth", smooth)
    _apply_crease(result, "crease_edge", "EDGE", mesh.edge_creases)
    _apply_crease(result, "crease_vert", "POINT", mesh.vertex_creases)
    _apply_colors(mesh, result)
    _apply_authored_marks(mesh, result)
    _apply_attributes(mesh, result)
    if mesh.has_custom_normals and mesh.corner_normals:
        values = mesh.corner_normals
        result.normals_split_custom_set(
            [tuple(values[at : at + 3]) for at in range(0, len(values), 3)]
        )


def _apply_colors(mesh: Mesh, result: bpy.types.Mesh) -> None:
    for name, layer in mesh.color_layers.items():
        data_type = "FLOAT_COLOR" if layer.is_float else "BYTE_COLOR"
        domain = "CORNER" if layer.is_corner else "POINT"
        attribute = result.color_attributes.new(name, data_type, domain)
        values = layer.values
        if not layer.is_float:
            values = array("f", [value / _BYTE_MAX for value in values])
        channel = "color_srgb" if layer.is_srgb else "color"
        attribute.data.foreach_set(channel, values)


def apply_vertex_groups(mesh: Mesh, obj: bpy.types.Object) -> None:
    """Add a mesh's vertex groups and weights to the object using it."""
    groups = [obj.vertex_groups.new(name=name) for name in mesh.vertex_groups]
    if not mesh.weight_offsets:
        return
    for vertex in range(mesh.vertex_count):
        start, end = mesh.weight_offsets[vertex], mesh.weight_offsets[vertex + 1]
        for at in range(start, end):
            groups[mesh.weight_groups[at]].add(
                [vertex], mesh.weight_values[at], "REPLACE"
            )


def _bools(flags: array) -> array:
    return array("b", [1 if flag else 0 for flag in flags])


def _validate_geometry(mesh: Mesh) -> array:
    """Reject geometry Blender would otherwise repair or discard on import."""
    if mesh.hole_starts:
        raise FormatError("Blender has no faces with holes")
    if not all(math.isfinite(value) for value in mesh.positions):
        raise FormatError("Blender mesh positions must be finite")
    edge_keys = {}
    corner_edges = array("I")
    for index in range(0, len(mesh.edges), 2):
        first, second = mesh.edges[index : index + 2]
        key = (min(first, second), max(first, second))
        if first == second or key in edge_keys:
            raise FormatError("Blender cannot preserve duplicate or self-loop edges")
        edge_keys[key] = index // 2
    offset = 0
    for face in range(mesh.face_count):
        size = mesh.arity or mesh.face_sizes[face]
        vertices = mesh.corners[offset : offset + size]
        if len(set(vertices)) != size:
            raise FormatError("A face contains a repeated vertex")
        for corner in range(size):
            first, second = vertices[corner], vertices[(corner + 1) % size]
            if (min(first, second), max(first, second)) not in edge_keys:
                raise FormatError("A face boundary is missing its explicit edge")
            corner_edges.append(edge_keys[(min(first, second), max(first, second))])
        offset += size
    return corner_edges


def create_mesh(mesh: Mesh, *, name: str) -> bpy.types.Mesh:
    """Create a mesh datablock, leaving scene linking and selection to the caller.

    Blender keeps no stored tangents, so it recalculates them from UVs when needed.
    Shape keys need the object: see apply_shape_keys. A mesh with holes is refused:
    Blender has no such faces and would have to change it.
    On failure the partial datablock is removed. Existing scene data is untouched.
    """
    mesh.validate()
    _validate_attributes_fit(mesh)
    corner_edges = _validate_geometry(mesh)
    result = bpy.data.meshes.new(name)
    try:
        result.vertices.add(mesh.vertex_count)
        result.edges.add(mesh.edge_count)
        result.loops.add(len(mesh.corners))
        result.polygons.add(mesh.face_count)
        result.vertices.foreach_set("co", mesh.positions)
        # Blender's foreach_set expects 32-bit integer buffers, even when the
        # file stores 16-bit IDs. Convert only at this application boundary.
        result.edges.foreach_set("vertices", array("I", mesh.edges))
        result.loops.foreach_set("vertex_index", array("I", mesh.corners))
        starts = array("I")
        sizes = array("I")
        offset = 0
        for face in range(mesh.face_count):
            size = mesh.arity or mesh.face_sizes[face]
            starts.append(offset)
            sizes.append(size)
            offset += size
        result.polygons.foreach_set("loop_start", starts)
        result.polygons.foreach_set("loop_total", sizes)
        result.loops.foreach_set("edge_index", corner_edges)
        # Recalculating edges can reorder endpoints in Blender 4.x. The file
        # already contains every edge, so retain them and set loop links directly.
        result.update()
        _apply_surface(mesh, result)
        result.update()
        _apply_materials(mesh, result)
        return result
    except Exception:
        bpy.data.meshes.remove(result)
        raise
