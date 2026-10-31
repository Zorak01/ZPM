# SPDX-License-Identifier: GPL-3.0-or-later

"""Typed mesh buffers shared by the format code and application adapters."""

from array import array
from dataclasses import dataclass, field

from .container.layout import MIN_RING_CORNERS
from .errors import FormatError


def _empty_floats() -> array:
    return array("f")


def _empty_flags() -> array:
    return array("B")


def _empty_indices() -> array:
    return array("I")


@dataclass(slots=True)
class ColorLayer:
    """One named color layer: four channels per vertex, or per corner when is_corner.

    values holds bytes for RGBA8, or Float32 for RGBA32F. is_srgb says the color
    channels are sRGB encoded, not linear; alpha is always linear.
    """

    values: array
    is_corner: bool
    is_srgb: bool = False

    @property
    def is_float(self) -> bool:
        return self.values.typecode == "f"


@dataclass(slots=True)
class Mesh:
    """Mesh-local geometry, without object transforms or evaluated modifiers.

    positions: flat XYZ Float32 buffer, one triple per vertex.
    face_sizes: one unsigned integer per face; omitted for uniform faces.
    corners: vertex IDs in face order, with each face's winding preserved.
    edges: flat endpoint pairs, including loose edges.
    arity: uniform face size, or zero when face_sizes is present.

    Surface data is optional. An empty buffer means the mesh does not have it:
    corner_normals: flat XYZ Float32, one triple per corner.
    has_custom_normals: normals were authored, so importers must keep them.
    uv_layers: layer name to flat UV Float32, one pair per corner.
    sharp_edges, seam_edges: one 0 or 1 byte per edge.
    flat_faces: one 0 or 1 byte per face.
    edge_creases, vertex_creases: one Float32 from 0 to 1 per edge or vertex.
    uv_seams: UV layer name to one 0 or 1 byte per edge, only for a layer whose seams
        differ from seam_edges.
    tangent_layers: UV layer name to flat XYZW Float32, one per corner; W is the
        bitangent sign.
    sharp_corners: one 0 or 1 byte per corner.
    smoothing_groups: one group per corner; corners in different groups share no
        smooth shading. 0 means no group.
    crease_chain_offsets: where each chain's vertices start, plus one final total.
    crease_chain_vertices: vertex IDs along each chain; neighbours share an edge.
    material_slots: slot names; an empty name is an empty slot.
    face_materials: one slot index per face. Empty means every face uses slot 0.
    hole_starts: rising corner indices, each the first corner of a hole ring. A face's
        corners are its outer ring, then each hole ring. Each ring closes on itself.
    color_layers: layer name to ColorLayer.
    vertex_groups: group names; a weight's group is an index into this list.
    weight_offsets: where each vertex's weights start, plus one final total.
    weight_groups, weight_values: one group index and one Float32 per weight.

    Arrays remain mutable and are owned by the caller. Call validate after edits.
    """

    positions: array
    face_sizes: array
    corners: array
    edges: array
    arity: int = 0
    corner_normals: array = field(default_factory=_empty_floats)
    has_custom_normals: bool = False
    uv_layers: dict = field(default_factory=dict)
    sharp_edges: array = field(default_factory=_empty_flags)
    seam_edges: array = field(default_factory=_empty_flags)
    flat_faces: array = field(default_factory=_empty_flags)
    edge_creases: array = field(default_factory=_empty_floats)
    vertex_creases: array = field(default_factory=_empty_floats)
    uv_seams: dict = field(default_factory=dict)
    tangent_layers: dict = field(default_factory=dict)
    sharp_corners: array = field(default_factory=_empty_flags)
    smoothing_groups: array = field(default_factory=_empty_indices)
    crease_chain_offsets: array = field(default_factory=_empty_indices)
    crease_chain_vertices: array = field(default_factory=_empty_indices)
    material_slots: list = field(default_factory=list)
    face_materials: array = field(default_factory=_empty_indices)
    hole_starts: array = field(default_factory=_empty_indices)
    color_layers: dict = field(default_factory=dict)
    vertex_groups: list = field(default_factory=list)
    weight_offsets: array = field(default_factory=_empty_indices)
    weight_groups: array = field(default_factory=_empty_indices)
    weight_values: array = field(default_factory=_empty_floats)

    @property
    def vertex_count(self) -> int:
        return len(self.positions) // 3

    @property
    def face_count(self) -> int:
        return len(self.corners) // self.arity if self.arity else len(self.face_sizes)

    @property
    def edge_count(self) -> int:
        return len(self.edges) // 2

    @property
    def buffer_bytes(self) -> int:
        """Array payload only, excluding Python objects and input files."""
        return sum(
            len(buffer) * buffer.itemsize
            for buffer in (self.positions, self.face_sizes, self.corners, self.edges)
        )

    def validate(self) -> None:
        """Validate structure and index ranges without allocating expanded faces."""
        if self.positions.typecode != "f" or self.positions.itemsize != 4:
            raise FormatError("Positions must be a Float32 array")
        if len(self.positions) % 3:
            raise FormatError("Positions must contain complete XYZ triples")
        for buffer in (self.face_sizes, self.corners, self.edges):
            if buffer.typecode not in ("H", "I") or buffer.itemsize not in (2, 4):
                raise FormatError("Topology must use unsigned 16- or 32-bit arrays")
        if len(self.edges) % 2:
            raise FormatError("Edges must contain endpoint pairs")
        if self.arity:
            if self.arity < 3 or self.face_sizes or len(self.corners) % self.arity:
                raise FormatError("Invalid uniform face layout")
        elif any(size < 3 for size in self.face_sizes):
            raise FormatError("Faces must contain at least three corners")
        elif sum(self.face_sizes) != len(self.corners):
            raise FormatError("Face sizes do not match the corner count")
        if self.vertex_count > 0xFFFFFFFF:
            raise FormatError("Vertex count exceeds the format limit")
        for buffer in (self.corners, self.edges):
            if buffer and max(buffer) >= self.vertex_count:
                raise FormatError("Topology references a missing vertex")
        self._validate_surface()

    def _validate_surface(self) -> None:
        expected = (
            ("Corner normals", self.corner_normals, len(self.corners) * 3),
            ("Sharp edges", self.sharp_edges, self.edge_count),
            ("Seam edges", self.seam_edges, self.edge_count),
            ("Flat faces", self.flat_faces, self.face_count),
            ("Edge creases", self.edge_creases, self.edge_count),
            ("Vertex creases", self.vertex_creases, self.vertex_count),
            ("Sharp corners", self.sharp_corners, len(self.corners)),
            ("Smoothing groups", self.smoothing_groups, len(self.corners)),
            ("Face materials", self.face_materials, self.face_count),
            ("Weight offsets", self.weight_offsets, self.vertex_count + 1),
        )
        for name, buffer, length in expected:
            if buffer and len(buffer) != length:
                raise FormatError(f"{name} do not match the mesh")
        for name, layer in self.uv_layers.items():
            if len(layer) != len(self.corners) * 2:
                raise FormatError(f"UV layer {name} does not match the corner count")
        for name, flags in self.uv_seams.items():
            if name not in self.uv_layers or len(flags) != self.edge_count:
                raise FormatError(f"Seams of UV layer {name} do not match the mesh")
        for name, layer in self.tangent_layers.items():
            if name not in self.uv_layers or len(layer) != len(self.corners) * 4:
                raise FormatError(f"Tangents of UV layer {name} do not match the mesh")
        for name, layer in self.color_layers.items():
            owners = len(self.corners) if layer.is_corner else self.vertex_count
            if len(layer.values) != owners * 4:
                raise FormatError(f"Color layer {name} does not match the mesh")
        if self.face_materials and max(self.face_materials) >= len(self.material_slots):
            raise FormatError("A face references a missing material slot")
        self._validate_weights()
        self._validate_crease_chains()
        self._validate_holes()

    def _validate_crease_chains(self) -> None:
        offsets, vertices = self.crease_chain_offsets, self.crease_chain_vertices
        if not offsets:
            if vertices:
                raise FormatError("Crease chains have no offsets")
            return
        if offsets[0] != 0 or offsets[-1] != len(vertices) or len(offsets) < 2:
            raise FormatError("Crease chain offsets do not cover every vertex")
        edges = {
            frozenset(self.edges[at : at + 2]) for at in range(0, len(self.edges), 2)
        }
        for start, end in zip(offsets, offsets[1:], strict=False):
            if end < start + 2:
                raise FormatError("A crease chain needs at least two vertices")
            for at in range(start, end - 1):
                if frozenset(vertices[at : at + 2]) not in edges:
                    raise FormatError("A crease chain follows a missing edge")

    def _validate_holes(self) -> None:
        if not self.hole_starts:
            return
        holes = self.hole_starts
        sizes = [self.arity] * self.face_count if self.arity else self.face_sizes
        next_hole = 0
        at = 0
        for size in sizes:
            end = at + size
            ring = at
            while next_hole < len(holes) and holes[next_hole] < end:
                if holes[next_hole] - ring < MIN_RING_CORNERS:
                    raise FormatError("A face ring has too few corners")
                ring = holes[next_hole]
                next_hole += 1
            if end - ring < MIN_RING_CORNERS:
                raise FormatError("A face ring has too few corners")
            at = end
        if next_hole != len(holes):
            raise FormatError("A hole starts outside its face")

    def _validate_weights(self) -> None:
        if len(self.weight_groups) != len(self.weight_values):
            raise FormatError("Weight groups and values do not match")
        if self.weight_offsets:
            offsets = self.weight_offsets
            if offsets[0] != 0 or offsets[-1] != len(self.weight_values):
                raise FormatError("Weight offsets do not cover every weight")
            if any(a > b for a, b in zip(offsets, offsets[1:], strict=False)):
                raise FormatError("Weight offsets must rise")
        elif self.weight_values:
            raise FormatError("Weights have no offsets")
        if self.weight_groups and max(self.weight_groups) >= len(self.vertex_groups):
            raise FormatError("A weight references a missing vertex group")
