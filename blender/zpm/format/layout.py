# SPDX-License-Identifier: GPL-3.0-or-later

"""File layout constants. docs/mesh-core.md is the written contract for these."""

import struct
from enum import IntEnum, IntFlag

MAGIC = b"ZPM\0"
VERSION = 1
FILE_HEADER = struct.Struct("<4sHHI")
DIRECTORY_ENTRY = struct.Struct("<HHI")
MESH_INFO = struct.Struct("<6I")
COLUMN_COUNT = struct.Struct("<I")
TAIL_BYTES = 32
ALIGNMENT = 4

BLOCK_SIZE = 64
BLOCKS_PER_GROUP = 8
MAX_WIDTH = 32
MAX_SLOPE = 127
MAX_UNIFORM_ARITY = 255
MAX_RELATIVE_ARITY = 4
UINT32_MAX = 0xFFFFFFFF
SIGN_BIT = 0x80000000
MAGNITUDE_MASK = 0x7FFFFFFF
AXIS_COUNT = 3
DEFAULT_MAX_BYTES = 512 * 1024 * 1024


class SectionKind(IntEnum):
    """What a section holds. A directory lists its sections in rising kind order."""

    MESH_INFO = 1
    POSITION_REPEAT_MASK = 2
    POSITION_REPEAT_IDS = 3
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
    EDGE_FIRST = 48
    EDGE_SPAN = 49
    EDGE_LOOSE_MASK = 50
    EDGE_LOOSE_FIRST = 51
    EDGE_LOOSE_SECOND = 52
    EDGE_CORNERS = 53
    EDGE_FLIPS = 54
    EDGE_FIRST_USE = 55


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
