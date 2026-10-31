# SPDX-License-Identifier: GPL-3.0-or-later

"""Run with blender --background --factory-startup --python this_file."""

import sys
import tempfile
from array import array
from pathlib import Path

import bpy
import mathutils

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "blender"))

import zpm
from zpm.addon import mesh_adapter
from zpm.format import FormatError, Mesh, load, same_geometry, snap_mesh


def check_colors_tangents_and_weights(directory):
    """Colors, tangents, vertex groups and weights survive saving and loading."""
    bpy.ops.mesh.primitive_uv_sphere_add(segments=12, ring_count=8)
    obj = bpy.context.active_object
    mesh = obj.data
    mesh.uv_layers.new(name="UVMap") if not mesh.uv_layers else None
    corner = mesh.color_attributes.new("Paint", "BYTE_COLOR", "CORNER")
    for index, data in enumerate(corner.data):
        data.color_srgb = ((index % 256) / 255, 0.5, 0.25, 1.0)
    point = mesh.color_attributes.new("Heat", "FLOAT_COLOR", "POINT")
    for index, data in enumerate(point.data):
        data.color = (index * 0.5, 0.25, 0.75, 1.0)
    head = obj.vertex_groups.new(name="Head")
    arm = obj.vertex_groups.new(name="Arm")
    head.add(list(range(0, 40)), 0.75, "REPLACE")
    arm.add(list(range(20, 60)), 0.25, "REPLACE")
    bpy.context.view_layer.objects.active = obj
    path = str(Path(directory) / "colors.zpm")
    assert bpy.ops.export_mesh.zpm(filepath=path) == {"FINISHED"}
    geometry = load(path)
    assert sorted(geometry.tangent_layers) == sorted(mesh.uv_layers.keys())
    assert sorted(geometry.color_layers) == ["Heat", "Paint"]
    assert geometry.vertex_groups == ["Head", "Arm"]
    assert len(geometry.weight_values) == 80
    before = set(bpy.data.objects.keys())
    assert bpy.ops.import_mesh.zpm(filepath=path) == {"FINISHED"}
    imported = bpy.data.objects[(set(bpy.data.objects.keys()) - before).pop()]
    assert [group.name for group in imported.vertex_groups] == ["Head", "Arm"]
    total = sum(len(vertex.groups) for vertex in imported.data.vertices)
    assert total == 80, total
    assert sorted(imported.data.color_attributes.keys()) == ["Heat", "Paint"]
    expected = snap_mesh(mesh_adapter.capture_object(obj))
    actual = snap_mesh(mesh_adapter.capture_object(imported))
    assert same_geometry(actual, expected, compare_normals=False)
    print("PASS: colors, tangents, vertex groups and weights")


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
    expected = snap_mesh(mesh_adapter.capture_mesh(mesh))
    path = str(Path(directory) / "surface.zpm")
    assert bpy.ops.export_mesh.zpm(filepath=path) == {"FINISHED"}
    result = mesh_adapter.create_mesh(load(path), name="surface-result")
    actual = snap_mesh(mesh_adapter.capture_mesh(result))
    assert same_geometry(actual, expected, compare_normals=False)
    assert sorted(result.uv_layers.keys()) == sorted(mesh.uv_layers.keys())
    assert "Detail" in result.uv_layers

    normals = [(0.0, 0.6, 0.8)] * len(mesh.loops)
    mesh.normals_split_custom_set(normals)
    assert mesh.has_custom_normals
    assert bpy.ops.export_mesh.zpm(filepath=path) == {"FINISHED"}
    result = mesh_adapter.create_mesh(load(path), name="custom-result")
    assert result.has_custom_normals
    # Blender's own custom normal storage is off by about 0.0105 before ZPM is involved.
    for corner in result.corner_normals:
        assert (corner.vector - mathutils.Vector(normals[0])).length < 0.02
    print("PASS: surface data and custom normals")


def check_materials_and_authored_marks(directory):
    """Slots, per-layer seams, sRGB flags, sharp corners, groups and chains survive."""
    bpy.ops.mesh.primitive_grid_add(x_subdivisions=8, y_subdivisions=8)
    obj = bpy.context.active_object
    mesh = obj.data
    mesh.uv_layers.new(name="First")
    mesh.uv_layers.new(name="Second")
    mesh.materials.append(bpy.data.materials.new("Skin"))
    mesh.materials.append(None)
    mesh.materials.append(bpy.data.materials.new("Metal"))
    for index, face in enumerate(mesh.polygons):
        face.material_index = 0 if index < 20 else 2
    mesh.edges[1].use_seam = True
    seams = mesh.attributes.new("zpm_seam:Second", "BOOLEAN", "EDGE")
    seams.data[4].value = True
    sharp = mesh.attributes.new("zpm_sharp_corner", "BOOLEAN", "CORNER")
    sharp.data[6].value = True
    groups = mesh.attributes.new("zpm_smoothing_group", "INT", "CORNER")
    for corner, data in enumerate(groups.data):
        data.value = corner // 40
    mesh["zpm_crease_chain_offsets"] = [0, 2]
    mesh["zpm_crease_chain_vertices"] = list(mesh.edges[0].vertices)
    mesh.color_attributes.new("Linear", "FLOAT_COLOR", "POINT")
    mesh.color_attributes.new("Paint", "BYTE_COLOR", "POINT")
    expected = snap_mesh(mesh_adapter.capture_mesh(mesh))
    path = str(Path(directory) / "materials.zpm")
    assert bpy.ops.export_mesh.zpm(filepath=path) == {"FINISHED"}
    geometry = load(path)
    assert geometry.material_slots == ["Skin", "", "Metal"]
    assert sorted(geometry.tangent_layers) == sorted(mesh.uv_layers.keys())
    assert list(geometry.uv_seams) == ["Second"]
    assert geometry.color_layers["Paint"].is_srgb
    assert not geometry.color_layers["Linear"].is_srgb
    result = mesh_adapter.create_mesh(geometry, name="materials-result")
    assert [m.name if m else "" for m in result.materials] == ["Skin", "", "Metal"]
    actual = snap_mesh(mesh_adapter.capture_mesh(result))
    assert same_geometry(actual, expected, compare_normals=False)
    print("PASS: materials, per-layer data and authored marks")


def check_holes_are_refused():
    """Blender has no faces with holes, so importing one fails, changing nothing."""
    square = [(0, 0, 0), (4, 0, 0), (4, 4, 0), (0, 4, 0)]
    inner = [(1, 1, 0), (1, 3, 0), (3, 3, 0)]
    geometry = Mesh(
        array("f", [part for point in square + inner for part in point]),
        array("I", [7]),
        array("I", range(7)),
        array("I", [0, 1, 1, 2, 2, 3, 0, 3, 4, 5, 5, 6, 4, 6]),
        hole_starts=array("I", [4]),
    )
    before = len(bpy.data.meshes)
    try:
        mesh_adapter.create_mesh(geometry, name="holes")
    except FormatError:
        assert len(bpy.data.meshes) == before
        print("PASS: holes refused")
        return
    raise AssertionError("A face with holes was accepted")


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
            expected = snap_mesh(mesh_adapter.capture_mesh(source))
            path = str(Path(directory) / f"{name}.zpm")
            assert bpy.ops.export_mesh.zpm(filepath=path) == {"FINISHED"}
            geometry = load(path)
            result = mesh_adapter.create_mesh(geometry, name="api-result")
            assert same_geometry(
                mesh_adapter.capture_mesh(result), expected, compare_normals=False
            ), name
            before = set(bpy.data.objects.keys())
            assert bpy.ops.import_mesh.zpm(filepath=path) == {"FINISHED"}
            imported = set(bpy.data.objects.keys()) - before
            assert len(imported) == 1
            imported_obj = bpy.data.objects[imported.pop()]
            assert same_geometry(
                mesh_adapter.capture_mesh(imported_obj.data),
                expected,
                compare_normals=False,
            ), name
            assert tuple(imported_obj.location) == (0, 0, 0)
            assert tuple(obj.location) == (10, 20, 30)
            print(f"PASS: {name}, API and file operators")
        check_surface_data(directory)
        check_colors_tangents_and_weights(directory)
        check_materials_and_authored_marks(directory)
        check_holes_are_refused()
        geometry = mesh_adapter.capture_mesh(source)
        geometry.corners[0] = geometry.vertex_count + 1
        before = len(bpy.data.meshes)
        try:
            mesh_adapter.create_mesh(geometry, name="invalid")
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
