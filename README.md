# ZPM

A 3D file format for meshes, materials, rigs, and animation. Includes a Blender extension, Python API, and runtime readers.

Early development. The file layout can change between builds.

[Install](#blender) · [Format](docs/mesh-core.md) · [Benchmarks](#benchmarks) · [TODO](TODO.md)

## Features

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

## Blender

Requires Blender 4.4+ for animation. Build the native reader, then package the extension:

```sh
python -m pip install ziglang
python tools/build_native.py
blender --factory-startup --command extension build --source-dir blender/zpm --output-dir dist
```

In Blender, open Preferences > Get Extensions > Install from Disk and select `dist/zpm-0.1.0.zip`. Enable ZPM. Import and export are under the File menu in Object Mode.

Point-cloud and hair round trips are tested on Blender 5.2.3. Older Blender versions without the required Python APIs report these imports as unsupported.

The extension works without the native reader, using Python instead. Export includes selected objects, or the whole scene if nothing is selected. Modifiers, constraints, and drivers are not stored, except for the armature link. Keep the source `.blend` file.

**Embed Textures** stores images inside the file. JPEG tables may be recoded, and PNGs may be stored as lossless WebP when smaller.

## Python

Python 3.11+. The format API does not require Blender.

```sh
python -m pip install .
```

```python
from zpm.format import load, save

scene = load("model.zpm")
print(len(scene.objects), len(scene.meshes))
save("copy.zpm", scene)
```

Python expands files into editable arrays. The C# reader supports indexed access to the direct format. Saved files normally use the packed format, which has Python and native C++ geometry readers. The C# reader does not yet support packed files.

## Benchmarks

| Goal | What we measure |
| --- | --- |
| Speed | Encoding, decoding, and eventually complete import/load time |
| Memory | Decoded data size, allocations, and eventually peak loading memory |
| File size | Bytes on disk for the same data and quality |
| Features | What survives an export/import round trip |

- [Current reader measurements](benchmarks/mesh-read/README.md)
- [Draco and meshoptimizer comparisons](benchmarks/format-compare/README.md)
- [Frozen reference results](benchmarks/mesh-core/README.md)

The frozen suite covers 11 fixtures and 10 profiles. See each report for implementation, precision, inputs, and timing boundaries.

Saving may reorder elements and snap positions within the bounds documented in [what saving changes](docs/mesh-core.md#what-saving-changes).

## Development

```sh
python -m pip install -e ".[dev]"
python -m ruff check .
python -m ruff format --check .
python -m unittest discover -s tests -v
python tools/check_version.py
python tools/verify_benchmarks.py
python tools/audit_public.py
```

Blender integration checks:

```sh
blender --background --factory-startup --python-exit-code 1 --python tests/blender_roundtrip.py
```

## Source

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

[Architecture](docs/architecture.md) · [Encoding techniques](docs/techniques.md) · [Release process](docs/releases.md)

[GPL-3.0-or-later](LICENSE)
