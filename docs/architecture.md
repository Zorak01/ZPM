# Architecture

Dependencies point one way: Blender code uses the format, benchmarks use the runtime
reader, and nothing depends on benchmarks.

| Path | Responsibility |
| --- | --- |
| `blender/zpm/addon/operators.py` | File selection, user reports, scene linking and menus |
| `blender/zpm/addon/mesh_adapter.py` | Converts between Blender meshes and `Mesh`, and holds Blender-only mesh rules |
| `blender/zpm/addon/scene_adapter.py` | Converts a Blender scene to `Scene` and back: objects, hierarchy, collections |
| `blender/zpm/addon/material_adapter.py`, `image_packing.py`, `animation_adapter.py`, `point_data_adapter.py`, `rna_values.py` | Materials and images, PNGs as lossless WebP, animation, point clouds, hair and volumes, and open-ended property values |
| `blender/zpm/format/__init__.py` | The public API: `load`, `save`, `encode`, `decode`, `snap_as_saved`, `Mesh`, `Scene` |
| `blender/zpm/format/mesh.py`, `errors.py`, `files.py` | The data model, the one error type, bounded reads and atomic writes |
| `blender/zpm/format/container/` | The file's outer structure: layout constants, labelled sections, the byte map |
| `blender/zpm/format/storage/` | Storage types readable by index: columns, rank masks, select bits, back references, corner streams, name tables |
| `blender/zpm/format/codec/` | One module per kind of data, holding both directions: positions, corners, edges, surface, colors, weights |
| `blender/zpm/format/codec/packed/` | The packed form saving writes: the walk, splits, positions, normals, UVs, tangents, weights, animation tracks and scene tables, one module each, and what normals and UVs share |
| `blender/zpm/format/scene.py` | Scene data: objects, materials, images, lights, armatures, curves, lattices, cameras, animation, and the file as a whole |
| `blender/zpm/format/processing/` | Whole-mesh changes and checks: snapping, including the packing grid, reordering, comparison, face facts; and JPEGs re-coded with their own Huffman tables |
| `blender/zpm/format/native/` | The native reader of packed geometry: its C++ source, the loader, and libraries in `bin/<platform>/` |
| `runtime/csharp/Zpm/` | Runtime reader: by index, no allocation |
| `tools/zpm_map.py` | Prints a file's byte map |
| `tools/build_native.py` | Builds the native reader for Windows, Linux and macOS, x64 and ARM, with zig |
| `benchmarks/mesh-core/` | Frozen earlier codec, kept as the comparison |
| `benchmarks/mesh-read/` | Measures the runtime reader next to that codec |
| `benchmarks/format-compare/` | Measures ZPM against Draco and meshoptimizer; `packed_decode.cpp` times the native reader and checks it against Python by checksum |

`zpm.format` is the portable entry point and never imports `bpy`. It runs in ordinary Python.

Failed imports remove partial data. Existing meshes are not modified. The extension registers
only its operators and File menus.

There is one writer, in Python. The C# reader reads the direct form and is checked against
it on every benchmark run, through every read path, on every fixture. Saved files use the
packed form, which the C# reader does not read.

Packed geometry has two readers, Python and native C++, for one reason: Python is the
reference, and native reads 20 to 53 times faster, which is the difference between an
import in seconds and one that waits. Python stays the reference. The native reader
returns exactly what Python returns or refuses, and then Python reads, so no result
depends on which ran. It is used only when a library is built for the platform and the
mesh fits the caller's byte limit; tests compare the two on every mesh kind and on
damaged files, and the benchmark compares them by checksum.

## Compatibility

The package and manifest share version 0.1.0. Layout changes require an updated contract and
tests, and the layout is not frozen yet, so older prototype files may stop opening.

## Measurements

Results must name the implementation and boundary. Compare timings only from one machine and session.
