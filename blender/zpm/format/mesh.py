# SPDX-License-Identifier: GPL-3.0-or-later

"""Typed mesh buffers shared by the format code and application adapters."""

from array import array
from dataclasses import dataclass, field

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

    values holds bytes for RGBA8, or Float32 for RGBA32F.
    """

    values: array
    is_corner: bool

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
    corner_tangents: flat XYZW Float32, one per corner; W is the bitangent sign.
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
    corner_tangents: array = field(default_factory=_empty_floats)
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
            ("Corner tangents", self.corner_tangents, len(self.corners) * 4),
            ("Weight offsets", self.weight_offsets, self.vertex_count + 1),
        )
        for name, buffer, length in expected:
            if buffer and len(buffer) != length:
                raise FormatError(f"{name} do not match the mesh")
        for name, layer in self.uv_layers.items():
            if len(layer) != len(self.corners) * 2:
                raise FormatError(f"UV layer {name} does not match the corner count")
        for name, layer in self.color_layers.items():
            owners = len(self.corners) if layer.is_corner else self.vertex_count
            if len(layer.values) != owners * 4:
                raise FormatError(f"Color layer {name} does not match the mesh")
        self._validate_weights()

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
