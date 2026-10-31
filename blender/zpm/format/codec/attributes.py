# SPDX-License-Identifier: GPL-3.0-or-later

"""Custom attributes: named data on any domain, stored exactly.

Each attribute is one column holding all of component 0, then all of component 1, and
so on: a component's neighbours are alike, so blocks stay narrow, and any value is
at component * elements + element. Floats use their ordered bits, so close values are
close integers and every bit pattern survives.
"""

from array import array

from ..container.layout import (
    MAX_LAYERS,
    AttributeDomain,
    AttributeMeaning,
    AttributeType,
    SectionKind,
    bits_to_ordered,
    ordered_to_bits,
    to_signed,
)
from ..container.sections import Section, SectionReader, column_section
from ..errors import FormatError
from ..mesh import ATTRIBUTE_TYPECODES, Attribute, Mesh
from ..storage.names import encode_names, read_names

_TYPE_SHIFT = 8
_COMPONENTS_SHIFT = 16
_MEANING_SHIFT = 24
_FIELD_MASK = 255
_SIGNED_TYPES = (AttributeType.INT8, AttributeType.INT16, AttributeType.INT32)


def _pack_format(attribute: Attribute) -> int:
    return (
        attribute.domain
        | attribute.value_type << _TYPE_SHIFT
        | attribute.components << _COMPONENTS_SHIFT
        | attribute.meaning << _MEANING_SHIFT
    )


def _stored_values(attribute: Attribute) -> list:
    values = attribute.values
    if attribute.value_type == AttributeType.FLOAT32:
        bits = array("I")
        bits.frombytes(values.tobytes())
        values = [bits_to_ordered(value) for value in bits]
    return [
        value
        for component in range(attribute.components)
        for value in values[component :: attribute.components]
    ]


def encode_attributes(mesh: Mesh) -> list:
    """Return the name and format tables, and one column per attribute."""
    names = sorted(mesh.attributes)
    if len(names) > MAX_LAYERS:
        raise FormatError("The mesh has too many custom attributes")
    if not names:
        return []
    attributes = [mesh.attributes[name] for name in names]
    formats = array("I", [_pack_format(attribute) for attribute in attributes])
    sections = [
        Section(SectionKind.ATTRIBUTE_NAMES, encode_names(names)),
        Section(SectionKind.ATTRIBUTE_FORMATS, formats.tobytes()),
    ]
    for layer, attribute in enumerate(attributes):
        sections.append(
            column_section(
                SectionKind.ATTRIBUTE_VALUES, _stored_values(attribute), layer=layer
            )
        )
    return sections


def _to_values(stored: list, value_type: AttributeType) -> array:
    if value_type == AttributeType.FLOAT32:
        bits = array("I", [ordered_to_bits(value) for value in stored])
        values = array("f")
        values.frombytes(bits.tobytes())
        return values
    if value_type == AttributeType.BOOL and stored and max(stored) > 1:
        raise FormatError("A boolean attribute holds a value other than 0 or 1")
    if value_type in _SIGNED_TYPES:
        stored = [to_signed(value) for value in stored]
    try:
        return array(ATTRIBUTE_TYPECODES[value_type], stored)
    except OverflowError as error:
        raise FormatError("An attribute value is out of range") from error


def _read_attribute(sections, stored_format, layer, sizes) -> Attribute:
    try:
        domain = AttributeDomain(stored_format & _FIELD_MASK)
        value_type = AttributeType(stored_format >> _TYPE_SHIFT & _FIELD_MASK)
        meaning = AttributeMeaning(stored_format >> _MEANING_SHIFT & _FIELD_MASK)
    except ValueError as error:
        raise FormatError("An attribute has an unknown format") from error
    components = stored_format >> _COMPONENTS_SHIFT & _FIELD_MASK
    if not components:
        raise FormatError("An attribute has no components")
    elements = sizes[domain]
    count = elements * components
    stored = sections.column(SectionKind.ATTRIBUTE_VALUES, count, layer)
    values = _to_values(stored, value_type)
    interleaved = array(values.typecode, values)
    for component in range(components):
        start = component * elements
        interleaved[component::components] = values[start : start + elements]
    return Attribute(domain, value_type, components, interleaved, meaning)


def read_attributes(sections: SectionReader, sizes: dict) -> dict:
    """Return name to Attribute. sizes gives each AttributeDomain's element count."""
    if not sections.has(SectionKind.ATTRIBUTE_NAMES):
        return {}
    names = read_names(sections.raw(SectionKind.ATTRIBUTE_NAMES))
    formats = array("I")
    formats.frombytes(sections.raw(SectionKind.ATTRIBUTE_FORMATS))
    if len(formats) != len(names):
        raise FormatError("Attribute formats do not match their names")
    return {
        name: _read_attribute(sections, formats[layer], layer, sizes)
        for layer, name in enumerate(names)
    }
