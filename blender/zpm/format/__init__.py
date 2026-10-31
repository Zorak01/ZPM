# SPDX-License-Identifier: GPL-3.0-or-later

"""Public, Blender-independent mesh API.

All positions are Float32. Index arrays use unsigned 16- or 32-bit integers.
The codec preserves their values and the original face and edge order.
"""

from .codec import decode, encode, load, save
from .model import FormatError, Mesh

__all__ = ["FormatError", "Mesh", "decode", "encode", "load", "save"]
