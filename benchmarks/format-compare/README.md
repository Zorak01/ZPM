# ZPM against Draco and meshoptimizer

## Latest run: 2026-11-02

24 meshes, run `20261102T213753Z`. [Raw JSON](results/20261102T213753Z.json). 20-bit positions, 12-bit normals, 16-bit UVs. Edges are left
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
that time by stream, each timed alone. **Native meshopt is the true test: ZPM is faster on 11 of 24**, and slower on
Avocado 1.3x, BoomBox 1.5x, CesiumMan 1.7x, DamagedHelmet 1.2x, Duck 1.4x, Lantern 1.7x, RiggedFigure 1.1x, WaterBottle 1.3x, 04263 2.0x, 04362 3.2x, minotaur 1.7x, samurai 2.6x, werewolf 2.4x. It beats meshopt's WebAssembly on 20 of 24. "Floats" also turns normals and UVs into floats, per
corner, and beats Draco's decode on 24 of 24.

**Not fully like for like:** ZPM is C# on .NET 10. Meshopt native is C++ built with `zig c++ -O3 -mavx2`, from current
source. Draco is native C++ called through Python. Timings on this shared handheld PC can vary by about 2x between runs,
for ZPM and for meshopt, so treat small differences as ties. ZPM needs no decode step at all to read part of a mesh; that
is not what this table shows.

| Mesh | ZPM, integers | Meshopt native | Meshopt WASM | Positions | Corners | Normals | UVs | ZPM, floats | Draco |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| armadillo | **634** | 1,388 | 2,367 | 71 | 546 | 0 | 0 | 2,047 | 60,916 |
| Avocado | **4** | 3 | 10 | 1 | 1 | 0 | 1 | 104 | 231 |
| BoomBox | **41** | 26 | 45 | 13 | 13 | 3 | 10 | 978 | 2,611 |
| CesiumMan | **39** | 24 | 40 | 13 | 10 | 3 | 10 | 750 | 1,719 |
| cow | **19** | 77 | 122 | 6 | 12 | 0 | 0 | 102 | 3,136 |
| DamagedHelmet | **125** | 101 | 166 | 55 | 30 | 12 | 24 | 2,573 | 8,176 |
| Duck | **25** | 18 | 36 | 6 | 9 | 2 | 7 | 680 | 1,553 |
| fandisk | **37** | 98 | 195 | 10 | 28 | 0 | 0 | 221 | 4,910 |
| happy | **624** | 1,476 | 2,444 | 75 | 558 | 0 | 0 | 2,042 | 61,156 |
| Lantern | **31** | 18 | 31 | 9 | 7 | 9 | 5 | 594 | 1,408 |
| lucy | **645** | 1,814 | 2,744 | 74 | 556 | 0 | 0 | 2,066 | 58,417 |
| RiggedFigure | **5** | 4 | 7 | 1 | 1 | 0 | 0 | 14 | 156 |
| spot | **41** | 124 | 189 | 7 | 12 | 0 | 24 | 281 | 4,031 |
| stanford-bunny | **357** | 1,001 | 1,604 | 50 | 301 | 0 | 0 | 1,348 | 37,614 |
| suzanne | **5** | 7 | 11 | 1 | 3 | 0 | 0 | 29 | 238 |
| teapot | **28** | 66 | 105 | 15 | 12 | 0 | 0 | 118 | 2,733 |
| WaterBottle | **25** | 20 | 38 | 6 | 9 | 2 | 8 | 763 | 1,535 |
| xyzrgb_dragon | **1,583** | 4,241 | 6,342 | 193 | 1,411 | 0 | 0 | 5,291 | 203,068 |
| 04263 | **146** | 75 | 125 | 53 | 19 | 67 | 0 | 656 | 3,942 |
| 04362 | **28** | 9 | 21 | 5 | 2 | 6 | 5 | 78 | 315 |
| 11375 | **1** | 1 | 4 | 0 | 0 | 0 | 0 | 4 | 30 |
| minotaur | **25** | 14 | 25 | 7 | 4 | 14 | 0 | 94 | 504 |
| samurai | **45** | 18 | 32 | 13 | 6 | 22 | 0 | 152 | 860 |
| werewolf | **22** | 10 | 18 | 5 | 3 | 10 | 4 | 129 | 526 |

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
timing, build `meshopt_native.cpp` as its first lines say.

```sh
python benchmarks/format-compare/compare.py --fixtures FOLDER --meshopt node_modules/meshoptimizer --meshopt-native meshopt_native.exe
```
