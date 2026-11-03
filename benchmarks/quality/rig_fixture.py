# SPDX-License-Identifier: GPL-3.0-or-later
"""Run inside Blender: build a rig, save it, and write the matrices Blender calculates.

blender --background --factory-startup --python rig_fixture.py -- OUTPUT_FOLDER
"""

import json
import sys
from pathlib import Path

import bpy

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "blender"))

from zpm.addon import scene_adapter  # noqa: E402
from zpm.format import encode_scene  # noqa: E402


def _flat(matrix) -> list:
    return [value for row in matrix for value in row]


def _build_rig() -> list:
    for item in list(bpy.data.objects):
        bpy.data.objects.remove(item)
    armature = bpy.data.armatures.new("Skeleton")
    rig = bpy.data.objects.new("Rig", armature)
    bpy.context.collection.objects.link(rig)
    rig.location = (1, 2, 3)
    rig.rotation_mode = "XYZ"
    rig.rotation_euler = (0.3, 0.2, 0.1)
    rig.scale = (2, 2, 2)
    bpy.context.view_layer.objects.active = rig
    bpy.ops.object.mode_set(mode="EDIT")
    root = armature.edit_bones.new("Root")
    root.tail = (0, 0, 1)
    root.roll = 0.4
    spine = armature.edit_bones.new("Spine")
    spine.head = (0, 0, 1)
    spine.tail = (0.2, 0.3, 2)
    spine.roll = -0.7
    spine.parent = root
    spine.use_connect = True
    loose = armature.edit_bones.new("Loose")
    loose.head = (1, 0, 1)
    loose.tail = (2, 0.5, 0.5)
    loose.parent = spine
    down = armature.edit_bones.new("Down")
    down.head = (0, 2, 0)
    down.tail = (0, 1, 0)
    down.roll = 1.2
    bpy.ops.object.mode_set(mode="OBJECT")
    root_pose = rig.pose.bones["Root"]
    root_pose.rotation_quaternion = (0.9, 0.1, 0.2, 0.3)
    root_pose.location = (0.1, 0.2, 0.3)
    spine_pose = rig.pose.bones["Spine"]
    spine_pose.rotation_mode = "ZXY"
    spine_pose.rotation_euler = (0.5, -0.2, 0.9)
    spine_pose.location = (5, 5, 5)
    loose_pose = rig.pose.bones["Loose"]
    loose_pose.rotation_mode = "AXIS_ANGLE"
    loose_pose.rotation_axis_angle = (0.8, 1, 1, 0)
    loose_pose.location = (0.5, 0, 0)
    loose_pose.scale = (1.5, 1.5, 1.5)

    child = bpy.data.objects.new("Child", None)
    bpy.context.collection.objects.link(child)
    child.parent = rig
    child.location = (0.5, 0.5, 0.5)
    child.rotation_mode = "ZYX"
    child.rotation_euler = (0.1, 0.9, -0.4)
    child.scale = (1, 2, 3)
    grandchild = bpy.data.objects.new("Grandchild", None)
    bpy.context.collection.objects.link(grandchild)
    grandchild.parent = child
    grandchild.location = (1, 0, 0)
    held = bpy.data.objects.new("Held", None)
    bpy.context.collection.objects.link(held)
    held.parent = rig
    held.parent_type = "BONE"
    held.parent_bone = "Loose"
    held.location = (0.1, 0.1, 0.1)
    delta = bpy.data.objects.new("Delta", None)
    bpy.context.collection.objects.link(delta)
    delta.location = (1, 2, 3)
    delta.delta_location = (0.5, 0.25, -1)
    delta.rotation_mode = "XZY"
    delta.rotation_euler = (0.2, 0.4, 0.6)
    delta.delta_rotation_euler = (0.3, -0.1, 0.5)
    delta.scale = (1, 2, 3)
    delta.delta_scale = (2, 0.5, 1.5)
    turned = bpy.data.objects.new("DeltaTurned", None)
    bpy.context.collection.objects.link(turned)
    turned.rotation_mode = "QUATERNION"
    turned.rotation_quaternion = (0.9, 0.1, 0.2, 0.3)
    turned.delta_rotation_quaternion = (0.8, 0.4, -0.2, 0.1)

    base_mesh = bpy.data.meshes.new("Base")
    base_mesh.from_pydata(
        [(0, 0, 0), (2, 0, 0.5), (0.5, 3, 1), (-1, 1, 2)], [], [(0, 1, 2), (0, 2, 3)]
    )
    base = bpy.data.objects.new("Base", base_mesh)
    bpy.context.collection.objects.link(base)
    base.location = (3, -1, 2)
    base.rotation_mode = "XYZ"
    base.rotation_euler = (0.4, 0.3, 0.2)
    base.scale = (2, 1, 0.5)
    single = bpy.data.objects.new("OnVertex", None)
    bpy.context.collection.objects.link(single)
    single.parent = base
    single.parent_type = "VERTEX"
    single.parent_vertices = (2, 0, 0)
    single.location = (0.1, 0.2, 0.3)
    triple = bpy.data.objects.new("OnTriangle", None)
    bpy.context.collection.objects.link(triple)
    triple.parent = base
    triple.parent_type = "VERTEX_3"
    triple.parent_vertices = (0, 1, 2)
    triple.location = (0.1, 0.2, 0.3)
    bpy.context.view_layer.update()
    return [rig, child, grandchild, held, delta, turned, base, single, triple]


def _add_inheriting_bones(rig) -> None:
    """One child per way of taking a parent's scale, with and without its rotation."""
    armature = rig.data
    bpy.context.view_layer.objects.active = rig
    bpy.ops.object.mode_set(mode="EDIT")
    names = []
    for mode in ("FULL", "FIX_SHEAR", "ALIGNED", "AVERAGE", "NONE", "NONE_LEGACY"):
        for inherits in (True, False):
            name = f"{mode}-{inherits}"
            bone = armature.edit_bones.new(name)
            bone.head = (1.5, 0.2, 0.4)
            bone.tail = (1.9, 1.0, 0.3)
            bone.roll = 0.3
            bone.parent = armature.edit_bones["Loose"]
            bone.inherit_scale = mode
            bone.use_inherit_rotation = inherits
            names.append(name)
    bpy.ops.object.mode_set(mode="OBJECT")
    for name in names:
        pose = rig.pose.bones[name]
        pose.location = (0.2, 0.1, -0.1)
        pose.rotation_quaternion = (0.95, 0.1, -0.2, 0.05)
        pose.scale = (1.2, 1.2, 1.2)
    bpy.context.view_layer.update()


def main():
    folder = Path(sys.argv[sys.argv.index("--") + 1])
    objects = _build_rig()
    _add_inheriting_bones(objects[0])
    scene = scene_adapter.capture_scene(bpy.context.scene, objects)
    (folder / "rig.zpm").write_bytes(encode_scene(scene))
    expected = {}
    for obj in objects:
        expected["world:" + obj.name] = _flat(obj.matrix_world)
    rig = objects[0]
    for bone in rig.data.bones:
        expected[f"rest:Rig:{bone.name}"] = _flat(bone.matrix_local)
        expected[f"pose:Rig:{bone.name}"] = _flat(rig.pose.bones[bone.name].matrix)
    (folder / "expected.json").write_text(json.dumps(expected))


main()
