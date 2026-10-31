# Mesh read benchmarks

Measured **2026-10-31** (run `20261031T063431Z`) on an AMD Ryzen Z1 Extreme, Windows 11,
.NET 10 with tiered compilation off. [Raw JSON](results/20261031T063431Z.json) |
[CSV](results/20261031T063431Z.csv)

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
| quad_grid | 16,641 | 462,924 | 114,823 | **46,156** | 10.03x | 2.49x |
| triangle_grid | 16,641 | 593,996 | 257,643 | **167,100** | 3.55x | 1.54x |
| shuffled_quad_ids | 16,641 | 462,924 | 275,603 | **67,384** | 6.87x | 4.09x |
| mixed_tri_quad_ngon | 16,641 | 572,516 | 199,903 | **194,492** | 2.94x | 1.03x |
| disjoint_ngons | 138,987 | 3,401,288 | 1,025,297 | **389,796** | 8.73x | 2.63x |
| random_triangle_indices | 16,641 | 494,620 | 420,964 | **245,688** | 2.01x | 1.71x |
| repository_obj418 | 1,274 | 32,104 | 23,587 | **11,000** | 2.92x | 2.14x |
| loose_nonmanifold | 6 | 186 | 117 | **288** | 0.65x | 0.41x |
| float_bit_patterns | 3 | 118 | 103 | **236** | 0.50x | 0.44x |
| wire_only | 3 | 108 | 97 | **212** | 0.51x | 0.46x |
| empty_mesh | 0 | 64 | 64 | **76** | 0.84x | 0.84x |
| 04263 | 16,694 | 335,108 | 214,841 | **84,160** | 3.98x | 2.55x |
| 04362 | 1,728 | 31,168 | 20,003 | **3,620** | 8.61x | 5.53x |
| 11375 | 104 | 2,000 | 823 | **496** | 4.03x | 1.66x |
| minotaur | 2,190 | 48,100 | 36,912 | **10,328** | 4.66x | 3.57x |
| samurai | 3,682 | 77,820 | 56,903 | **14,928** | 5.21x | 3.81x |
| werewolf | 1,511 | 36,532 | 28,444 | **9,976** | 3.66x | 2.85x |

Meshes of a few vertices are larger than before: the table and 32-byte tail outweigh their data.

## Reading (microseconds)

Old profiles must decode everything before any read, so their first-face time is a full decode.
Compact16 decode is mostly a memory copy of a much larger file and stays the fastest full read.

| Mesh | Old smallest decode | Compact16 decode | ZPM full read | ZPM vs old | First face: old | First face: ZPM | Gain |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| quad_grid | 872.34 | 8.69 | 402.17 | 2.2x | 872.34 | 0.146 | 5,962x |
| triangle_grid | 1584.40 | 12.47 | 457.82 | 3.5x | 1584.40 | 0.112 | 14,193x |
| shuffled_quad_ids | 1402.68 | 7.73 | 291.97 | 4.8x | 1402.68 | 0.121 | 11,621x |
| mixed_tri_quad_ngon | 3489.04 | 10.69 | 500.23 | 7.0x | 3489.04 | 0.138 | 25,304x |
| disjoint_ngons | 8372.52 | 61.34 | 1963.34 | 4.3x | 8372.52 | 0.127 | 65,982x |
| random_triangle_indices | 4076.20 | 11.63 | 992.48 | 4.1x | 4076.20 | 0.100 | 40,959x |
| repository_obj418 | 113.95 | 0.71 | 51.97 | 2.2x | 113.95 | 0.159 | 717x |
| 04263 | 1133.90 | 6.69 | 301.02 | 3.8x | 1133.90 | 0.104 | 10,891x |
| 04362 | 134.57 | 1.04 | 17.43 | 7.7x | 134.57 | 0.128 | 1,053x |
| 11375 | 6.34 | 0.13 | 1.71 | 3.7x | 6.34 | 0.102 | 62x |
| minotaur | 349.59 | 1.31 | 52.43 | 6.7x | 349.59 | 0.202 | 1,730x |
| samurai | 586.70 | 1.95 | 92.66 | 6.3x | 586.70 | 0.183 | 3,206x |
| werewolf | 323.27 | 1.34 | 44.13 | 7.3x | 323.27 | 0.180 | 1,795x |

## One element at a time (nanoseconds, random order)

Allocated on every read path: **0 bytes**, on every mesh.

| Mesh | Vertex | Corner | Face | Edge | Open | Validate (us) | Compact16 validate (us) |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| quad_grid | 17.6 | 9.1 | 35.4 | 27.1 | 111 | 63.94 | 76.99 |
| triangle_grid | 14.9 | 9.6 | 18.5 | 30.6 | 93 | 74.92 | 125.04 |
| shuffled_quad_ids | 10.8 | 7.9 | 23.5 | 22.9 | 97 | 54.63 | 75.41 |
| mixed_tri_quad_ngon | 15.6 | 3.8 | 20.8 | 22.1 | 117 | 342.13 | 180.26 |
| disjoint_ngons | 24.6 | 4.2 | 33.7 | 13.8 | 93 | 207.66 | 240.67 |
| random_triangle_indices | 13.7 | 6.6 | 19.5 | 20.0 | 80 | 166.30 | 99.36 |
| repository_obj418 | 20.6 | 5.0 | 16.9 | 17.6 | 142 | 16.86 | 5.35 |
| 04263 | 20.9 | 3.9 | 9.9 | 14.8 | 94 | 57.34 | 89.70 |
| 04362 | 24.6 | 3.9 | 15.6 | 13.1 | 112 | 4.19 | 3.42 |
| 11375 | 21.2 | 3.1 | 11.3 | 7.8 | 91 | 0.40 | 0.38 |
| minotaur | 38.5 | 4.7 | 15.9 | 16.6 | 186 | 34.42 | 10.55 |
| samurai | 32.0 | 2.6 | 9.4 | 9.5 | 174 | 41.60 | 20.83 |
| werewolf | 36.8 | 4.7 | 15.5 | 16.5 | 165 | 25.14 | 11.63 |

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

Each file is compared with its decoded mesh through every C# read path. 16,000 fuzzed files were tried:
11,270 rejected, 4,730 accepted and then read in full with every ID in range and no crash.
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
