# ZPM

**Zorak Portable Mesh** - an exact mesh-core codec, Python API, and Blender extension.

Preserve vertex positions, explicit edges, and triangle/quad/n-gon topology
without quantizing positions or triangulating authored faces.

> Experimental version 0.1. The current packet is not a frozen final format.
> Mesh attributes and scene data are not implemented.

## Supported data

| Data | Representation |
| --- | --- |
| Positions | Exact Float32 XYZ, including signed zero |
| Vertices | Original IDs and order |
| Faces | Triangles, quads, n-gons, mixed face sizes; original winding |
| Edges | Explicit endpoint order, loose edges and nonmanifold geometry |
| Compact profile | UInt16 IDs through 65,536 vertices; UInt32 beyond that |
| Uniform faces | One shared face size |
| Reference profile | Raw UInt32 counts and IDs |

No general-purpose compression codec is selected. UVs, vertex colors, authored
normals, seams, creases, materials, textures, skinning, blendshapes, rigs,
object transforms, hierarchy and animation are outside this build's scope.

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
[Interactive charts](benchmarks/mesh-core/results/index.html) (open locally) |
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
trips, and extension packaging. Hosted checks run after this repository is pushed.
See [contributing](CONTRIBUTING.md) for the development workflow.

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

[Contributing](CONTRIBUTING.md) | [Security](SECURITY.md) |
[Changelog](CHANGELOG.md) | [GPL-3.0-or-later](LICENSE)
