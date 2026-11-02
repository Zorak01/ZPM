# SPDX-License-Identifier: GPL-3.0-or-later
"""Run inside Blender: how far do stored weights move a posed surface?

blender --background --factory-startup --python weight_test.py -- RIGGED.glb

Turns every bone by the given angle, skins each vertex with the source weights and with
the weights ZPM stores, and reports the largest difference against the mesh's size.
"""

import random
import sys
from pathlib import Path

import bpy
from mathutils import Euler, Matrix, Vector

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "blender"))

from zpm.addon import mesh_adapter  # noqa: E402
from zpm.format import snap_mesh  # noqa: E402

_EXTREME_ANGLE = 1.2
_SEED = 1


def _skin(position: Vector, rows: list, matrices: dict) -> Vector:
    total = Matrix(((0, 0, 0, 0), (0, 0, 0, 0), (0, 0, 0, 0), (0, 0, 0, 0)))
    for group, weight in rows:
        total += matrices.get(group, Matrix.Identity(4)) * weight
    return total @ position


def _rows(mesh, vertex: int) -> list:
    start, end = mesh.weight_offsets[vertex], mesh.weight_offsets[vertex + 1]
    return [
        (mesh.vertex_groups[mesh.weight_groups[at]], mesh.weight_values[at])
        for at in range(start, end)
    ]


def main():
    path = sys.argv[sys.argv.index("--") + 1]
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.import_scene.gltf(filepath=path)
    skinned = next(o for o in bpy.context.scene.objects if o.type == "MESH")
    rig = next(o for o in bpy.context.scene.objects if o.type == "ARMATURE")
    generator = random.Random(_SEED)
    for bone in rig.pose.bones:
        bone.rotation_mode = "XYZ"
        bone.rotation_euler = Euler(
            [generator.uniform(-_EXTREME_ANGLE, _EXTREME_ANGLE) for _ in range(3)]
        )
    bpy.context.view_layer.update()
    matrices = {
        bone.name: bone.matrix @ bone.bone.matrix_local.inverted()
        for bone in rig.pose.bones
    }
    source = mesh_adapter.capture_object(skinned)
    snapped = snap_mesh(source)
    corners = [
        Vector(source.positions[at : at + 3])
        for at in range(0, 3 * source.vertex_count, 3)
    ]
    low = [min(c[axis] for c in corners) for axis in range(3)]
    high = [max(c[axis] for c in corners) for axis in range(3)]
    extent = max(b - a for a, b in zip(low, high, strict=True))
    worst = 0.0
    for vertex, position in enumerate(corners):
        exact = _skin(position, _rows(source, vertex), matrices)
        stored = _skin(position, _rows(snapped, vertex), matrices)
        worst = max(worst, (exact - stored).length)
    print(f"RESULT worst {worst:.7f} of extent {extent:.4f}: {worst / extent:.7%}")


main()
