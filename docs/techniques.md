# How ZPM gets small and fast

Every technique ZPM uses, what problem it solves, and what it costs. Update this with every change.
Numbers are from [the latest run](../benchmarks/mesh-read/README.md), using `samurai`, a real
character with 3,682 vertices, unless noted.

## The big idea

Most compressed formats are like a zip: to read one face you unpack everything first. ZPM has no
unpack step. The file is laid out so any value can be computed from where it sits, using a few reads
and some arithmetic. Opening a file reads its directory, and reading never allocates memory.

| Samurai | Bytes | First face readable after | Full read |
| --- | ---: | ---: | ---: |
| Plain arrays with 16-bit IDs (Compact16) | 77,820 | 1.34 µs, copying the file | 1.34 µs |
| Smallest earlier experiment | 56,903 | 298.66 µs, decoding everything | 298.66 µs |
| **ZPM** | **14,236** | **0.13 µs** | 49.52 µs |

These compare geometry only, because the older layouts hold nothing else. With normals, samurai is
24,916 bytes against 156,516 for plain arrays (6.28x).

ZPM is 5.47x smaller than Compact16 and 4.00x smaller than the earlier experiment. The price: a plain
array still wins a full read, and one vertex costs about 23 ns against about 1 ns.

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

### Positions as grid integers

**Problem:** Stored as float bits, -0.001 and +0.001 are about 2 billion apart, so any block crossing
zero wastes nearly all its bits. Models are usually centered on zero.

**Technique:** Once snapped, every coordinate is a whole number of grid steps, so store that number.
Values near each other are numerically near. Reading is one multiply by a power of two, which is exact.
Up to 1.44x smaller on positions, and up to 1.23x on whole files. Meshes without a grid keep float bits.

### Repeated positions stored once

**Problem:** UV seams split vertices, so real models repeat positions constantly. Samurai has 3,682
vertices but only 1,423 unique positions.

**Technique:** A rank mask marks repeats. A marked vertex stores how far back its position is. An
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

## Surface data

### Normals in 12 bits per direction part

**Technique:** Three floats cost 12 bytes. Folding the sphere onto a square (octahedral mapping) needs two
numbers, and at 12 bits each the worst error measured was about 0.056°. Going to 14 bits barely
improved it, because Blender's own values carry about 0.03° of error, so it costs size for nothing.

**Prior art:** octahedral normals are common in engines and formats.

### Flat normals are not stored at all

**Problem:** On flat-shaded meshes, like scans and hard surfaces, every corner's normal equals its face's.
Other formats store them anyway.

**Technique:** If normals were not authored and each is within 0.1° of its face's, the file stores none.
A reader calculates a normal from the face's corners when asked, using no extra memory, at the cost
of reading those corners.

### Two ways to store per-corner data

**Problem:** A normal or UV exists per corner, which is far more than per vertex.

**Technique:** The encoder tries both forms and keeps the smaller:
- **Each distinct value once**, with corners reaching back using the same rank trick as corners. Wins
  on flat shading: 2.8x smaller than per-corner storage on a flat-shaded test mesh.
- **One value per vertex**, with a mask for corners that disagree. Wins on smooth surfaces: 3.1x
  smaller than per-corner on a smooth test mesh, where only 36 of 31,488 corners disagreed.

Either way any corner still reads directly.

### UVs on a tiny grid

**Technique:** UVs become whole numbers of 1 / 2^20 steps: about 1/256 of a texel on a 4K texture.
Reading is one divide. Layers keep their names, and tiles beyond 0-1 work.

### Flags and creases cost almost nothing when unused

**Technique:** Sharp, seam and flat flags are ordinary columns, so an all-zero block stores no data at all,
and a flag that is never set has no section. Creases are 16 bits.

### Colors that stay exact, and float colors on a grid

**Technique:** Color layers can be per vertex or per corner, as Blender holds them. An RGBA8 color packs into
one pair of 16-bit values, so it uses the same stream as normals and UVs and stays exact. Float colors keep
values above 1, on a grid of 1 / 2^16, and take two streams.

### Tangents with their sign in a spare bit

**Technique:** A tangent is a 12-bit direction like a normal, and the sign needs one bit, so it sits just
above the second value. Each UV layer has its own tangents, using that layer's number. Blender can't calculate them for n-gons, so
those meshes store none. This adds real size for now: about 20% on `DamagedHelmet`.

### Vertex groups and weights with no padding

**Problem:** Most formats give every vertex a fixed four or eight weight slots, whether it needs them or not.

**Technique:** Any number of weights per vertex, nothing padded. A column of offsets says where each vertex's
weights start, and because offsets climb steadily, they compress well. A vertex's weights are one lookup
away.

### Vertices with equal weights share a row

**Technique:** Rigged meshes repeat the same weights across many vertices. Each distinct set is stored once, with
one more column saying which row a vertex uses, whenever that is smaller. A read costs one extra lookup.

**Numbers:** `CesiumMan` 21,916 to 19,232 bytes (1.14x), with 1,949 distinct sets across 3,273 vertices.
`RiggedFigure` 2,964 to 1,476 bytes (2.01x), with 124 across 370. Weights are 16-bit, so only exact matches share.
They remain a large part of rigged files.

### Material slots, with each material's faces kept together

**Problem:** Renderers draw one material at a time, and formats often split a mesh per material, which
duplicates vertices along the borders.

**Technique:** One slot number per face. The face walk stays inside one material and starts follow
slot order, so when reordering wins, each material is one unbroken run of faces. A block holding one slot stores
no data, so the column shrinks, and a renderer gets draw ranges without sorting. A file where every face uses
slot 0 stores no column.

**Cost:** on 4 real meshes with 4 test materials split by position, the file grew 0.6% to 9.3% over
no materials; the 9.3% is a 104-vertex mesh. Where the reordered file won, the column was 2.3x to 2.7x
smaller than with a walk ignoring materials. These are test materials, not authored ones.

### Holes as a short list of ring starts

**Technique:** A face's corners are its outer ring, then each hole ring. One rising column names the corner
each hole starts at, so a mesh without holes pays nothing. Finding a face's rings is a binary search. Edge
references and calculated face normals both assume one ring per face, so a mesh with holes stores endpoints and
normals instead. Blender has no such faces, so importing one there is refused rather than changed.

### Marks only some tools author

**Technique:** Sharp corners, smoothing groups and crease chains are ordinary columns, absent when unused.
Blender has no home for them, so they stay in `zpm_sharp_corner`, `zpm_smoothing_group`, `zpm_seam:LAYER`
attributes and `zpm_crease_chain_*` properties. Smoothing groups are per corner, which also covers per-face groups.

### Seams per UV layer, stored only when different

**Technique:** The mesh keeps one seam column. A UV layer gets its own only if it differs, so the usual case,
Blender's single set of seams, costs nothing extra.

### Color space in a spare bit

**Technique:** A layer's format word gains an sRGB flag, costing no bytes. Blender's byte colors are sRGB and its
float colors linear, and each is restored the same way.

### Blendshapes that store only what moved, exactly

**Problem:** glTF stores every vertex of every target unless told otherwise, and a face rig has dozens of targets
that each move a small patch. Adding a float offset back to a float position can also drift by a bit.

**Technique:** All targets share one list of rising vertex IDs, with one start per target. A vertex missing from a
target's run did not move, and a binary search finds it with no memory. Rising runs fit the column's slope, so
a target moving every vertex costs almost nothing in IDs, with no sparse or dense mode to choose. Offsets are whole
steps on the mesh's own position grid, so adding one back is exact, and targets share the same tiny move limit as
positions. Absolute and relative targets store alike. Meshes with no grid store each target's float bits.

**Numbers:** 52 made-up smooth bumps of 8% of the model's size, moving vertices by up to 3% of it, not real
face-rig data: `minotaur` 34,489 bytes, 2.1x smaller than sparse ID + three floats and 39.6x smaller than dense
floats. `04263` 388,620 bytes, 2.6x and 26.8x. Most cost is offsets, about 15 bits each per axis on `04263`.

Targets moving exactly the same vertices share one list, through one more column present only when that happens.

**Prior art:** sparse targets are common, and so are quantized ones. Exact grid-step offsets sharing the mesh's
grid, readable without unpacking, we have not seen, though we have not surveyed every format.

### Custom attributes, exact, on any domain

**Technique:** One column per attribute, all of component 0 then all of component 1, so like values sit together
and any value is one multiply away. Floats use their ordered bits, so every pattern survives and close values take
few bits. Any domain, 8 to 32-bit types, up to 255 components, and a declared meaning like vector or color.

**Numbers:** a smooth Float32 attribute took 52,784 of 66,776 raw bytes on `04263` (1.27x), and 8,560 of 8,760
on `minotaur` (1.02x). Ordered bits barely help noisy low digits, so this is a clear place to improve.

## Scenes

### Meshes kept whole inside the scene file

**Problem:** A file holding many meshes usually needs an owner on every section, or a decode step per mesh.

**Technique:** Each mesh is a complete mesh file inside one section. Opening a mesh is its own directory read, with
the same reader, no copy and no allocation, so an engine can open only the meshes it needs. The cost per mesh is a
directory entry per section and the 32-byte tail.

### Tables read in place

**Technique:** Objects, materials, lights and bones are arrays of fixed records whose layout matches a C# struct, so
reading one is a pointer add, with no parsing. Validation walks them once to prove each link is in range and no
parent chain loops.

**Cost:** records store every field, so a default material costs its whole 64 bytes. This is tiny beside a mesh but
not the smallest it could be: a table of 100,000 objects takes 14.8 MB uncompressed.

### Hierarchies with no working memory

**Problem:** To place a scene, a reader normally builds child lists and sorts parents before children, allocating as
it goes, every time it loads.

**Technique:** The file stores both: an order with parents first, and each item's children. Placing everything is one
pass with a single output buffer and no recursion. Files with no parents store neither.

**Cost:** 4 bytes per object for the order, and 8 for child lists.

### Rest, bind and pose matrices worked out on demand

**Problem:** Other formats store a matrix per bone that head, tail and roll already imply, and they can disagree.

**Technique:** Only head, tail and roll are stored, and the reader calculates the rest of it: rest, inverse bind, pose
and skinning matrices, and every object's world matrix, without allocating.

**Numbers:** `benchmarks/quality/rig_math.py` compares 12 matrices with what Blender calculates: Euler, quaternion and
axis-angle rotations, roll, a bone pointing straight down, connected bones, nonuniform scale, and bone parents.
Largest difference: 0.0000018.

**Limits:** constraints, drivers and modifiers aren't run, nor object delta transforms. Vertex parents need mesh data,
so `GetWorldMatrices` says when it could not place one exactly. Partial bone inheritance is treated as full.

### Objects share data

**Technique:** Objects using one mesh point at it, each with its own placement, so 1,000 copies cost 1,000 records
and one mesh. A mesh is only shared when its vertex groups match too, because groups live with the mesh.

### Images stored once, untouched

**Technique:** Embedded images keep their original encoded bytes, so nothing is lost and a GPU loader can read them
straight from the file. Identical files, compared whole, are stored once. By default images are links, and **Embed
Textures** is the one setting on export.

### Materials any application can read

**Problem:** A format shaped like one application's material system is useless to the others.

**Technique:** A material has the fields renderers share, plus a model name, named parameters, and texture slots
whose role is a name, not a fixed list. A reader uses what it understands and can skip the rest. Blender's other
Principled inputs go in as parameters, stored only when they differ from the default. A node graph those fields
can't describe also goes into an optional extension, so Blender can rebuild it exactly while others ignore it.

### One value tree for everything open-ended

**Problem:** Custom properties, modifiers, constraints, drivers and application settings have no fixed shape, and a
table for each would mean endless layouts.

**Technique:** One table of fixed 16-byte nodes, with shared strings and one array column. Children always sit later
in the table, so a corrupt file can't make a reader loop. Any resource can carry a tree, found by binary search,
read in place with no allocation. Links to objects are typed, so validation proves them.

**Cost and limit:** a key costs at least 16 bytes, and string keys are slower than a typed field, so this is for
data read rarely, not per frame. Modifiers, constraints and drivers are kept for their source application: ZPM does
not promise other readers can evaluate them.

### Animation that addresses anything

**Technique:** A track names an owner, a field path and an array index, so any stored field can animate, including
ones that don't exist yet. Tracks with the same key times share one list, values sit in one column, and every key
reads by index. A held value takes one key. Reference keys are checked against what they name when the file is
validated, including resources that vary over time.

**Numbers:** made-up smooth rotation curves on 50 objects, 200 tracks of 250 keys: 187,108 bytes against 400,000 for plain time and value floats (2.14x smaller). Key times are stored once, in 796 bytes. Values take 28.1 bits each, because exact float bits keep their noisy low digits.

Keys on a straight line between their neighbours are dropped on export, when a track is all linear. No value moves
more than 1 / 100,000 of the track's own range.

**Can improve:** exact bits are expensive. A tiny quantization grid like positions use would shrink values a lot.

### Curves and lattices in exact columns

**Technique:** Control points sit in one column per field, shared across splines, so like values compress and any
point reads by index. Handles exist only if some spline uses them.

### Optional sections and required extensions

**Technique:** Section kinds from `0x8000` up can be skipped by readers that don't know them, so files can grow
without breaking old readers. An extension can say it is required, and then a reader lacking it refuses the file
with its name, rather than showing something wrong.

### Identical meshes stored once

**Technique:** Meshes of equal size are compared by their stored bytes, and objects then share one. Animated meshes
keep their own copy. This costs an extra encode only when two meshes could match.

### Units and axes only when they differ

**Technique:** 8 bytes, absent for 1 m with +Z up and -Y forward. Blender places imported root objects to match, so
a file in other units or axes looks right without changing its vertices.

## The file itself

### Labelled sections

A short header, then a directory naming each section, and its layer for things like UV sets. There are no mode fields: if a section is
present, that's the layout. `python tools/zpm_map.py FILE` names every byte in a file, and a test proves
each byte is named exactly once.

### One format

Nothing to choose on export. The encoder tries each legal form of every stream and keeps the smallest.

### Safe to read bad files

Opening trusts nothing it hasn't checked, and validation proves every read stays inside the file and
every ID exists. Block descriptors prove most of it without reading the data. Each benchmark run also
reads thousands of deliberately corrupted files: one early run caught a real crash.

### What a rendered test shows about 12-bit normals

`benchmarks/quality/shading_test.py` renders a near-mirror mesh reflecting a sharp checker pattern, the hardest case
we could set up, with exact and stored normals. Identical normals differ by exactly 0, so the render is repeatable.

| Normals compared with exact | Mean difference | Largest pixel difference |
| --- | ---: | ---: |
| Stored at 12 bits | 0.13% | 12.8% |
| Turned by 1° (the control) | 1.33% | 45.4% |

**What this means:** on a mirror-like surface, some pixels at sharp reflection edges do change, so 12 bits is not
invisible in this worst case. Blender stores custom normals coarsely itself, about 0.6° earlier, and that is in both
renders, so this may overstate ZPM's part. A less extreme glossy surface and direct evaluation still need testing.

## Tried and rejected

| Idea | Result | Why not |
| --- | --- | --- |
| Patching outlier values inside a block | Saved 1.3% | Slower path on every read |
| Palette per block | Saved 0.2% | Second lookup on every read |
| Next-new-vertex coding on Blender's own order | No gain | Needs first-use numbering, which came later and then won big |
| Walking faces newest first | No gain over widest first | Often worse on real meshes |
| Reordering with the space-filling curve alone | 1.10x to 1.51x, 1.36x larger on one mesh | The walk beats it clearly |
| 16-bit position grid | Under 1% over 20-bit | 16x larger error for almost nothing |
| 14-bit normals | No real accuracy gain over 12-bit | Blender's values already carry 0.03° error |
| Storing every normal per corner | 3x larger on smooth meshes | One value per vertex is far smaller |
| Restarting neighbour prediction every 64 or 256 faces | Positions up to 3x larger | Restarts throw away too much |
| Tighter face walks: buckets, depth first, greedy | Best on bunny only, 1.1x | Today's walk wins on 5 of 6 meshes |
| Outlier patches on corner distances | Under 3% | Distances are large everywhere, not just a few |
| Merging exact duplicate vertices | 1.31x on samurai | Vertices must stay as Blender has them |

## What we can and can't claim

- **Can say:** on these 17 meshes, smaller than every earlier ZPM experiment on all but tiny meshes,
  with zero allocations when reading.
- **Can't say:** smaller than Draco or meshopt, or faster at reading a whole mesh. It has been measured
  and ZPM currently loses: see [the comparison](../benchmarks/format-compare/README.md). It wins on reading
  part of a mesh and on memory.
- **Not new inventions:** the building blocks above are known. What's unusual is combining them in a
  mesh file so nothing needs decoding. We have not surveyed every format, so say "unusual", not "first".
- **Tiny meshes get bigger:** a mesh of a few vertices costs more, because of the directory and tail.
