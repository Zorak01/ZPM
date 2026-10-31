# SPDX-License-Identifier: GPL-3.0-or-later

"""Public, Blender-independent mesh API.

All positions are Float32. Index arrays use unsigned 16- or 32-bit integers.
Saving may reorder elements and snap values by a tiny amount; see optimize.py.
"""

from .decoder import decode
from .encoder import encode
from .file_map import FileRegion, map_file
from .files import load, save
from .model import FormatError, Mesh
from .optimize import same_geometry, snap_mesh

__all__ = [
    "FileRegion",
    "FormatError",
    "Mesh",
    "decode",
    "encode",
    "load",
    "map_file",
    "same_geometry",
    "save",
    "snap_mesh",
]
