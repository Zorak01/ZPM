# SPDX-License-Identifier: GPL-3.0-or-later

"""Storing nearby faces, vertices and edges together, with all their data following."""

import math
from array import array
from collections import deque
from dataclasses import replace

from ..container.layout import (
    AXIS_COUNT,
    NO_GRID,
    AttributeDomain,
    bits_to_ordered,
    float_bits,
)
from ..mesh import ColorLayer, Mesh
from ..storage.back_references import split_back_references
from ..storage.column import encode_column
from ..storage.corner_strips import can_use_strips, encode_corner_strips
from .faces import face_edges, face_lists
from .snapping import grid_exponent

_MORTON_BITS = 10
_MORTON_MAX = (1 << _MORTON_BITS) - 1
_UNSEEN_EDGE = float("inf")
_CHANNELS = 4
_CLUSTER_VERTICES = 128


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


def _walk_faces(faces: list, starts: list, materials: list) -> list:
    """Return face indices walking across shared edges, widest first.

    The walk stays inside one material and starts follow material order, so each
    material's faces end up as one run a renderer can draw directly.
    """
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
                    if not visited[neighbour] and (
                        materials[neighbour] == materials[index]
                    ):
                        visited[neighbour] = True
                        waiting.append(neighbour)
    return walked


def _cluster_faces(faces: list, starts: list, materials: list) -> list:
    """Return face indices in small patches, grown across shared edges along the curve.

    Vertices of one patch land near each other in the file, so their blocks need fewer
    bits. It suits dense surfaces, where a wide walk scatters each block across the
    model; reorder_mesh keeps whichever order is smaller.
    """
    faces_of_edge = {}
    for index, face in enumerate(faces):
        for key in face_edges(face):
            faces_of_edge.setdefault(key, []).append(index)
    placed = [False] * len(faces)
    order = []
    for start in starts:
        if placed[start]:
            continue
        placed[start] = True
        waiting = deque([start])
        vertices = set()
        while waiting and len(vertices) < _CLUSTER_VERTICES:
            index = waiting.popleft()
            order.append(index)
            vertices.update(faces[index])
            for key in face_edges(faces[index]):
                for neighbour in faces_of_edge[key]:
                    if (
                        not placed[neighbour]
                        and materials[neighbour] == materials[index]
                    ):
                        placed[neighbour] = True
                        waiting.append(neighbour)
        for index in waiting:
            placed[index] = False
    return order


def _strip_faces(faces: list, starts: list, materials: list) -> list:
    """Return face indices so each usually shares an edge with the one before it.

    Goes deep along the surface, next to the neighbour most surrounded by placed faces,
    so few strips end early. Stays inside one material.
    """
    faces_of_edge = {}
    for index, face in enumerate(faces):
        for key in face_edges(face):
            faces_of_edge.setdefault(key, []).append(index)
    placed = [False] * len(faces)
    order = []

    def touching(index: int) -> int:
        return sum(
            placed[other]
            for key in face_edges(faces[index])
            for other in faces_of_edge[key]
        )

    for start in starts:
        if placed[start]:
            continue
        waiting = [start]
        while waiting:
            index = waiting.pop()
            if placed[index]:
                continue
            placed[index] = True
            order.append(index)
            neighbours = [
                other
                for key in face_edges(faces[index])
                for other in faces_of_edge[key]
                if not placed[other] and materials[other] == materials[index]
            ]
            neighbours.sort(key=touching, reverse=True)
            waiting.extend(reversed(neighbours))
    return order


def _strip_turns(faces: list, order: list) -> dict:
    """Return how far to turn each face so it continues the strip before it.

    A face continues when it holds an edge of the previous one the other way round, and
    then has that edge first. Turning a face keeps its winding.
    """
    turns = {}
    previous = None
    for index in order:
        face = faces[index]
        turn = 0
        if previous is not None:
            shared = {
                (previous[(edge + 1) % len(previous)], previous[edge])
                for edge in range(1, len(previous))
            }
            for start, first in enumerate(face):
                if (first, face[(start + 1) % len(face)]) in shared:
                    turn = start
                    break
        turns[index] = turn
        previous = face[turn:] + face[:turn]
    return turns


def _fits_strips(faces: list) -> bool:
    return can_use_strips([len(face) for face in faces])


def _turned(face: list, turn: int) -> list:
    return face[turn:] + face[:turn]


def _order_cost(mesh: Mesh, faces: list, order: list) -> int:
    """Return what positions and corners would take in this order, stored simply."""
    fits_strips = _fits_strips(faces)
    turns = _strip_turns(faces, order) if fits_strips else {}
    new_ids = {}
    corners = []
    for index in order:
        for vertex in _turned(faces[index], turns.get(index, 0)):
            corners.append(new_ids.setdefault(vertex, len(new_ids)))
    positions = mesh.positions
    exponent = grid_exponent(mesh)
    cost = 0
    for axis in range(AXIS_COUNT):
        values = [positions[vertex * AXIS_COUNT + axis] for vertex in new_ids]
        column = (
            [bits_to_ordered(float_bits(value)) for value in values]
            if exponent == NO_GRID
            else [round(math.ldexp(value, -exponent)) for value in values]
        )
        cost += len(encode_column(column)[0])
    split = split_back_references(corners)
    plain = len(encode_column(split[1])[0]) if split else 0
    if fits_strips:
        sizes = [len(faces[index]) for index in order]
        strips = sum(
            len(section.data) for section in encode_corner_strips(corners, sizes)
        )
        return cost + min(plain, strips) if split else cost + strips
    return cost + plain


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
    return ColorLayer(
        _pick(layer.values, order, _CHANNELS), layer.is_corner, layer.is_srgb
    )


def _reordered_targets(mesh: Mesh, vertex_order: list) -> list:
    return [
        replace(
            target,
            positions=_pick(target.positions, vertex_order, AXIS_COUNT),
            normal_offsets=_pick(target.normal_offsets, vertex_order, AXIS_COUNT),
            tangent_offsets=_pick(target.tangent_offsets, vertex_order, AXIS_COUNT),
        )
        for target in mesh.shape_targets
    ]


def _reordered_attributes(mesh: Mesh, orders: dict) -> dict:
    return {
        name: replace(
            attribute,
            values=_pick(
                attribute.values, orders[attribute.domain], attribute.components
            ),
        )
        if attribute.domain in orders
        else attribute
        for name, attribute in mesh.attributes.items()
    }


def reorder_mesh(mesh: Mesh) -> Mesh:
    """Return the same shape with nearby faces, vertices and edges stored together.

    Vertices are numbered by first use, and edges in the order faces first reach them.
    Winding is kept. A face may start at another corner, to continue a strip. Edges
    are stored lower ID first.
    All surface data follows. A mesh without faces, or with a non-finite position, is
    returned unchanged.
    """
    if not mesh.face_count or not all(math.isfinite(v) for v in mesh.positions):
        return mesh
    source_faces = face_lists(mesh)
    materials = list(mesh.face_materials) or [0] * mesh.face_count
    starts = sorted(_spatial_face_order(mesh, source_faces), key=materials.__getitem__)
    order = min(
        (
            _walk_faces(source_faces, starts, materials),
            _cluster_faces(source_faces, starts, materials),
            _strip_faces(source_faces, starts, materials),
        ),
        key=lambda candidate: _order_cost(mesh, source_faces, candidate),
    )
    turns = _strip_turns(source_faces, order) if _fits_strips(source_faces) else {}
    new_ids = {}
    for index in order:
        for vertex in _turned(source_faces[index], turns.get(index, 0)):
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
        first_corner[index]
        + (position + turns.get(index, 0)) % len(source_faces[index])
        for index in order
        for position in range(len(source_faces[index]))
    ]
    new_corners = {old: new for new, old in enumerate(corner_order)}
    faces = [
        [
            new_ids[vertex]
            for vertex in _turned(source_faces[index], turns.get(index, 0))
        ]
        for index in order
    ]
    edge_order, edges = _reordered_edges(mesh, new_ids, faces)
    return replace(
        mesh,
        positions=_pick(mesh.positions, vertex_order, AXIS_COUNT),
        face_sizes=array("I", [len(face) for face in faces]),
        corners=array("I", [vertex for face in faces for vertex in face]),
        edges=array("I", edges),
        arity=0,
        corner_normals=_pick(mesh.corner_normals, corner_order, AXIS_COUNT),
        tangent_layers={
            name: _pick(layer, corner_order, _CHANNELS)
            for name, layer in mesh.tangent_layers.items()
        },
        uv_seams={
            name: _pick(flags, edge_order) for name, flags in mesh.uv_seams.items()
        },
        sharp_corners=_pick(mesh.sharp_corners, corner_order),
        smoothing_groups=_pick(mesh.smoothing_groups, corner_order),
        crease_chain_vertices=array(
            "I", [new_ids[vertex] for vertex in mesh.crease_chain_vertices]
        ),
        face_materials=_pick(mesh.face_materials, order),
        attributes=_reordered_attributes(
            mesh,
            {
                AttributeDomain.POINT: vertex_order,
                AttributeDomain.EDGE: edge_order,
                AttributeDomain.FACE: order,
                AttributeDomain.CORNER: corner_order,
            },
        ),
        shape_targets=_reordered_targets(mesh, vertex_order),
        hole_starts=array("I", sorted(new_corners[at] for at in mesh.hole_starts)),
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
