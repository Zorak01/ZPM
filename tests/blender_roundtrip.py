# SPDX-License-Identifier: GPL-3.0-or-later

"""Run with blender --background --factory-startup --python this_file."""

import sys
import tempfile
from pathlib import Path

import bpy
import mathutils

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "blender"))

import zpm
from zpm import mesh_io
from zpm.format import FormatError, load, same_geometry, snap_mesh


def check_surface_data(directory):
    """UVs, sharp edges, seams, creases, flat faces and custom normals survive."""
    bpy.ops.mesh.primitive_monkey_add()
    obj = bpy.context.active_object
    mesh = obj.data
    second = mesh.uv_layers.new(name="Detail")
    for index, data in enumerate(second.uv):
        data.vector = (index * 0.01, index * 0.02)
    mesh.edges[3].use_edge_sharp = True
    mesh.edges[5].use_seam = True
    mesh.polygons[2].use_smooth = True
    creases = mesh.attributes.new("crease_edge", "FLOAT", "EDGE")
    creases.data[7].value = 0.5
    vertex_creases = mesh.attributes.new("crease_vert", "FLOAT", "POINT")
    vertex_creases.data[9].value = 0.25
    expected = snap_mesh(mesh_io.capture(mesh))
    path = str(Path(directory) / "surface.zpm")
    assert bpy.ops.export_mesh.zpm(filepath=path) == {"FINISHED"}
    result = mesh_io.create(load(path), name="surface-result")
    actual = snap_mesh(mesh_io.capture(result))
    assert same_geometry(actual, expected, compare_normals=False)
    assert sorted(result.uv_layers.keys()) == sorted(mesh.uv_layers.keys())
    assert "Detail" in result.uv_layers

    normals = [(0.0, 0.6, 0.8)] * len(mesh.loops)
    mesh.normals_split_custom_set(normals)
    assert mesh.has_custom_normals
    assert bpy.ops.export_mesh.zpm(filepath=path) == {"FINISHED"}
    result = mesh_io.create(load(path), name="custom-result")
    assert result.has_custom_normals
    # Blender's own custom normal storage is off by about 0.0105 before ZPM is involved.
    for corner in result.corner_normals:
        assert (corner.vector - mathutils.Vector(normals[0])).length < 0.02
    print("PASS: surface data and custom normals")


def run():
    zpm.register()
    cases = {
        "mixed": (
            [
                (0, 0, 0),
                (1, 0, 0),
                (1, 1, 0),
                (0, 1, 0),
                (-1, 1, 0),
                (-1, 0, 0),
                (2, 2, 2),
                (3, 3, 3),
            ],
            [(6, 7)],
            [(0, 1, 2), (0, 2, 3, 4), (0, 1, 2, 4, 5)],
        ),
        "wire": ([(0, 0, 0), (1, 2, 3)], [(1, 0)], []),
        "empty": ([], [], []),
        "nonmanifold": (
            [(0, 0, 0), (1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, 1)],
            [],
            [(0, 1, 2), (1, 0, 3), (0, 1, 4)],
        ),
    }
    with tempfile.TemporaryDirectory() as directory:
        for name, (vertices, edges, faces) in cases.items():
            source = bpy.data.meshes.new(name)
            source.from_pydata(vertices, edges, faces)
            obj = bpy.data.objects.new(name, source)
            bpy.context.collection.objects.link(obj)
            bpy.context.view_layer.objects.active = obj
            obj.location = (10, 20, 30)
            expected = snap_mesh(mesh_io.capture(source))
            path = str(Path(directory) / f"{name}.zpm")
            assert bpy.ops.export_mesh.zpm(filepath=path) == {"FINISHED"}
            geometry = load(path)
            result = mesh_io.create(geometry, name="api-result")
            assert same_geometry(
                mesh_io.capture(result), expected, compare_normals=False
            ), name
            before = set(bpy.data.objects.keys())
            assert bpy.ops.import_mesh.zpm(filepath=path) == {"FINISHED"}
            imported = set(bpy.data.objects.keys()) - before
            assert len(imported) == 1
            imported_obj = bpy.data.objects[imported.pop()]
            assert same_geometry(
                mesh_io.capture(imported_obj.data), expected, compare_normals=False
            ), name
            assert tuple(imported_obj.location) == (0, 0, 0)
            assert tuple(obj.location) == (10, 20, 30)
            print(f"PASS: {name}, API and file operators")
        check_surface_data(directory)
        geometry = mesh_io.capture(source)
        geometry.corners[0] = geometry.vertex_count + 1
        before = len(bpy.data.meshes)
        try:
            mesh_io.create(geometry, name="invalid")
        except FormatError:
            pass
        else:
            raise AssertionError("Invalid geometry was accepted")
        assert len(bpy.data.meshes) == before
    zpm.unregister()
    zpm.register()
    zpm.unregister()
    print(f"ZPM BLENDER TESTS PASSED: {bpy.app.version_string}")


run()
