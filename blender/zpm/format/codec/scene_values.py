# SPDX-License-Identifier: GPL-3.0-or-later

"""Value trees: typed, named data any resource can carry, readable in place.

A tree is a table of fixed nodes. A dictionary or list node points at its children,
always later in the table, so walking can never loop. Strings are shared across the
whole file, and arrays sit in one column, so repeats and small numbers cost little.
"""

from ..container.layout import (
    MAX_VALUE_DEPTH,
    NO_INDEX,
    UINT32_MAX,
    VALUE_NODE,
    VALUE_OWNER,
    ResourceKind,
    SectionKind,
    ValueType,
    bits_to_ordered,
    float_bits,
    float_from_bits,
    ordered_to_bits,
    to_signed,
)
from ..container.sections import Section, SectionReader, column_section
from ..errors import FormatError
from ..scene import TREE_FIELDS, Reference, Scene
from ..storage.names import encode_names, read_names
from .scene_tables import checked_enum, read_records

_INT_LIMIT = 1 << 31


class StringTable:
    """Hands out one index per distinct string."""

    def __init__(self):
        self._indices = {}

    def add(self, text: str) -> int:
        return self._indices.setdefault(text, len(self._indices))

    def section(self) -> list:
        if not self._indices:
            return []
        return [Section(SectionKind.VALUE_STRINGS, encode_names(list(self._indices)))]


def read_strings(sections: SectionReader) -> list:
    kind = SectionKind.VALUE_STRINGS
    return read_names(sections.raw(kind)) if sections.has(kind) else []


def _stored_int(value: int) -> int:
    if not -_INT_LIMIT <= value < _INT_LIMIT:
        raise FormatError(f"A value does not fit 32 bits: {value}")
    return value & UINT32_MAX


class _TreeWriter:
    def __init__(self, strings: StringTable):
        self._strings = strings
        self.nodes = []
        self.pool = []

    def _array(self, values: list) -> tuple:
        first = len(self.pool)
        self.pool += values
        return first, len(values)

    def _children(self, items: list) -> tuple:
        first = len(self.nodes)
        self.nodes += [None] * len(items)
        for at, (key, value) in enumerate(items):
            self.nodes[first + at] = self.pack(key, value)
        return first, len(items)

    def pack(self, key: str, value) -> bytes:
        name = self._strings.add(key) if key else NO_INDEX
        kind, a, b = self._describe(value)
        return VALUE_NODE.pack(name, kind, a, b)

    def _describe(self, value) -> tuple:
        if isinstance(value, bool):
            return ValueType.BOOL, int(value), 0
        if isinstance(value, int):
            return ValueType.INT, _stored_int(value), 0
        if isinstance(value, float):
            return ValueType.FLOAT, float_bits(value), 0
        if isinstance(value, str):
            return ValueType.STRING, self._strings.add(value), 0
        if isinstance(value, Reference):
            return ValueType.REFERENCE, value.index, 0
        if isinstance(value, dict):
            return (ValueType.DICT, *self._children(list(value.items())))
        return self._describe_list(list(value))

    def _describe_list(self, value: list) -> tuple:
        if value and all(type(item) is float for item in value):
            ordered = [bits_to_ordered(float_bits(item)) for item in value]
            return (ValueType.FLOAT_ARRAY, *self._array(ordered))
        if value and all(type(item) is int for item in value):
            stored = [_stored_int(item) for item in value]
            return (ValueType.INT_ARRAY, *self._array(stored))
        return (ValueType.LIST, *self._children([("", item) for item in value]))


def _tree_of(resource) -> dict:
    return {
        name: getattr(resource, name)
        for name in TREE_FIELDS[type(resource).__name__]
        if getattr(resource, name)
    }


def encode_values(scene: Scene, strings: StringTable) -> list:
    """Return every resource's tree. Resources with nothing to say store nothing."""
    writer = _TreeWriter(strings)
    owners = bytearray()
    for kind in ResourceKind:
        for index, resource in enumerate(scene.resources(kind)):
            tree = _tree_of(resource)
            if tree:
                writer.nodes.append(None)
                slot = len(writer.nodes) - 1
                writer.nodes[slot] = writer.pack("", tree)
                owners += VALUE_OWNER.pack(kind, index, slot)
    if not writer.nodes:
        return []
    sections = [
        Section(SectionKind.VALUE_NODES, b"".join(writer.nodes)),
        Section(SectionKind.VALUE_OWNERS, bytes(owners)),
    ]
    if writer.pool:
        sections.append(column_section(SectionKind.VALUE_POOL, writer.pool))
    return sections


class _TreeReader:
    def __init__(self, sections: SectionReader, strings: list):
        kind = SectionKind.VALUE_NODES
        count = len(sections.raw(kind)) // VALUE_NODE.size if sections.has(kind) else 0
        self._nodes = read_records(sections, kind, VALUE_NODE, count)
        pool = SectionKind.VALUE_POOL
        self._pool = (
            sections.column(pool, sections.count_of(pool)) if sections.has(pool) else []
        )
        self._strings = strings

    def _string(self, index: int) -> str:
        if index >= len(self._strings):
            raise FormatError("A value names a missing string")
        return self._strings[index]

    def _children(self, at: int, first: int, count: int) -> list:
        if first <= at or first + count > len(self._nodes):
            raise FormatError("A value has children out of order")
        return list(range(first, first + count))

    def _slice(self, first: int, count: int) -> list:
        if first + count > len(self._pool):
            raise FormatError("A value runs past its pool")
        return self._pool[first : first + count]

    def value(self, at: int, depth: int = 0):
        """Return the value at one node. Raises FormatError if it is malformed."""
        if depth > MAX_VALUE_DEPTH or at >= len(self._nodes):
            raise FormatError("A value is too deep or missing")
        _, raw_type, a, b = self._nodes[at]
        kind = checked_enum(ValueType, raw_type)
        if kind == ValueType.BOOL:
            return bool(a)
        if kind == ValueType.INT:
            return to_signed(a)
        if kind == ValueType.FLOAT:
            return float_from_bits(a)
        if kind == ValueType.STRING:
            return self._string(a)
        if kind == ValueType.REFERENCE:
            return Reference(a)
        if kind == ValueType.INT_ARRAY:
            return [to_signed(v) for v in self._slice(a, b)]
        if kind == ValueType.FLOAT_ARRAY:
            return [float_from_bits(ordered_to_bits(v)) for v in self._slice(a, b)]
        children = self._children(at, a, b)
        if kind == ValueType.LIST:
            return [self.value(child, depth + 1) for child in children]
        return {
            self._string(self._nodes[child][0]): self.value(child, depth + 1)
            for child in children
        }


def read_values(sections: SectionReader, scene: Scene, strings: list) -> None:
    """Fill each resource's tree fields from the file."""
    kind = SectionKind.VALUE_OWNERS
    if not sections.has(kind):
        return
    count = len(sections.raw(kind)) // VALUE_OWNER.size
    reader = _TreeReader(sections, strings)
    for raw_kind, index, node in read_records(sections, kind, VALUE_OWNER, count):
        resources = scene.resources(checked_enum(ResourceKind, raw_kind))
        if index >= len(resources):
            raise FormatError("A value tree has a missing owner")
        resource = resources[index]
        tree = reader.value(node)
        if not isinstance(tree, dict):
            raise FormatError("A resource's values must be a dictionary")
        allowed = TREE_FIELDS[type(resource).__name__]
        for name, value in tree.items():
            if name not in allowed:
                raise FormatError(f"Unknown value field: {name}")
            setattr(resource, name, value)
