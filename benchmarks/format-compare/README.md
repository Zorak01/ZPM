# ZPM against Draco and meshoptimizer

Measured **2026-10-31** (run `20261031T070500Z`) on an AMD Ryzen Z1 Extreme. [Raw JSON](results/20261031T070500Z.json)

**Today's result: ZPM loses on disk size and whole-mesh speed. It wins on reading part of a mesh and on
memory, because it needs no decode step.** ZPM's goal of beating all of them is not met yet.

Every format holds the same six real triangle meshes at ZPM's precision: 20-bit positions, 12-bit normals,
20-bit UVs. Edges are left out of ZPM's bytes because the others do not store them. Meshopt gets its own
recommended vertex and index order.

## File size (bytes)

| Mesh | ZPM | Draco | Meshopt | Meshopt + zstd | ZPM vs Draco | ZPM vs meshopt |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 04263 | **79,328** | 38,498 | 149,516 | 129,127 | 2.06x larger | 1.88x smaller |
| 04362 | **8,896** | 6,906 | 18,804 | 16,355 | 1.29x larger | 2.11x smaller |
| 11375 | **752** | 501 | 567 | 479 | 1.50x larger | 1.33x larger |
| minotaur | **15,916** | 8,823 | 11,829 | 10,689 | 1.80x larger | 1.35x larger |
| samurai | **23,696** | 13,705 | 18,445 | 15,902 | 1.73x larger | 1.28x larger |
| werewolf | **14,708** | 7,521 | 9,855 | 9,263 | 1.96x larger | 1.49x larger |

Meshopt is designed to be followed by a general compressor like zstd. A file compressed that way must be
decompressed first, so it is not comparable when reading in place, and ZPM could use zstd for download too.

## Reading a whole mesh (microseconds)

| Mesh | ZPM, stored values | ZPM, as floats | Draco decode | Meshopt decode, WASM | First value: ZPM | Memory for one value: ZPM |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 04263 | 307.1 | 837.8 | 5318.0 | 136.9 | under 1 | 0 bytes |
| 04362 | 34.4 | 66.9 | 452.7 | 20.2 | under 1 | 0 bytes |
| 11375 | 2.8 | 3.6 | 35.4 | 3.9 | under 1 | 0 bytes |
| minotaur | 66.2 | 103.2 | 765.8 | 25.1 | under 1 | 0 bytes |
| samurai | 99.2 | 230.5 | 1164.0 | 26.7 | under 1 | 0 bytes |
| werewolf | 82.4 | 149.6 | 770.9 | 19.5 | under 1 | 0 bytes |

"Stored values" is ZPM's closest match to meshopt: both produce integers. "As floats" also turns every
normal and UV into floats, one corner at a time.

**This is not like for like.** ZPM is C# on .NET 10, Draco is native C++ called through Python, and meshopt
is WebAssembly in Node. Native meshopt would likely be faster than shown, so its lead is probably larger.

## Memory needed to read

| Mesh | ZPM reader needs | Meshopt must fill | ZPM file in memory |
| --- | ---: | ---: | ---: |
| 04263 | 0 B | 369,920 B | 88,940 B |
| 04362 | 0 B | 47,376 B | 9,220 B |
| 11375 | 0 B | 1,488 B | 776 B |
| minotaur | 0 B | 34,168 B | 16,672 B |
| samurai | 0 B | 53,896 B | 24,840 B |
| werewolf | 0 B | 34,440 B | 15,364 B |

Draco and meshopt must decode a whole mesh into new memory before reading any of it, and need the file
as well unless it is freed. ZPM reads the file where it is. Draco's own decode memory was not measured.

## Why ZPM is bigger

- **Duplicate vertices.** Samurai has 3,682 vertices, but only 1,729 differ in position, normal and UV.
  The others merge them. Merging would take its file from 24,916 to about 19,000 bytes in a size test,
  but that changes vertex IDs and is not allowed by the current rules.
- **Reading by index costs bits.** Draco and meshopt can predict each value from the one before.
  ZPM must be able to jump to any value, which rules out most of that prediction.

## Not measured

Load time including disk, GPU upload, and real use in a game. Only six meshes, all triangles. Fixed run order
on a shared handheld PC, so small timings are noisy.

## Reproduce

Needs Python packages `DracoPy`, `zstandard` and `numpy`, the npm package `meshoptimizer`, and a mesh-read
run with surface files.

```sh
python benchmarks/format-compare/compare.py --zpm-run benchmarks/mesh-read/runs/RUN --meshopt node_modules/meshoptimizer
```
