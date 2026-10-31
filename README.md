# ZPM

**Zorak Portable Mesh** is a 3D file format being built to carry models,
materials, rigs, and animation between Blender, engines, and other tools.

The goal is simple: **smaller files, faster loading, lower memory use, and more
of your work preserved when you export.**

ZPM is in early development. Mesh import and export work today. The wider
feature set below is planned, not implemented yet.

## What ZPM will store

| Area | What it includes | Status |
| --- | --- | --- |
| Mesh geometry | Vertices and their positions; edges; triangles, quads, and n-gons; original face boundaries and winding; loose edges and nonmanifold meshes | Working |
| Surface data | Normals, tangents, sharp edges, seams, and crease values | Planned |
| UVs | Multiple named UV sets and coordinates spanning UDIM tiles | Planned |
| Vertex colors | Multiple named RGBA layers, stored per vertex or face corner; 8-bit or 32-bit float values | Planned |
| Custom attributes | Named data attached to vertices, face corners, edges, or faces | Planned |
| Materials and textures | Material assignments, material properties, texture references, and embedded texture data | Planned |
| Objects and hierarchy | Mesh objects, empty objects, names, transforms, parenting, collections, and instances | Planned |
| Bones and rigs | Bone head, tail, length, roll, parenting, rest transforms, pose data, and control shapes | Planned |
| Skinning | Named vertex groups, painted weights, and bone influences | Planned |
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

The current mesh core preserves positions and topology without quantizing,
welding, or triangulating the model. Smaller encodings are tested against that
baseline. Performance improvements are claims to demonstrate with public data,
not assumptions about the format.

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

## Python API

Python 3.11+. No runtime dependencies. Importing the format API does not import bpy.

```sh
python -m pip install .
```

```python
from zpm.format import Profile, load, save

mesh = load("model.zpm")
print(mesh.vertex_count, mesh.face_count, mesh.buffer_bytes)
save("copy.zpm", mesh, profile=Profile.COMPACT)
```

See the [packet contract](docs/mesh-core.md) and [architecture](docs/architecture.md).

## Public benchmarks

[Results and methodology](benchmarks/mesh-core/README.md) |
[Storage chart](benchmarks/mesh-core/results/storage.svg) |
[Raw measurements](benchmarks/mesh-core/results/20261031T033106Z.json)

The published run covers **11 fixtures x 10 profiles**. Exact reconstruction is
checked for fresh and reused output buffers. It measures the frozen **.NET 9
reference codec**, not Python API speed or end-to-end Blender loading.

These results compare mesh-core encodings on identical input. They do not
establish an advantage over GLB, glTF, FBX, or other complete scene formats.

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
| `blender/zpm/format` | Blender-independent model, validation, codec, file I/O |
| `blender/zpm/mesh_io.py` | Blender bulk-buffer adapter |
| `blender/zpm/operators.py` | Operators and menus |
| `tests` | Codec and Blender integration tests |
| `benchmarks/mesh-core` | Frozen reference, fixtures, measurements, runner |
| `docs` | Public contracts and architecture |
| `tools` | Validation and build utilities |

[GPL-3.0-or-later](LICENSE)
