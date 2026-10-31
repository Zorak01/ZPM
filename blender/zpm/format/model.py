# SPDX-License-Identifier: GPL-3.0-or-later

"""Typed mesh buffers shared by the codec and application adapters."""

from array import array
from dataclasses import dataclass


class FormatError(ValueError):
    """A mesh or packet does not satisfy the supported format contract."""


@dataclass(slots=True)
class Mesh:
    """Mesh-local geometry, without object transforms or evaluated modifiers.

    positions: flat XYZ Float32 buffer in source vertex order.
    face_sizes: one unsigned integer per face; omitted for uniform faces.
    corners: vertex IDs in face order, with each face's winding preserved.
    edges: flat endpoint pairs in source edge order, including loose edges.
    arity: uniform face size, or zero when face_sizes is present.

    Arrays remain mutable and are owned by the caller. Call validate after edits.
    Float bit patterns, including signed zero, are not quantized or normalized.
    """

    positions: array
    face_sizes: array
    corners: array
    edges: array
    arity: int = 0

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
        """Array payload only, excluding Python objects and input packets."""
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
