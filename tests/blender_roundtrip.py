# SPDX-License-Identifier: GPL-3.0-or-later

"""Run with blender --background --factory-startup --python this_file."""

import sys
import tempfile
from array import array
from pathlib import Path

import bpy
import mathutils
from bpy_extras import anim_utils

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "blender"))

import zpm
from zpm.addon import mesh_adapter
from zpm.format import (
    FormatError,
    Material,
    Mesh,
    Scene,
    load,
    same_geometry,
    save,
    snap_mesh,
)


def export_alone(obj, path):
    """Export one object, the way a person does: select it, then export."""
    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    assert bpy.ops.export_mesh.zpm(filepath=path) == {"FINISHED"}


def check_whole_scene(directory):
    """Parents, bones, skins, poses, lights, materials and collections survive."""
    bpy.ops.object.select_all(action="DESELECT")
    armature = bpy.data.armatures.new("RigData")
    rig = bpy.data.objects.new("Rig", armature)
    group = bpy.data.collections.new("Characters")
    bpy.context.scene.collection.children.link(group)
    group.objects.link(rig)
    bpy.context.view_layer.objects.active = rig
    bpy.ops.object.mode_set(mode="EDIT")
    hip = armature.edit_bones.new("Hip")
    hip.tail = (0, 0, 1)
    spine = armature.edit_bones.new("Spine")
    spine.head = (0, 0, 1)
    spine.tail = (0, 0, 2)
    spine.parent = hip
    spine.use_connect = True
    spine.roll = 0.5
    bpy.ops.object.mode_set(mode="OBJECT")
    rig.pose.bones["Spine"].location = (0.5, 0, 0)

    bpy.ops.mesh.primitive_cube_add()
    body = bpy.context.active_object
    body.name = "Body"
    bpy.context.collection.objects.unlink(body)
    group.objects.link(body)
    body.parent = rig
    body.location = (1, 2, 3)
    body.vertex_groups.new(name="Spine")
    modifier = body.modifiers.new("Armature", "ARMATURE")
    modifier.object = rig

    image = bpy.data.images.new("Paint", 4, 4)
    image.pack()
    material = bpy.data.materials.new("Skin")
    material.use_nodes = True
    shader = next(
        node for node in material.node_tree.nodes if node.type == "BSDF_PRINCIPLED"
    )
    shader.inputs["Metallic"].default_value = 0.25
    texture = material.node_tree.nodes.new("ShaderNodeTexImage")
    texture.image = image
    material.node_tree.links.new(texture.outputs["Color"], shader.inputs["Base Color"])
    body.data.materials.append(material)

    copy = bpy.data.objects.new("Copy", body.data)
    bpy.context.collection.objects.link(copy)
    light_data = bpy.data.lights.new("Key", "SPOT")
    light_data.energy = 50
    light = bpy.data.objects.new("Lamp", light_data)
    bpy.context.collection.objects.link(light)
    empty = bpy.data.objects.new("Marker", None)
    bpy.context.collection.objects.link(empty)
    tiled = bpy.data.images.new("Tiles", 4, 4, tiled=True)
    tiled.tiles.new(tile_number=1002)

    curve_data = bpy.data.curves.new("Road", "CURVE")
    bezier = curve_data.splines.new("BEZIER")
    bezier.bezier_points.add(1)
    bezier.bezier_points[1].co = (2, 0, 0)
    bezier.bezier_points[1].radius = 0.5
    nurbs = curve_data.splines.new("NURBS")
    nurbs.points.add(3)
    nurbs.points[1].co = (0, 1, 0, 2)
    nurbs.use_endpoint_u = True
    curve_data.bevel_depth = 0.25
    road = bpy.data.objects.new("RoadCurve", curve_data)
    bpy.context.collection.objects.link(road)
    cage_data = bpy.data.lattices.new("Cage")
    cage_data.points_u = 3
    cage_data.points[1].co_deform.z = 0.5
    cage = bpy.data.objects.new("CageObject", cage_data)
    bpy.context.collection.objects.link(cage)
    cage.vertex_groups.new(name="Top").add([1], 0.5, "REPLACE")
    lens = bpy.data.cameras.new("Lens")
    lens.lens = 35
    eye = bpy.data.objects.new("Eye", lens)
    bpy.context.collection.objects.link(eye)

    body["note"] = "keep me"
    body["levels"] = [1, 2, 3]
    bevel = body.modifiers.new("Soften", "BEVEL")
    bevel.width = 0.25
    follow = body.constraints.new("COPY_LOCATION")
    follow.name = "Follow"
    follow.target = empty
    follow.influence = 0.5
    shape = bpy.data.objects.new("Shape", None)
    bpy.context.collection.objects.link(shape)
    spine_pose = rig.pose.bones["Spine"]
    spine_pose.custom_shape = shape
    spine_pose.custom_shape_scale_xyz = (2, 2, 2)
    track = spine_pose.constraints.new("TRACK_TO")
    track.target = empty
    driver = body.driver_add("scale", 2).driver
    driver.expression = "var * 2"
    variable = driver.variables.new()
    variable.name = "var"
    variable.targets[0].id = empty
    variable.targets[0].data_path = "location.x"
    empty.keyframe_insert("location", index=0, frame=1)
    empty.location.x = 4
    empty.keyframe_insert("location", index=0, frame=10)
    empty.animation_data.action.name = "Slide"
    shader.inputs["IOR"].default_value = 1.25
    spine_pose.custom_shape_transform = rig.pose.bones["Hip"]
    armature.bones["Spine"].inherit_scale = "AVERAGE"
    arms = armature.collections.new("Arms")
    arms.assign(armature.bones["Spine"])
    empty.empty_display_size = 2.5
    empty.delta_location = (0, 0, 1)
    vertex_child = bpy.data.objects.new("VertexChild", None)
    bpy.context.collection.objects.link(vertex_child)
    vertex_child.parent = copy
    vertex_child.parent_type = "VERTEX"
    vertex_child.parent_vertices = (2, 0, 0)
    override = bpy.data.materials.new("OnlyCopy")
    copy.material_slots[0].link = "OBJECT"
    copy.material_slots[0].material = override
    image["source_note"] = "scan"
    group["team"] = 7
    body.data["cost"] = 3
    body.shape_key_add(name="Basis", from_mix=False)
    smile = body.shape_key_add(name="Smile", from_mix=False)
    key_driver = smile.driver_add("value").driver
    key_driver.expression = "lens / 100"
    key_variable = key_driver.variables.new()
    key_variable.name = "lens"
    key_variable.targets[0].id_type = "CAMERA"
    key_variable.targets[0].id = lens
    key_variable.targets[0].data_path = "lens"
    light_data.keyframe_insert("energy", frame=1)
    light_data.energy = 80
    light_data.keyframe_insert("energy", frame=5)
    light_data.energy = 50
    socket = shader.inputs["Metallic"]
    socket.keyframe_insert("default_value", frame=1)
    socket.default_value = 0.75
    socket.keyframe_insert("default_value", frame=9)
    socket.default_value = 0.25
    noise = material.node_tree.nodes.new("ShaderNodeTexNoise")
    material.node_tree.links.new(noise.outputs[0], shader.inputs["Roughness"])

    bpy.ops.object.select_all(action="DESELECT")
    for obj in (rig, body, copy, light, empty, road, cage, eye, shape, vertex_child):
        obj.select_set(True)
    path = str(Path(directory) / "scene.zpm")
    assert bpy.ops.export_mesh.zpm(filepath=path, embed_textures=True) == {"FINISHED"}
    stored = load(path)
    assert len(stored.meshes) == 1, "Copy must share its mesh"
    assert {item.name for item in stored.objects} == {
        "Rig",
        "Body",
        "Copy",
        "Lamp",
        "Marker",
        "RoadCurve",
        "CageObject",
        "Eye",
        "Shape",
        "VertexChild",
    }
    assert stored.materials[0].metallic == 0.25
    assert stored.images[0].name == "Paint"
    assert stored.lights[0].power == 50
    assert stored.materials[0].parameters["IOR"] == 1.25
    assert stored.extensions[0].name == "blender.node_graphs"
    assert stored.clips[0].name == "Slide"

    before = set(bpy.data.objects.keys())
    assert bpy.ops.import_mesh.zpm(filepath=path) == {"FINISHED"}
    made = {
        name.split(".")[0]: bpy.data.objects[name]
        for name in set(bpy.data.objects.keys()) - before
    }
    assert set(made) == {
        "Rig",
        "Body",
        "Copy",
        "Lamp",
        "Marker",
        "RoadCurve",
        "CageObject",
        "Eye",
        "Shape",
        "VertexChild",
    }
    assert made["Body"].parent == made["Rig"]
    assert tuple(made["Body"].location) == (1, 2, 3)
    assert made["Body"].data == made["Copy"].data
    assert made["Body"].modifiers["Armature"].object == made["Rig"]
    assert made["Rig"].data.bones["Spine"].parent.name == "Hip"
    assert abs(stored.armatures[0].bones[1].roll - 0.5) < 1e-5
    edit_roll = bpy.types.Bone.AxisRollFromMatrix(
        made["Rig"].data.bones["Spine"].matrix_local.to_3x3()
    )[1]
    assert abs(edit_roll - 0.5) < 1e-5
    assert tuple(made["Rig"].pose.bones["Spine"].location) == (0.5, 0, 0)
    assert made["Lamp"].data.type == "SPOT"
    assert made["Lamp"].data.energy == 50
    assert made["Marker"].type == "EMPTY"
    skin = made["Body"].data.materials[0]
    node = next(n for n in skin.node_tree.nodes if n.type == "BSDF_PRINCIPLED")
    assert node.inputs["Metallic"].default_value == 0.25
    assert node.inputs["Base Color"].links
    assert node.inputs["IOR"].default_value == 1.25
    assert any(
        c.name.startswith("Characters") and made["Body"].name in c.objects
        for c in bpy.data.collections
    )
    new_road = made["RoadCurve"].data
    assert new_road.bevel_depth == 0.25
    assert new_road.splines[0].bezier_points[1].radius == 0.5
    assert tuple(new_road.splines[1].points[1].co) == (0, 1, 0, 2)
    assert new_road.splines[1].use_endpoint_u
    new_cage = made["CageObject"]
    assert new_cage.data.points_u == 3
    assert new_cage.data.points[1].co_deform.z == 0.5
    assert new_cage.vertex_groups[0].name == "Top"
    assert made["Eye"].data.lens == 35
    assert made["Body"]["note"] == "keep me"
    assert list(made["Body"]["levels"]) == [1, 2, 3]
    assert [m.name for m in made["Body"].modifiers] == ["Armature", "Soften"]
    assert made["Body"].modifiers["Soften"].width == 0.25
    copied = made["Body"].constraints["Follow"]
    assert copied.target == made["Marker"] and copied.influence == 0.5
    posed = made["Rig"].pose.bones["Spine"]
    assert posed.custom_shape == made["Shape"]
    assert tuple(posed.custom_shape_scale_xyz) == (2, 2, 2)
    assert posed.constraints[0].target == made["Marker"]
    driven = made["Body"].animation_data.drivers[0]
    assert driven.driver.expression == "var * 2"
    assert driven.driver.variables[0].targets[0].id == made["Marker"]
    assert driven.driver.variables[0].targets[0].data_path == "location.x"
    action = made["Marker"].animation_data.action
    assert action.name.startswith("Slide")
    curve = anim_utils.action_get_channelbag_for_slot(
        action, made["Marker"].animation_data.action_slot
    ).fcurves[0]
    assert curve.data_path == "location"
    assert [tuple(p.co) for p in curve.keyframe_points] == [(1, 0), (10, 4)]
    graph_nodes = {node.bl_idname for node in skin.node_tree.nodes}
    assert "ShaderNodeTexNoise" in graph_nodes
    assert posed.custom_shape_transform.name == "Hip"
    assert made["Rig"].data.bones["Spine"].inherit_scale == "AVERAGE"
    assert made["Rig"].data.collections_all["Arms"].bones[0].name == "Spine"
    assert made["Marker"].empty_display_size == 2.5
    assert tuple(made["Marker"].delta_location) == (0, 0, 1)
    assert made["VertexChild"].parent == made["Copy"]
    assert made["VertexChild"].parent_type == "VERTEX"
    assert made["VertexChild"].parent_vertices[0] == 2
    assert made["Copy"].material_slots[0].link == "OBJECT"
    assert made["Copy"].material_slots[0].material.name.startswith("OnlyCopy")
    assert made["Body"].material_slots[0].link == "DATA"
    imported_image = next(
        block for block in bpy.data.images if block.get("source_note") == "scan"
    )
    assert imported_image is not None
    assert any(c.get("team") == 7 and c is not group for c in bpy.data.collections)
    assert made["Body"].data["cost"] == 3
    key_block_driver = made["Body"].data.shape_keys.animation_data.drivers[0]
    assert key_block_driver.driver.expression == "lens / 100"
    assert key_block_driver.driver.variables[0].targets[0].id.name.startswith("Lens")
    light_curve = anim_utils.action_get_channelbag_for_slot(
        made["Lamp"].data.animation_data.action,
        made["Lamp"].data.animation_data.action_slot,
    ).fcurves[0]
    assert light_curve.data_path == "energy"
    tree_curve = anim_utils.action_get_channelbag_for_slot(
        skin.node_tree.animation_data.action, skin.node_tree.animation_data.action_slot
    ).fcurves[0]
    assert "default_value" in tree_curve.data_path
    print("PASS: whole scene")


def check_failed_import_leaves_nothing(directory):
    """A file that fails partway through leaves no data blocks behind."""
    scene = Scene(
        meshes=[
            Mesh(
                array("f", [0, 0, 0, 1, 0, 0, 0, 1, 0]),
                array("I", [3]),
                array("I", [0, 1, 2]),
                array("I", [0, 1, 1, 2, 0, 2]),
                hole_starts=array("I"),
            ),
            Mesh(
                array(
                    "f", [0, 0, 0, 4, 0, 0, 4, 4, 0, 0, 4, 0, 1, 1, 0, 1, 3, 0, 3, 3, 0]
                ),
                array("I", [7]),
                array("I", range(7)),
                array("I", [0, 1, 1, 2, 2, 3, 0, 3, 4, 5, 5, 6, 4, 6]),
                hole_starts=array("I", [4]),
            ),
        ],
        materials=[Material("OnlyHereBeforeFailure")],
    )
    path = str(Path(directory) / "fails.zpm")
    save(path, scene)
    meshes, materials = len(bpy.data.meshes), len(bpy.data.materials)
    try:
        bpy.ops.import_mesh.zpm(filepath=path)
    except RuntimeError:
        pass
    else:
        raise AssertionError("A scene Blender cannot hold was accepted")
    assert len(bpy.data.meshes) == meshes
    assert len(bpy.data.materials) == materials
    print("PASS: failed import leaves nothing")


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
    export_alone(bpy.context.active_object, path)
    geometry = load(path).meshes[0]
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
    export_alone(bpy.context.active_object, path)
    result = mesh_adapter.create_mesh(load(path).meshes[0], name="surface-result")
    actual = snap_mesh(mesh_adapter.capture_mesh(result))
    assert same_geometry(actual, expected, compare_normals=False)
    assert sorted(result.uv_layers.keys()) == sorted(mesh.uv_layers.keys())
    assert "Detail" in result.uv_layers

    normals = [(0.0, 0.6, 0.8)] * len(mesh.loops)
    mesh.normals_split_custom_set(normals)
    assert mesh.has_custom_normals
    export_alone(bpy.context.active_object, path)
    result = mesh_adapter.create_mesh(load(path).meshes[0], name="custom-result")
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
    export_alone(bpy.context.active_object, path)
    geometry = load(path).meshes[0]
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


def check_attributes_and_shape_keys(directory):
    """Custom attributes of each type, shape keys and units survive."""
    bpy.ops.mesh.primitive_monkey_add()
    obj = bpy.context.active_object
    mesh = obj.data
    types = (
        ("a_bool", "BOOLEAN", "POINT"),
        ("a_int8", "INT8", "POINT"),
        ("a_int", "INT", "FACE"),
        ("a_int16", "INT16_2D", "CORNER"),
        ("a_int32", "INT32_2D", "EDGE"),
        ("a_float", "FLOAT", "EDGE"),
        ("a_float2", "FLOAT2", "POINT"),
        ("a_vector", "FLOAT_VECTOR", "CORNER"),
        ("a_color", "FLOAT_COLOR", "EDGE"),
        ("a_quaternion", "QUATERNION", "FACE"),
        ("a_matrix", "FLOAT4X4", "POINT"),
    )
    for name, data_type, domain in types:
        mesh.attributes.new(name, data_type, domain)
    mesh.attributes["a_int"].data[3].value = -7
    mesh.attributes["a_float"].data[2].value = 0.125
    mesh.attributes["a_vector"].data[1].vector = (1.5, -2.5, 3.5)
    mesh.attributes["a_bool"].data[0].value = True
    obj.vertex_groups.new(name="Lips")
    basis = obj.shape_key_add(name="Basis", from_mix=False)
    smile = obj.shape_key_add(name="Smile", from_mix=False)
    smile.relative_key = basis
    smile.slider_min = -1.0
    smile.value = 0.25
    smile.vertex_group = "Lips"
    for index in range(0, len(smile.data), 7):
        smile.data[index].co.z += 0.1
    bpy.context.scene.unit_settings.scale_length = 0.5
    expected = snap_mesh(mesh_adapter.capture_mesh(mesh))
    path = str(Path(directory) / "attributes.zpm")
    export_alone(bpy.context.active_object, path)
    geometry = load(path).meshes[0]
    assert load(path).space.meters_per_unit == 0.5
    assert geometry.tangent_method.startswith("MikkTSpace")
    assert sorted(geometry.attributes) == sorted(row[0] for row in types)
    before = set(bpy.data.objects.keys())
    assert bpy.ops.import_mesh.zpm(filepath=path) == {"FINISHED"}
    imported = bpy.data.objects[(set(bpy.data.objects.keys()) - before).pop()]
    assert imported.scale[0] == 1.0, "Same units must not rescale"
    actual = snap_mesh(mesh_adapter.capture_mesh(imported.data))
    assert same_geometry(actual, expected, compare_normals=False)
    bpy.context.scene.unit_settings.scale_length = 1.0
    print("PASS: custom attributes, shape keys and units")


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
            export_alone(obj, path)
            geometry = load(path).meshes[0]
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
            assert tuple(imported_obj.location) == (10, 20, 30)
            assert tuple(obj.location) == (10, 20, 30)
            print(f"PASS: {name}, API and file operators")
        check_surface_data(directory)
        check_colors_tangents_and_weights(directory)
        check_materials_and_authored_marks(directory)
        check_attributes_and_shape_keys(directory)
        check_holes_are_refused()
        check_whole_scene(directory)
        check_failed_import_leaves_nothing(directory)
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
