# SPDX-License-Identifier: GPL-3.0-or-later

"""Blender's settings as plain named values, for data the format holds generally."""

import bpy

from ..format import Reference

_SKIPPED = {"rna_type", "name", "type", "is_active", "bl_rna"}
_PRIVATE_PREFIXES = ("zpm_", "_RNA_UI", "cycles")
_ID_COLLECTIONS = {
    "ARMATURE": "armatures",
    "CAMERA": "cameras",
    "CURVE": "curves",
    "KEY": "shape_keys",
    "LATTICE": "lattices",
    "LIGHT": "lights",
    "MATERIAL": "materials",
    "MESH": "meshes",
    "SCENE": "scenes",
    "WORLD": "worlds",
}


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


def capture_drivers(block, object_index: dict) -> list:
    """Return a data block's drivers, kept for Blender, not for other readers."""
    animation = block.animation_data
    if animation is None:
        return []
    return [
        {
            "path": curve.data_path,
            "index": curve.array_index,
            "type": curve.driver.type,
            "expression": curve.driver.expression,
            "variables": [
                _capture_variable(variable, object_index)
                for variable in curve.driver.variables
            ],
        }
        for curve in animation.drivers
    ]


def _capture_variable(variable, object_index: dict) -> dict:
    return {
        "name": variable.name,
        "type": variable.type,
        "targets": [
            _capture_target(target, object_index) for target in variable.targets
        ],
    }


def _capture_target(target, object_index: dict) -> dict:
    """Return a driver target. Objects link by index, other data blocks by name."""
    values = capture_rna(target, object_index, skip={"id"})
    linked = target.id
    if linked is None:
        return values
    if linked.name in object_index and target.id_type == "OBJECT":
        values["id"] = Reference(object_index[linked.name])
    else:
        values["id_type"] = target.id_type
        values["id_name"] = linked.name
    return values


def _apply_target(target, stored: dict, objects: list) -> None:
    values = dict(stored)
    name = values.pop("id_name", "")
    id_type = values.pop("id_type", None)
    apply_rna(target, values, objects)
    if name and id_type in _ID_COLLECTIONS:
        _set(target, "id_type", id_type)
        block = getattr(bpy.data, _ID_COLLECTIONS[id_type]).get(name)
        _set(target, "id", block)


def _add_driver(block, path: str, index: int):
    """Add a driver. A field holding one value takes no array index."""
    try:
        return block.driver_add(path, index)
    except TypeError:
        return block.driver_add(path)


def apply_drivers(block, drivers: list, objects: list) -> None:
    for driver in drivers:
        curve = _add_driver(block, driver["path"], driver["index"])
        curve.driver.type = driver["type"]
        curve.driver.expression = driver["expression"]
        for stored in driver["variables"]:
            variable = curve.driver.variables.new()
            variable.name = stored["name"]
            variable.type = stored["type"]
            for target, values in zip(
                variable.targets, stored["targets"], strict=False
            ):
                _apply_target(target, values, objects)
