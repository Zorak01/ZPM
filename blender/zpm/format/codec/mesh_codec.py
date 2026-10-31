# SPDX-License-Identifier: GPL-3.0-or-later

"""Whole files to and from Mesh. Each kind of data lives in its own module here."""

from array import array

from ..container.layout import (
    DEFAULT_MAX_BYTES,
    MAX_UNIFORM_ARITY,
    MESH_INFO,
    UINT32_MAX,
    AttributeDomain,
    MeshFlags,
    SectionKind,
)
from ..container.sections import Section, SectionReader, write_file
from ..errors import FormatError
from ..mesh import Mesh
from ..processing.faces import corner_normals_from_faces, face_lists
from ..processing.reordering import reorder_mesh
from ..processing.snapping import grid_exponent, normals_match_faces, snap_mesh
from .attributes import encode_attributes, read_attributes
from .colors import encode_colors, read_colors
from .corners import (
    encode_corners,
    encode_face_starts,
    encode_holes,
    read_corners,
    read_face_sizes,
    read_holes,
)
from .edges import encode_edge_endpoints, encode_edges, read_edges
from .materials import encode_materials, read_materials
from .positions import encode_positions, read_positions
from .shapes import encode_shapes, read_shapes
from .space import encode_space, read_space
from .surface import (
    encode_marks,
    encode_normals,
    encode_tangent_method,
    encode_tangents,
    encode_uv_layers,
    encode_uv_seams,
    read_marks,
    read_normals,
    read_tangent_method,
    read_tangents,
    read_uv_layers,
    read_uv_seams,
)
from .weights import encode_weights, read_weights

_SHORT_ID_LIMIT = 65536


def encode(mesh: Mesh) -> bytes:
    """Return the smallest file bytes for a validated mesh.

    Values snap as snap_mesh describes. Faces, vertices and edges are reordered only
    when that makes the file smaller. Costs seconds on large meshes; reading the
    result costs nothing extra.
    """
    mesh.validate()
    snapped = snap_mesh(mesh)
    as_stored = _encode_mesh(snapped)
    reordered = _encode_mesh(reorder_mesh(snapped))
    return reordered if len(reordered) < len(as_stored) else as_stored


def _uniform_arity(sizes: list) -> int:
    if sizes and sizes[0] <= MAX_UNIFORM_ARITY and len(set(sizes)) == 1:
        return sizes[0]
    return 0


def _encode_mesh(mesh: Mesh) -> bytes:
    corners = list(mesh.corners)
    edges = list(mesh.edges)
    face_count = mesh.face_count
    sizes = [mesh.arity] * face_count if mesh.arity else list(mesh.face_sizes)
    vertex_count = mesh.vertex_count
    if max(vertex_count, face_count, len(corners), mesh.edge_count) > UINT32_MAX:
        raise FormatError("The mesh exceeds the file's 32-bit size limit")
    sections = []
    unique_count = 0
    exponent = grid_exponent(mesh)
    if vertex_count:
        positions, unique_count = encode_positions(mesh, exponent)
        sections += positions
    arity = _uniform_arity(sizes) if corners else 0
    if corners:
        sections += encode_corners(corners, face_count, arity)
        if not arity:
            sections.append(encode_face_starts(sizes, len(corners)))
        sections += encode_holes(mesh.hole_starts)
    if edges:
        # An edge reference means "this corner and the next around its face", which
        # holds only when a face is one ring.
        sections += (
            encode_edge_endpoints(edges)
            if mesh.hole_starts
            else encode_edges(edges, corners, sizes)
        )
    flags = MeshFlags.NONE
    if mesh.has_custom_normals:
        flags |= MeshFlags.CUSTOM_NORMALS
    if normals_match_faces(mesh):
        flags |= MeshFlags.NORMALS_FROM_FACES
    elif mesh.corner_normals:
        sections += encode_normals(mesh)
    sections += encode_space(mesh)
    sections += encode_tangents(mesh)
    sections += encode_tangent_method(mesh)
    sections += encode_attributes(mesh)
    sections += encode_shapes(mesh, exponent)
    sections += encode_uv_layers(mesh)
    sections += encode_uv_seams(mesh)
    sections += encode_materials(mesh)
    sections += encode_colors(mesh)
    sections += encode_marks(mesh)
    sections += encode_weights(mesh)
    info = MESH_INFO.pack(
        vertex_count,
        face_count,
        len(corners),
        mesh.edge_count,
        unique_count,
        arity,
        exponent,
        flags,
    )
    sections.append(Section(SectionKind.MESH_INFO, info))
    return write_file(sections)


def decode(data: bytes, *, max_bytes: int = DEFAULT_MAX_BYTES) -> Mesh:
    """Expand one file into a Mesh, with strict length, range and size checks.

    For editing tools: runtime readers index the file instead. max_bytes limits both
    file size and the main expanded buffers. It is not a peak process-memory limit.
    """
    if len(data) > max_bytes:
        raise FormatError("File exceeds the configured size limit")
    sections = SectionReader(data)
    info = sections.raw(SectionKind.MESH_INFO)
    if len(info) != MESH_INFO.size:
        raise FormatError("Mesh info has the wrong size")
    (
        vertices,
        faces,
        corner_count,
        edge_count,
        unique,
        arity,
        exponent,
        flags,
    ) = MESH_INFO.unpack(info)
    width = 2 if vertices <= _SHORT_ID_LIMIT else 4
    expanded = vertices * 12 + (corner_count + edge_count * 2) * width + faces * 4
    if expanded > max_bytes:
        raise FormatError("Decoded mesh exceeds the configured size limit")
    if unique > vertices or (faces and not corner_count):
        raise FormatError("Invalid element counts")
    if arity and arity * faces != corner_count:
        raise FormatError("Invalid uniform face layout")

    positions = array("f", read_positions(sections, vertices, unique, exponent))
    corners = read_corners(sections, corner_count, faces, arity)
    mixed = corner_count and not arity
    stored_sizes = read_face_sizes(sections, corner_count, faces) if mixed else []
    sizes = [arity] * faces if arity else stored_sizes
    holes = read_holes(sections)
    if holes and edge_count and not sections.has(SectionKind.EDGE_FIRST):
        raise FormatError("Edges in a mesh with holes must store their endpoints")
    edges = read_edges(sections, edge_count, corners, sizes)
    code = "H" if width == 2 else "I"
    sizes = {
        AttributeDomain.POINT: vertices,
        AttributeDomain.EDGE: edge_count,
        AttributeDomain.FACE: faces,
        AttributeDomain.CORNER: corner_count,
        AttributeDomain.MESH: 1,
    }
    try:
        mesh = Mesh(
            positions,
            array("I", stored_sizes),
            array(code, corners),
            array(code, edges),
            arity=arity,
            corner_normals=read_normals(sections, corners),
            has_custom_normals=bool(MeshFlags(flags) & MeshFlags.CUSTOM_NORMALS),
            uv_layers=read_uv_layers(sections, corners),
            uv_seams=read_uv_seams(sections, edge_count),
            tangent_layers=read_tangents(sections, corners),
            hole_starts=holes,
            space=read_space(sections),
            tangent_method=read_tangent_method(sections),
            attributes=read_attributes(sections, sizes),
            shape_targets=read_shapes(sections, positions, exponent),
            color_layers=read_colors(sections, corners),
            **read_materials(sections, faces),
            **read_marks(sections, edge_count, faces, vertices, corner_count),
            **read_weights(sections, vertices),
        )
    except OverflowError as error:
        raise FormatError("Topology references a missing vertex") from error
    if MeshFlags(flags) & MeshFlags.NORMALS_FROM_FACES:
        derived = corner_normals_from_faces(positions, face_lists(mesh))
        if derived is None:
            raise FormatError("A face with no area cannot supply a normal")
        mesh.corner_normals = derived
    mesh.validate()
    return mesh
