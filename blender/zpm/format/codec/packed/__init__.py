# SPDX-License-Identifier: GPL-3.0-or-later

"""The packed form of a mesh's geometry: small, read once from start to end.

Connectivity walks faces across shared edges over positions welded together, so a face
usually costs one symbol per new corner. Every other stream then takes corners in walk
order in one pass, needing no tables of neighbours, which keeps decoding fast:

    walk: connectivity        splits: which vertex each corner uses
    positions: parallelograms normals, uvs: predicted from the decoded geometry
    tangents: predicted from positions, UVs and normals, as MikkTSpace makes them,
        or in their direct sections when those are smaller

Each predicted value is a rANS number, and each stream picks its own smallest form.
Everything else a mesh holds stays in its usual sections. All prediction uses integers,
or IEEE doubles rounding halves to even, so every reader gets the same integers. The
native reader in format/native reads the geometry many times faster when it is built
for the platform; Python reads whatever it does not.
"""

import math
from array import array
from typing import NamedTuple

from ...container.layout import DEFAULT_MAX_BYTES, NO_GRID, SectionKind
from ...container.sections import Section, SectionReader, stored_bytes
from ...errors import FormatError
from ...mesh import Mesh
from ...native import MeshCounts, NativeGeometry, PackedStreams, read_packed_geometry
from ...processing.faces import face_edges, face_lists
from ...processing.reordering import reorder_by
from ...processing.snapping import PackingGrid
from ..quantize import (
    decode_normal,
    decode_tangent,
    decode_uv,
    encode_normal,
    encode_tangent,
    encode_uv,
)
from ..surface import encode_tangents as encode_direct_tangents
from .layout import Layout
from .normals import encode_normals, read_normals
from .positions import STEP, encode_positions, grid_rows, read_positions
from .splits import encode_splits, read_splits
from .tangents import encode_tangents, read_tangents
from .uvs import encode_uvs, read_uvs
from .walk import FaceWalk, read_connectivity


class PackedMesh(NamedTuple):
    """A mesh in packed order, and the sections holding its geometry."""

    mesh: Mesh
    sections: list


class PackedGeometry(NamedTuple):
    """The mesh fields the packed sections hold, as read back."""

    positions: array
    sizes: list
    corners: list
    edges: list
    corner_normals: array
    uv_layers: dict
    tangent_layers: dict


def pack_geometry(grid: PackingGrid) -> PackedMesh | None:
    """Return the snapped mesh in packed order with its sections, or None if it cannot
    be packed: no faces, holes, no grid, or edges that are not exactly those its faces
    reach plus loose ones. Costs seconds on large meshes.
    """
    mesh = grid.mesh
    if not mesh.face_count or mesh.hole_starts or grid.exponent == NO_GRID:
        return None
    rows = grid_rows(mesh.positions, grid.exponent, grid.step)
    weld = {}
    faces = [
        [weld.setdefault(rows[v], len(weld)) for v in face] for face in face_lists(mesh)
    ]
    walk = FaceWalk(faces, list(mesh.face_materials) or [0] * mesh.face_count)
    walk.run()
    ordered = reorder_by(mesh, walk.order, walk.turns)
    if not _uses_only_face_edges(ordered):
        return None
    corners = list(ordered.corners)
    new_rows = grid_rows(ordered.positions, grid.exponent, grid.step)
    position_rows = [None] * (walk.newest + 1)
    for row, source in weld.items():
        if source in walk.ids:
            position_rows[walk.ids[source]] = row
    connectivity = walk.to_bytes()
    starts = [0]
    for face in face_lists(ordered):
        starts.append(starts[-1] + len(face))
    welded = [walk.ids[weld[new_rows[v]]] for v in corners]
    layout = read_connectivity(
        connectivity, len(starts) - 1, starts[1] if walk.is_uniform else 0, len(welded)
    )
    used = max(corners) + 1 if corners else 0
    sections = [
        Section(SectionKind.PACKED_CONNECTIVITY, connectivity),
        Section(SectionKind.PACKED_SPLITS, encode_splits(layout, corners)),
        Section(
            SectionKind.PACKED_POSITIONS,
            encode_positions(layout, position_rows, new_rows[used:], grid.step),
        ),
    ]
    normals = _normal_pairs(ordered, grid)
    if normals:
        data = encode_normals(layout, corners, normals, position_rows)
        sections.append(Section(SectionKind.PACKED_NORMALS, data))
    tangent_sections = []
    for layer, name in enumerate(sorted(ordered.uv_layers)):
        uvs = _uv_pairs(ordered.uv_layers[name])
        data = encode_uvs(layout, corners, uvs, position_rows)
        sections.append(Section(SectionKind.PACKED_UVS, data, layer=layer))
        if name in ordered.tangent_layers:
            tangents = _tangent_pairs(ordered.tangent_layers[name])
            data = encode_tangents(
                layout, corners, tangents, position_rows, normals, uvs
            )
            kind = SectionKind.PACKED_TANGENTS
            tangent_sections.append(Section(kind, data, layer=layer))
    # On a small mesh the stream's tables can cost more than the direct sections.
    direct = encode_direct_tangents(ordered, None)
    smaller = stored_bytes(direct) < stored_bytes(tangent_sections)
    sections += direct if smaller else tangent_sections
    return PackedMesh(ordered, sections)


def read_geometry(
    sections: SectionReader,
    info: tuple,
    uv_names: list,
    *,
    use_native: bool = True,
    max_bytes: int = DEFAULT_MAX_BYTES,
) -> PackedGeometry:
    """Return the mesh fields the packed sections hold, for a mesh with this info.

    Reads natively when it can within max_bytes, else in Python; both return the same.
    use_native=False always reads in Python. Raises FormatError for anything
    malformed; a reader also catches IndexError, KeyError, ValueError and
    OverflowError, which a damaged stream can raise.
    """
    vertices, faces, corner_count, edge_count, _, arity, exponent, _flags = info
    if exponent == NO_GRID:
        raise FormatError("A packed mesh has no grid")
    native = _read_natively(sections, info, uv_names, max_bytes) if use_native else None
    if native is not None:
        return _from_native(sections, native, uv_names, edge_count)
    layout = read_connectivity(
        sections.raw(SectionKind.PACKED_CONNECTIVITY), faces, arity, corner_count
    )
    if len(layout.welded) != corner_count:
        raise FormatError("Packed faces do not fill their corners")
    splits = read_splits(sections.raw(SectionKind.PACKED_SPLITS), layout)
    extra = vertices - len(splits.position_of)
    if extra < 0:
        raise FormatError("A packed mesh has too many vertices")
    stored = read_positions(layout, sections.raw(SectionKind.PACKED_POSITIONS), extra)
    unit = stored.step or 2.0**exponent
    positions = array("f")
    for position in splits.position_of:
        positions.extend(value * unit for value in stored.rows[position])
    for row in stored.extras:
        positions.extend(value * unit for value in row)
    starts = layout.starts
    corners = splits.corners
    normals = []
    if sections.has(SectionKind.PACKED_NORMALS):
        data = sections.raw(SectionKind.PACKED_NORMALS)
        normals = read_normals(layout, data, corners, stored.rows)
    uv_layers, tangent_layers = {}, {}
    for layer, name in enumerate(uv_names):
        data = sections.raw(SectionKind.PACKED_UVS, layer)
        uvs = read_uvs(layout, data, corners, stored.rows)
        uv_layers[name] = array("f", [decode_uv(part) for pair in uvs for part in pair])
        if sections.has(SectionKind.PACKED_TANGENTS, layer):
            data = sections.raw(SectionKind.PACKED_TANGENTS, layer)
            pairs = read_tangents(layout, data, corners, stored.rows, normals, uvs)
            tangent_layers[name] = array(
                "f", [part for pair in pairs for part in decode_tangent(*pair)]
            )
    return PackedGeometry(
        positions,
        [b - a for a, b in zip(starts, starts[1:], strict=False)],
        corners,
        _edges(sections, layout, corners, edge_count),
        array("f", [part for pair in normals for part in decode_normal(*pair)]),
        uv_layers,
        tangent_layers,
    )


def _read_natively(
    sections: SectionReader, info: tuple, uv_names: list, max_bytes: int
) -> NativeGeometry | None:
    """Return the native reader's geometry, or None to read in Python instead."""
    vertices, faces, corner_count, _, _, arity, exponent, _ = info
    kinds = (SectionKind.PACKED_CONNECTIVITY, SectionKind.PACKED_SPLITS)
    layers = range(len(uv_names))
    if not all(sections.has(kind) for kind in kinds) or not all(
        sections.has(SectionKind.PACKED_UVS, layer) for layer in layers
    ):
        return None
    positions = sections.raw(SectionKind.PACKED_POSITIONS)
    if len(positions) <= STEP.size:
        return None
    (step,) = STEP.unpack_from(positions)
    if not (step == 0 or math.isfinite(step) and step > 0):
        return None
    normals = b""
    if sections.has(SectionKind.PACKED_NORMALS):
        normals = sections.raw(SectionKind.PACKED_NORMALS)
    streams = PackedStreams(
        sections.raw(SectionKind.PACKED_CONNECTIVITY),
        sections.raw(SectionKind.PACKED_SPLITS),
        positions[STEP.size :],
        normals,
        [sections.raw(SectionKind.PACKED_UVS, layer) for layer in layers],
    )
    counts = MeshCounts(vertices, faces, corner_count, arity)
    return read_packed_geometry(counts, step or 2.0**exponent, streams, max_bytes)


def _from_native(
    sections: SectionReader, native: NativeGeometry, uv_names: list, edge_count: int
) -> PackedGeometry:
    starts = native.face_starts
    corners = list(native.corners)
    edges = list(native.edges) + _loose_edges(sections)
    if len(edges) != edge_count * 2:
        raise FormatError("Packed edges do not match their count")
    tangent_layers = {}
    tangent_layers_held = [
        layer
        for layer in range(len(uv_names))
        if sections.has(SectionKind.PACKED_TANGENTS, layer)
    ]
    if tangent_layers_held:
        layout = Layout(
            list(native.welded), list(starts), [], native.position_count, []
        )
        rows = _pairs(native.rows, 3)
        normals = _pairs(native.normal_pairs, 2)
        for layer in tangent_layers_held:
            data = sections.raw(SectionKind.PACKED_TANGENTS, layer)
            uvs = _pairs(native.uv_pairs[layer], 2)
            pairs = read_tangents(layout, data, corners, rows, normals, uvs)
            tangent_layers[uv_names[layer]] = array(
                "f", [part for pair in pairs for part in decode_tangent(*pair)]
            )
    return PackedGeometry(
        native.positions,
        [b - a for a, b in zip(starts, starts[1:], strict=False)],
        corners,
        edges,
        native.normals,
        dict(zip(uv_names, native.uvs, strict=True)),
        tangent_layers,
    )


def _pairs(values: array, width: int) -> list:
    """Return flat values as tuples of width each."""
    return list(zip(*(values[part::width] for part in range(width)), strict=True))


def _normal_pairs(ordered: Mesh, grid: PackingGrid) -> list:
    """Return each corner's stored normal, or nothing when no normals are stored."""
    if not ordered.corner_normals or grid.normals_from_faces:
        return []
    normals = ordered.corner_normals
    return [encode_normal(*normals[at : at + 3]) for at in range(0, len(normals), 3)]


def _uv_pairs(values: array) -> list:
    return [
        (encode_uv(values[at]), encode_uv(values[at + 1]))
        for at in range(0, len(values), 2)
    ]


def _tangent_pairs(values: array) -> list:
    return [encode_tangent(*values[at : at + 4]) for at in range(0, len(values), 4)]


def _edges(
    sections: SectionReader, layout: Layout, corners: list, edge_count: int
) -> list:
    """Return edges as the faces reach them, then any loose ones the file lists."""
    starts = layout.starts
    reached = {}
    for a, b in zip(starts, starts[1:], strict=False):
        for key in face_edges(corners[a:b]):
            reached.setdefault(key, len(reached))
    edges = [vertex for key in reached for vertex in key] + _loose_edges(sections)
    if len(edges) != edge_count * 2:
        raise FormatError("Packed edges do not match their count")
    return edges


def _loose_edges(sections: SectionReader) -> list:
    """Return both vertices of each edge on no face, as the file lists them."""
    kind = SectionKind.PACKED_LOOSE_EDGES
    if not sections.has(kind):
        return []
    return list(sections.column(kind, sections.count_of(kind)))


def _uses_only_face_edges(mesh: Mesh) -> bool:
    """Return whether edges are exactly those the faces reach, then any loose ones."""
    reached = {key for face in face_lists(mesh) for key in face_edges(face)}
    stored = list(zip(mesh.edges[0::2], mesh.edges[1::2], strict=True))
    if len(set(stored)) != len(stored) or any(a > b for a, b in stored):
        return False
    held = set(stored)
    return all(key in held for key in reached)
