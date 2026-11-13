# SPDX-License-Identifier: GPL-3.0-or-later

"""Blender's settings as plain named values, for data the format holds generally."""

from ..format import Reference

_SKIPPED = {"rna_type", "name", "type", "is_active", "bl_rna"}
_PRIVATE_PREFIXES = ("zpm_", "_RNA_UI", "cycles")


def capture_rna(item, object_index: dict, skip=frozenset()) -> dict:
    """Return every writable setting that differs from its default.

    Object links become References when that object is being saved. Other links and
    lists are left out: they belong to whoever owns them.
    """
    values = {}
    for prop in item.bl_rna.properties:
        name = prop.identifier
        if name in _SKIPPED or name in skip or prop.is_readonly:
            continue
        value = _capture_property(item, prop, object_index)
        if value is not None:
            values[name] = value
    return values


def capture_named(item, names: tuple, object_index: dict) -> dict:
    """Return these settings, for those that differ from their default."""
    properties = item.bl_rna.properties
    values = {}
    for name in names:
        if name in properties:
            value = _capture_property(item, properties[name], object_index)
            if value is not None:
                values[name] = value
    return values


def _capture_property(item, prop, object_index: dict):
    value = getattr(item, prop.identifier)
    if prop.type == "POINTER":
        if prop.fixed_type.identifier == "Object" and value is not None:
            index = object_index.get(value.name)
            return Reference(index) if index is not None else None
        return None
    if prop.type in ("BOOLEAN", "INT", "FLOAT"):
        if prop.array_length:
            if len(prop.array_dimensions) > 1 and prop.array_dimensions[1]:
                return None
            stored = list(value)
            return stored if stored != list(prop.default_array) else None
        return value if value != prop.default else None
    if prop.type == "ENUM" and not prop.is_enum_flag:
        return value if value != prop.default else None
    if prop.type == "STRING":
        return value if value != prop.default else None
    return None


def apply_rna(item, values: dict, objects: list) -> None:
    """Set values by name. A setting this Blender version lacks is left alone."""
    links = {k: v for k, v in values.items() if isinstance(v, Reference)}
    for name, link in links.items():
        _set(item, name, objects[link.index])
    for name, value in values.items():
        if name not in links:
            _set(item, name, value)


def _set(item, name: str, value) -> None:
    try:
        setattr(item, name, value)
    except (AttributeError, TypeError, ValueError):
        pass


def capture_properties(item) -> dict:
    """Return an ID's custom properties, without those ZPM or renderers keep there."""
    return {
        key: _plain(item[key])
        for key in item.keys()
        if not key.startswith(_PRIVATE_PREFIXES)
    }


def _plain(value):
    if hasattr(value, "to_dict"):
        return value.to_dict()
    if hasattr(value, "to_list"):
        return value.to_list()
    return value


def apply_properties(item, values: dict) -> None:
    for key, value in values.items():
        item[key] = value
