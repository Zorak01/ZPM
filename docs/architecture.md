# Architecture

Dependencies point one way: Blender code uses the format, benchmarks use the runtime
reader, and nothing depends on benchmarks.

| Path | Responsibility |
| --- | --- |
| `blender/zpm/operators.py` | File selection, user reports, scene linking and menus |
| `blender/zpm/mesh_io.py` | Converts between Blender meshes and `Mesh`, and holds Blender-only rules |
| `blender/zpm/format/model.py` | `Mesh` and `FormatError` |
| `blender/zpm/format/layout.py` | Byte layout constants and section kinds |
| `blender/zpm/format/column.py`, `rank_mask.py`, `select_bits.py` | The three storage types |
| `blender/zpm/format/encoder.py`, `decoder.py` | `Mesh` to bytes and back |
| `blender/zpm/format/optimize.py` | Position snapping, reordering, and the geometry check |
| `blender/zpm/format/surface_values.py` | Stored forms of normals, UVs and creases |
| `blender/zpm/format/topology.py` | Face facts both directions share |
| `blender/zpm/format/files.py` | Bounded reads and atomic writes |
| `blender/zpm/format/file_map.py` | Names every byte of a file |
| `runtime/csharp/Zpm/` | Runtime reader: by index, no allocation |
| `tools/zpm_map.py` | Prints a file's byte map |
| `benchmarks/mesh-core/` | Frozen earlier codec, kept as the comparison |
| `benchmarks/mesh-read/` | Measures the runtime reader next to that codec |

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
