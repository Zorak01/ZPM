# Architecture

Dependencies point one way: Blender code uses the format, benchmarks use the runtime
reader, and nothing depends on benchmarks.

| Path | Responsibility |
| --- | --- |
| `blender/zpm/addon/operators.py` | File selection, user reports, scene linking and menus |
| `blender/zpm/addon/mesh_adapter.py` | Converts between Blender objects and `Mesh`, and holds Blender-only rules |
| `blender/zpm/format/__init__.py` | The public API: `load`, `save`, `encode`, `decode`, `Mesh` |
| `blender/zpm/format/mesh.py`, `errors.py`, `files.py` | The data model, the one error type, bounded reads and atomic writes |
| `blender/zpm/format/container/` | The file's outer structure: layout constants, labelled sections, the byte map |
| `blender/zpm/format/storage/` | Storage types readable by index: columns, rank masks, select bits, back references, corner streams, name tables |
| `blender/zpm/format/codec/` | One module per kind of data, holding both directions: positions, corners, edges, surface, colors, weights |
| `blender/zpm/format/processing/` | Whole-mesh changes and checks: snapping, reordering, comparison, face facts |
| `runtime/csharp/Zpm/` | Runtime reader: by index, no allocation |
| `tools/zpm_map.py` | Prints a file's byte map |
| `benchmarks/mesh-core/` | Frozen earlier codec, kept as the comparison |
| `benchmarks/mesh-read/` | Measures the runtime reader next to that codec |
| `benchmarks/format-compare/` | Measures ZPM against Draco and meshoptimizer |

`zpm.format` is the portable entry point and never imports `bpy`. It runs in ordinary Python.

Failed imports remove partial data. Existing meshes are not modified. The extension registers
only its operators and File menus.

There is one writer, in Python. The C# reader is checked against it on every benchmark run,
through every read path, on every fixture.

## Compatibility

The package and manifest share version 0.1.0. Layout changes require an updated contract and
tests, and the layout is not frozen yet, so older prototype files may stop opening.

## Measurements

Results must name the implementation and boundary. Compare timings only from one machine and session.
