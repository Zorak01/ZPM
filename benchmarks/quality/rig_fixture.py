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
from zpm.format import save  # noqa: E402


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
    bpy.context.view_layer.update()
    return [rig, child, grandchild, held]


def main():
    folder = Path(sys.argv[sys.argv.index("--") + 1])
    objects = _build_rig()
    save(folder / "rig.zpm", scene_adapter.capture_scene(bpy.context.scene, objects))
    expected = {}
    for obj in objects:
        expected["world:" + obj.name] = _flat(obj.matrix_world)
    rig = objects[0]
    for bone in rig.data.bones:
        expected[f"rest:Rig:{bone.name}"] = _flat(bone.matrix_local)
        expected[f"pose:Rig:{bone.name}"] = _flat(rig.pose.bones[bone.name].matrix)
    (folder / "expected.json").write_text(json.dumps(expected))


main()
