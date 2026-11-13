# SPDX-License-Identifier: GPL-3.0-or-later
"""Write the largest mesh of each GLB as a ZMS1 fixture plus NAME.surface.

blender --background --factory-startup --python blender_to_zms.py -- OUT a.glb b.obj ...
"""

import struct
import sys
from array import array
from pathlib import Path

import bpy


def write_fixture(mesh, path):
    vertices = len(mesh.vertices)
    faces = len(mesh.polygons)
    corners = len(mesh.loops)
    edges = len(mesh.edges)
    positions = array("f", [0.0]) * (vertices * 3)
    sizes = array("I", [0]) * faces
    corner_ids = array("I", [0]) * corners
    edge_ids = array("I", [0]) * (edges * 2)
    mesh.vertices.foreach_get("co", positions)
    mesh.polygons.foreach_get("loop_total", sizes)
    mesh.loops.foreach_get("vertex_index", corner_ids)
    mesh.edges.foreach_get("vertices", edge_ids)
    with path.open("wb") as stream:
        stream.write(struct.pack("<I4I", 0x31534D5A, vertices, faces, corners, edges))
        for buffer in (positions, sizes, corner_ids, edge_ids):
            stream.write(buffer.tobytes())
    write_surface(mesh, path.with_suffix(".surface"), corners)
    print(f"{path.name}: v={vertices} f={faces} c={corners} e={edges}", flush=True)


def write_surface(mesh, path, corners):
    """Write normals and UV layers, Float32 per corner, in source order."""
    normals = array("f", [0.0]) * (corners * 3)
    mesh.corner_normals.foreach_get("vector", normals)
    with path.open("wb") as stream:
        stream.write(struct.pack("<I?", len(mesh.uv_layers), mesh.has_custom_normals))
        stream.write(normals.tobytes())
        for layer in sorted(mesh.uv_layers, key=lambda item: item.name):
            values = array("f", [0.0]) * (corners * 2)
            layer.uv.foreach_get("vector", values)
            name = layer.name.encode("utf-8")
            stream.write(struct.pack("<I", len(name)) + name + values.tobytes())


def main():
    arguments = sys.argv[sys.argv.index("--") + 1 :]
    output = Path(arguments[0])
    output.mkdir(parents=True, exist_ok=True)
    for name in arguments[1:]:
        source = Path(name)
        bpy.ops.wm.read_factory_settings(use_empty=True)
        if source.suffix.lower() == ".obj":
            bpy.ops.wm.obj_import(filepath=str(source))
        else:
            bpy.ops.import_scene.gltf(filepath=str(source))
        meshes = [mesh for mesh in bpy.data.meshes if mesh.vertices]
        if not meshes:
            print(f"{source.name}: no mesh", flush=True)
            continue
        largest = max(meshes, key=lambda mesh: len(mesh.vertices))
        write_fixture(largest, output / f"{source.stem}.zms")


main()
