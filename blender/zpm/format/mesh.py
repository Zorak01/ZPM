# SPDX-License-Identifier: GPL-3.0-or-later

"""Typed mesh buffers shared by the format code and application adapters."""

from array import array
from dataclasses import dataclass, field

from .container.layout import (
    MIN_RING_CORNERS,
    NO_SHAPE_TARGET,
    AttributeDomain,
    AttributeMeaning,
    AttributeType,
    ShapeInterpolation,
)
from .errors import FormatError

MAX_ATTRIBUTE_COMPONENTS = 255
ATTRIBUTE_TYPECODES = {
    AttributeType.BOOL: "B",
    AttributeType.INT8: "b",
    AttributeType.UINT8: "B",
    AttributeType.INT16: "h",
    AttributeType.UINT16: "H",
    AttributeType.INT32: "i",
    AttributeType.UINT32: "I",
    AttributeType.FLOAT32: "f",
}


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
class Attribute:
    """One named custom attribute, stored exactly.

    values holds components per element, interleaved, with one array type per
    AttributeType, as ATTRIBUTE_TYPECODES lists.
    """

    domain: AttributeDomain
    value_type: AttributeType
    components: int
    values: array
    meaning: AttributeMeaning = AttributeMeaning.NONE


@dataclass(slots=True)
class ShapeTarget:
    """One blendshape: where every vertex goes when it is fully applied.

    positions: flat XYZ Float32, one triple per vertex, as absolute positions.
    reference: index of the target this one is relative to; its own index if none.
    is_relative: blends as an offset from reference, not by time between targets.
    value, minimum, maximum: its current weight and allowed range.
    frame: its place in time, for absolute targets.
    vertex_group: name of the group limiting it, or empty.
    in_between_of, in_between_weight: the target this one shapes on its way to, and at
        what weight; NO_SHAPE_TARGET if none.
    normal_offsets, tangent_offsets: flat XYZ changes per vertex, or empty.
    """

    name: str
    positions: array
    reference: int = 0
    is_relative: bool = True
    value: float = 0.0
    minimum: float = 0.0
    maximum: float = 1.0
    frame: float = 0.0
    vertex_group: str = ""
    is_muted: bool = False
    interpolation: ShapeInterpolation = ShapeInterpolation.LINEAR
    in_between_of: int = NO_SHAPE_TARGET
    in_between_weight: float = 0.0
    normal_offsets: array = field(default_factory=_empty_floats)
    tangent_offsets: array = field(default_factory=_empty_floats)


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

    tangent_method: what made the tangents and its version, or empty if unknown.
    attributes: name to custom Attribute.
    shape_targets: blendshapes, in the order they evaluate.
    properties: named values for tools; see the value tree in codec/scene_values.py.

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
    tangent_method: str = ""
    attributes: dict = field(default_factory=dict)
    shape_targets: list = field(default_factory=list)
    properties: dict = field(default_factory=dict)
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

    def domain_size(self, domain: AttributeDomain) -> int:
        """Return how many elements an attribute on this domain has."""
        return {
            AttributeDomain.POINT: self.vertex_count,
            AttributeDomain.EDGE: self.edge_count,
            AttributeDomain.FACE: self.face_count,
            AttributeDomain.CORNER: len(self.corners),
            AttributeDomain.MESH: 1,
        }[domain]

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
        self._validate_attributes()
        self._validate_shape_targets()

    def _validate_attributes(self) -> None:
        for name, attribute in self.attributes.items():
            elements = self.domain_size(attribute.domain)
            if not 1 <= attribute.components <= MAX_ATTRIBUTE_COMPONENTS or len(
                attribute.values
            ) != (elements * attribute.components):
                raise FormatError(f"Attribute {name} does not match the mesh")
            if attribute.values.typecode != ATTRIBUTE_TYPECODES[attribute.value_type]:
                raise FormatError(f"Attribute {name} has the wrong array type")

    def _validate_shape_targets(self) -> None:
        count = len(self.shape_targets)
        names = {target.name for target in self.shape_targets}
        if len(names) != count:
            raise FormatError("Shape target names must be different")
        for target in self.shape_targets:
            offsets = (target.normal_offsets, target.tangent_offsets)
            if (
                len(target.positions) != len(self.positions)
                or target.reference >= count
                or (
                    target.in_between_of != NO_SHAPE_TARGET
                    and target.in_between_of >= count
                )
                or any(
                    buffer and len(buffer) != len(self.positions) for buffer in offsets
                )
            ):
                raise FormatError(f"Shape target {target.name} does not match the mesh")

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
