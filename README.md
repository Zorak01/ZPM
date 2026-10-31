# ZPM

**Zorak Portable Mesh** is a 3D file format being built to carry models,
materials, rigs, and animation between Blender, engines, and other tools.

The goal is simple: **smaller files, faster loading, lower memory use, and more
of your work preserved when you export.**

ZPM is in early development. Mesh import and export work today. The wider
feature set below is planned, not implemented yet.

**Reading a ZPM mesh means no decode step.** A mesh is read by index
straight from the file bytes, with no allocation: one face on a real 3,682-vertex
character takes about 0.09 microseconds, and the file is 2.1x smaller than the
smallest earlier experiment, which had to decode all of it first.
[Measurements](benchmarks/mesh-read/README.md) | [Layout](docs/mesh-core.md#layout)

## What ZPM will store

| Area | What it includes | Status |
| --- | --- | --- |
| Mesh geometry | Vertices and their positions; edges; triangles, quads, and n-gons; original face boundaries and winding; loose edges and nonmanifold meshes | Working |
| Surface data | Normals, sharp edges, flat faces, seams, and crease values | Working |
| Tangents | Stored per corner with their sign, from the first UV layer | Working |
| UVs | Multiple named UV sets and coordinates spanning UDIM tiles | Working |
| Vertex colors | Multiple named RGBA layers, stored per vertex or face corner; 8-bit or float values | Working |
| Custom attributes | Named data attached to vertices, face corners, edges, or faces | Planned |
| Materials and textures | Material assignments, material properties, texture references, and textures embedded in the file when asked for | Planned |
| Objects and hierarchy | Mesh objects, empty objects, names, transforms, parenting, collections, and instances | Planned |
| Bones and rigs | Bone head, tail, length, roll, parenting, rest transforms, pose data, and control shapes | Planned |
| Skinning | Named vertex groups and painted weights; bone influences come with bones | Working |
| Blendshapes | Named shape targets, changed vertices, values, ranges, and relative shape references | Planned |
| Rig behavior | Constraints, drivers, and their referenced objects, bones, and properties | Planned |
| Other objects | Lights, curves, and lattices, with their associated properties | Planned |
| Animation | Tracks that can target any stored property, including custom attributes | Planned |

Animation is intended to apply throughout the format: object transforms, bone
poses, light settings, material values, blendshape values, and other stored
properties should all be animatable.

## What better means

| Goal | What we measure |
| --- | --- |
| Speed | Encoding, decoding, and eventually complete import/load time |
| Memory | Decoded data size, allocations, and eventually peak loading memory |
| File size | Bytes on disk for the same data and quality |
| Features | What survives an export/import round trip |

The mesh core keeps triangles, quads and n-gons as they are, and never welds or drops
data. To shrink files it may reorder elements and snap positions by at most 0.00005% of the
model's size. [What saving changes](docs/mesh-core.md#what-saving-changes). Performance
improvements are claims to demonstrate with public data, not assumptions about the format.

## Blender

Requires Blender 4.2+. Locally verified with **4.5.1 LTS** and **5.1.2**.
Build an installable extension ZIP:

```sh
blender --factory-startup --command extension build --source-dir blender/zpm --output-dir dist
```

In Preferences > Get Extensions > Install from Disk, select `dist/zpm-0.1.0.zip`.
Enable **ZPM Mesh**. In Object Mode, use File > Import / Export > **ZPM Mesh Core**.

Exports contain the active object's base mesh in local coordinates; modifiers
are not evaluated. Imports create a new object at the origin. Keep the source
`.blend` for data outside the supported mesh-core scope.

There is nothing to choose on export: the file picks its smallest form by itself.

## Python API

Python 3.11+. No runtime dependencies. Importing the format API does not import bpy.

```sh
python -m pip install .
```

```python
from zpm.format import load, save

mesh = load("model.zpm")
print(mesh.vertex_count, mesh.face_count, mesh.buffer_bytes)
save("copy.zpm", mesh)
```

The Python API expands a whole file into arrays for editing. By-index reading is
in the [C# reference reader](runtime/csharp/Zpm/ZpmMeshView.cs).

See the [file contract](docs/mesh-core.md), [architecture](docs/architecture.md) and
[how each technique works](docs/techniques.md).

## Public benchmarks

[Results and methodology](benchmarks/mesh-core/README.md) |
[Storage chart](benchmarks/mesh-core/results/storage.svg) |
[Raw measurements](benchmarks/mesh-core/results/20261031T033106Z.json)

The published run covers **11 fixtures x 10 profiles**. Exact reconstruction is
checked for fresh and reused output buffers. It measures the frozen **.NET 9
reference codec**, not Python API speed or end-to-end Blender loading.

These results compare mesh-core encodings on identical input. For other formats, see
[ZPM against Draco and meshoptimizer](benchmarks/format-compare/README.md): today ZPM loses on
size and whole-mesh speed, and wins on partial reads and memory.

These are earlier experiments, kept as the comparison.
[Current results](benchmarks/mesh-read/README.md) measure today's layout next to
them, rerun on one machine.

## Development

```sh
python -m pip install -e ".[dev]"
python -m ruff check .
python -m ruff format --check .
python -m unittest discover -s tests -v
python tools/verify_benchmarks.py
```

For real Blender round trips:

```sh
blender --background --factory-startup --python-exit-code 1 --python tests/blender_roundtrip.py
```

CI checks Python on Windows and Linux, benchmark integrity, Blender 4.5 round
trips, and extension packaging.

## Layout

| Path | Purpose |
| --- | --- |
| `blender/zpm/addon` | Blender operators and the mesh adapter |
| `blender/zpm/format` | Blender-independent format: `container`, `storage`, `codec`, `processing` |
| `tests` | Codec and Blender integration tests |
| `benchmarks/mesh-core` | Frozen reference, fixtures, measurements, runner |
| `runtime/csharp/Zpm` | Runtime reader: by index, no allocation |
| `benchmarks/mesh-read` | Runtime reader measurements |
| `docs` | Public contracts and architecture |
| `tools` | Validation, build and file-map utilities |

[GPL-3.0-or-later](LICENSE)
