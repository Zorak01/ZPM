# Direct-read benchmarks

Measured **2026-10-31** (run `20261031T053849Z`) on an AMD Ryzen Z1 Extreme, Windows 11,
.NET 10 with tiered compilation off. [Raw JSON](results/20261031T053849Z.json) |
[CSV](results/20261031T053849Z.csv)

Do not compare these timings with the mesh-core results: those came from another machine.
Here the frozen codec is rerun in the same session, rolled forward to the same runtime.

- **Direct**: smallest ZMD1 layout. **Fast**: ZMD1 without dedup, palettes or corner references.
- **Old smallest**: whichever of the ten frozen mesh-core profiles has the fewest bytes.
- The last six meshes are real models read through Blender. They are private game assets:
  only their measurements are published, so the public fixtures alone reproduce the first 11 rows.

## File size (bytes)

| Mesh | Vertices | Compact16 | Old smallest | Fast | **Direct** | Smaller than compact16 | Smaller than old smallest |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| quad_grid | 16,641 | 462,924 | 114,823 | 143,924 | **46,076** | 10.05x | 2.49x |
| triangle_grid | 16,641 | 593,996 | 257,643 | 298,956 | **182,196** | 3.26x | 1.41x |
| shuffled_quad_ids | 16,641 | 462,924 | 275,603 | 323,748 | **224,472** | 2.06x | 1.23x |
| mixed_tri_quad_ngon | 16,641 | 572,516 | 199,903 | 300,880 | **196,209** | 2.92x | 1.02x |
| disjoint_ngons | 138,987 | 3,401,288 | 1,025,297 | 871,934 | **419,442** | 8.11x | 2.44x |
| random_triangle_indices | 16,641 | 494,620 | 420,964 | 382,179 | **362,948** | 1.36x | 1.16x |
| repository_obj418 | 1,274 | 32,104 | 23,587 | 23,303 | **17,113** | 1.88x | 1.38x |
| loose_nonmanifold | 6 | 186 | 117 | 235 | **235** | 0.79x | 0.50x |
| float_bit_patterns | 3 | 118 | 103 | 217 | **197** | 0.60x | 0.52x |
| wire_only | 3 | 108 | 97 | 169 | **169** | 0.64x | 0.57x |
| empty_mesh | 0 | 64 | 64 | 80 | **80** | 0.80x | 0.80x |
| 04263 | 16,694 | 335,108 | 214,841 | 186,692 | **86,424** | 3.88x | 2.49x |
| 04362 | 1,728 | 31,168 | 20,003 | 26,656 | **6,356** | 4.90x | 3.15x |
| 11375 | 104 | 2,000 | 823 | 1,560 | **542** | 3.69x | 1.52x |
| minotaur | 2,190 | 48,100 | 36,912 | 38,534 | **18,235** | 2.64x | 2.02x |
| samurai | 3,682 | 77,820 | 56,903 | 63,050 | **26,691** | 2.92x | 2.13x |
| werewolf | 1,511 | 36,532 | 28,444 | 29,763 | **16,574** | 2.20x | 1.72x |

Meshes of a few vertices are larger than before: the table and 32-byte tail outweigh their data.

## Reading (microseconds)

Old profiles must decode everything before any read, so their first-face time is a full decode.
Compact16 decode is mostly a memory copy of a much larger file and stays the fastest full read.

| Mesh | Old smallest decode | Compact16 decode | Fast full read | Direct full read | Direct vs old | First face: old | First face: direct | Gain |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| quad_grid | 867.50 | 7.48 | 168.71 | 275.08 | 3.2x | 867.50 | 0.103 | 8,447x |
| triangle_grid | 1550.94 | 11.02 | 216.48 | 337.56 | 4.6x | 1550.94 | 0.105 | 14,786x |
| shuffled_quad_ids | 1460.55 | 7.97 | 118.12 | 285.43 | 5.1x | 1460.55 | 0.080 | 18,226x |
| mixed_tri_quad_ngon | 1190.55 | 13.82 | 188.74 | 294.82 | 4.0x | 1190.55 | 0.086 | 13,920x |
| disjoint_ngons | 6486.60 | 78.68 | 659.91 | 1518.90 | 4.3x | 6486.60 | 0.092 | 70,319x |
| random_triangle_indices | 1785.86 | 16.56 | 132.83 | 466.60 | 3.8x | 1785.86 | 0.066 | 26,941x |
| repository_obj418 | 101.69 | 0.62 | 12.27 | 12.78 | 8.0x | 101.69 | 0.071 | 1,430x |
| 04263 | 998.00 | 5.42 | 150.61 | 138.29 | 7.2x | 998.00 | 0.104 | 9,561x |
| 04362 | 124.62 | 0.60 | 9.50 | 11.24 | 11.1x | 124.62 | 0.089 | 1,395x |
| 11375 | 5.72 | 0.12 | 0.57 | 0.65 | 8.8x | 5.72 | 0.084 | 68x |
| minotaur | 191.52 | 0.90 | 13.55 | 21.21 | 9.0x | 191.52 | 0.090 | 2,137x |
| samurai | 340.14 | 1.38 | 23.52 | 35.42 | 9.6x | 340.14 | 0.091 | 3,732x |
| werewolf | 120.95 | 0.71 | 10.22 | 15.64 | 7.7x | 120.95 | 0.090 | 1,351x |

## One element at a time (nanoseconds, random order)

Every Direct read path allocated **0 bytes** on every mesh.

| Mesh | Vertex | Corner | Face | Edge | Open | Validate (us) | Compact16 validate (us) |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| quad_grid | 11.1 | 8.3 | 22.0 | 20.4 | 81 | 29.96 | 79.59 |
| mixed_tri_quad_ngon | 15.6 | 2.9 | 16.1 | 13.1 | 69 | 45.16 | 86.67 |
| disjoint_ngons | 14.4 | 2.7 | 30.9 | 13.3 | 61 | 69.57 | 228.44 |
| shuffled_quad_ids | 11.1 | 3.1 | 17.8 | 16.1 | 62 | 248.60 | 77.30 |
| 04263 | 13.5 | 9.7 | 17.9 | 28.6 | 86 | 6.64 | 39.10 |
| samurai | 21.6 | 3.0 | 8.9 | 12.4 | 82 | 20.36 | 9.56 |
| werewolf | 19.5 | 2.6 | 8.3 | 10.4 | 81 | 13.60 | 5.14 |

A plain array read is about 1 ns. A compact16 mixed face costs over 2,000 ns because it walks all
earlier faces. Validation reads every block descriptor, and also reads a block's values when the
descriptor alone cannot prove its IDs in range. It is slower than compact16 on some meshes.

## Boundaries

- Files are already in memory. Disk, memory mapping and GPU upload are not measured.
- No comparison with GLB, meshopt or Draco has been run, so no advantage over them is claimed.
  Sequential mesh codecs may well be smaller, but they must decode before reading.
- Whole-file zstd sizes are not reported: the local zstandard build does not load on this Python.
- Fixed run order, no confidence intervals, a shared handheld PC. Small timings are noisy.
- AVX2 speeds up full reads. The scalar path gives the same results but was not timed alone.

## Correctness

Each encode is compared with its source through every read path. 30,000 fuzzed files were tried:
17,009 rejected, 12,991 accepted and then read in full with every ID in range and no crash.
The Python and C# encoders wrote identical bytes for all 17 meshes.

## Reproduce

Needs the built frozen reference (`benchmarks/mesh-core`) and a .NET 10 runtime.

```sh
cd benchmarks/direct-read/reference && dotnet build -c Release && cd ../../..
python benchmarks/direct-read/run.py
```

Without an SDK, build with a bare Roslyn compiler through `build_with_csc.py`.
`tools/blender_to_zms.py` turns GLB files into fixtures; pass their folder as `--private-fixtures`.
Runs go to the ignored `runs/` folder. Keep published results unchanged; compare changes as new runs.
