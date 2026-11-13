# SPDX-License-Identifier: GPL-3.0-or-later

"""Public, Blender-independent mesh API.

All positions are Float32. Index arrays use unsigned 16- or 32-bit integers.
Saving may reorder elements and snap values by a tiny amount; see processing/.
"""

from .codec.mesh_codec import decode, encode, snap_as_saved
from .codec.scene_codec import decode_scene, encode_scene
from .container.file_map import FileRegion, map_file
from .container.layout import (
    AttributeDomain,
    AttributeMeaning,
    AttributeType,
    ShapeInterpolation,
    SpaceAxis,
)
from .errors import FormatError
from .files import load, save
from .mesh import Attribute, ColorLayer, Mesh, ShapeTarget
from .processing.comparison import same_geometry
from .processing.snapping import snap_mesh
from .scene import (
    Armature,
    Bone,
    Camera,
    Clip,
    Collection,
    Curve,
    Extension,
    Image,
    ImageTile,
    Lattice,
    Light,
    Material,
    PoseChannel,
    Reference,
    Sampler,
    Scene,
    SceneObject,
    SceneSpace,
    Spline,
    TextureSlot,
    Track,
)

__all__ = [
    "Armature",
    "Attribute",
    "Bone",
    "Camera",
    "Clip",
    "Collection",
    "Curve",
    "Extension",
    "Image",
    "ImageTile",
    "Lattice",
    "Light",
    "Material",
    "PoseChannel",
    "Reference",
    "Sampler",
    "Scene",
    "SceneObject",
    "SceneSpace",
    "Spline",
    "TextureSlot",
    "Track",
    "AttributeDomain",
    "AttributeMeaning",
    "AttributeType",
    "ColorLayer",
    "FileRegion",
    "FormatError",
    "Mesh",
    "ShapeInterpolation",
    "ShapeTarget",
    "SpaceAxis",
    "decode",
    "decode_scene",
    "encode",
    "encode_scene",
    "load",
    "map_file",
    "same_geometry",
    "save",
    "snap_as_saved",
    "snap_mesh",
]
