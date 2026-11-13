# SPDX-License-Identifier: GPL-3.0-or-later

"""Columns: UInt32 arrays stored in 64-value blocks, readable by index in the file.

Within a block, value[j] = base + slope * j + residual[j], in wrapping arithmetic.
Bases are stored as their rise above the lowest one, in as few bits as they need, and
widths as their rise above the lowest one, in four bits.
"""

from array import array

from ..container.layout import (
    BLOCK_SIZE,
    BLOCKS_PER_GROUP,
    COLUMN_HEAD,
    MAX_SLOPE,
    MAX_WIDTH,
    UINT32_MAX,
    WIDTH_NIBBLE_MAX,
    SectionFlags,
    align,
    to_signed,
)
from ..errors import FormatError

_OVER_WIDTH = MAX_WIDTH + 1
_SHORT_GROUP_LIMIT = 65535
_WIDTH_NIBBLE_BITS = 4


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


def _pack_bits(values: list, width: int) -> bytes:
    packed = 0
    for index, value in enumerate(values):
        packed |= value << (index * width)
    return packed.to_bytes((len(values) * width + 7) // 8, "little")


def _pack_bases(bases: list) -> tuple:
    """Return (lowest base, bits per base, each base's rise above the lowest)."""
    if not bases:
        return 0, 0, b""
    signed = [to_signed(base) for base in bases]
    lowest = min(signed)
    bits = (max(signed) - lowest).bit_length()
    return lowest & UINT32_MAX, bits, _pack_bits([v - lowest for v in signed], bits)


def _pack_widths(widths: list) -> tuple:
    """Return (lowest width, each width's rise above the lowest, in four bits)."""
    if not widths:
        return 0, b""
    lowest = min(widths)
    return lowest, _pack_bits([width - lowest for width in widths], _WIDTH_NIBBLE_BITS)


def encode_column(values: list) -> tuple:
    """Return (bytes, SectionFlags) for a column holding these integers."""
    count = len(values)
    blocks = (count + BLOCK_SIZE - 1) // BLOCK_SIZE
    fits = [
        _fit_block(
            values, block * BLOCK_SIZE, min(BLOCK_SIZE, count - block * BLOCK_SIZE)
        )
        for block in range(blocks)
    ]
    for _slope, _low, width in fits:
        if width > MAX_WIDTH:
            raise FormatError("Value range exceeds 32 bits")
    # A width far below the widest cannot be stored in four bits, so it is raised.
    floor = max((fit[2] for fit in fits), default=0) - WIDTH_NIBBLE_MAX
    widths = [max(fit[2], floor) for fit in fits]
    groups = []
    payload = bytearray()
    for block, ((slope, low, _fitted), width) in enumerate(
        zip(fits, widths, strict=True)
    ):
        start = block * BLOCK_SIZE
        length = min(BLOCK_SIZE, count - start)
        if block and block % BLOCKS_PER_GROUP == 0:
            groups.append(len(payload))
        packed = 0
        for j in range(length if width else 0):
            packed |= (values[start + j] - slope * j - low) << (j * width)
        payload += packed.to_bytes((length * width + 7) // 8, "little")
    slopes = array("b", [fit[0] for fit in fits])
    has_slopes = any(slopes)
    lowest, base_bits, packed_bases = _pack_bases([fit[1] & UINT32_MAX for fit in fits])
    width_floor, packed_widths = _pack_widths(widths)
    head = bytearray(COLUMN_HEAD.pack(count, lowest, width_floor, base_bits))
    head += packed_bases
    if has_slopes:
        head += slopes.tobytes()
    head += packed_widths
    head += bytes(align(len(head)) - len(head))
    short_groups = bool(groups) and groups[-1] <= _SHORT_GROUP_LIMIT
    table = array("H" if short_groups else "I", groups).tobytes()
    data = bytes(head) + table + bytes(align(len(table)) - len(table)) + bytes(payload)
    flags = SectionFlags.NONE
    if has_slopes:
        flags |= SectionFlags.HAS_SLOPES
    if short_groups:
        flags |= SectionFlags.SHORT_GROUPS
    return data, flags


class _Layout:
    """Where a column's parts sit, from its head and flags."""

    def __init__(self, section: bytes, flags: SectionFlags):
        self.count, self.lowest, self.width_floor, self.base_bits = (
            COLUMN_HEAD.unpack_from(section)
        )
        self.blocks = (self.count + BLOCK_SIZE - 1) // BLOCK_SIZE
        # The first group always starts at zero, so it is not stored.
        self.groups = max(self.blocks - 1, 0) // BLOCKS_PER_GROUP
        self.group_code = "H" if flags & SectionFlags.SHORT_GROUPS else "I"
        slope_bytes = self.blocks if flags & SectionFlags.HAS_SLOPES else 0
        self.bases_at = COLUMN_HEAD.size
        self.slopes_at = self.bases_at + (self.blocks * self.base_bits + 7) // 8
        self.widths_at = self.slopes_at + slope_bytes
        self.groups_at = align(self.widths_at + (self.blocks + 1) // 2)
        group_bytes = self.groups * array(self.group_code).itemsize
        self.payload_at = self.groups_at + align(group_bytes)

    def width(self, section: bytes, block: int) -> int:
        byte = section[self.widths_at + block // 2]
        rise = byte >> (block % 2 * _WIDTH_NIBBLE_BITS) & WIDTH_NIBBLE_MAX
        return self.width_floor + rise


def column_length(section: bytes, flags: SectionFlags) -> int:
    """Return the bytes a column uses, before padding, from its count and widths."""
    layout = _Layout(section, flags)
    payload = 0
    for block in range(layout.blocks):
        length = min(BLOCK_SIZE, layout.count - block * BLOCK_SIZE)
        payload += (length * layout.width(section, block) + 7) // 8
    return layout.payload_at + payload


def read_column(section: bytes, flags: SectionFlags) -> list:
    """Expand a whole column. Raises FormatError when any part of it is malformed."""
    if len(section) < COLUMN_HEAD.size:
        raise FormatError("Column is truncated")
    layout = _Layout(section, flags)
    if layout.base_bits > MAX_WIDTH or layout.payload_at > len(section):
        raise FormatError("Column is truncated")
    has_slopes = flags & SectionFlags.HAS_SLOPES
    slopes = (
        array("b", section[layout.slopes_at : layout.widths_at]) if has_slopes else None
    )
    group_table = array(
        layout.group_code,
        section[
            layout.groups_at : layout.groups_at
            + layout.groups * array(layout.group_code).itemsize
        ],
    )
    rises = int.from_bytes(section[layout.bases_at : layout.slopes_at], "little")
    rise_mask = (1 << layout.base_bits) - 1
    values = []
    running = 0
    for block in range(layout.blocks):
        width = layout.width(section, block)
        if width > MAX_WIDTH:
            raise FormatError("Column width is out of range")
        if block and block % BLOCKS_PER_GROUP == 0:
            if group_table[block // BLOCKS_PER_GROUP - 1] != running:
                raise FormatError("Column offsets do not add up")
        length = min(BLOCK_SIZE, layout.count - block * BLOCK_SIZE)
        size = (length * width + 7) // 8
        start = layout.payload_at + running
        if start + size > len(section):
            raise FormatError("Column is truncated")
        packed = int.from_bytes(section[start : start + size], "little")
        running += size
        base = layout.lowest + (rises >> (block * layout.base_bits) & rise_mask)
        slope = slopes[block] if slopes else 0
        mask = (1 << width) - 1
        values.extend(
            (base + slope * j + ((packed >> (j * width)) & mask)) & UINT32_MAX
            for j in range(length)
        )
    if align(layout.payload_at + running) != len(section):
        raise FormatError("Column length does not match its section")
    return values
