# SPDX-License-Identifier: GPL-3.0-or-later

"""Blender mesh adapter. No UI, filesystem access or operator calls."""

import math
from array import array

import bpy

from ..format import ColorLayer, FormatError, Mesh

_MAX_TANGENT_FACE_SIZE = 4
_BYTE_MAX = 255
# Blender has no home for these, so they live under these attribute and property names.
_SHARP_CORNER_ATTRIBUTE = "zpm_sharp_corner"
_SMOOTHING_GROUP_ATTRIBUTE = "zpm_smoothing_group"
_UV_SEAM_ATTRIBUTE_PREFIX = "zpm_seam:"
_CHAIN_OFFSETS_PROPERTY = "zpm_crease_chain_offsets"
_CHAIN_VERTICES_PROPERTY = "zpm_crease_chain_vertices"


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
    return {
        **_capture_materials(mesh),
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
        "tangent_layers": _capture_tangents(mesh),
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
    """Fill slots, reusing a material of the same name. Materials hold no data yet."""
    if mesh.face_materials:
        indices = array("i", mesh.face_materials)
        result.polygons.foreach_set("material_index", indices)
    for name in mesh.material_slots:
        material = None
        if name:
            material = bpy.data.materials.get(name) or bpy.data.materials.new(name)
        result.materials.append(material)


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
    A mesh with holes is refused: Blender has no such faces and would have to change it.
    On failure the partial datablock is removed. Existing scene data is untouched.
    """
    mesh.validate()
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
