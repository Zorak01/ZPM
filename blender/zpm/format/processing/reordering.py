# SPDX-License-Identifier: GPL-3.0-or-later

"""Storing nearby faces, vertices and edges together, with all their data following."""

import math
from array import array
from collections import deque
from dataclasses import replace

from ..container.layout import AXIS_COUNT
from ..mesh import ColorLayer, Mesh
from .faces import face_edges, face_lists

_MORTON_BITS = 10
_MORTON_MAX = (1 << _MORTON_BITS) - 1
_UNSEEN_EDGE = float("inf")
_CHANNELS = 4


def _morton_code(x: int, y: int, z: int) -> int:
    code = 0
    for bit in range(_MORTON_BITS):
        code |= (x >> bit & 1) << (bit * AXIS_COUNT)
        code |= (y >> bit & 1) << (bit * AXIS_COUNT + 1)
        code |= (z >> bit & 1) << (bit * AXIS_COUNT + 2)
    return code


def _spatial_face_order(mesh: Mesh, faces: list) -> list:
    """Return face indices along a space-filling curve through their centers."""
    positions = mesh.positions
    low = [min(positions[axis::AXIS_COUNT]) for axis in range(AXIS_COUNT)]
    high = [max(positions[axis::AXIS_COUNT]) for axis in range(AXIS_COUNT)]
    span = [max(high[axis] - low[axis], 1e-30) for axis in range(AXIS_COUNT)]

    def key(index: int) -> int:
        face = faces[index]
        cells = []
        for axis in range(AXIS_COUNT):
            total = sum(positions[vertex * AXIS_COUNT + axis] for vertex in face)
            center = total / len(face)
            cell = int((center - low[axis]) / span[axis] * _MORTON_MAX)
            cells.append(max(0, min(_MORTON_MAX, cell)))
        return _morton_code(*cells)

    return sorted(range(len(faces)), key=key)


def _walk_faces(faces: list, starts: list) -> list:
    """Return face indices walking across shared edges, widest first."""
    faces_of_edge = {}
    for index, face in enumerate(faces):
        for key in face_edges(face):
            faces_of_edge.setdefault(key, []).append(index)
    visited = [False] * len(faces)
    walked = []
    for start in starts:
        if visited[start]:
            continue
        visited[start] = True
        waiting = deque([start])
        while waiting:
            index = waiting.popleft()
            walked.append(index)
            for key in face_edges(faces[index]):
                for neighbour in faces_of_edge[key]:
                    if not visited[neighbour]:
                        visited[neighbour] = True
                        waiting.append(neighbour)
    return walked


def _pick(buffer: array, order: list, width: int = 1) -> array:
    """Return a buffer of groups of width values, rearranged by order."""
    if not buffer:
        return buffer
    return array(
        buffer.typecode,
        [buffer[i * width + part] for i in order for part in range(width)],
    )


def _reordered_edges(mesh: Mesh, new_ids: dict, faces: list) -> tuple:
    """Return (old edge order, new pairs), in first-reached order, loose edges last."""
    first_use = {}
    for face in faces:
        for key in face_edges(face):
            first_use.setdefault(key, len(first_use))
    pairs = []
    for at in range(0, len(mesh.edges), 2):
        a, b = new_ids[mesh.edges[at]], new_ids[mesh.edges[at + 1]]
        pairs.append((min(a, b), max(a, b)))
    order = sorted(
        range(len(pairs)), key=lambda i: first_use.get(pairs[i], _UNSEEN_EDGE)
    )
    return order, [vertex for i in order for vertex in pairs[i]]


def _reordered_weights(mesh: Mesh, vertex_order: list) -> dict:
    if not mesh.weight_offsets:
        return {}
    offsets = array("I", [0])
    groups = array("I")
    values = array("f")
    for vertex in vertex_order:
        start, end = mesh.weight_offsets[vertex], mesh.weight_offsets[vertex + 1]
        groups.extend(mesh.weight_groups[start:end])
        values.extend(mesh.weight_values[start:end])
        offsets.append(len(values))
    return {"weight_offsets": offsets, "weight_groups": groups, "weight_values": values}


def _reordered_color(layer: ColorLayer, corner_order, vertex_order) -> ColorLayer:
    order = corner_order if layer.is_corner else vertex_order
    return ColorLayer(_pick(layer.values, order, _CHANNELS), layer.is_corner)


def reorder_mesh(mesh: Mesh) -> Mesh:
    """Return the same shape with nearby faces, vertices and edges stored together.

    Vertices are numbered by first use, and edges in the order faces first reach them.
    Winding and each face's first corner are kept. Edges are stored lower ID first.
    All surface data follows. A mesh without faces, or with a non-finite position, is
    returned unchanged.
    """
    if not mesh.face_count or not all(math.isfinite(v) for v in mesh.positions):
        return mesh
    source_faces = face_lists(mesh)
    order = _walk_faces(source_faces, _spatial_face_order(mesh, source_faces))
    new_ids = {}
    for index in order:
        for vertex in source_faces[index]:
            new_ids.setdefault(vertex, len(new_ids))
    for vertex in range(mesh.vertex_count):
        new_ids.setdefault(vertex, len(new_ids))
    vertex_order = sorted(new_ids, key=new_ids.get)
    first_corner = []
    at = 0
    for face in source_faces:
        first_corner.append(at)
        at += len(face)
    corner_order = [
        first_corner[index] + position
        for index in order
        for position in range(len(source_faces[index]))
    ]
    faces = [[new_ids[vertex] for vertex in source_faces[index]] for index in order]
    edge_order, edges = _reordered_edges(mesh, new_ids, faces)
    return replace(
        mesh,
        positions=_pick(mesh.positions, vertex_order, AXIS_COUNT),
        face_sizes=array("I", [len(face) for face in faces]),
        corners=array("I", [vertex for face in faces for vertex in face]),
        edges=array("I", edges),
        arity=0,
        corner_normals=_pick(mesh.corner_normals, corner_order, AXIS_COUNT),
        corner_tangents=_pick(mesh.corner_tangents, corner_order, _CHANNELS),
        uv_layers={
            name: _pick(layer, corner_order, 2)
            for name, layer in mesh.uv_layers.items()
        },
        color_layers={
            name: _reordered_color(layer, corner_order, vertex_order)
            for name, layer in mesh.color_layers.items()
        },
        sharp_edges=_pick(mesh.sharp_edges, edge_order),
        seam_edges=_pick(mesh.seam_edges, edge_order),
        flat_faces=_pick(mesh.flat_faces, order),
        edge_creases=_pick(mesh.edge_creases, edge_order),
        vertex_creases=_pick(mesh.vertex_creases, vertex_order),
        **_reordered_weights(mesh, vertex_order),
    )
