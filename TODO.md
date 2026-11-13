# TODO

## Implemented

- [x] Mesh topology, surface attributes, UVs, colors, tangents, and skin weights.
- [x] Scene hierarchy, materials, textures, armatures, blendshapes, and animation.
- [x] Blender scene import and export.
- [x] Python packed codec and native C++ packed-geometry reader.
- [x] C# indexed reader for the direct format.
- [x] Draco and meshoptimizer comparisons, with recorded inputs and results.

## Next

- [ ] Add packed-file support to the C# reader.
- [ ] Move packed tangent decoding into the native reader. It currently runs in Python.
- [ ] Build native libraries in CI and include them in extension packages for each supported platform.
- [ ] Run the native-reader parity tests in CI with a compiled library.
- [ ] Add end-to-end Blender import/export timings and peak-memory measurements.
- [ ] Recheck Blender round trips after the latest scene and tangent changes.

## Before 0.1.0

- [ ] Review and freeze the file contract, including compatibility rules.
- [ ] Choose the final name and icon.
- [ ] Run the release checks and inspect the extension ZIP, wheel, and checksums.
- [ ] Publish the first release. Version 0.1.0 is still unreleased.
