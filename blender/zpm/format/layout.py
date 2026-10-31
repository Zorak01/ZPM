# SPDX-License-Identifier: GPL-3.0-or-later

"""File layout constants. docs/mesh-core.md is the written contract for these."""

import struct
from enum import IntEnum, IntFlag

MAGIC = b"ZPM\0"
VERSION = 1
FILE_HEADER = struct.Struct("<4sHHI")
DIRECTORY_ENTRY = struct.Struct("<HBBI")
MESH_INFO = struct.Struct("<6IiI")
COLUMN_COUNT = struct.Struct("<I")
TAIL_BYTES = 32
ALIGNMENT = 4

BLOCK_SIZE = 64
BLOCKS_PER_GROUP = 8
MAX_WIDTH = 32
MAX_SLOPE = 127
MAX_UNIFORM_ARITY = 255
MAX_RELATIVE_ARITY = 4
MAX_UV_LAYERS = 255
UINT32_MAX = 0xFFFFFFFF
SIGN_BIT = 0x80000000
AXIS_COUNT = 3
DEFAULT_MAX_BYTES = 512 * 1024 * 1024
NO_GRID = -(2**31)
MAX_GRID_VALUE = 1 << 24
NORMAL_BITS = 12
UV_GRID_BITS = 20
CREASE_MAX = 65535


class MeshFlags(IntFlag):
    NONE = 0
    CUSTOM_NORMALS = 1


class SectionKind(IntEnum):
    """What a section holds. A directory lists sections by rising kind, then layer."""

    MESH_INFO = 1
    POSITION_REPEAT_MASK = 2
    POSITION_REPEAT_IDS = 3
    POSITION_GRID_X = 12
    POSITION_GRID_Y = 13
    POSITION_GRID_Z = 14
    VERTEX_CREASES = 15
    POSITION_BITS_X = 16
    POSITION_BITS_Y = 17
    POSITION_BITS_Z = 18
    POSITION_CONSTANT_X = 20
    POSITION_CONSTANT_Y = 21
    POSITION_CONSTANT_Z = 22
    POSITION_PALETTE_X = 24
    POSITION_PALETTE_Y = 25
    POSITION_PALETTE_Z = 26
    POSITION_INDICES_X = 28
    POSITION_INDICES_Y = 29
    POSITION_INDICES_Z = 30
    CORNER_IDS = 32
    CORNER_FIRST = 33
    CORNER_OFFSET_1 = 34
    CORNER_OFFSET_2 = 35
    CORNER_OFFSET_3 = 36
    CORNER_REPEAT_MASK = 37
    CORNER_BACK_DISTANCES = 38
    FACE_STARTS = 40
    FACE_FLAT = 41
    EDGE_FIRST = 48
    EDGE_SPAN = 49
    EDGE_LOOSE_MASK = 50
    EDGE_LOOSE_FIRST = 51
    EDGE_LOOSE_SECOND = 52
    EDGE_CORNERS = 53
    EDGE_FLIPS = 54
    EDGE_FIRST_USE = 55
    EDGE_SHARP = 56
    EDGE_SEAM = 57
    EDGE_CREASES = 58
    NORMAL_REPEAT_MASK = 64
    NORMAL_DISTANCES = 65
    NORMAL_VALUES_A = 66
    NORMAL_VALUES_B = 67
    NORMAL_VERTEX_A = 68
    NORMAL_VERTEX_B = 69
    NORMAL_EXCEPTION_MASK = 70
    NORMAL_EXCEPTION_A = 71
    NORMAL_EXCEPTION_B = 72
    UV_NAMES = 79
    UV_REPEAT_MASK = 80
    UV_DISTANCES = 81
    UV_VALUES_A = 82
    UV_VALUES_B = 83
    UV_VERTEX_A = 84
    UV_VERTEX_B = 85
    UV_EXCEPTION_MASK = 86
    UV_EXCEPTION_A = 87
    UV_EXCEPTION_B = 88


class StreamRole(IntEnum):
    """A section's place in a corner stream, added to the stream's first kind."""

    REPEAT_MASK = 0
    DISTANCES = 1
    VALUES_A = 2
    VALUES_B = 3
    VERTEX_A = 4
    VERTEX_B = 5
    EXCEPTION_MASK = 6
    EXCEPTION_A = 7
    EXCEPTION_B = 8


class SectionFlags(IntFlag):
    NONE = 0
    HAS_SLOPES = 1
    SIGNED_INDICES = 2


def align(length: int) -> int:
    return (length + ALIGNMENT - 1) & ~(ALIGNMENT - 1)


def bits_to_ordered(bits: int) -> int:
    """Map Float32 bits to an order that follows numeric value; exact for all bits."""
    return bits ^ UINT32_MAX if bits & SIGN_BIT else bits ^ SIGN_BIT


def ordered_to_bits(ordered: int) -> int:
    """Undo bits_to_ordered."""
    return ordered ^ SIGN_BIT if ordered & SIGN_BIT else ordered ^ UINT32_MAX


def to_signed(value: int) -> int:
    """Read a stored UInt32 as the Int32 it holds."""
    return value - (1 << 32) if value & SIGN_BIT else value
