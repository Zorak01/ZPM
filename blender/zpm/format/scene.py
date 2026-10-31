# SPDX-License-Identifier: GPL-3.0-or-later

"""Typed scene data: everything around meshes. Links between resources are indices."""

from dataclasses import dataclass, field

from .container.layout import (
    NO_INDEX,
    AlphaMode,
    AreaShape,
    LightType,
    LightUnit,
    ObjectFlags,
    ObjectType,
    ResourceKind,
    RotationMode,
    SamplerFilter,
    SamplerWrap,
    SkinFlags,
    SpaceAxis,
    SplineFlags,
    SplineType,
    TrackFlags,
    TrackValue,
)
from .errors import FormatError

_IDENTITY_MATRIX = (1.0, 0, 0, 0, 0, 1.0, 0, 0, 0, 0, 1.0, 0, 0, 0, 0, 1.0)
ROLE_BASE_COLOR = "base_color"
ROLE_METALLIC = "metallic"
ROLE_ROUGHNESS = "roughness"
ROLE_NORMAL = "normal"
ROLE_EMISSION = "emission"
ROLE_ALPHA = "alpha"
TREE_FIELDS = {
    "Scene": ("properties",),
    "SceneObject": (
        "properties",
        "modifiers",
        "constraints",
        "bone_settings",
        "drivers",
    ),
    "Mesh": ("properties",),
    "Material": ("model", "parameters", "properties"),
    "Image": ("properties",),
    "Light": ("settings", "properties"),
    "Armature": ("settings", "properties"),
    "Bone": ("settings", "properties"),
    "Collection": ("properties",),
    "Curve": ("settings", "properties"),
    "Lattice": (
        "vertex_groups",
        "weight_offsets",
        "weight_groups",
        "weight_values",
        "settings",
        "properties",
    ),
    "Camera": ("settings", "properties"),
}


@dataclass(frozen=True, slots=True)
class Reference:
    """A value in a value tree that names an object by its index."""

    index: int


@dataclass(frozen=True, slots=True)
class SceneSpace:
    """What a scene's coordinates mean. The default is what Blender uses."""

    meters_per_unit: float = 1.0
    up: SpaceAxis = SpaceAxis.Z
    forward: SpaceAxis = SpaceAxis.NEG_Y


@dataclass(slots=True)
class PoseChannel:
    """One bone's posed transform on one armature object, when it differs from rest."""

    bone: int
    location: tuple = (0.0, 0.0, 0.0)
    rotation: tuple = (1.0, 0.0, 0.0, 0.0)
    scale: tuple = (1.0, 1.0, 1.0)
    rotation_mode: RotationMode = RotationMode.QUATERNION


@dataclass(slots=True)
class SceneObject:
    """One placed thing.

    data indexes the list its type names: meshes, armatures or lights. skin_bones gives
    the bone for each of the mesh's vertex groups, or NO_INDEX. modifiers, constraints,
    bone_settings and drivers hold whatever their source application calls them: they
    are kept for that application, and are not a portable way to evaluate a rig.
    """

    name: str
    object_type: ObjectType = ObjectType.EMPTY
    data: int = NO_INDEX
    parent: int = NO_INDEX
    parent_bone: int = NO_INDEX
    rotation_mode: RotationMode = RotationMode.QUATERNION
    flags: ObjectFlags = ObjectFlags.NONE
    instance_collection: int = NO_INDEX
    location: tuple = (0.0, 0.0, 0.0)
    rotation: tuple = (1.0, 0.0, 0.0, 0.0)
    scale: tuple = (1.0, 1.0, 1.0)
    parent_inverse: tuple = _IDENTITY_MATRIX
    skin_armature: int = NO_INDEX
    skin_flags: SkinFlags = SkinFlags.NONE
    skin_bones: list = field(default_factory=list)
    pose: list = field(default_factory=list)
    properties: dict = field(default_factory=dict)
    modifiers: list = field(default_factory=list)
    constraints: list = field(default_factory=list)
    bone_settings: list = field(default_factory=list)
    drivers: list = field(default_factory=list)


@dataclass(slots=True)
class Collection:
    """A named group of objects. An object may be in several."""

    name: str
    parent: int = NO_INDEX
    members: list = field(default_factory=list)
    properties: dict = field(default_factory=dict)


@dataclass(slots=True)
class TextureSlot:
    """One image a material uses, how it samples it, and which UVs it reads.

    role says what it feeds: a ROLE_ name for what every renderer shares, or any other
    name for what only some do.
    """

    role: str
    image: int
    sampler: int
    uv_layer: str = ""
    offset: tuple = (0.0, 0.0)
    rotation: float = 0.0
    scale: tuple = (1.0, 1.0)


@dataclass(slots=True)
class Material:
    """One material, in terms any application can read.

    The fields are what real-time and offline renderers share. model is empty for
    plain metallic-roughness, or names another shading model. parameters holds the
    named values a model has beyond these fields.
    """

    name: str
    base_color: tuple = (0.8, 0.8, 0.8, 1.0)
    metallic: float = 0.0
    roughness: float = 0.5
    emission: tuple = (0.0, 0.0, 0.0)
    emission_strength: float = 0.0
    normal_scale: float = 1.0
    alpha_cutoff: float = 0.5
    alpha_mode: AlphaMode = AlphaMode.OPAQUE
    is_double_sided: bool = True
    slots: list = field(default_factory=list)
    model: str = ""
    parameters: dict = field(default_factory=dict)
    properties: dict = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Sampler:
    """How a texture is filtered and wrapped. Materials share identical ones."""

    filter: SamplerFilter = SamplerFilter.LINEAR
    wrap_u: SamplerWrap = SamplerWrap.REPEAT
    wrap_v: SamplerWrap = SamplerWrap.REPEAT


@dataclass(slots=True)
class ImageTile:
    """One image file: a UDIM tile, or the only tile of a plain image.

    payload holds the original encoded bytes when embedded, or is empty. path is
    where the file was, or empty.
    """

    number: int = 0
    path: str = ""
    payload: bytes = b""


@dataclass(slots=True)
class Image:
    name: str
    color_space: str = ""
    width: int = 0
    height: int = 0
    channels: int = 0
    has_alpha: bool = False
    tiles: list = field(default_factory=list)
    properties: dict = field(default_factory=dict)


@dataclass(slots=True)
class Light:
    """A light's settings. Placement comes from the object using it."""

    name: str
    light_type: LightType = LightType.POINT
    color: tuple = (1.0, 1.0, 1.0)
    power: float = 10.0
    unit: LightUnit = LightUnit.WATTS
    casts_shadow: bool = True
    radius: float = 0.1
    spot_angle: float = 0.0
    spot_blend: float = 0.0
    sun_angle: float = 0.0
    area_shape: AreaShape = AreaShape.SQUARE
    area_width: float = 0.0
    area_height: float = 0.0
    settings: dict = field(default_factory=dict)
    properties: dict = field(default_factory=dict)


@dataclass(slots=True)
class Bone:
    """One bone in armature space. parent indexes its armature's bones."""

    name: str
    parent: int = NO_INDEX
    head: tuple = (0.0, 0.0, 0.0)
    tail: tuple = (0.0, 1.0, 0.0)
    roll: float = 0.0
    envelope_distance: float = 0.25
    head_radius: float = 0.1
    tail_radius: float = 0.05
    is_connected: bool = False
    is_deform: bool = True
    inherits_rotation: bool = True
    inherits_scale: bool = True
    settings: dict = field(default_factory=dict)
    properties: dict = field(default_factory=dict)


@dataclass(slots=True)
class Armature:
    name: str
    bones: list = field(default_factory=list)
    settings: dict = field(default_factory=dict)
    properties: dict = field(default_factory=dict)


@dataclass(slots=True)
class Camera:
    """A camera's settings, named as its source application names them."""

    name: str
    settings: dict = field(default_factory=dict)
    properties: dict = field(default_factory=dict)


@dataclass(slots=True)
class Spline:
    """One curve. positions is flat XYZ; left_handles, right_handles only for Bezier.

    handle_types holds two HandleType per point. knots is empty when calculated from
    order and flags.
    """

    spline_type: SplineType = SplineType.POLY
    flags: SplineFlags = SplineFlags.NONE
    order: int = 4
    resolution: int = 12
    positions: list = field(default_factory=list)
    weights: list = field(default_factory=list)
    radii: list = field(default_factory=list)
    tilts: list = field(default_factory=list)
    left_handles: list = field(default_factory=list)
    right_handles: list = field(default_factory=list)
    handle_types: list = field(default_factory=list)
    knots: list = field(default_factory=list)

    @property
    def point_count(self) -> int:
        return len(self.positions) // 3


@dataclass(slots=True)
class Curve:
    name: str
    splines: list = field(default_factory=list)
    settings: dict = field(default_factory=dict)
    properties: dict = field(default_factory=dict)


@dataclass(slots=True)
class Lattice:
    """A grid of U x V x W points. rest and deformed are flat XYZ.

    Weights work as on a mesh: weight_offsets says where each point's start.
    """

    name: str
    points_u: int = 2
    points_v: int = 2
    points_w: int = 2
    rest: list = field(default_factory=list)
    deformed: list = field(default_factory=list)
    softbody_weights: list = field(default_factory=list)
    vertex_groups: list = field(default_factory=list)
    weight_offsets: list = field(default_factory=list)
    weight_groups: list = field(default_factory=list)
    weight_values: list = field(default_factory=list)
    settings: dict = field(default_factory=dict)
    properties: dict = field(default_factory=dict)


@dataclass(slots=True)
class Track:
    """One animated value: any field of any resource, by owner and path.

    values holds components per key. key_modes and left and right handle types are
    empty when every key is linear. handles holds four floats per key: left time and
    value, right time and value. target_kind says what a REFERENCE key indexes.
    """

    owner_kind: ResourceKind
    owner: int
    path: str
    times: list
    values: list
    array_index: int = 0
    value_type: TrackValue = TrackValue.FLOAT
    target_kind: ResourceKind = ResourceKind.OBJECT
    components: int = 1
    flags: TrackFlags = TrackFlags.NONE
    key_modes: list = field(default_factory=list)
    left_handle_types: list = field(default_factory=list)
    right_handle_types: list = field(default_factory=list)
    handles: list = field(default_factory=list)


@dataclass(slots=True)
class Clip:
    """A named piece of animation. Times are in frames at frames_per_second."""

    name: str
    frames_per_second: float = 24.0
    start: float = 0.0
    end: float = 0.0
    tracks: list = field(default_factory=list)


@dataclass(slots=True)
class Extension:
    """A named, versioned payload. A reader refuses a required one it does not know."""

    name: str
    version: int = 1
    is_required: bool = False
    payload: bytes = b""


@dataclass(slots=True)
class Scene:
    """A whole file: meshes, and everything that places and describes them."""

    space: SceneSpace = field(default_factory=SceneSpace)
    meshes: list = field(default_factory=list)
    materials: list = field(default_factory=list)
    samplers: list = field(default_factory=list)
    images: list = field(default_factory=list)
    objects: list = field(default_factory=list)
    collections: list = field(default_factory=list)
    lights: list = field(default_factory=list)
    armatures: list = field(default_factory=list)
    curves: list = field(default_factory=list)
    lattices: list = field(default_factory=list)
    cameras: list = field(default_factory=list)
    clips: list = field(default_factory=list)
    extensions: list = field(default_factory=list)
    properties: dict = field(default_factory=dict)

    def resources(self, kind: ResourceKind) -> list:
        """Return every resource of one kind. Bones run across all armatures."""
        return {
            ResourceKind.SCENE: [self],
            ResourceKind.OBJECT: self.objects,
            ResourceKind.MESH: self.meshes,
            ResourceKind.MATERIAL: self.materials,
            ResourceKind.IMAGE: self.images,
            ResourceKind.LIGHT: self.lights,
            ResourceKind.ARMATURE: self.armatures,
            ResourceKind.BONE: [b for a in self.armatures for b in a.bones],
            ResourceKind.COLLECTION: self.collections,
            ResourceKind.CURVE: self.curves,
            ResourceKind.LATTICE: self.lattices,
            ResourceKind.CAMERA: self.cameras,
        }[kind]

    def validate(self) -> None:
        """Raise FormatError for a link to something missing, or a parent cycle."""
        self._validate_tracks()
        self._validate_curves_and_lattices()
        self._validate_references()
        self._validate_objects()
        self._validate_collections()
        self._validate_materials()
        for armature in self.armatures:
            count = len(armature.bones)
            for index, bone in enumerate(armature.bones):
                if bone.parent != NO_INDEX and bone.parent >= count:
                    raise FormatError(f"Bone {bone.name} has a missing parent")
                if bone.parent == index:
                    raise FormatError(f"Bone {bone.name} is its own parent")
        self._validate_no_cycles([o.parent for o in self.objects], "Object parents")
        self._validate_no_cycles([c.parent for c in self.collections], "Collections")
        for armature in self.armatures:
            parents = [bone.parent for bone in armature.bones]
            self._validate_no_cycles(parents, "Bone parents")

    def _validate_tracks(self) -> None:
        for clip in self.clips:
            for track in clip.tracks:
                if track.owner >= len(self.resources(track.owner_kind)):
                    raise FormatError(f"Track {track.path} has a missing owner")
                if len(track.values) != len(track.times) * track.components:
                    raise FormatError(f"Track {track.path} does not match its keys")
                if any(
                    a > b for a, b in zip(track.times, track.times[1:], strict=False)
                ):
                    raise FormatError(f"Track {track.path} goes back in time")
                if track.value_type == TrackValue.REFERENCE:
                    limit = len(self.resources(track.target_kind))
                    if any(value >= limit for value in track.values):
                        raise FormatError(f"Track {track.path} keys something missing")

    def _validate_curves_and_lattices(self) -> None:
        for curve in self.curves:
            for spline in curve.splines:
                count = spline.point_count
                sized = (spline.weights, spline.radii, spline.tilts)
                if any(len(buffer) != count for buffer in sized):
                    raise FormatError(f"A spline of {curve.name} does not match")
                if spline.spline_type == SplineType.BEZIER and (
                    len(spline.left_handles) != count * 3
                    or len(spline.right_handles) != count * 3
                    or len(spline.handle_types) != count * 2
                ):
                    raise FormatError(f"A spline of {curve.name} lacks handles")
        for lattice in self.lattices:
            count = lattice.points_u * lattice.points_v * lattice.points_w
            if (
                len(lattice.rest) != count * 3
                or len(lattice.deformed) != count * 3
                or len(lattice.softbody_weights) != count
            ):
                raise FormatError(f"Lattice {lattice.name} does not match its size")

    def _validate_references(self) -> None:
        """Check every Reference in every value tree names an object that exists."""

        def walk(value) -> None:
            if isinstance(value, Reference) and value.index >= len(self.objects):
                raise FormatError("A value references a missing object")
            if isinstance(value, dict):
                value = list(value.values())
            if isinstance(value, list):
                for item in value:
                    walk(item)

        for kind in ResourceKind:
            for resource in self.resources(kind):
                for name in TREE_FIELDS[type(resource).__name__]:
                    walk(getattr(resource, name))

    def _data_count(self, object_type: ObjectType) -> int:
        return {
            ObjectType.MESH: len(self.meshes),
            ObjectType.ARMATURE: len(self.armatures),
            ObjectType.LIGHT: len(self.lights),
            ObjectType.CURVE: len(self.curves),
            ObjectType.LATTICE: len(self.lattices),
            ObjectType.CAMERA: len(self.cameras),
        }.get(object_type, 0)

    def _validate_objects(self) -> None:
        count = len(self.objects)
        for item in self.objects:
            if item.data != NO_INDEX and item.data >= self._data_count(
                item.object_type
            ):
                raise FormatError(f"Object {item.name} has missing data")
            links = (item.parent, item.skin_armature)
            if any(link != NO_INDEX and link >= count for link in links):
                raise FormatError(f"Object {item.name} links to a missing object")
            collection = item.instance_collection
            if collection != NO_INDEX and collection >= len(self.collections):
                raise FormatError(f"Object {item.name} links to a missing collection")
            bones = self._bones_of(item)
            pose_bones = [channel.bone for channel in item.pose]
            if any(bone >= bones for bone in pose_bones):
                raise FormatError(f"Object {item.name} poses a missing bone")

    def _bones_of(self, item: SceneObject) -> int:
        if item.object_type == ObjectType.ARMATURE and item.data != NO_INDEX:
            return len(self.armatures[item.data].bones)
        return 0

    def _validate_collections(self) -> None:
        for collection in self.collections:
            if collection.parent != NO_INDEX and collection.parent >= len(
                self.collections
            ):
                raise FormatError(f"Collection {collection.name} has a missing parent")
            if any(member >= len(self.objects) for member in collection.members):
                raise FormatError(f"Collection {collection.name} has a missing object")

    def _validate_materials(self) -> None:
        for material in self.materials:
            for slot in material.slots:
                if slot.image >= len(self.images) or slot.sampler >= len(self.samplers):
                    raise FormatError(f"Material {material.name} has a missing link")

    @staticmethod
    def _validate_no_cycles(parents: list, what: str) -> None:
        """Walk up from each item. Costs the depth of the tree per item."""
        for start in range(len(parents)):
            at = parents[start]
            for _ in parents:
                if at == NO_INDEX:
                    break
                at = parents[at]
            else:
                raise FormatError(f"{what} form a cycle")
