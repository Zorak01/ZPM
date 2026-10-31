# SPDX-License-Identifier: GPL-3.0-or-later

"""Rank masks: one mark per element, plus the unmarked count before each 64-bit word."""

import struct

from ..errors import FormatError

_BITS_PER_WORD = 64
_BYTES_PER_WORD = 12


def encode_rank_mask(marks: list) -> bytes:
    words = []
    ranks = []
    clear = 0
    for start in range(0, len(marks), _BITS_PER_WORD):
        ranks.append(clear)
        word = 0
        for position, marked in enumerate(marks[start : start + _BITS_PER_WORD]):
            if marked:
                word |= 1 << position
            else:
                clear += 1
        words.append(word)
    count = len(words)
    return struct.pack(f"<{count}Q{count}I", *words, *ranks)


def read_rank_mask(section: bytes, count: int) -> list:
    """Return one bool per element. Raises FormatError when the ranks do not add up."""
    words = (count + _BITS_PER_WORD - 1) // _BITS_PER_WORD
    if len(section) != words * _BYTES_PER_WORD:
        raise FormatError("Rank mask length does not match its element count")
    masks = struct.unpack_from(f"<{words}Q", section)
    ranks = struct.unpack_from(f"<{words}I", section, words * 8)
    marks = []
    clear = 0
    for word in range(words):
        if ranks[word] != clear:
            raise FormatError("Rank mask does not add up")
        used = min(_BITS_PER_WORD, count - word * _BITS_PER_WORD)
        if used < _BITS_PER_WORD and masks[word] >> used:
            raise FormatError("Rank mask has stray bits")
        for position in range(used):
            marked = bool(masks[word] >> position & 1)
            marks.append(marked)
            clear += not marked
    return marks
