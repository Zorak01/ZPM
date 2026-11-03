# ZPM against Draco and meshoptimizer

## Packed form, latest run: 2026-11-03

24 meshes, run `20261103T023951Z`. [Raw JSON](results/20261103T023951Z.json). The packed form is what saving writes since 2026-11-03. 20-bit
Draco positions, 12-bit normals, 16-bit UVs. No zstd on any of them. `*` means normals are calculated from faces, so
Draco holds positions, faces and UVs only, as ZPM does.

ZPM's position grid is a power of two, finer than Draco's at 20 bits on every mesh and coarser than at 21, so the two
Draco columns bracket a comparison at equal precision. Packed bytes are the whole mesh file, header and all, with edges
worked out from the faces. Meshopt gets the same vertices.

**Total: packed 2,075,556 bytes, Draco 1,968,482 at 20 bits and 2,121,378 at 21, meshopt 15,227,867.** Packed is 1.054x Draco
at 20 bits, smaller on 7 of 24, and 0.978x at 21 bits, smaller on 20 of 24: about a tie, not a win. It is
7.34x smaller than meshopt and smaller on 23 of 24. **Larger than Draco at 21 bits: DamagedHelmet 1.027x, RiggedFigure 1.048x, spot 1.003x, 11375 1.268x.
Larger than meshopt: suzanne 1.073x.**

Decoding, in microseconds, is `packed_decode.cpp`: native C++ built with `zig c++ -O3 -mavx2`, decoding connectivity,
vertices, positions, normals and UVs into integer arrays, the median of many runs. It leaves out edges and the other
sections, and it trusts its input; the Python reader checks everything. Draco is its native decoder called through
Python; meshopt is native. **Packed decodes 3.7x to 12.8x faster than Draco on 24 of 24, and
3.5x to 22.4x slower than native meshopt on 24 of 24.** Every packed file decodes to the
same integers in C++ and Python, by checksum.

| Mesh | Packed | Draco, 20 bits | Draco, 21 bits | Meshopt, same vertices | Packed decode | Draco decode | Meshopt native decode |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| armadillo | **278,784** | 270,636 * | 290,769 | 2,291,258 | **6,387** | 60,980 | 1,516 |
| Avocado | **4,724** | 4,680 | 4,926 | 6,255 | **57** | 235 | 3 |
| BoomBox | **33,936** | 32,446 | 34,379 | 51,417 | **611** | 2,705 | 27 |
| CesiumMan | **21,884** | 21,759 | 22,851 | 41,501 | **437** | 1,675 | 23 |
| cow | **17,956** | 17,616 * | 18,670 | 140,222 | **283** | 3,638 | 78 |
| DamagedHelmet | **107,004** | 98,931 | 104,195 | 192,323 | **2,149** | 7,847 | 97 |
| Duck | **23,972** | 23,536 | 24,587 | 35,146 | **391** | 1,516 | 18 |
| fandisk | **28,708** | 29,055 * | 31,656 | 160,302 | **697** | 4,801 | 102 |
| happy | **280,800** | 260,185 * | 289,365 | 2,259,566 | **6,789** | 65,883 | 1,614 |
| Lantern | **18,976** | 18,734 | 19,579 | 32,262 | **358** | 1,338 | 20 |
| lucy | **278,356** | 260,701 * | 280,697 | 2,289,250 | **6,606** | 58,592 | 1,626 |
| RiggedFigure | **1,764** | 1,637 | 1,683 | 3,624 | **18** | 79 | 3 |
| spot | **26,432** | 25,150 * | 26,366 | 194,541 | **442** | 3,336 | 126 |
| stanford-bunny | **168,096** | 158,339 * | 171,710 | 1,525,807 | **4,155** | 34,684 | 933 |
| suzanne | **5,352** | 5,230 | 5,446 | 4,988 | **46** | 230 | 5 |
| teapot | **15,800** | 16,833 * | 18,218 | 107,678 | **294** | 2,246 | 69 |
| WaterBottle | **20,080** | 22,510 | 23,660 | 34,393 | **398** | 1,547 | 20 |
| xyzrgb_dragon | **670,344** | 624,576 * | 673,960 | 5,612,096 | **16,356** | 148,182 | 3,993 |
| 04263 | **36,512** | 38,449 | 40,126 | 148,852 | **662** | 3,105 | 68 |
| 04362 | **6,864** | 6,906 | 6,998 | 19,226 | **58** | 292 | 9 |
| 11375 | **648** | 501 | 511 | 1,069 | **6** | 29 | 1 |
| minotaur | **8,296** | 8,823 | 9,108 | 22,741 | **100** | 500 | 10 |
| samurai | **12,640** | 13,705 | 14,129 | 36,377 | **160** | 764 | 18 |
| werewolf | **7,628** | 7,544 | 7,789 | 16,973 | **129** | 492 | 9 |

Saving takes longer: up to 14.3 s for one mesh in Python.

## Direct form, run of 2026-11-02

The form read in place, with no decode step. Saving wrote it until 2026-11-03, and now falls back to it only for
meshes the packed form cannot hold. Its `*` rows gave Draco no UVs; fixed since, which raised Draco on `spot` from
16,529 to 25,150 bytes.

24 meshes, run `20261102T222257Z`. [Raw JSON](results/20261102T222257Z.json). 20-bit positions, 12-bit normals, 16-bit UVs. Edges are left
out of ZPM's bytes and read times, because the others produce none. No zstd on any of them. `*` means normals are
calculated from faces, so Draco is shown with geometry only as well.

ZPM keeps vertices exactly as the source has them, so **same vertices** is the true comparison: meshopt gets that same
mesh. **Merged** lets meshopt drop exact duplicates first, which changes vertex IDs; it is shown for context only.

**Total: ZPM 3,349,416 bytes, meshopt 15,227,867, Draco 1,959,861.** ZPM is 4.55x smaller than meshopt and 1.71x larger
than Draco. Totals are dominated by a few big scans, so count meshes too: ZPM is smaller than meshopt on 21 of 24, and
than Draco on 0 of 24. **Still larger than meshopt: Avocado 1.028x, BoomBox 1.005x, suzanne 1.100x.**

ZPM's bytes include its header, directory, mesh info and tail, about 200 to 300 bytes, while meshopt's are bare streams with
no container counted.

| Mesh | ZPM | Meshopt, same vertices | ZPM / meshopt | Draco | Meshopt, merged |
| --- | ---: | ---: | ---: | ---: | ---: |
| armadillo | **435,652** | 2,291,258 | 0.19 | 270,636 * | 2,291,258 |
| Avocado | **6,428** | 6,255 | 1.03 | 4,680 | 6,255 |
| BoomBox | **51,676** | 51,417 | 1.01 | 32,446 | 51,417 |
| CesiumMan | **36,488** | 41,501 | 0.88 | 21,759 | 35,594 |
| cow | **25,940** | 140,222 | 0.18 | 17,616 * | 140,222 |
| DamagedHelmet | **178,768** | 192,323 | 0.93 | 98,931 | 190,305 |
| Duck | **31,476** | 35,146 | 0.90 | 23,536 | 35,146 |
| fandisk | **59,752** | 160,302 | 0.37 | 29,055 * | 160,302 |
| happy | **441,840** | 2,259,566 | 0.20 | 260,185 * | 2,259,566 |
| Lantern | **28,004** | 32,262 | 0.87 | 18,734 | 32,247 |
| lucy | **433,480** | 2,289,250 | 0.19 | 260,701 * | 2,289,250 |
| RiggedFigure | **2,592** | 3,624 | 0.72 | 1,637 | 3,175 |
| spot | **44,396** | 194,541 | 0.23 | 16,529 * | 194,541 |
| stanford-bunny | **315,276** | 1,525,807 | 0.21 | 158,339 * | 1,525,807 |
| suzanne | **5,488** | 4,988 | 1.10 | 5,230 | 4,988 |
| teapot | **21,872** | 107,678 | 0.20 | 16,833 * | 107,678 |
| WaterBottle | **30,136** | 34,393 | 0.88 | 22,510 | 33,941 |
| xyzrgb_dragon | **1,075,376** | 5,612,096 | 0.19 | 624,576 * | 5,612,096 |
| 04263 | **73,560** | 148,852 | 0.49 | 38,449 | 148,862 |
| 04362 | **8,080** | 19,226 | 0.42 | 6,906 | 18,804 |
| 11375 | **688** | 1,069 | 0.64 | 501 | 567 |
| minotaur | **12,240** | 22,741 | 0.54 | 8,823 | 11,787 |
| samurai | **19,312** | 36,377 | 0.53 | 13,705 | 18,438 |
| werewolf | **10,896** | 16,973 | 0.64 | 7,544 | 9,866 |

### Reading a whole mesh (microseconds)

"Integers" gets stored values into buffers, which is what meshopt's decode produces. The four columns after meshopt split
that time by stream, each timed alone. **Native meshopt is the true test: ZPM is faster on 9 of 24**, and slower on
Avocado 1.3x, BoomBox 1.7x, CesiumMan 1.7x, DamagedHelmet 1.3x, Duck 1.4x, Lantern 2.2x, RiggedFigure 1.2x, suzanne 1.4x, WaterBottle 1.3x, 04263 2.4x, 04362 1.7x, 11375 1.7x, minotaur 1.8x, samurai 2.9x, werewolf 2.3x. It beats meshopt's WebAssembly on 19 of 24. "Floats" also turns normals and UVs into floats, per
corner, and beats Draco's decode on 24 of 24.

**Not fully like for like:** ZPM is C# on .NET 10. Meshopt native is C++ built with `zig c++ -O3 -mavx2`, from current
source. Draco is native C++ called through Python. Timings on this shared handheld PC can vary by about 2x between runs,
for ZPM and for meshopt, so treat small differences as ties. ZPM needs no decode step at all to read part of a mesh; that
is not what this table shows.

| Mesh | ZPM, integers | Meshopt native | Meshopt WASM | Positions | Corners | Normals | UVs | ZPM, floats | Draco |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| armadillo | **639** | 1,624 | 2,402 | 77 | 564 | 0 | 0 | 2,069 | 61,605 |
| Avocado | **4** | 3 | 11 | 1 | 1 | 0 | 1 | 117 | 233 |
| BoomBox | **47** | 28 | 49 | 12 | 14 | 3 | 11 | 1,045 | 2,433 |
| CesiumMan | **40** | 24 | 57 | 14 | 10 | 5 | 10 | 872 | 1,904 |
| cow | **20** | 85 | 140 | 6 | 13 | 0 | 0 | 102 | 3,566 |
| DamagedHelmet | **127** | 100 | 169 | 54 | 34 | 14 | 25 | 2,682 | 8,486 |
| Duck | **25** | 18 | 33 | 6 | 9 | 2 | 7 | 687 | 1,552 |
| fandisk | **34** | 124 | 209 | 9 | 25 | 0 | 0 | 217 | 4,866 |
| happy | **712** | 6,344 | 7,836 | 115 | 790 | 0 | 0 | 2,468 | 178,126 |
| Lantern | **80** | 35 | 78 | 20 | 28 | 20 | 9 | 1,083 | 3,423 |
| lucy | **1,249** | 2,753 | 4,427 | 167 | 952 | 0 | 0 | 3,859 | 123,491 |
| RiggedFigure | **4** | 4 | 12 | 1 | 1 | 0 | 0 | 14 | 118 |
| spot | **49** | 153 | 206 | 8 | 13 | 0 | 31 | 471 | 5,516 |
| stanford-bunny | **479** | 1,912 | 2,248 | 94 | 438 | 0 | 0 | 2,113 | 63,212 |
| suzanne | **6** | 4 | 15 | 1 | 4 | 0 | 0 | 37 | 484 |
| teapot | **41** | 83 | 156 | 17 | 26 | 0 | 0 | 143 | 4,394 |
| WaterBottle | **42** | 32 | 43 | 10 | 18 | 2 | 9 | 1,277 | 4,291 |
| xyzrgb_dragon | **2,803** | 17,478 | 20,955 | 328 | 2,291 | 0 | 0 | 8,010 | 303,222 |
| 04263 | **319** | 133 | 241 | 122 | 40 | 229 | 0 | 1,426 | 9,496 |
| 04362 | **29** | 17 | 30 | 34 | 10 | 10 | 9 | 127 | 845 |
| 11375 | **1** | 1 | 11 | 1 | 0 | 1 | 0 | 7 | 50 |
| minotaur | **42** | 23 | 33 | 17 | 5 | 28 | 0 | 269 | 1,253 |
| samurai | **72** | 25 | 53 | 15 | 10 | 33 | 0 | 212 | 1,168 |
| werewolf | **52** | 23 | 40 | 11 | 6 | 19 | 10 | 364 | 671 |

The sections below are from the older run of 2026-10-31.

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

Needs Python packages `DracoPy`, `zstandard` and `numpy`, the npm package `meshoptimizer`, and a built reader. For native
timing, build `meshopt_native.cpp` as its first lines say, and the packed decoder:

```sh
python -m ziglang c++ -O3 -std=c++17 -mavx2 benchmarks/format-compare/packed_decode.cpp -o packed_decode.exe
```

```sh
python benchmarks/format-compare/compare.py --fixtures FOLDER --meshopt node_modules/meshoptimizer --meshopt-native meshopt_native.exe --packed-native packed_decode.exe
```
