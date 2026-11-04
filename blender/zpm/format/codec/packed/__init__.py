# SPDX-License-Identifier: GPL-3.0-or-later

"""The packed form of a mesh's geometry: small, read once from start to end.

Connectivity walks faces across shared edges over positions welded together, so a face
usually costs one symbol per new corner. Every other stream then takes corners in walk
order in one pass, needing no tables of neighbours, which keeps decoding fast:

    walk: connectivity        splits: which vertex each corner uses
    positions: parallelograms normals, uvs: predicted from the decoded geometry

Each predicted value is a rANS number, and each stream picks its own smallest form.
Everything else a mesh holds stays in its usual sections. All prediction uses integers,
or IEEE doubles rounding halves to even, so every reader gets the same integers.
"""

from array import array
from typing import NamedTuple

from ...container.layout import NO_GRID, SectionKind
from ...container.sections import Section, SectionReader
from ...errors import FormatError
from ...mesh import Mesh
from ...processing.faces import face_edges, face_lists
from ...processing.reordering import reorder_by
from ...processing.snapping import PackingGrid
from ..quantize import decode_normal, decode_uv, encode_normal, encode_uv
from .layout import Layout
from .normals import encode_normals, read_normals
from .positions import encode_positions, grid_rows, read_positions
from .splits import encode_splits, read_splits
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
    sections += _normal_sections(ordered, layout, corners, position_rows, grid)
    sections += _uv_sections(ordered, layout, corners, position_rows)
    return PackedMesh(ordered, sections)


def read_geometry(
    sections: SectionReader, info: tuple, uv_names: list
) -> PackedGeometry:
    """Return the mesh fields the packed sections hold, for a mesh with this info.

    Raises FormatError for anything malformed; a reader also catches IndexError,
    KeyError, ValueError and OverflowError, which a damaged stream can raise.
    """
    vertices, faces, corner_count, edge_count, _, arity, exponent, _flags = info
    if exponent == NO_GRID:
        raise FormatError("A packed mesh has no grid")
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
    return PackedGeometry(
        positions,
        [b - a for a, b in zip(starts, starts[1:], strict=False)],
        splits.corners,
        _edges(sections, layout, splits.corners, edge_count),
        _read_normal_values(sections, layout, splits.corners, stored.rows),
        {
            name: _read_uv_values(sections, layer, layout, splits.corners, stored.rows)
            for layer, name in enumerate(uv_names)
        },
    )


def _normal_sections(ordered: Mesh, layout: Layout, corners, rows, grid) -> list:
    if not ordered.corner_normals or grid.normals_from_faces:
        return []
    normals = ordered.corner_normals
    pairs = [encode_normal(*normals[at * 3 : at * 3 + 3]) for at in range(len(corners))]
    data = encode_normals(layout, corners, pairs, rows)
    return [Section(SectionKind.PACKED_NORMALS, data)]


def _uv_sections(ordered: Mesh, layout: Layout, corners, rows) -> list:
    sections = []
    for layer, name in enumerate(sorted(ordered.uv_layers)):
        values = ordered.uv_layers[name]
        pairs = [
            (encode_uv(values[at * 2]), encode_uv(values[at * 2 + 1]))
            for at in range(len(corners))
        ]
        data = encode_uvs(layout, corners, pairs, rows)
        sections.append(Section(SectionKind.PACKED_UVS, data, layer=layer))
    return sections


def _read_normal_values(
    sections: SectionReader, layout: Layout, corners, rows
) -> array:
    if not sections.has(SectionKind.PACKED_NORMALS):
        return array("f")
    pairs = read_normals(
        layout, sections.raw(SectionKind.PACKED_NORMALS), corners, rows
    )
    return array("f", [part for pair in pairs for part in decode_normal(*pair)])


def _read_uv_values(
    sections: SectionReader, layer: int, layout, corners, rows
) -> array:
    data = sections.raw(SectionKind.PACKED_UVS, layer)
    pairs = read_uvs(layout, data, corners, rows)
    return array("f", [decode_uv(part) for pair in pairs for part in pair])


def _edges(
    sections: SectionReader, layout: Layout, corners: list, edge_count: int
) -> list:
    """Return edges as the faces reach them, then any loose ones the file lists."""
    starts = layout.starts
    reached = {}
    for a, b in zip(starts, starts[1:], strict=False):
        for key in face_edges(corners[a:b]):
            reached.setdefault(key, len(reached))
    loose = []
    if sections.has(SectionKind.PACKED_LOOSE_EDGES):
        kind = SectionKind.PACKED_LOOSE_EDGES
        loose = sections.column(kind, sections.count_of(kind))
    edges = [vertex for key in reached for vertex in key] + list(loose)
    if len(edges) != edge_count * 2:
        raise FormatError("Packed edges do not match their count")
    return edges


def _uses_only_face_edges(mesh: Mesh) -> bool:
    """Return whether edges are exactly those the faces reach, then any loose ones."""
    reached = {key for face in face_lists(mesh) for key in face_edges(face)}
    stored = list(zip(mesh.edges[0::2], mesh.edges[1::2], strict=True))
    if len(set(stored)) != len(stored) or any(a > b for a, b in stored):
        return False
    held = set(stored)
    return all(key in held for key in reached)
