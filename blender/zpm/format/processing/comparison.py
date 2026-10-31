# SPDX-License-Identifier: GPL-3.0-or-later

"""Checking two meshes hold the same data, whatever order they store it in."""

from array import array

from ..container.layout import AXIS_COUNT
from ..mesh import Mesh
from .faces import face_lists

_CHANNELS = 4


def _group(buffer: array, index: int, width: int) -> tuple:
    return tuple(buffer[index * width : (index + 1) * width]) if buffer else ()


def _weights_of(mesh: Mesh, vertex: int) -> tuple:
    if not mesh.weight_offsets:
        return ()
    start, end = mesh.weight_offsets[vertex], mesh.weight_offsets[vertex + 1]
    return tuple(
        (mesh.vertex_groups[mesh.weight_groups[at]], mesh.weight_values[at])
        for at in range(start, end)
    )


def _key(mesh: Mesh, compare_normals: bool) -> tuple:
    bits = mesh.positions.tobytes()
    size = 4 * AXIS_COUNT
    corner_colors = [
        mesh.color_layers[name]
        for name in sorted(mesh.color_layers)
        if mesh.color_layers[name].is_corner
    ]
    point_colors = [
        mesh.color_layers[name]
        for name in sorted(mesh.color_layers)
        if not mesh.color_layers[name].is_corner
    ]

    def point(vertex: int) -> tuple:
        return (
            bits[vertex * size : (vertex + 1) * size],
            _group(mesh.vertex_creases, vertex, 1),
            _weights_of(mesh, vertex),
            tuple(_group(layer.values, vertex, _CHANNELS) for layer in point_colors),
        )

    uv_names = sorted(mesh.uv_layers)
    faces = []
    corner = 0
    for index, face in enumerate(face_lists(mesh)):
        rows = []
        for vertex in face:
            rows.append(
                (
                    point(vertex),
                    _group(mesh.corner_normals, corner, AXIS_COUNT)
                    if compare_normals
                    else (),
                    _group(mesh.corner_tangents, corner, _CHANNELS)
                    if compare_normals
                    else (),
                    tuple(_group(mesh.uv_layers[n], corner, 2) for n in uv_names),
                    tuple(_group(c.values, corner, _CHANNELS) for c in corner_colors),
                )
            )
            corner += 1
        faces.append((tuple(rows), _group(mesh.flat_faces, index, 1)))
    edges = []
    for edge in range(mesh.edge_count):
        ends = sorted((point(mesh.edges[edge * 2]), point(mesh.edges[edge * 2 + 1])))
        edges.append(
            (
                tuple(ends),
                _group(mesh.sharp_edges, edge, 1),
                _group(mesh.seam_edges, edge, 1),
                _group(mesh.edge_creases, edge, 1),
            )
        )
    points = sorted(point(vertex) for vertex in range(mesh.vertex_count))
    return (
        points,
        sorted(faces),
        sorted(edges),
        uv_names,
        sorted(mesh.color_layers),
        sorted(mesh.vertex_groups),
    )


def same_geometry(first: Mesh, second: Mesh, *, compare_normals: bool = True) -> bool:
    """Return whether two meshes hold the same data in any order.

    Compares points, faces with their winding and first corner, edges and all surface
    data. Pass compare_normals=False when one side recalculated normals and tangents.
    Walks the whole mesh.
    """
    return _key(first, compare_normals) == _key(second, compare_normals)
