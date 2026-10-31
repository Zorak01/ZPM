# SPDX-License-Identifier: GPL-3.0-or-later

"""Columns: UInt32 arrays stored in 64-value blocks, readable by index in the file.

Within a block, value[j] = base + slope * j + residual[j], in wrapping arithmetic.
"""

from array import array

from .layout import (
    BLOCK_SIZE,
    BLOCKS_PER_GROUP,
    COLUMN_COUNT,
    MAX_SLOPE,
    MAX_WIDTH,
    UINT32_MAX,
    SectionFlags,
    align,
)
from .model import FormatError

_OVER_WIDTH = MAX_WIDTH + 1


def _measure_fit(values: list, start: int, length: int, slope: int) -> tuple:
    """Return (lowest residual, bits needed) for one block at one slope."""
    residuals = [values[start + j] - slope * j for j in range(length)]
    low = min(residuals)
    span = max(residuals) - low
    return low, span.bit_length() if span <= UINT32_MAX else _OVER_WIDTH


def _clamp_slope(value: int) -> int:
    return max(-MAX_SLOPE, min(MAX_SLOPE, value))


def _candidate_slopes(values: list, start: int, length: int) -> tuple:
    middle = length // 2
    after = middle + 1 if middle + 1 < length else 1
    return (
        _clamp_slope(int((values[start + length - 1] - values[start]) / (length - 1))),
        _clamp_slope(values[start + 1] - values[start]),
        _clamp_slope(values[start + after] - values[start + middle]),
    )


def _fit_block(values: list, start: int, length: int) -> tuple:
    """Return (slope, base, width), choosing the slope needing the fewest bits."""
    best_slope = 0
    low, width = _measure_fit(values, start, length, 0)
    if length < 3 or width == 0:
        return best_slope, low, width
    for slope in _candidate_slopes(values, start, length):
        if slope in (0, best_slope):
            continue
        sloped_low, sloped_width = _measure_fit(values, start, length, slope)
        if sloped_width < width:
            best_slope, low, width = slope, sloped_low, sloped_width
    return best_slope, low, width


def encode_column(values: list) -> tuple:
    """Return (bytes, SectionFlags) for a column holding these integers."""
    count = len(values)
    blocks = (count + BLOCK_SIZE - 1) // BLOCK_SIZE
    bases = array("I")
    slopes = array("b")
    widths = bytearray()
    groups = array("I")
    payload = bytearray()
    for block in range(blocks):
        start = block * BLOCK_SIZE
        length = min(BLOCK_SIZE, count - start)
        slope, low, width = _fit_block(values, start, length)
        if width > MAX_WIDTH:
            raise FormatError("Value range exceeds 32 bits")
        bases.append(low & UINT32_MAX)
        slopes.append(slope)
        widths.append(width)
        if block % BLOCKS_PER_GROUP == 0:
            groups.append(len(payload))
        packed = 0
        for j in range(length if width else 0):
            packed |= (values[start + j] - slope * j - low) << (j * width)
        payload += packed.to_bytes((length * width + 7) // 8, "little")
    has_slopes = any(slopes)
    head = bytearray(COLUMN_COUNT.pack(count))
    head += bases.tobytes()
    if has_slopes:
        head += slopes.tobytes()
    head += widths
    head += bytes(align(len(head)) - len(head))
    data = bytes(head) + groups.tobytes() + bytes(payload)
    return data, SectionFlags.HAS_SLOPES if has_slopes else SectionFlags.NONE


def column_length(section: bytes, flags: SectionFlags) -> int:
    """Return the bytes a column uses, before padding, from its count and widths."""
    count = COLUMN_COUNT.unpack_from(section)[0]
    blocks = (count + BLOCK_SIZE - 1) // BLOCK_SIZE
    groups = (blocks + BLOCKS_PER_GROUP - 1) // BLOCKS_PER_GROUP
    slope_bytes = blocks if flags & SectionFlags.HAS_SLOPES else 0
    widths_at = COLUMN_COUNT.size + blocks * 4 + slope_bytes
    payload_at = align(widths_at + blocks) + groups * 4
    payload = 0
    for block in range(blocks):
        length = min(BLOCK_SIZE, count - block * BLOCK_SIZE)
        payload += (length * section[widths_at + block] + 7) // 8
    return payload_at + payload


def read_column(section: bytes, flags: SectionFlags) -> list:
    """Expand a whole column. Raises FormatError when any part of it is malformed."""
    if len(section) < COLUMN_COUNT.size:
        raise FormatError("Column is truncated")
    count = COLUMN_COUNT.unpack_from(section)[0]
    blocks = (count + BLOCK_SIZE - 1) // BLOCK_SIZE
    groups = (blocks + BLOCKS_PER_GROUP - 1) // BLOCKS_PER_GROUP
    slope_bytes = blocks if flags & SectionFlags.HAS_SLOPES else 0
    bases_at = COLUMN_COUNT.size
    slopes_at = bases_at + blocks * 4
    widths_at = slopes_at + slope_bytes
    groups_at = align(widths_at + blocks)
    payload_at = groups_at + groups * 4
    if payload_at > len(section):
        raise FormatError("Column is truncated")
    bases = array("I", section[bases_at:slopes_at])
    slopes = array("b", section[slopes_at:widths_at]) if slope_bytes else None
    group_table = array("I", section[groups_at:payload_at])
    values = []
    running = 0
    for block in range(blocks):
        width = section[widths_at + block]
        if width > MAX_WIDTH:
            raise FormatError("Column width is out of range")
        if block % BLOCKS_PER_GROUP == 0:
            if group_table[block // BLOCKS_PER_GROUP] != running:
                raise FormatError("Column offsets do not add up")
        length = min(BLOCK_SIZE, count - block * BLOCK_SIZE)
        size = (length * width + 7) // 8
        start = payload_at + running
        if start + size > len(section):
            raise FormatError("Column is truncated")
        packed = int.from_bytes(section[start : start + size], "little")
        running += size
        base = bases[block]
        slope = slopes[block] if slopes else 0
        mask = (1 << width) - 1
        values.extend(
            (base + slope * j + ((packed >> (j * width)) & mask)) & UINT32_MAX
            for j in range(length)
        )
    if align(payload_at + running) != len(section):
        raise FormatError("Column length does not match its section")
    return values
