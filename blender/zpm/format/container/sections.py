# SPDX-License-Identifier: GPL-3.0-or-later

"""Sections in memory, and the directory that joins them into a file."""

import struct
from dataclasses import dataclass

from ..errors import FormatError
from ..storage.column import encode_column, read_column
from .layout import (
    DIRECTORY_ENTRY,
    FILE_HEADER,
    LARGEST_NARROW_FILE,
    MAGIC,
    OPTIONAL_KIND_START,
    TAIL_BYTES,
    VERSION,
    WIDE_DIRECTORY_ENTRY,
    SectionFlags,
    SectionKind,
    align,
)


@dataclass(frozen=True, slots=True)
class Section:
    """One labelled stretch of a file, before it has an offset."""

    kind: SectionKind
    data: bytes
    flags: SectionFlags = SectionFlags.NONE
    layer: int = 0


def column_section(
    kind: SectionKind,
    values: list,
    extra: SectionFlags = SectionFlags.NONE,
    layer: int = 0,
) -> Section:
    """Return a section holding these integers as a column."""
    data, flags = encode_column(values)
    return Section(kind, data, flags | extra, layer)


def stored_bytes(sections: list) -> int:
    """Return what these sections add to a file, so the encoder can compare forms."""
    return sum(align(len(section.data)) + DIRECTORY_ENTRY.size for section in sections)


def directory_entry_for(file_bytes: int) -> struct.Struct:
    """Return a file's directory entry: only a file past 4 GiB needs wide offsets."""
    return (
        DIRECTORY_ENTRY if file_bytes <= LARGEST_NARROW_FILE else WIDE_DIRECTORY_ENTRY
    )


def write_file(sections: list) -> bytes:
    """Join sections in rising kind and layer order, behind their directory."""
    ordered = sorted(sections, key=lambda section: (section.kind, section.layer))
    narrow_bytes = FILE_HEADER.size + len(ordered) * DIRECTORY_ENTRY.size + TAIL_BYTES
    narrow_bytes += sum(align(len(section.data)) for section in ordered)
    entry = directory_entry_for(narrow_bytes)
    offset = FILE_HEADER.size + len(ordered) * entry.size
    directory = bytearray()
    body = bytearray()
    for section in ordered:
        directory += entry.pack(section.kind, section.flags, section.layer, offset)
        padded = align(len(section.data))
        body += section.data + bytes(padded - len(section.data))
        offset += padded
    header = FILE_HEADER.pack(MAGIC, VERSION, len(ordered), offset + TAIL_BYTES)
    return header + bytes(directory) + bytes(body) + bytes(TAIL_BYTES)


class SectionReader:
    """The directory of one file. Raises FormatError for a layout no writer makes."""

    def __init__(self, data: bytes):
        if len(data) < FILE_HEADER.size + TAIL_BYTES:
            raise FormatError("File is truncated")
        magic, version, count, file_bytes = FILE_HEADER.unpack_from(data)
        if magic != MAGIC or version != VERSION:
            raise FormatError("Unsupported ZPM file or version")
        if file_bytes != len(data):
            raise FormatError("File length does not match its header")
        limit = file_bytes - TAIL_BYTES
        entry = directory_entry_for(file_bytes)
        expected = FILE_HEADER.size + count * entry.size
        if expected > limit:
            raise FormatError("File directory is truncated")
        if any(data[limit:]):
            raise FormatError("File tail is not zero")
        entries = [
            entry.unpack_from(data, FILE_HEADER.size + index * entry.size)
            for index in range(count)
        ]
        self._sections = {}
        previous = (0, 0)
        for index, (kind, flags, layer, offset) in enumerate(entries):
            if (kind, layer) <= previous or offset != expected:
                raise FormatError("File directory is out of order")
            end = entries[index + 1][3] if index + 1 < count else limit
            if end < offset or end > limit or end != align(end):
                raise FormatError("File section is out of bounds")
            previous = (kind, layer)
            expected = end
            if kind >= OPTIONAL_KIND_START:
                continue
            try:
                known = SectionKind(kind)
                self._sections[known, layer] = (SectionFlags(flags), data[offset:end])
            except ValueError as error:
                raise FormatError("File has an unknown section") from error
        if expected != limit:
            raise FormatError("File has bytes no section claims")

    def restore(self, sections: list) -> None:
        """Add sections a packed stream held. Raises FormatError for one the file
        already holds."""
        for section in sections:
            key = (section.kind, section.layer)
            if key in self._sections:
                raise FormatError(f"File holds section {section.kind.name} twice")
            self._sections[key] = (section.flags, section.data)

    def has(self, kind: SectionKind, layer: int = 0) -> bool:
        return (kind, layer) in self._sections

    def raw(self, kind: SectionKind, layer: int = 0) -> bytes:
        """Return a section's bytes, padding included."""
        if (kind, layer) not in self._sections:
            raise FormatError(f"File is missing section {kind.name}")
        return self._sections[kind, layer][1]

    def flags(self, kind: SectionKind, layer: int = 0) -> SectionFlags:
        self.raw(kind, layer)
        return self._sections[kind, layer][0]

    def count_of(self, kind: SectionKind, layer: int = 0) -> int:
        """Return how many values a column section holds, from its first field."""
        section = self.raw(kind, layer)
        if len(section) < 4:
            raise FormatError(f"Section {kind.name} is truncated")
        return struct.unpack_from("<I", section)[0]

    def column(self, kind: SectionKind, count: int, layer: int = 0) -> list:
        """Expand a column section, which must hold exactly count values."""
        flags, section = self.flags(kind, layer), self.raw(kind, layer)
        values = read_column(section, flags)
        if len(values) != count:
            raise FormatError(f"Section {kind.name} has the wrong element count")
        return values
