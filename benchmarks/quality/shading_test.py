# SPDX-License-Identifier: GPL-3.0-or-later
"""Render a glossy mesh with exact and stored normals, and report the difference.

blender --background --factory-startup --python benchmarks/quality/shading_test.py
Prints JSON. Compares pixels, so grazing highlights and hard corners are included.
"""

import json
import math
import sys
import tempfile
from array import array
from pathlib import Path

import bpy

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "blender"))

from zpm.format.codec.quantize import decode_normal, encode_normal  # noqa: E402

_SIZE = 512
_SAMPLES = 32
_ROUGHNESS = 0.05
_CHANNELS = 4
_CONTROL_DEGREES = 1.0


def _build_scene() -> bpy.types.Object:
    for item in list(bpy.data.objects):
        bpy.data.objects.remove(item)
    bpy.ops.mesh.primitive_monkey_add()
    obj = bpy.context.active_object
    modifier = obj.modifiers.new("Smooth", "SUBSURF")
    modifier.levels = 2
    bpy.ops.object.modifier_apply(modifier="Smooth")
    bpy.ops.object.shade_smooth()
    material = bpy.data.materials.new("Glossy")
    material.use_nodes = True
    shader = next(
        node for node in material.node_tree.nodes if node.type == "BSDF_PRINCIPLED"
    )
    shader.inputs["Metallic"].default_value = 1.0
    shader.inputs["Roughness"].default_value = _ROUGHNESS
    obj.data.materials.append(material)
    camera = bpy.data.objects.new("Camera", bpy.data.cameras.new("Camera"))
    bpy.context.collection.objects.link(camera)
    camera.location = (0, -3.5, 0.5)
    camera.rotation_euler = (math.radians(82), 0, 0)
    sun = bpy.data.objects.new("Sun", bpy.data.lights.new("Sun", "SUN"))
    bpy.context.collection.objects.link(sun)
    sun.rotation_euler = (math.radians(50), math.radians(20), 0)
    sun.data.energy = 5
    scene = bpy.context.scene
    scene.camera = camera
    scene.render.engine = "CYCLES"
    scene.cycles.device = "CPU"
    scene.cycles.samples = _SAMPLES
    scene.cycles.seed = 1
    scene.cycles.use_denoising = False
    scene.render.resolution_x = _SIZE
    scene.render.resolution_y = _SIZE
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_depth = "16"
    return obj


def _add_environment() -> None:
    """Give the mesh sharp detail to reflect, so small normal changes show."""
    world = bpy.data.worlds.new("Detail")
    world.use_nodes = True
    bpy.context.scene.world = world
    nodes, links = world.node_tree.nodes, world.node_tree.links
    checker = nodes.new("ShaderNodeTexChecker")
    checker.inputs["Scale"].default_value = 40.0
    coordinates = nodes.new("ShaderNodeTexCoord")
    links.new(coordinates.outputs["Generated"], checker.inputs["Vector"])
    background = next(n for n in nodes if n.type == "BACKGROUND")
    links.new(checker.outputs["Color"], background.inputs["Color"])


def _render(path: Path) -> array:
    bpy.context.scene.render.filepath = str(path)
    bpy.ops.render.render(write_still=True)
    image = bpy.data.images.load(str(path))
    pixels = array("f", [0.0]) * (_SIZE * _SIZE * _CHANNELS)
    image.pixels.foreach_get(pixels)
    bpy.data.images.remove(image)
    return pixels


def _turned(normals: array, degrees: float) -> array:
    """Return normals turned around one axis: a change the test must be able to see."""
    cosine, sine = math.cos(math.radians(degrees)), math.sin(math.radians(degrees))
    turned = array("f")
    for at in range(0, len(normals), 3):
        x, y, z = normals[at : at + 3]
        turned.extend((x, y * cosine - z * sine, y * sine + z * cosine))
    return turned


def _copy_with_normals(source, normals: array) -> bpy.types.Object:
    """Return a hidden copy using these normals, with its own mesh."""
    obj = source.copy()
    obj.data = source.data.copy()
    bpy.context.collection.objects.link(obj)
    obj.data.normals_split_custom_set(
        [tuple(normals[at : at + 3]) for at in range(0, len(normals), 3)]
    )
    obj.hide_render = True
    return obj


def _render_only(obj, folder: Path, name: str) -> array:
    obj.hide_render = False
    pixels = _render(folder / name)
    obj.hide_render = True
    return pixels


def main():
    source = _build_scene()
    _add_environment()
    mesh = source.data
    source.hide_render = True
    exact = array("f", [0.0]) * (len(mesh.loops) * 3)
    mesh.corner_normals.foreach_get("vector", exact)
    stored = array(
        "f",
        [
            part
            for at in range(0, len(exact), 3)
            for part in decode_normal(*encode_normal(*exact[at : at + 3]))
        ],
    )
    exact_object = _copy_with_normals(source, exact)
    stored_object = _copy_with_normals(source, stored)
    control_object = _copy_with_normals(source, _turned(exact, _CONTROL_DEGREES))
    with tempfile.TemporaryDirectory() as directory:
        folder = Path(directory)
        first = _render_only(exact_object, folder, "exact.png")
        second = _render_only(stored_object, folder, "stored.png")
        third = _render_only(exact_object, folder, "exact_again.png")
        control = _render_only(control_object, folder, "control.png")
    differences = [abs(a - b) for a, b in zip(first, second, strict=True)]
    noise = [abs(a - b) for a, b in zip(first, third, strict=True)]
    seen = [abs(a - b) for a, b in zip(first, control, strict=True)]
    colors = [v for at, v in enumerate(first) if at % _CHANNELS != _CHANNELS - 1]
    print(
        "ZPM_SHADING "
        + json.dumps(
            {
                "corners": len(mesh.loops),
                "max_difference": max(differences),
                "mean_difference": sum(differences) / len(differences),
                "pixels_over_1_in_255": sum(d > 1 / 255 for d in differences) // 1,
                "max_with_same_normals": max(noise),
                "max_with_normals_turned_1_degree": max(seen),
                "mean_with_normals_turned_1_degree": sum(seen) / len(seen),
                "brightest_pixel": max(colors),
            }
        )
    )


main()
