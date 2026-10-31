# Mesh read benchmarks

Measured **2026-10-31** (run `20261031T065021Z`) on an AMD Ryzen Z1 Extreme, Windows 11,
.NET 10 with tiered compilation off. [Raw JSON](results/20261031T065021Z.json) |
[CSV](results/20261031T065021Z.csv)

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
| repository_obj418 | 1,274 | 32,104 | 23,587 | **8,960** | 3.58x | 2.63x |
| loose_nonmanifold | 6 | 186 | 117 | **280** | 0.66x | 0.42x |
| float_bit_patterns | 3 | 118 | 103 | **244** | 0.48x | 0.42x |
| wire_only | 3 | 108 | 97 | **212** | 0.51x | 0.46x |
| empty_mesh | 0 | 64 | 64 | **84** | 0.76x | 0.76x |
| 04263 | 16,694 | 335,108 | 214,841 | **69,336** | 4.83x | 3.10x |
| 04362 | 1,728 | 31,168 | 20,003 | **3,464** | 9.00x | 5.77x |
| 11375 | 104 | 2,000 | 823 | **464** | 4.31x | 1.77x |
| minotaur | 2,190 | 48,100 | 36,912 | **9,700** | 4.96x | 3.81x |
| samurai | 3,682 | 77,820 | 56,903 | **14,236** | 5.47x | 4.00x |
| werewolf | 1,511 | 36,532 | 28,444 | **9,148** | 3.99x | 3.11x |

Meshes of a few vertices are larger than before: the table and 32-byte tail outweigh their data.

## Reading (microseconds)

Old profiles must decode everything before any read, so their first-face time is a full decode.
Compact16 decode is mostly a memory copy of a much larger file and stays the fastest full read.

| Mesh | Old smallest decode | Compact16 decode | ZPM full read | ZPM vs old | First face: old | First face: ZPM | Gain |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| quad_grid | 753.20 | 8.81 | 246.29 | 3.1x | 753.20 | 0.133 | 5,664x |
| triangle_grid | 1389.00 | 11.85 | 399.35 | 3.5x | 1389.00 | 0.127 | 10,965x |
| shuffled_quad_ids | 1362.22 | 9.09 | 301.95 | 4.5x | 1362.22 | 0.134 | 10,176x |
| mixed_tri_quad_ngon | 1102.37 | 10.91 | 422.87 | 2.6x | 1102.37 | 0.124 | 8,885x |
| disjoint_ngons | 6142.74 | 92.18 | 1818.84 | 3.4x | 6142.74 | 0.125 | 49,165x |
| random_triangle_indices | 1753.81 | 8.22 | 454.84 | 3.9x | 1753.81 | 0.094 | 18,625x |
| repository_obj418 | 90.98 | 0.62 | 16.88 | 5.4x | 90.98 | 0.106 | 860x |
| 04263 | 924.60 | 5.06 | 170.86 | 5.4x | 924.60 | 0.104 | 8,857x |
| 04362 | 120.66 | 0.60 | 15.67 | 7.7x | 120.66 | 0.131 | 923x |
| 11375 | 5.60 | 0.12 | 0.85 | 6.6x | 5.60 | 0.101 | 56x |
| minotaur | 174.29 | 0.86 | 30.36 | 5.7x | 174.29 | 0.130 | 1,346x |
| samurai | 298.66 | 1.34 | 49.52 | 6.0x | 298.66 | 0.130 | 2,302x |
| werewolf | 116.75 | 0.66 | 22.85 | 5.1x | 116.75 | 0.122 | 960x |

## One element at a time (nanoseconds, random order)

Allocated on every read path: **0 bytes**, on every mesh.

| Mesh | Vertex | Corner | Face | Edge | Open | Validate (us) | Compact16 validate (us) |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| quad_grid | 10.9 | 7.3 | 22.2 | 20.8 | 111 | 28.29 | 72.08 |
| triangle_grid | 15.4 | 8.8 | 17.6 | 22.1 | 109 | 83.92 | 108.46 |
| shuffled_quad_ids | 11.5 | 7.8 | 23.5 | 22.4 | 110 | 60.89 | 72.56 |
| mixed_tri_quad_ngon | 15.4 | 3.5 | 18.8 | 12.1 | 105 | 221.38 | 80.55 |
| disjoint_ngons | 14.3 | 3.2 | 31.6 | 13.0 | 93 | 116.20 | 222.81 |
| random_triangle_indices | 8.3 | 3.3 | 10.3 | 11.6 | 84 | 133.35 | 85.96 |
| repository_obj418 | 11.0 | 2.8 | 9.2 | 9.2 | 97 | 8.06 | 4.69 |
| 04263 | 13.5 | 2.0 | 6.7 | 7.4 | 98 | 48.18 | 38.87 |
| 04362 | 22.7 | 3.0 | 14.6 | 16.7 | 116 | 4.06 | 3.01 |
| 11375 | 12.1 | 1.4 | 5.2 | 8.8 | 96 | 0.37 | 0.20 |
| minotaur | 22.4 | 2.5 | 8.0 | 8.3 | 122 | 13.05 | 6.11 |
| samurai | 22.6 | 2.3 | 7.6 | 7.9 | 122 | 19.41 | 9.45 |
| werewolf | 19.0 | 2.8 | 9.0 | 9.3 | 113 | 10.41 | 5.14 |

## Normals and UVs

Real meshes with their surface data included. Raw is plain arrays: 32-bit floats and 16-bit IDs.

| Mesh | Corners | Raw with normals and UVs | ZPM with surface | Smaller | ZPM geometry only | Random normal ns | Allocated |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 04263 | 25,332 | 639,092 | **92,896** | 6.88x | 69,336 | 20.6 | 0 |
| 04362 | 1,728 | 65,728 | **9,236** | 7.12x | 3,464 | 12.9 | 0 |
| 11375 | 120 | 4,400 | **776** | 5.67x | 464 | 13.6 | 0 |
| minotaur | 4,362 | 100,444 | **16,688** | 6.02x | 9,700 | 14.2 | 0 |
| samurai | 6,558 | 156,516 | **24,916** | 6.28x | 14,236 | 20.2 | 0 |
| werewolf | 3,852 | 113,572 | **15,380** | 7.38x | 9,148 | 12.1 | 0 |

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
