# SPDX-License-Identifier: GPL-3.0-or-later

"""Public, Blender-independent mesh API.

All positions are Float32. Index arrays use unsigned 16- or 32-bit integers.
Saving may reorder elements and snap values by a tiny amount; see processing/.
"""

from .codec.mesh_codec import decode, encode
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
from .mesh import Attribute, ColorLayer, Mesh, MeshSpace, ShapeTarget
from .processing.comparison import same_geometry
from .processing.snapping import snap_mesh

__all__ = [
    "Attribute",
    "AttributeDomain",
    "AttributeMeaning",
    "AttributeType",
    "ColorLayer",
    "FileRegion",
    "FormatError",
    "Mesh",
    "MeshSpace",
    "ShapeInterpolation",
    "ShapeTarget",
    "SpaceAxis",
    "decode",
    "encode",
    "load",
    "map_file",
    "same_geometry",
    "save",
    "snap_mesh",
]
