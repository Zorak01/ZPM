# How ZPM gets small and fast

Every technique ZPM uses, what problem it solves, and what it costs. Update this with every change.
Numbers are from [run 20261031T063431Z](../benchmarks/mesh-read/README.md), using `samurai`, a real
character with 3,682 vertices, unless noted.

## The big idea

Most compressed formats are like a zip: to read one face you unpack everything first. ZPM has no
unpack step. The file is laid out so any value can be computed from where it sits, using a few reads
and some arithmetic. Opening a file reads its directory, and reading never allocates memory.

| Samurai | Bytes | First face readable after | Full read |
| --- | ---: | ---: | ---: |
| Plain arrays with 16-bit IDs (Compact16) | 77,820 | 1.95 µs, copying the file | 1.95 µs |
| Smallest earlier experiment | 56,903 | 586.70 µs, decoding everything | 586.70 µs |
| **ZPM** | **14,928** | **0.18 µs** | 92.66 µs |

ZPM is 5.21x smaller than Compact16 and 3.81x smaller than the earlier experiment. The price: a plain
array still wins a full read, and one vertex costs about 32 ns against about 1 ns.

## Storing numbers

### Blocks of 64 with a base and a small width

**Problem:** IDs near each other waste most of their bits. 3,501, 3,498 and 3,510 all need 12 bits,
yet they differ by only a little.

**Technique:** Split a list into blocks of 64. Store each block's lowest value once, then each value's
difference from it, using only the bits the largest difference needs. Every value in a block has the
same width, so value J sits at bit `J × width`, with no need to walk through earlier values.

**Prior art:** frame-of-reference coding, common in databases and search engines, less so in mesh files.

### A slope for values that climb

**Technique:** Values that rise steadily are stored as `base + slope × J + small difference`. On a
perfect grid the differences vanish and the block's data takes zero bytes.

**Prior art:** research on learned or linear compression in databases.

### Finding a block with one multiply

**Problem:** Blocks have different widths, so where does block N start?

**Technique:** A full block holds 64 values, so it is exactly `width × 8` bytes. Offsets are stored
only every 8 blocks. To add up the widths between, read those 8 width bytes as one 64-bit number and
multiply by `0x0101010101010101`: the top byte of the result is their sum. No loop.

### Eight values at a time

**Technique:** Eight values always span exactly `width` bytes, so reading a whole block uses the same
AVX2 shuffle pattern throughout. Machines without AVX2 run a plain loop with the same result.

## Counting without scanning

### Rank masks

**Technique:** One bit per element, with a running count stored every 64 elements. "How many marked
elements come before this one?" is a stored count plus one popcount instruction. This is what lets
ZPM skip elements yet still find the right slot instantly.

**Prior art:** rank structures from succinct data structures research.

### Select bits

**Technique:** The opposite question: "where is the Nth marked element?" Store the position of every
64th mark, then jump there and scan a few words. Finding a mixed-size face costs about 20 ns, where
walking all earlier faces in the old layout cost over 2,000 ns.

## Positions

### Repeated positions stored once

**Problem:** UV seams split vertices, so real models repeat positions constantly. Samurai has 3,682
vertices but only 1,423 unique positions.

**Technique:** A rank mask marks repeats. A marked vertex stores which unique position it uses. An
unmarked vertex's unique position is simply its rank, costing no bits at all.

### Per-axis palettes

**Problem:** Exact floats look random, yet modelled objects reuse the same coordinates.

**Technique:** Each axis gets a sorted list of its distinct values, and vertices store small indices.
The sorted list compresses well too, because neighbours are close.

### Mirrored values share an entry

**Technique:** Palettes store sizes without signs, and the sign goes in the index's lowest bit. On a
symmetric model, +x and −x cost one entry.

### Float bits in numeric order

**Problem:** In raw float bits, −1.0 and +1.0 are nowhere near each other.

**Technique:** A reversible bit trick puts all floats in numeric order, so close values have close
bits and small differences. It works for every bit pattern.

### Snapping to a tiny grid

**Technique:** Positions snap to a grid of at most 1 / 2^20 of the model's size. The largest move is
0.00005% of its size: about 1 micron on a 2 m character. Identical positions stay identical, so shared
points cannot crack apart. The step is a power of two, so snapped values are exact floats and saving
again changes nothing. Going to a coarser 16-bit grid saved under 1% more, so it was not worth it.

## Topology

### Walking faces across shared edges

**Technique:** The encoder sorts faces along a space-filling curve (Morton order), then walks across
shared edges, widest first, so neighbours land close together in the file. Vertices are renumbered in
the order faces reach them. It also tries the original order and keeps the smaller file.

**Prior art:** vertex and face reordering is common in mesh optimizers.

### Corners that say how far back they reach

**Problem:** Corners were the biggest part of the file, and only about half of them use a new vertex.

**Technique:** With vertices numbered by first use, a corner either uses the next new vertex, which
costs no bits, or an earlier one. Earlier ones store their distance back from the newest vertex, and
after the walk those are tiny: 1.7 on average on samurai.

**The unusual part:** sequential mesh codecs use this "next new vertex" idea, but they need a running
count, so they must decode from the start. In ZPM the newest vertex at any corner is its rank in the
mask, so any corner reads directly. **Result:** samurai's corners went from 9,464 to 2,539 bytes
(3.7x), with a corner still costing about 3 ns.

### Edges that cost one bit per corner

**Problem:** A list of edges repeats what the faces already say.

**Technique:** Edges are stored in the order faces first reach them, so edge N is the Nth marked
corner. Its two vertices are that corner and the next one around its face. **Result:** samurai's edges
went from 20,456 bytes as 16-bit pairs to 1,144 bytes (17.9x). Edges on no face are stored separately.

## The file itself

### Labelled sections

A short header, then a directory naming each section. There are no mode fields: if a section is
present, that's the layout. `python tools/zpm_map.py FILE` names every byte in a file, and a test proves
each byte is named exactly once.

### One format

Nothing to choose on export. The encoder tries each legal form of every stream and keeps the smallest.

### Safe to read bad files

Opening trusts nothing it hasn't checked, and validation proves every read stays inside the file and
every ID exists. Block descriptors prove most of it without reading the data. Each benchmark run also
reads thousands of deliberately corrupted files: one early run caught a real crash.

## Tried and rejected

| Idea | Result | Why not |
| --- | --- | --- |
| Patching outlier values inside a block | Saved 1.3% | Slower path on every read |
| Palette per block | Saved 0.2% | Second lookup on every read |
| Next-new-vertex coding on Blender's own order | No gain | Needs first-use numbering, which came later and then won big |
| Walking faces newest first | No gain over widest first | Often worse on real meshes |
| Reordering with the space-filling curve alone | 1.10x to 1.51x, 1.36x larger on one mesh | The walk beats it clearly |
| 16-bit position grid | Under 1% over 20-bit | 16x larger error for almost nothing |

## What we can and can't claim

- **Can say:** on these 17 meshes, smaller than every earlier ZPM experiment on all but tiny meshes,
  with zero allocations when reading.
- **Can't say yet:** smaller or faster than GLB, meshopt or Draco. That comparison hasn't been run, and
  sequential codecs may well be smaller, because they are allowed to decode first.
- **Not new inventions:** the building blocks above are known. What's unusual is combining them in a
  mesh file so nothing needs decoding. We have not surveyed every format, so say "unusual", not "first".
- **Tiny meshes get bigger:** a mesh of a few vertices costs more, because of the directory and tail.
