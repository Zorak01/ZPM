# SPDX-License-Identifier: GPL-3.0-or-later

"""Blender mesh adapter. No UI, filesystem access or operator calls."""

import math
from array import array

import bpy

from .format import FormatError, Mesh


def capture(mesh: bpy.types.Mesh) -> Mesh:
    """Copy the authored mesh with Blender's bulk buffer API.

    Object transforms and modifiers are intentionally outside the mesh codec.
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
    result = Mesh(positions, sizes, corners, edges)
    result.validate()
    _validate_geometry(result)
    return result


def _validate_geometry(mesh: Mesh) -> array:
    """Reject geometry Blender would otherwise repair or discard on import."""
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


def create(mesh: Mesh, *, name: str) -> bpy.types.Mesh:
    """Create a mesh datablock, leaving scene linking and selection to the caller.

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
        return result
    except Exception:
        bpy.data.meshes.remove(result)
        raise
