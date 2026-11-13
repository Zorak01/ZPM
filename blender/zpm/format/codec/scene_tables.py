# SPDX-License-Identifier: GPL-3.0-or-later

"""Small helpers for scene tables: fixed records, name lists and index lists."""

from array import array

from ..container.layout import BYTE_OFFSET, INDEX, bits_to_ordered, ordered_to_bits
from ..container.sections import Section, SectionReader, column_section
from ..errors import FormatError
from ..storage.names import encode_names, read_names


def names_section(kind, names: list) -> list:
    return [Section(kind, encode_names(names))] if names else []


def read_name_list(sections: SectionReader, kind) -> list:
    return read_names(sections.raw(kind)) if sections.has(kind) else []


def indices_section(kind, values: list) -> list:
    return [Section(kind, array("I", values).tobytes())] if values else []


def read_indices(sections: SectionReader, kind, count: int) -> list:
    """Return exactly count UInt32 values. Raises FormatError for any other length."""
    if not count and not sections.has(kind):
        return []
    section = sections.raw(kind)
    if len(section) != count * INDEX.size:
        raise FormatError(f"Section {kind.name} has the wrong size")
    return [value[0] for value in INDEX.iter_unpack(section)]


def read_records(sections: SectionReader, kind, record, count: int) -> list:
    """Return exactly count unpacked records. Raises FormatError for other lengths."""
    if not count and not sections.has(kind):
        return []
    section = sections.raw(kind)
    if len(section) != count * record.size:
        raise FormatError(f"Section {kind.name} has the wrong size")
    return list(record.iter_unpack(section))


def starts_of(groups: list) -> list:
    """Return where each group starts in the joined list, plus one final total."""
    starts = [0]
    for group in groups:
        starts.append(starts[-1] + len(group))
    return starts


def read_starts(sections: SectionReader, kind, count: int) -> list:
    """Return count + 1 rising starts, from zero. Raises FormatError otherwise."""
    if not count and not sections.has(kind):
        return [0]
    starts = read_indices(sections, kind, count + 1)
    if starts[0] != 0 or any(a > b for a, b in zip(starts, starts[1:], strict=False)):
        raise FormatError(f"Section {kind.name} must rise from zero")
    return starts


def byte_offsets_section(kind, groups: list) -> list:
    """Return 64-bit starts of byte strings, plus one final total."""
    packed = b"".join(BYTE_OFFSET.pack(start) for start in starts_of(groups))
    return [Section(kind, packed)]


def read_byte_offsets(sections: SectionReader, kind) -> list:
    """Return rising 64-bit starts from zero. Raises FormatError otherwise."""
    section = sections.raw(kind)
    if not section or len(section) % BYTE_OFFSET.size:
        raise FormatError(f"Section {kind.name} has the wrong size")
    starts = [value[0] for value in BYTE_OFFSET.iter_unpack(section)]
    if starts[0] != 0 or any(a > b for a, b in zip(starts, starts[1:], strict=False)):
        raise FormatError(f"Section {kind.name} must rise from zero")
    return starts


def float_column(kind, values: list) -> Section:
    """Return Float32 values as a column of ordered bits: exact, small when alike."""
    bits = array("I")
    bits.frombytes(array("f", values).tobytes())
    return column_section(kind, [bits_to_ordered(value) for value in bits])


def read_float_column(sections: SectionReader, kind, count: int) -> list:
    if not count and not sections.has(kind):
        return []
    bits = array("I", [ordered_to_bits(v) for v in sections.column(kind, count)])
    values = array("f")
    values.frombytes(bits.tobytes())
    return list(values)


def checked_enum(enum_type, value: int):
    try:
        return enum_type(value)
    except ValueError as error:
        raise FormatError(f"Unknown {enum_type.__name__}: {value}") from error
