# SPDX-License-Identifier: GPL-3.0-or-later

"""Mesh changes the encoder may make to shrink a file without visibly changing it."""

import math
from array import array
from collections import deque
from dataclasses import replace

from .layout import AXIS_COUNT
from .model import Mesh

GRID_BITS = 20
_MORTON_BITS = 10
_MORTON_MAX = (1 << _MORTON_BITS) - 1
_UNSEEN_EDGE = float("inf")


def face_lists(mesh: Mesh) -> list:
    """Return each face as a list of its vertex IDs, in file order."""
    sizes = [mesh.arity] * mesh.face_count if mesh.arity else mesh.face_sizes
    faces = []
    at = 0
    for size in sizes:
        faces.append(list(mesh.corners[at : at + size]))
        at += size
    return faces


def snap_positions(mesh: Mesh) -> Mesh:
    """Return the mesh with positions on a grid of at most 1 / 2^20 of its largest size.

    The largest move is half a step: at most 1 / 2^21 of the model's size. Equal
    positions stay equal, so shared points cannot crack apart. The step is a power of
    two, so snapped values are exact floats and snapping them again changes nothing.
    A mesh with a non-finite position, or of no size, is returned unchanged.
    """
    positions = mesh.positions
    if not positions or not all(math.isfinite(value) for value in positions):
        return mesh
    extent = max(
        max(positions[axis::AXIS_COUNT]) - min(positions[axis::AXIS_COUNT])
        for axis in range(AXIS_COUNT)
    )
    if extent == 0:
        return mesh
    step = math.ldexp(1.0, math.frexp(extent)[1] - 1 - GRID_BITS)
    return replace(
        mesh, positions=array("f", [round(value / step) * step for value in positions])
    )


def _morton_code(x: int, y: int, z: int) -> int:
    code = 0
    for bit in range(_MORTON_BITS):
        code |= (x >> bit & 1) << (bit * AXIS_COUNT)
        code |= (y >> bit & 1) << (bit * AXIS_COUNT + 1)
        code |= (z >> bit & 1) << (bit * AXIS_COUNT + 2)
    return code


def _spatial_face_order(mesh: Mesh, faces: list) -> list:
    """Sort faces along a space-filling curve through their centers."""
    positions = mesh.positions
    low = [min(positions[axis::AXIS_COUNT]) for axis in range(AXIS_COUNT)]
    high = [max(positions[axis::AXIS_COUNT]) for axis in range(AXIS_COUNT)]
    span = [max(high[axis] - low[axis], 1e-30) for axis in range(AXIS_COUNT)]

    def key(face: list) -> int:
        cells = []
        for axis in range(AXIS_COUNT):
            total = sum(positions[vertex * AXIS_COUNT + axis] for vertex in face)
            center = total / len(face)
            cell = int((center - low[axis]) / span[axis] * _MORTON_MAX)
            cells.append(max(0, min(_MORTON_MAX, cell)))
        return _morton_code(*cells)

    return sorted(faces, key=key)


def _walk_faces(faces: list) -> list:
    """Visit faces across shared edges, widest first, so neighbours stay close."""
    faces_of_edge = {}
    for index, face in enumerate(faces):
        for position, vertex in enumerate(face):
            other = face[(position + 1) % len(face)]
            faces_of_edge.setdefault(
                (min(vertex, other), max(vertex, other)), []
            ).append(index)
    visited = [False] * len(faces)
    walked = []
    for start in range(len(faces)):
        if visited[start]:
            continue
        visited[start] = True
        waiting = deque([start])
        while waiting:
            face = faces[waiting.popleft()]
            walked.append(face)
            for position, vertex in enumerate(face):
                other = face[(position + 1) % len(face)]
                key = (min(vertex, other), max(vertex, other))
                for neighbour in faces_of_edge[key]:
                    if not visited[neighbour]:
                        visited[neighbour] = True
                        waiting.append(neighbour)
    return walked


def reorder_mesh(mesh: Mesh) -> Mesh:
    """Return the same shape with nearby faces, vertices and edges stored together.

    Vertices are numbered by first use, and edges in the order faces first reach them.
    Winding and each face's first corner are kept. Edges are stored lower ID first.
    A mesh without faces, or with a non-finite position, is returned unchanged.
    """
    if not mesh.face_count or not all(math.isfinite(v) for v in mesh.positions):
        return mesh
    faces = _walk_faces(_spatial_face_order(mesh, face_lists(mesh)))
    new_ids = {}
    for face in faces:
        for vertex in face:
            new_ids.setdefault(vertex, len(new_ids))
    for vertex in range(mesh.vertex_count):
        new_ids.setdefault(vertex, len(new_ids))
    positions = array("f", [0.0]) * len(mesh.positions)
    for old, new in new_ids.items():
        positions[new * AXIS_COUNT : (new + 1) * AXIS_COUNT] = mesh.positions[
            old * AXIS_COUNT : (old + 1) * AXIS_COUNT
        ]
    faces = [[new_ids[vertex] for vertex in face] for face in faces]
    return Mesh(
        positions,
        array("I", [len(face) for face in faces]),
        array("I", [vertex for face in faces for vertex in face]),
        array("I", _reordered_edges(mesh, new_ids, faces)),
    )


def _reordered_edges(mesh: Mesh, new_ids: dict, faces: list) -> list:
    """Store edges in the order faces first reach them; edges on no face go last."""
    first_use = {}
    for face in faces:
        for position, vertex in enumerate(face):
            other = face[(position + 1) % len(face)]
            key = (min(vertex, other), max(vertex, other))
            first_use.setdefault(key, len(first_use))
    pairs = []
    for at in range(0, len(mesh.edges), 2):
        a, b = new_ids[mesh.edges[at]], new_ids[mesh.edges[at + 1]]
        pairs.append((min(a, b), max(a, b)))
    pairs.sort(key=lambda pair: first_use.get(pair, _UNSEEN_EDGE))
    return [vertex for pair in pairs for vertex in pair]


def _geometry_key(mesh: Mesh) -> tuple:
    bits = mesh.positions.tobytes()
    size = 4 * AXIS_COUNT

    def point(vertex: int) -> bytes:
        return bits[vertex * size : (vertex + 1) * size]

    faces = sorted(tuple(point(v) for v in face) for face in face_lists(mesh))
    edges = sorted(
        tuple(sorted((point(mesh.edges[at]), point(mesh.edges[at + 1]))))
        for at in range(0, len(mesh.edges), 2)
    )
    points = sorted(point(vertex) for vertex in range(mesh.vertex_count))
    return points, faces, edges


def same_geometry(first: Mesh, second: Mesh) -> bool:
    """Return whether two meshes hold the same points, faces and edges in any order.

    Each face must keep its first corner and winding. This walks the whole mesh.
    """
    return _geometry_key(first) == _geometry_key(second)
