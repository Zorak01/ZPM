# Architecture

`zpm.format` is the portable entry point. Its `Mesh` owns typed position,
face-size, corner and edge arrays. The codec validates buffers, preserves exact
Float32 bits, bounds input reads and replaces output files atomically.

`mesh_io` converts arrays using Blender's `foreach_get` and `foreach_set`.
Blender-specific restrictions belong here, not in the portable codec. Explicit
loop-to-edge links preserve edge endpoint order across Blender versions.

`operators` handles file selection, user reports and scene linking. Failed imports
remove partial data. Existing meshes are not modified. The extension registers
only its operators and File menus.

## Compatibility

The package and manifest share version 0.1.0. Packet semantic changes require
an updated contract and tests. The benchmark reference has experimental encodings
that the Python API does not accept; only aligned profiles are supported there.

## Measurements

`benchmarks/mesh-core/reference` is a frozen measurement implementation, not
the add-on's implementation. Results must name the implementation and boundary.
