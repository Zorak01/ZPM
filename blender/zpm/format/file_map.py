# SPDX-License-Identifier: GPL-3.0-or-later

"""Names every byte of a ZPM file, so a file can be inspected and its layout proven."""

from dataclasses import dataclass

from .column import column_length
from .layout import (
    DIRECTORY_ENTRY,
    FILE_HEADER,
    MESH_INFO,
    TAIL_BYTES,
    SectionFlags,
    SectionKind,
    align,
)
from .model import FormatError

_CONSTANT_BYTES = 4
_FIXED_LENGTHS = {
    SectionKind.MESH_INFO: MESH_INFO.size,
    SectionKind.POSITION_CONSTANT_X: _CONSTANT_BYTES,
    SectionKind.POSITION_CONSTANT_Y: _CONSTANT_BYTES,
    SectionKind.POSITION_CONSTANT_Z: _CONSTANT_BYTES,
}
_RAW_KINDS = {
    SectionKind.POSITION_REPEAT_MASK,
    SectionKind.EDGE_LOOSE_MASK,
    SectionKind.FACE_STARTS,
    SectionKind.CORNER_REPEAT_MASK,
    SectionKind.EDGE_FIRST_USE,
}


@dataclass(frozen=True, slots=True)
class FileRegion:
    """One named stretch of a file: offset, length, and what it holds."""

    name: str
    offset: int
    length: int
    flags: SectionFlags = SectionFlags.NONE


def _used_length(kind: SectionKind, flags: SectionFlags, section: bytes) -> int:
    if kind in _RAW_KINDS:
        return len(section)
    if kind in _FIXED_LENGTHS:
        return _FIXED_LENGTHS[kind]
    return column_length(section, flags)


def map_file(data: bytes) -> list:
    """Return regions in file order, covering every byte exactly once.

    Lengths come from each section's own contents. Raises FormatError when
    padding is not zero or is longer than alignment needs.
    """
    count = FILE_HEADER.unpack_from(data)[2]
    limit = len(data) - TAIL_BYTES
    regions = [
        FileRegion("FILE_HEADER", 0, FILE_HEADER.size),
        FileRegion("DIRECTORY", FILE_HEADER.size, count * DIRECTORY_ENTRY.size),
    ]
    entries = [
        DIRECTORY_ENTRY.unpack_from(data, FILE_HEADER.size + i * DIRECTORY_ENTRY.size)
        for i in range(count)
    ]
    for index, (raw_kind, raw_flags, offset) in enumerate(entries):
        end = entries[index + 1][2] if index + 1 < count else limit
        kind = SectionKind(raw_kind)
        flags = SectionFlags(raw_flags)
        used = _used_length(kind, flags, data[offset:end])
        regions.append(FileRegion(kind.name, offset, used, flags))
        if align(used) != end - offset or any(data[offset + used : end]):
            raise FormatError(f"Section {kind.name} has invalid padding")
        if used != end - offset:
            regions.append(FileRegion("PADDING", offset + used, end - offset - used))
    regions.append(FileRegion("TAIL", limit, TAIL_BYTES))
    return regions
