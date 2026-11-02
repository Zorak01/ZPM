# SPDX-License-Identifier: GPL-3.0-or-later

"""Point clouds, hair curves, and data with settings only, to and from the format.

Point clouds and hair hold what a mesh's columns hold, so they are stored as meshes with
no faces, and any reader can use them as points and wires. A property names what they
were. Volumes, speakers and light probes are kept as settings on their object.
"""

from array import array
from pathlib import Path

import bpy

from ..format import Extension, Mesh
from . import mesh_adapter, rna_values

SOURCE_TYPE_PROPERTY = "source_object_type"
SOURCE_SETTINGS_PROPERTY = "source_settings"
SOURCE_DATA_SETTING = "source_data"
POINT_CLOUD = "POINTCLOUD"
HAIR = "CURVES"
VOLUME = "VOLUME"
POINT_DATA_TYPES = {POINT_CLOUD, HAIR}
SETTINGS_DATA_TYPES = {VOLUME, "SPEAKER", "LIGHT_PROBE"}
VOLUME_EXTENSION_PREFIX = "blender.volume:"
_CURVE_SIZES_PROPERTY = "curve_sizes"
_CURVE_ATTRIBUTES_PROPERTY = "curve_attributes"
_POSITION = "position"
_HIDDEN_PREFIX = "."
_POINT_DOMAIN = "POINT"
_CURVE_DOMAIN = "CURVE"
_VOLUME_SUFFIX = ".vdb"
_VOLUME_PARTS = ("display", "render")


def _positions_of(data) -> array:
    stored = data.attributes[_POSITION].data
    positions = array("f", [0.0]) * (len(stored) * 3)
    stored.foreach_get("vector", positions)
    return positions


def _point_attributes(data) -> dict:
    return {
        attribute.name: mesh_adapter.capture_attribute(attribute)
        for attribute in data.attributes
        if attribute.domain == _POINT_DOMAIN
        and attribute.name != _POSITION
        and not attribute.name.startswith(_HIDDEN_PREFIX)
    }


def _material_slots(data) -> list:
    return [material.name if material else "" for material in data.materials]


def capture_point_cloud(data: bpy.types.PointCloud, object_index: dict) -> Mesh:
    """Copy a point cloud as a mesh of loose points."""
    return Mesh(
        _positions_of(data),
        array("I"),
        array("I"),
        array("I"),
        attributes=_point_attributes(data),
        material_slots=_material_slots(data),
        properties={
            **rna_values.capture_properties(data),
            SOURCE_TYPE_PROPERTY: POINT_CLOUD,
            SOURCE_SETTINGS_PROPERTY: rna_values.capture_rna(data, object_index),
        },
    )


def _curve_attributes(data) -> dict:
    stored = {}
    for attribute in data.attributes:
        if attribute.domain != _CURVE_DOMAIN:
            continue
        if attribute.name.startswith(_HIDDEN_PREFIX):
            continue
        values = mesh_adapter.capture_attribute(attribute, keep_domain=False)
        stored[attribute.name] = {
            "type": attribute.data_type,
            "values": list(values.values),
        }
    return stored


def capture_hair(data: bpy.types.Curves, object_index: dict) -> Mesh:
    """Copy hair curves as a mesh of wires, one run of edges per curve."""
    sizes = [curve.points_length for curve in data.curves]
    edges = array("I")
    first = 0
    for size in sizes:
        for point in range(first, first + size - 1):
            edges.extend((point, point + 1))
        first += size
    return Mesh(
        _positions_of(data),
        array("I"),
        array("I"),
        edges,
        attributes=_point_attributes(data),
        material_slots=_material_slots(data),
        properties={
            **rna_values.capture_properties(data),
            SOURCE_TYPE_PROPERTY: HAIR,
            SOURCE_SETTINGS_PROPERTY: rna_values.capture_rna(data, object_index),
            _CURVE_SIZES_PROPERTY: sizes,
            _CURVE_ATTRIBUTES_PROPERTY: _curve_attributes(data),
        },
    )


def _set_positions_and_attributes(data, mesh: Mesh) -> None:
    data.attributes[_POSITION].data.foreach_set("vector", mesh.positions)
    for name, attribute in mesh.attributes.items():
        mesh_adapter.apply_attribute(data.attributes, name, attribute)


def _create_point_cloud(mesh: Mesh, name: str):
    data = bpy.data.pointclouds.new(name)
    data.resize(mesh.vertex_count)
    _set_positions_and_attributes(data, mesh)
    return data


def _create_hair(mesh: Mesh, name: str):
    data = bpy.data.hair_curves.new(name)
    data.add_curves(mesh.properties[_CURVE_SIZES_PROPERTY])
    _set_positions_and_attributes(data, mesh)
    for attribute_name, stored in mesh.properties[_CURVE_ATTRIBUTES_PROPERTY].items():
        target = data.attributes.get(attribute_name) or data.attributes.new(
            attribute_name, stored["type"], _CURVE_DOMAIN
        )
        field, code = mesh_adapter.attribute_buffer(stored["type"])
        target.data.foreach_set(field, array(code, stored["values"]))
    return data


def create_point_data(mesh: Mesh, name: str):
    """Create the point cloud or hair a mesh of this kind came from."""
    if mesh.properties[SOURCE_TYPE_PROPERTY] == POINT_CLOUD:
        return _create_point_cloud(mesh, name)
    return _create_hair(mesh, name)


def plain_properties(mesh: Mesh) -> dict:
    """Return a mesh's properties without what this module keeps there."""
    kept = (
        SOURCE_TYPE_PROPERTY,
        SOURCE_SETTINGS_PROPERTY,
        _CURVE_SIZES_PROPERTY,
        _CURVE_ATTRIBUTES_PROPERTY,
    )
    return {key: value for key, value in mesh.properties.items() if key not in kept}


def capture_settings_data(data, object_type: str) -> dict:
    """Return settings of data with no geometry of its own."""
    stored = {
        "type": object_type,
        "name": data.name,
        "settings": rna_values.capture_rna(data, {}),
        "properties": rna_values.capture_properties(data),
    }
    if object_type == "LIGHT_PROBE":
        stored["probe_type"] = data.type
    if object_type == VOLUME:
        stored["parts"] = {
            part: rna_values.capture_rna(getattr(data, part), {})
            for part in _VOLUME_PARTS
        }
    return stored


def capture_volume_file(data: bpy.types.Volume) -> Extension | None:
    """Return a volume's file as an extension, or None if it has none to read."""
    payload = bytes(data.packed_file.data) if data.packed_file else b""
    if not payload and data.filepath:
        file = Path(bpy.path.abspath(data.filepath))
        payload = file.read_bytes() if file.is_file() else b""
    if not payload:
        return None
    return Extension(VOLUME_EXTENSION_PREFIX + data.name, payload=payload)


def _restore_volume_file(data, extensions: list) -> None:
    """Use the saved path if it still exists, or else write the stored file out."""
    if Path(bpy.path.abspath(data.filepath)).is_file():
        return
    wanted = VOLUME_EXTENSION_PREFIX + data.name
    for extension in extensions:
        if extension.name == wanted:
            file = Path(bpy.app.tempdir) / (
                bpy.path.clean_name(data.name) + _VOLUME_SUFFIX
            )
            file.write_bytes(extension.payload)
            data.filepath = str(file)
            return


def create_settings_data(stored: dict, extensions: list):
    """Create a volume, speaker or light probe from its settings."""
    kind = stored["type"]
    if kind == VOLUME:
        data = bpy.data.volumes.new(stored["name"])
    elif kind == "SPEAKER":
        data = bpy.data.speakers.new(stored["name"])
    else:
        data = bpy.data.lightprobes.new(stored["name"], stored["probe_type"])
    rna_values.apply_rna(data, stored["settings"], [])
    rna_values.apply_properties(data, stored["properties"])
    if kind == VOLUME:
        for part, values in stored["parts"].items():
            rna_values.apply_rna(getattr(data, part), values, [])
        _restore_volume_file(data, extensions)
    return data
