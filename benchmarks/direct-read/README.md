# ZPM read benchmarks

Measured **2026-10-31** (run `20261031T055314Z`) on an AMD Ryzen Z1 Extreme, Windows 11,
.NET 10 with tiered compilation off. [Raw JSON](results/20261031T055314Z.json) |
[CSV](results/20261031T055314Z.csv)

Do not compare these timings with the mesh-core results: those came from another machine.
Here the frozen codec is rerun in the same session, rolled forward to the same runtime.

- **ZPM**: the one mesh layout. The encoder picks the smallest form of each stream by itself.
- **Compact16** and **old smallest**: earlier layouts, kept only here as the comparison.
  Old smallest is whichever of the ten frozen mesh-core profiles has the fewest bytes.
- The last six meshes are real models read through Blender. They are private game assets:
  only their measurements are published, so the public fixtures alone reproduce the first 11 rows.

## File size (bytes)

| Mesh | Vertices | Compact16 | Old smallest | **ZPM** | Smaller than compact16 | Smaller than old smallest |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| quad_grid | 16,641 | 462,924 | 114,823 | **46,076** | 10.05x | 2.49x |
| triangle_grid | 16,641 | 593,996 | 257,643 | **182,196** | 3.26x | 1.41x |
| shuffled_quad_ids | 16,641 | 462,924 | 275,603 | **224,472** | 2.06x | 1.23x |
| mixed_tri_quad_ngon | 16,641 | 572,516 | 199,903 | **196,209** | 2.92x | 1.02x |
| disjoint_ngons | 138,987 | 3,401,288 | 1,025,297 | **419,442** | 8.11x | 2.44x |
| random_triangle_indices | 16,641 | 494,620 | 420,964 | **362,948** | 1.36x | 1.16x |
| repository_obj418 | 1,274 | 32,104 | 23,587 | **17,113** | 1.88x | 1.38x |
| loose_nonmanifold | 6 | 186 | 117 | **235** | 0.79x | 0.50x |
| float_bit_patterns | 3 | 118 | 103 | **197** | 0.60x | 0.52x |
| wire_only | 3 | 108 | 97 | **169** | 0.64x | 0.57x |
| empty_mesh | 0 | 64 | 64 | **80** | 0.80x | 0.80x |
| 04263 | 16,694 | 335,108 | 214,841 | **86,424** | 3.88x | 2.49x |
| 04362 | 1,728 | 31,168 | 20,003 | **6,356** | 4.90x | 3.15x |
| 11375 | 104 | 2,000 | 823 | **542** | 3.69x | 1.52x |
| minotaur | 2,190 | 48,100 | 36,912 | **18,235** | 2.64x | 2.02x |
| samurai | 3,682 | 77,820 | 56,903 | **26,691** | 2.92x | 2.13x |
| werewolf | 1,511 | 36,532 | 28,444 | **16,574** | 2.20x | 1.72x |

Meshes of a few vertices are larger than before: the table and 32-byte tail outweigh their data.

## Reading (microseconds)

Old profiles must decode everything before any read, so their first-face time is a full decode.
Compact16 decode is mostly a memory copy of a much larger file and stays the fastest full read.

| Mesh | Old smallest decode | Compact16 decode | ZPM full read | ZPM vs old | First face: old | First face: ZPM | Gain |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| quad_grid | 895.77 | 9.95 | 337.53 | 2.7x | 895.77 | 0.128 | 7,010x |
| triangle_grid | 1672.21 | 12.37 | 447.89 | 3.7x | 1672.21 | 0.105 | 15,997x |
| shuffled_quad_ids | 1628.34 | 11.38 | 315.12 | 5.2x | 1628.34 | 0.091 | 17,916x |
| mixed_tri_quad_ngon | 1215.22 | 11.70 | 318.74 | 3.8x | 1215.22 | 0.100 | 12,149x |
| disjoint_ngons | 6699.63 | 70.15 | 1503.37 | 4.5x | 6699.63 | 0.098 | 68,329x |
| random_triangle_indices | 1919.04 | 55.08 | 477.90 | 4.0x | 1919.04 | 0.073 | 26,183x |
| repository_obj418 | 105.50 | 0.83 | 12.04 | 8.8x | 105.50 | 0.074 | 1,430x |
| 04263 | 982.07 | 5.21 | 138.57 | 7.1x | 982.07 | 0.098 | 10,017x |
| 04362 | 135.42 | 0.62 | 10.99 | 12.3x | 135.42 | 0.091 | 1,481x |
| 11375 | 5.94 | 0.12 | 0.70 | 8.5x | 5.94 | 0.085 | 70x |
| minotaur | 199.97 | 0.93 | 22.02 | 9.1x | 199.97 | 0.091 | 2,191x |
| samurai | 337.77 | 1.38 | 35.36 | 9.6x | 337.77 | 0.091 | 3,704x |
| werewolf | 133.02 | 0.84 | 16.77 | 7.9x | 133.02 | 0.091 | 1,467x |

## One element at a time (nanoseconds, random order)

Allocated on every read path: **0 bytes**, on every mesh.

| Mesh | Vertex | Corner | Face | Edge | Open | Validate (us) | Compact16 validate (us) |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| quad_grid | 17.0 | 9.7 | 41.4 | 32.2 | 86 | 33.24 | 106.92 |
| triangle_grid | 15.3 | 9.1 | 17.9 | 30.1 | 87 | 35.29 | 181.22 |
| shuffled_quad_ids | 16.6 | 3.3 | 13.0 | 14.6 | 78 | 273.48 | 83.60 |
| mixed_tri_quad_ngon | 14.7 | 2.9 | 16.6 | 13.4 | 83 | 45.52 | 94.71 |
| disjoint_ngons | 14.6 | 2.6 | 30.2 | 16.8 | 68 | 72.24 | 245.80 |
| random_triangle_indices | 8.0 | 3.1 | 9.7 | 13.9 | 64 | 215.60 | 117.20 |
| repository_obj418 | 11.5 | 3.1 | 9.6 | 12.7 | 64 | 11.30 | 4.71 |
| 04263 | 12.8 | 9.4 | 17.5 | 22.6 | 80 | 6.66 | 43.15 |
| 04362 | 21.6 | 2.7 | 10.4 | 10.9 | 81 | 5.72 | 3.16 |
| 11375 | 21.4 | 2.8 | 9.1 | 11.5 | 76 | 0.72 | 0.21 |
| minotaur | 21.3 | 4.0 | 8.6 | 10.8 | 83 | 8.88 | 6.20 |
| samurai | 21.4 | 3.0 | 8.7 | 11.3 | 82 | 20.29 | 9.66 |
| werewolf | 21.7 | 2.7 | 8.7 | 11.0 | 82 | 13.92 | 5.40 |

A plain array read is about 1 ns. A compact16 mixed face costs over 2,000 ns because it walks all
earlier faces. Validation reads every block descriptor, and also reads a block's values when the
descriptor alone cannot prove its IDs in range. It is slower than compact16 on some meshes.

## Tried and rejected

Size-tested on the same meshes, against plain 64-value blocks:

| Idea | Saved | Why not |
| --- | ---: | --- |
| Outlier patches inside a block | 1.3% | Adds a slower path to every read |
| Palette per block | 0.2% | Adds a second lookup to every read |
| First-use vertex marking for corners | none | Blender's vertex order is not first-use |

## Boundaries

- Files are already in memory. Disk, memory mapping and GPU upload are not measured.
- No comparison with GLB, meshopt or Draco has been run, so no advantage over them is claimed.
  Sequential mesh codecs may well be smaller, but they must decode before reading.
- Fixed run order, no confidence intervals, a shared handheld PC. Small timings are noisy.
- AVX2 speeds up full reads. The scalar path gives the same results but was not timed alone.

## Correctness

Each encode is compared with its source through every read path. 30,000 fuzzed files were tried:
17,009 rejected, 12,991 accepted and then read in full with every ID in range and no crash.
The Python and C# encoders write identical bytes.

## Reproduce

Needs the built frozen reference (`benchmarks/mesh-core`) and a .NET 10 runtime.

```sh
cd benchmarks/direct-read/reference && dotnet build -c Release && cd ../../..
python benchmarks/direct-read/run.py
```

Without an SDK, build with a bare Roslyn compiler through `build_with_csc.py`.
`tools/blender_to_zms.py` turns GLB files into fixtures; pass their folder as `--private-fixtures`.
Runs go to the ignored `runs/` folder. Keep published results unchanged; compare changes as new runs.
