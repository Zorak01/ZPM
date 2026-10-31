# SPDX-License-Identifier: GPL-3.0-or-later

"""Select bits: one bit per element, plus where every 64th set bit sits."""

import struct

from .model import FormatError

_BITS_PER_WORD = 64
_SET_BITS_PER_SELECT = 64


def _word_count(bit_count: int) -> int:
    return (bit_count + _BITS_PER_WORD - 1) // _BITS_PER_WORD


def _select_count(set_count: int) -> int:
    return (set_count + _SET_BITS_PER_SELECT - 1) // _SET_BITS_PER_SELECT


def encode_select_bits(positions: list, bit_count: int) -> bytes:
    """Return section bytes marking these rising positions."""
    words = [0] * _word_count(bit_count)
    for position in positions:
        words[position >> 6] |= 1 << (position & 63)
    selects = positions[0::_SET_BITS_PER_SELECT]
    return struct.pack(f"<{len(words)}Q{len(selects)}I", *words, *selects)


def read_select_bits(section: bytes, bit_count: int, set_count: int) -> list:
    """Return the marked positions. Raises FormatError when the section is malformed."""
    words = _word_count(bit_count)
    selects = _select_count(set_count)
    if len(section) != words * 8 + selects * 4:
        raise FormatError("Select bits length does not match the mesh")
    packed = int.from_bytes(section[: words * 8], "little")
    if packed >> bit_count:
        raise FormatError("Select bits has stray bits")
    positions = [position for position in range(bit_count) if packed >> position & 1]
    if len(positions) != set_count:
        raise FormatError("Select bits does not match its element count")
    table = struct.unpack_from(f"<{selects}I", section, words * 8)
    if list(table) != positions[0::_SET_BITS_PER_SELECT]:
        raise FormatError("Select table is invalid")
    return positions
