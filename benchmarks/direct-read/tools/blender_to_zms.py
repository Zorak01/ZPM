# SPDX-License-Identifier: GPL-3.0-or-later
"""Write the largest mesh of each GLB as a ZMS1 fixture, in Blender's own order.

blender --background --factory-startup --python blender_to_zms.py -- OUT a.glb ...
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
    print(f"{path.name}: v={vertices} f={faces} c={corners} e={edges}", flush=True)


def main():
    arguments = sys.argv[sys.argv.index("--") + 1 :]
    output = Path(arguments[0])
    output.mkdir(parents=True, exist_ok=True)
    for name in arguments[1:]:
        source = Path(name)
        bpy.ops.wm.read_factory_settings(use_empty=True)
        bpy.ops.import_scene.gltf(filepath=str(source))
        meshes = [mesh for mesh in bpy.data.meshes if mesh.vertices]
        if not meshes:
            print(f"{source.name}: no mesh", flush=True)
            continue
        largest = max(meshes, key=lambda mesh: len(mesh.vertices))
        write_fixture(largest, output / f"{source.stem}.zms")


main()
