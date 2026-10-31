# SPDX-License-Identifier: GPL-3.0-or-later

"""Run with blender --background --factory-startup --python this_file."""

import sys
import tempfile
from pathlib import Path

import bpy

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "blender"))

import zpm
from zpm import mesh_io
from zpm.format import FormatError, load


def snapshot(mesh):
    geometry = mesh_io.capture(mesh)
    return (
        geometry.positions.tobytes(),
        list(geometry.face_sizes),
        list(geometry.corners),
        list(geometry.edges),
    )


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
            expected = snapshot(source)
            for profile in ("compact", "raw32"):
                path = str(Path(directory) / f"{name}-{profile}.zpm")
                assert bpy.ops.export_mesh.zpm(filepath=path, profile=profile) == {
                    "FINISHED"
                }
                geometry = load(path)
                result = mesh_io.create(geometry, name="api-result")
                assert snapshot(result) == expected, (
                    name,
                    profile,
                    expected,
                    snapshot(result),
                )
                before = set(bpy.data.objects.keys())
                assert bpy.ops.import_mesh.zpm(filepath=path) == {"FINISHED"}
                imported = set(bpy.data.objects.keys()) - before
                assert len(imported) == 1
                imported_obj = bpy.data.objects[imported.pop()]
                assert snapshot(imported_obj.data) == expected
                assert tuple(imported_obj.location) == (0, 0, 0)
                assert tuple(obj.location) == (10, 20, 30)
            print(f"PASS: {name}, both profiles, API and file operators")
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
