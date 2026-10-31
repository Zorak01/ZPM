# SPDX-License-Identifier: GPL-3.0-or-later

"""Mesh changes the encoder may make to shrink a file without visibly changing it."""

import math
from array import array
from collections import deque
from dataclasses import replace

from .layout import AXIS_COUNT, MAX_GRID_VALUE, NO_GRID
from .model import Mesh
from .surface_values import (
    decode_crease,
    decode_normal,
    decode_uv,
    encode_crease,
    encode_normal,
    encode_uv,
)

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


def grid_exponent(positions: array) -> int:
    """Return E where the position grid step is 2^E, or NO_GRID if none fits.

    A mesh has no grid when a position is not finite, when it has no size, or when
    its values sit so far from the origin that grid integers would not fit a Float32.
    """
    if not positions or not all(math.isfinite(value) for value in positions):
        return NO_GRID
    extent = max(
        max(positions[axis::AXIS_COUNT]) - min(positions[axis::AXIS_COUNT])
        for axis in range(AXIS_COUNT)
    )
    if extent == 0:
        return NO_GRID
    exponent = math.frexp(extent)[1] - 1 - GRID_BITS
    largest = max(abs(value) for value in positions)
    return NO_GRID if math.ldexp(largest, -exponent) >= MAX_GRID_VALUE else exponent


def snap_mesh(mesh: Mesh) -> Mesh:
    """Return the mesh as the file will hold it.

    Positions move to a grid of at most 1 / 2^20 of the model's largest size, so no
    point moves more than 1 / 2^21 of it. Equal positions stay equal, so shared points
    cannot crack apart. The step is a power of two, so snapping again changes nothing.
    Normals become 12-bit directions, UVs a 1 / 2^20 grid and creases 16 bits.
    """
    changes = {}
    exponent = grid_exponent(mesh.positions)
    if exponent != NO_GRID:
        step = math.ldexp(1.0, exponent)
        changes["positions"] = array(
            "f", [round(value / step) * step for value in mesh.positions]
        )
    if mesh.corner_normals:
        normals = mesh.corner_normals
        changes["corner_normals"] = array(
            "f",
            [
                part
                for at in range(0, len(normals), AXIS_COUNT)
                for part in decode_normal(
                    *encode_normal(normals[at], normals[at + 1], normals[at + 2])
                )
            ],
        )
    changes["uv_layers"] = {
        name: array("f", [decode_uv(encode_uv(value)) for value in layer])
        for name, layer in mesh.uv_layers.items()
    }
    for name in ("edge_creases", "vertex_creases"):
        changes[name] = array(
            "f", [decode_crease(encode_crease(v)) for v in getattr(mesh, name)]
        )
    return replace(mesh, **changes)


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


def _face_edges(face: list):
    for position, vertex in enumerate(face):
        other = face[(position + 1) % len(face)]
        yield (min(vertex, other), max(vertex, other))


def _walk_faces(faces: list, starts: list) -> list:
    """Return face indices walking across shared edges, widest first."""
    faces_of_edge = {}
    for index, face in enumerate(faces):
        for key in _face_edges(face):
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
            for key in _face_edges(faces[index]):
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
        uv_layers={
            name: _pick(layer, corner_order, 2)
            for name, layer in mesh.uv_layers.items()
        },
        sharp_edges=_pick(mesh.sharp_edges, edge_order),
        seam_edges=_pick(mesh.seam_edges, edge_order),
        flat_faces=_pick(mesh.flat_faces, order),
        edge_creases=_pick(mesh.edge_creases, edge_order),
        vertex_creases=_pick(mesh.vertex_creases, vertex_order),
    )


def _reordered_edges(mesh: Mesh, new_ids: dict, faces: list) -> tuple:
    """Return (old edge order, new pairs), in first-reached order, loose edges last."""
    first_use = {}
    for face in faces:
        for key in _face_edges(face):
            first_use.setdefault(key, len(first_use))
    pairs = []
    for at in range(0, len(mesh.edges), 2):
        a, b = new_ids[mesh.edges[at]], new_ids[mesh.edges[at + 1]]
        pairs.append((min(a, b), max(a, b)))
    order = sorted(
        range(len(pairs)), key=lambda i: first_use.get(pairs[i], _UNSEEN_EDGE)
    )
    return order, [vertex for i in order for vertex in pairs[i]]


def _group(buffer: array, index: int, width: int) -> tuple:
    return tuple(buffer[index * width : (index + 1) * width]) if buffer else ()


def _surface_key(mesh: Mesh, compare_normals: bool) -> tuple:
    bits = mesh.positions.tobytes()
    size = 4 * AXIS_COUNT

    def point(vertex: int) -> tuple:
        crease = _group(mesh.vertex_creases, vertex, 1)
        return bits[vertex * size : (vertex + 1) * size], crease

    faces = []
    corner = 0
    for index, face in enumerate(face_lists(mesh)):
        rows = []
        for vertex in face:
            normal = _group(mesh.corner_normals, corner, 3) if compare_normals else ()
            uvs = tuple(
                _group(mesh.uv_layers[name], corner, 2)
                for name in sorted(mesh.uv_layers)
            )
            rows.append((point(vertex), normal, uvs))
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
    return points, sorted(faces), sorted(edges), sorted(mesh.uv_layers)


def same_geometry(first: Mesh, second: Mesh, *, compare_normals: bool = True) -> bool:
    """Return whether two meshes hold the same data in any order.

    Compares points, faces with their winding and first corner, edges and all surface
    data. Pass compare_normals=False when one side recalculated them. Walks the mesh.
    """
    return _surface_key(first, compare_normals) == _surface_key(second, compare_normals)
