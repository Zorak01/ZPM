# Mesh read benchmarks

## Latest run: 2026-11-02

Run `20261102T222054Z`, [raw JSON](results/20261102T222054Z.json), in 58 s. Connectivity only: positions, faces, corners and edges.

| Fixture | Bytes | Read all (µs) | Random vertex (ns) | Random corner (ns) | Random edge (ns) |
| --- | ---: | ---: | ---: | ---: | ---: |
| quad_grid | 28,296 | 214 | 23 | 12 | 28 |
| triangle_grid | 100,560 | 310 | 27 | 306 | 619 |
| shuffled_quad_ids | 28,296 | 209 | 22 | 12 | 27 |
| mixed_tri_quad_ngon | 116,880 | 429 | 29 | 457 | 943 |
| disjoint_ngons | 366,776 | 1,634 | 26 | 5 | 16 |
| random_triangle_indices | 209,688 | 409 | 14 | 5 | 15 |
| repository_obj418 | 8,508 | 16 | 18 | 5 | 12 |
| loose_nonmanifold | 264 | 0 | 10 | 4 | 7 |
| float_bit_patterns | 228 | 0 | 13 | 5 | 15 |
| wire_only | 200 | 0 | 11 | 0 | 7 |
| empty_mesh | 88 | 0 | 0 | 0 | 0 |
| 04263 | 63,388 | 120 | 21 | 13 | 27 |
| 04362 | 3,148 | 13 | 37 | 13 | 25 |
| 11375 | 436 | 1 | 21 | 2 | 7 |
| minotaur | 8,216 | 24 | 36 | 329 | 712 |
| samurai | 13,168 | 39 | 37 | 390 | 803 |
| werewolf | 7,644 | 19 | 31 | 326 | 679 |

Packed bases and 4-bit widths took a random vertex read from about 25 ns to about 45 ns in exchange for smaller files. A
random corner decodes part of a strip block, so read many corners with `GetCorners` or a `ZpmCornerCursor`.

The sections below are from the older run of 2026-10-31.

Measured **2026-10-31** (run `20261031T070333Z`) on an AMD Ryzen Z1 Extreme, Windows 11,
.NET 10 with tiered compilation off. [Raw JSON](results/20261031T070333Z.json) |
[CSV](results/20261031T070333Z.csv)

Do not compare these timings with the mesh-core results: those came from another machine.
Here the frozen codec is rerun in the same session, rolled forward to the same runtime.

- **ZPM**: the one mesh layout. The encoder picks the smallest form of each stream by itself.
- Earlier layouts keep exact bits and order, so the comparison is not like for like: ZPM moves points by the tiny amount below.
- **Compact16** and **old smallest**: earlier layouts, kept only here as the comparison.
  Old smallest is whichever of the ten frozen mesh-core profiles has the fewest bytes.
- The last six meshes are real models read through Blender. They are private game assets:
  only their measurements are published, so the public fixtures alone reproduce the first 11 rows.

## File size (bytes)

| Mesh | Vertices | Compact16 | Old smallest | **ZPM** | Smaller than compact16 | Smaller than old smallest |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| quad_grid | 16,641 | 462,924 | 114,823 | **45,924** | 10.08x | 2.50x |
| triangle_grid | 16,641 | 593,996 | 257,643 | **139,048** | 4.27x | 1.85x |
| shuffled_quad_ids | 16,641 | 462,924 | 275,603 | **67,152** | 6.89x | 4.10x |
| mixed_tri_quad_ngon | 16,641 | 572,516 | 199,903 | **166,576** | 3.44x | 1.20x |
| disjoint_ngons | 138,987 | 3,401,288 | 1,025,297 | **386,020** | 8.81x | 2.66x |
| random_triangle_indices | 16,641 | 494,620 | 420,964 | **214,784** | 2.30x | 1.96x |
| repository_obj418 | 1,274 | 32,104 | 23,587 | **8,944** | 3.59x | 2.64x |
| loose_nonmanifold | 6 | 186 | 117 | **280** | 0.66x | 0.42x |
| float_bit_patterns | 3 | 118 | 103 | **244** | 0.48x | 0.42x |
| wire_only | 3 | 108 | 97 | **212** | 0.51x | 0.46x |
| empty_mesh | 0 | 64 | 64 | **84** | 0.76x | 0.76x |
| 04263 | 16,694 | 335,108 | 214,841 | **67,908** | 4.93x | 3.16x |
| 04362 | 1,728 | 31,168 | 20,003 | **3,448** | 9.04x | 5.80x |
| 11375 | 104 | 2,000 | 823 | **464** | 4.31x | 1.77x |
| minotaur | 2,190 | 48,100 | 36,912 | **9,684** | 4.97x | 3.81x |
| samurai | 3,682 | 77,820 | 56,903 | **14,160** | 5.50x | 4.02x |
| werewolf | 1,511 | 36,532 | 28,444 | **9,132** | 4.00x | 3.11x |

Meshes of a few vertices are larger than before: the table and 32-byte tail outweigh their data.

## Reading (microseconds)

Old profiles must decode everything before any read, so their first-face time is a full decode.
Compact16 decode is mostly a memory copy of a much larger file and stays the fastest full read.

| Mesh | Old smallest decode | Compact16 decode | ZPM full read | ZPM vs old | First face: old | First face: ZPM | Gain |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| quad_grid | 919.97 | 9.80 | 303.81 | 3.0x | 919.97 | 0.151 | 6,101x |
| triangle_grid | 1747.42 | 12.18 | 992.27 | 1.8x | 1747.42 | 0.260 | 6,708x |
| shuffled_quad_ids | 1875.86 | 10.45 | 406.90 | 4.6x | 1875.86 | 0.191 | 9,819x |
| mixed_tri_quad_ngon | 1308.27 | 10.84 | 527.47 | 2.5x | 1308.27 | 0.131 | 9,989x |
| disjoint_ngons | 8737.74 | 143.00 | 2127.36 | 4.1x | 8737.74 | 0.147 | 59,296x |
| random_triangle_indices | 2056.34 | 8.89 | 533.18 | 3.9x | 2056.34 | 0.098 | 21,088x |
| repository_obj418 | 109.80 | 0.66 | 22.46 | 4.9x | 109.80 | 0.109 | 1,004x |
| 04263 | 1075.92 | 5.40 | 165.58 | 6.5x | 1075.92 | 0.131 | 8,191x |
| 04362 | 135.22 | 0.65 | 18.66 | 7.2x | 135.22 | 0.140 | 963x |
| 11375 | 6.21 | 0.13 | 1.62 | 3.8x | 6.21 | 0.107 | 58x |
| minotaur | 218.17 | 0.98 | 39.68 | 5.5x | 218.17 | 0.141 | 1,550x |
| samurai | 376.30 | 1.52 | 81.77 | 4.6x | 376.30 | 0.146 | 2,570x |
| werewolf | 145.94 | 1.10 | 38.46 | 3.8x | 145.94 | 0.165 | 882x |

## One element at a time (nanoseconds, random order)

Allocated on every read path: **0 bytes**, on every mesh.

| Mesh | Vertex | Corner | Face | Edge | Open | Validate (us) | Compact16 validate (us) |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| quad_grid | 12.7 | 8.6 | 25.2 | 23.4 | 126 | 39.08 | 84.62 |
| triangle_grid | 29.8 | 15.2 | 33.5 | 41.9 | 227 | 239.53 | 147.23 |
| shuffled_quad_ids | 13.2 | 9.7 | 38.4 | 31.1 | 153 | 82.34 | 95.40 |
| mixed_tri_quad_ngon | 17.2 | 3.7 | 21.5 | 13.9 | 109 | 297.06 | 100.70 |
| disjoint_ngons | 16.6 | 4.8 | 40.3 | 14.7 | 107 | 133.10 | 291.17 |
| random_triangle_indices | 9.4 | 3.9 | 11.4 | 12.8 | 86 | 183.19 | 104.24 |
| repository_obj418 | 12.1 | 3.8 | 10.0 | 10.5 | 99 | 9.53 | 5.13 |
| 04263 | 23.0 | 9.8 | 18.1 | 24.6 | 113 | 40.56 | 45.97 |
| 04362 | 25.1 | 4.2 | 9.9 | 12.7 | 131 | 7.05 | 3.24 |
| 11375 | 14.4 | 1.6 | 5.8 | 7.4 | 101 | 0.44 | 0.22 |
| minotaur | 32.7 | 2.8 | 9.5 | 9.8 | 131 | 19.51 | 6.76 |
| samurai | 27.7 | 2.6 | 8.6 | 9.3 | 138 | 32.21 | 11.09 |
| werewolf | 31.6 | 4.6 | 16.1 | 14.2 | 149 | 17.24 | 10.40 |

## Normals and UVs

Real meshes with their surface data included. Raw is plain arrays: 32-bit floats and 16-bit IDs.

| Mesh | Corners | Raw with normals and UVs | ZPM with surface | Smaller | ZPM geometry only | Random normal ns | Allocated |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 04263 | 25,332 | 639,092 | **88,940** | 7.19x | 67,908 | 22.7 | 0 |
| 04362 | 1,728 | 65,728 | **9,220** | 7.13x | 3,448 | 13.9 | 0 |
| 11375 | 120 | 4,400 | **776** | 5.67x | 464 | 14.6 | 0 |
| minotaur | 4,362 | 100,444 | **16,672** | 6.02x | 9,684 | 12.7 | 0 |
| samurai | 6,558 | 156,516 | **24,840** | 6.30x | 14,160 | 38.5 | 0 |
| werewolf | 3,852 | 113,572 | **15,364** | 7.39x | 9,132 | 42.2 | 0 |

A plain array read is about 1 ns. A compact16 mixed face costs over 2,000 ns because it walks all
earlier faces. Validation reads every block descriptor, and also reads a block's values when the
descriptor alone cannot prove its IDs in range. It is slower than compact16 on some meshes.

## Tried and rejected

Size-tested on the same meshes, against plain 64-value blocks:

| Idea | Saved | Why not |
| --- | ---: | --- |
| Outlier patches inside a block | 1.3% | Adds a slower path to every read |
| Palette per block | 0.2% | Adds a second lookup to every read |
| Walking faces newest first instead of widest first | none | Widest first matched or beat it on nearly every mesh |

## What saving changes

Positions snap to a grid of at most 1 / 2^20 of the model's size, so the largest move is 0.00005% of
its size: about 1 micron on a 2 m character. Faces, vertices and edges are reordered only when
that makes the file smaller. Each run checks every file against its snapped source: same points,
same faces with the same winding, same edges.

## Boundaries

- Files are already in memory. Disk, memory mapping and GPU upload are not measured.
- No comparison with GLB, meshopt or Draco has been run, so no advantage over them is claimed.
  Sequential mesh codecs may well be smaller, but they must decode before reading.
- Fixed run order, no confidence intervals, a shared handheld PC. Small timings are noisy.
- AVX2 speeds up full reads. The scalar path gives the same results but was not timed alone.

## Correctness

Each file is compared with its decoded mesh through every C# read path. 8,000 fuzzed files were tried:
5,533 rejected, 2,467 accepted and then read in full with every ID in range and no crash.
Python writes every file; the C# reader is checked against the source mesh.

## Reproduce

Needs the built frozen reference (`benchmarks/mesh-core`) and a .NET 10 runtime.

```sh
cd benchmarks/mesh-read/reference && dotnet build -c Release && cd ../../..
python benchmarks/mesh-read/run.py
```

Without an SDK, build with a bare Roslyn compiler through `build_with_csc.py`.
`tools/blender_to_zms.py` turns GLB files into fixtures; pass their folder as `--private-fixtures`.
Runs go to the ignored `runs/` folder. Keep published results unchanged; compare changes as new runs.
