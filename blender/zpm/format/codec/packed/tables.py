# SPDX-License-Identifier: GPL-3.0-or-later

"""Scene tables as one stream: fixed records, name tables and index lists.

Each table is rebuilt byte for byte on reading, so the table readers stay as they are.
Records go field by field, each field a number added to the record before's; names
share what starts them with the name before; index lists step from one to the next.
"""

import struct
from enum import IntEnum

from ...container import layout
from ...container.layout import SectionKind, align, bits_to_ordered, ordered_to_bits
from ...container.sections import Section
from ...errors import FormatError
from ...storage.names import encode_names, read_names
from ...storage.rans import SymbolReader, SymbolWriter

_WORD = struct.Struct("<I")
_FLOAT_FIELD = "f"

# Tables of fixed records, by the record each holds.
_RECORDS = {
    SectionKind.OBJECT_INFO: layout.OBJECT_INFO,
    SectionKind.POSE_CHANNELS: layout.POSE_CHANNEL,
    SectionKind.MATERIAL_INFO: layout.MATERIAL_INFO,
    SectionKind.TEXTURE_SLOTS: layout.TEXTURE_SLOT,
    SectionKind.VALUE_NODES: layout.VALUE_NODE,
    SectionKind.VALUE_OWNERS: layout.VALUE_OWNER,
    SectionKind.CURVE_INFO: layout.CURVE_INFO,
    SectionKind.SPLINE_INFO: layout.SPLINE_INFO,
    SectionKind.LATTICE_INFO: layout.LATTICE_INFO,
    SectionKind.CLIP_INFO: layout.CLIP_INFO,
    SectionKind.EXTENSION_INFO: layout.EXTENSION_INFO,
    SectionKind.SAMPLERS: layout.SAMPLER,
    SectionKind.IMAGE_INFO: layout.IMAGE_INFO,
    SectionKind.IMAGE_TILES: layout.IMAGE_TILE,
    SectionKind.LIGHT_INFO: layout.LIGHT_INFO,
    SectionKind.BONE_INFO: layout.BONE_INFO,
}
_NAMES = frozenset(
    {
        SectionKind.ARMATURE_NAMES,
        SectionKind.BONE_NAMES,
        SectionKind.CAMERA_NAMES,
        SectionKind.CLIP_NAMES,
        SectionKind.COLLECTION_NAMES,
        SectionKind.CURVE_NAMES,
        SectionKind.EXTENSION_NAMES,
        SectionKind.IMAGE_COLOR_SPACES,
        SectionKind.IMAGE_NAMES,
        SectionKind.IMAGE_PATHS,
        SectionKind.LATTICE_NAMES,
        SectionKind.LIGHT_NAMES,
        SectionKind.MATERIAL_NAMES,
        SectionKind.OBJECT_NAMES,
        SectionKind.TEXTURE_ROLES,
        SectionKind.TEXTURE_UV_NAMES,
        SectionKind.VALUE_STRINGS,
    }
)
_INDICES = frozenset(
    {
        SectionKind.ARMATURE_BONE_STARTS,
        SectionKind.COLLECTION_PARENTS,
        SectionKind.COLLECTION_MEMBER_STARTS,
        SectionKind.COLLECTION_MEMBERS,
        SectionKind.SKIN_STARTS,
        SectionKind.SKIN_BONES,
        SectionKind.TIME_STARTS,
    }
)
# Orders and child lists that follow from the parents, kept only for readers that
# index the file in place. A packed file is read whole, so it leaves them out.
_DERIVED = frozenset(
    {
        SectionKind.OBJECT_ORDER,
        SectionKind.OBJECT_CHILD_STARTS,
        SectionKind.OBJECT_CHILDREN,
        SectionKind.BONE_ORDER,
        SectionKind.BONE_CHILD_STARTS,
        SectionKind.BONE_CHILDREN,
    }
)


class _Context(IntEnum):
    KIND = 0
    COUNT = 1
    WHOLE_FIELD = 2
    FLOAT_FIELD = 3
    SHARED = 4
    LENGTH = 5
    BYTE = 6
    INDEX = 7


def pack_tables(sections: list) -> list:
    """Return the sections with every table this stream holds in one PACKED_TABLES
    section, and the orders and child lists that follow from parents left out."""
    kept = []
    tables = []
    for section in sections:
        if section.kind in _DERIVED:
            continue
        if _holds(section):
            tables.append(section)
        else:
            kept.append(section)
    if not tables:
        return kept
    writer = SymbolWriter(len(_Context))
    writer.write_choice(_Context.COUNT, len(tables))
    previous = 0
    for section in sorted(tables, key=lambda item: item.kind):
        writer.write_choice(_Context.KIND, section.kind - previous)
        previous = section.kind
        _write_table(writer, section)
    return [*kept, Section(SectionKind.PACKED_TABLES, writer.to_bytes())]


def unpack_tables(data: bytes, max_bytes: int) -> list:
    """Return the tables a PACKED_TABLES section holds, as sections. Raises
    FormatError for a damaged stream or tables past max_bytes in all."""
    reader = _TableReader(data, max_bytes)
    return reader.read_all()


def _holds(section: Section) -> bool:
    plain = section.flags == layout.SectionFlags.NONE and section.layer == 0
    kinds = (
        section.kind in _RECORDS or section.kind in _NAMES or section.kind in _INDICES
    )
    return plain and kinds


def _padded(data: bytes) -> bytes:
    """Return a table with the zero bytes a file pads it with."""
    return data + bytes(align(len(data)) - len(data))


def _float_fields(record: struct.Struct) -> list:
    """Return, for each 4-byte field of a record, whether it holds a Float32."""
    fields = []
    repeat = ""
    for character in record.format.lstrip("<"):
        if character.isdigit():
            repeat += character
            continue
        fields += [character == _FLOAT_FIELD] * int(repeat or 1)
        repeat = ""
    return fields


def _write_table(writer: SymbolWriter, section: Section) -> None:
    if section.kind in _RECORDS:
        _write_records(writer, section.data, _RECORDS[section.kind])
    elif section.kind in _NAMES:
        _write_names(writer, read_names(_padded(section.data)))
    else:
        _write_indices(writer, section.data)


def _write_records(writer: SymbolWriter, data: bytes, record: struct.Struct) -> None:
    floats = _float_fields(record)
    words = [word for (word,) in _WORD.iter_unpack(data)]
    count = len(data) // record.size
    writer.write_choice(_Context.COUNT, count)
    for field, is_float in enumerate(floats):
        context = _Context.FLOAT_FIELD if is_float else _Context.WHOLE_FIELD
        previous = 0
        for at in range(count):
            value = words[at * len(floats) + field]
            value = bits_to_ordered(value) if is_float else value
            writer.write_number(context, value - previous)
            previous = value


def _write_names(writer: SymbolWriter, names: list) -> None:
    writer.write_choice(_Context.COUNT, len(names))
    previous = b""
    for name in names:
        encoded = name.encode()
        shared = 0
        while (
            shared < min(len(encoded), len(previous))
            and encoded[shared] == previous[shared]
        ):
            shared += 1
        writer.write_choice(_Context.SHARED, shared)
        writer.write_choice(_Context.LENGTH, len(encoded) - shared)
        for byte in encoded[shared:]:
            writer.write(_Context.BYTE, byte)
        previous = encoded


def _write_indices(writer: SymbolWriter, data: bytes) -> None:
    values = [value for (value,) in _WORD.iter_unpack(data)]
    writer.write_choice(_Context.COUNT, len(values))
    previous = 0
    for value in values:
        writer.write_number(_Context.INDEX, value - previous)
        previous = value


class _TableReader:
    def __init__(self, data: bytes, max_bytes: int):
        self._reader = SymbolReader(data, len(_Context))
        self._budget = max_bytes

    def read_all(self) -> list:
        sections = []
        kind = 0
        for _ in range(self._reader.read_choice(_Context.COUNT)):
            step = self._reader.read_choice(_Context.KIND)
            if sections and not step:
                raise FormatError("Packed tables repeat a table")
            kind += step
            sections.append(Section(self._known(kind), self._read_table(kind)))
        return sections

    @staticmethod
    def _known(kind: int) -> SectionKind:
        if kind in _RECORDS or kind in _NAMES or kind in _INDICES:
            return SectionKind(kind)
        raise FormatError("Packed tables hold an unknown table")

    def _read_table(self, kind: int) -> bytes:
        if kind in _RECORDS:
            return self._read_records(_RECORDS[kind])
        if kind in _NAMES:
            return self._read_names()
        return self._read_indices()

    def _count(self, item_bytes: int) -> int:
        """Return the next count, charging its bytes to the budget."""
        count = self._reader.read_choice(_Context.COUNT)
        self._spend(count * item_bytes)
        return count

    def _spend(self, size: int) -> None:
        self._budget -= size
        if self._budget < 0:
            raise FormatError("Packed tables exceed the configured size limit")

    def _read_records(self, record: struct.Struct) -> bytes:
        floats = _float_fields(record)
        count = self._count(record.size)
        words = [0] * (count * len(floats))
        for field, is_float in enumerate(floats):
            context = _Context.FLOAT_FIELD if is_float else _Context.WHOLE_FIELD
            value = 0
            for at in range(count):
                value += self._reader.read_number(context)
                words[at * len(floats) + field] = self._word(value, is_float)
        return struct.pack(f"<{len(words)}I", *words)

    @staticmethod
    def _word(value: int, is_float: bool) -> int:
        if not 0 <= value <= layout.UINT32_MAX:
            raise FormatError("A packed table field is out of range")
        return ordered_to_bits(value) if is_float else value

    def _read_names(self) -> bytes:
        count = self._count(_WORD.size)
        names = []
        previous = b""
        for _ in range(count):
            shared = self._reader.read_choice(_Context.SHARED)
            length = self._reader.read_choice(_Context.LENGTH)
            if shared > len(previous):
                raise FormatError("A packed name shares more than the name before")
            self._spend(length + _WORD.size)
            suffix = bytes(self._reader.read(_Context.BYTE) for _ in range(length))
            previous = previous[:shared] + suffix
            names.append(previous)
        try:
            return _padded(encode_names([name.decode() for name in names]))
        except UnicodeDecodeError as error:
            raise FormatError("A packed name is not UTF-8") from error

    def _read_indices(self) -> bytes:
        count = self._count(_WORD.size)
        values = []
        value = 0
        for _ in range(count):
            value += self._reader.read_number(_Context.INDEX)
            values.append(self._word(value, False))
        return struct.pack(f"<{count}I", *values)
