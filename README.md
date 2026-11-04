# ZPM

**Zorak Portable Mesh** is a 3D file format being built to carry models,
materials, rigs, and animation between Blender, engines, and other tools.

The goal is simple: **smaller files, faster loading, lower memory use, and more
of your work preserved when you export.**

ZPM is in early development. Scenes import and export today, with everything in the table below,
but the layout can still change. On 24 test meshes, saved files are 7.92x smaller than meshoptimizer in total
and smaller on 24 of them, with the same vertices. At Draco's own 20-bit precision they are 0.977x Draco's size in
total and smaller on 24 of 24. Draco is larger on every one.

**Saved files decode once on load,** start to end: 2x to 8x faster than Draco in native code, and still
several times slower than meshoptimizer. ZPM also has a direct form, read by index straight from the file bytes with no
decode step and no heap allocation: one face on a real 3,682-vertex character takes about 0.09 microseconds. Saving
uses it only for meshes the packed form cannot hold.
[Measurements](benchmarks/mesh-read/README.md) | [Layout](docs/mesh-core.md#layout)

## What ZPM will store

| Area | What it includes | Status |
| --- | --- | --- |
| Mesh geometry | Vertices and their positions; edges; triangles, quads, and n-gons; faces with holes (not importable in Blender); original face boundaries and winding; loose edges and nonmanifold meshes | Working |
| Surface data | Normals, sharp edges and corners, smoothing groups, flat faces, seams per UV layer, crease values and crease chains | Working |
| Tangents | Stored per corner with their sign, for each UV layer | Working |
| UVs | Multiple named UV sets and coordinates spanning UDIM tiles | Working |
| Vertex colors | Multiple named RGBA layers, stored per vertex or face corner; 8-bit or float values; linear or sRGB | Working |
| Custom attributes | Named exact data on vertices, face corners, edges, faces, or the whole mesh; named values on any resource | Working |
| Materials and textures | Application-neutral materials: shared fields, any shading model, named parameters, texture slots with named roles, samplers, UV transforms, UDIM tiles, image links or embedded images. | Working |
| Objects and hierarchy | Mesh, empty, armature, light, curve, lattice and camera objects, names, transforms, every parent type including bones and vertices, collections, shared meshes, collection instances, per-object materials, and world matrices worked out by the reader | Working |
| Bones and rigs | Bone head, tail, roll, parenting, envelopes, flags, scale inheritance and pose data; rest, bind, pose and skinning matrices worked out by the reader | Working |
| Skinning | Named vertex groups and painted weights, the armature each mesh object follows, and each group's bone | Working |
| Blendshapes | Named shape targets, changed vertices, values, ranges, relative and absolute targets, in-betweens, and normal and tangent changes | Working |
| Other objects | Point, spot, sun and area lights, poly, Bezier and NURBS curves, lattices, and cameras | Working |
| Animation | Clips of tracks addressing any field by owner and path, with float, integer, boolean, string and reference keys | Working |

Tracks can address any stored field. Blender export writes object, shape key, material, light,
camera, armature, curve and lattice animation.

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

Requires Blender 4.4+ for animation. Verified with **5.2.3**; **4.5.1 LTS** and **5.1.2** passed before scenes were added.
Build an installable extension ZIP:

```sh
blender --factory-startup --command extension build --source-dir blender/zpm --output-dir dist
```

In Preferences > Get Extensions > Install from Disk, select `dist/zpm-0.1.0.zip`.
Enable **ZPM Mesh**. In Object Mode, use File > Import / Export > **ZPM Mesh Core**.

Exports hold the selected objects, or the whole scene when none is selected, with base meshes in
local coordinates; modifiers, constraints and drivers are not stored, apart from the armature link. Imports recreate
them. Keep the source `.blend` for data outside what ZPM stores yet.

The one setting is **Embed Textures**, which stores image files inside the `.zpm`, PNGs as lossless WebP with the same
pixels when that is smaller. Everything else
the file picks by itself.

## Python API

Python 3.11+. No runtime dependencies. Importing the format API does not import bpy.

```sh
python -m pip install .
```

```python
from zpm.format import load, save

scene = load("model.zpm")
mesh = scene.meshes[0]
print(len(scene.objects), mesh.vertex_count, mesh.face_count)
save("copy.zpm", scene)
```

The Python API expands a whole file into arrays for editing. By-index reading is
in the C# reference reader: [ZpmSceneView](runtime/csharp/Zpm/ZpmSceneView.cs) and
[ZpmMeshView](runtime/csharp/Zpm/ZpmMeshView.cs).

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
[ZPM against Draco and meshoptimizer](benchmarks/format-compare/README.md): saved files are
7.92x smaller than meshopt and 0.977x Draco's size, smaller than Draco on 24 of 24, and decode faster than Draco and slower than meshopt.

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
