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

### Small block headers

**Problem:** A block needs a base, a width, and a way to find it. At four bytes for a base, one for a width and four per
eight blocks for an offset, that overhead was larger than the gap to meshopt on small meshes.

**Technique:** A column stores its lowest base once, then each block's rise above it in only the bits the highest rise
needs. Widths are four-bit rises above the lowest width: a block far narrower than the widest is simply stored wider,
which real data rarely needs. An offset is kept per sixteen blocks, in two bytes when the data is under 64 KiB, and the
first is not stored because it is always zero. Sixteen four-bit widths are one 64-bit word, so finding a block is one
load and a few bit operations, with no loop.

**Numbers:** `BoomBox` from 53,712 to about 51,800 bytes across this and the savings below. Reads are not slower.

### Few bytes for a directory

A directory entry takes eight bytes with a 32-bit offset. Only a scene past 4 GiB uses twelve, which its own header length
says, so no flag is needed. A rank mask's counts take two bytes each when it covers at most 65,535 elements.

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

### Patches for dense surfaces

**Problem:** A wide walk scatters 64 consecutive vertices across the model, so a block's positions need nearly all 20 bits per axis: 59 bits per vertex measured.

**Technique:** The encoder also tries growing patches of about 128 vertices along the space-filling curve, and keeps whichever order is smaller. `stanford-bunny` positions fell from 263,124 to 235,331 bytes. It is chosen on big scans and not on most game models.

### Corners that say how far back they reach

**Problem:** Corners were the biggest part of the file, and only about half of them use a new vertex.

**Technique:** With vertices numbered by first use, a corner either uses the next new vertex, which
costs no bits, or an earlier one. Earlier ones store their distance back from the newest vertex, and
after the walk those are tiny: 1.7 on average on samurai.

**The unusual part:** sequential mesh codecs use this "next new vertex" idea, but they need a running
count, so they must decode from the start. In ZPM the newest vertex at any corner is its rank in the
mask, so any corner reads directly. **Result:** samurai's corners went from 9,464 to 2,539 bytes
(3.7x), with a corner still costing about 3 ns.

### Face strips in small blocks: the one thing that is not in place

**Problem:** Draco's connectivity is about 8x smaller than ZPM's was. On `cow` Draco spent about 2,000 bytes on it
against 17,874. Coding every stream would have gained only 1.21x, so a block layer everywhere was not worth it.

**Technique:** Only face corners are coded, in blocks of 384 corners, for meshes whose faces all have up to 32 corners.
A face usually shares an edge with the one before it. Faces are turned so that edge comes first, so a face only says
which edge it continues from, usually one bit, and its new vertices: one bit for the next new one, or a short distance back. A fixed bit code needs no tables, and every other stream stays readable in place.

**Numbers:** `cow` corners 17,874 to 5,844 bytes (3.1x), `stanford-bunny` 277,649 to 77,608 (3.6x), about 8 to 9 bits
per triangle, with Draco near 2. Whole files fell about 1.32x overall, up to 1.62x. A whole mesh reads faster than before: `stanford-bunny` went from 19,372 to about 2,500 µs.

**Cost:** one random corner went from about 8 ns to 200 to 500 ns, because it decodes up to its place in a block, on
the stack, with no allocation. A random flat normal costs 1.2 to 2 µs. Use `GetMesh`, `GetCornerNormals` and
`ZpmCornerCursor` to read many. Encoding `stanford-bunny` takes about 7 to 13 s in Python. All-triangle meshes have a separate fast path for whole reads, because the general decoder was measurably slower.

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

**Technique:** If normals were not authored and each is within 0.1° of its face's, judged before snapping, the file stores none. Snapping can turn a tiny face far more than it moves it, so judging afterwards missed whole scans: `stanford-bunny` went from 903,860 to 545,716 bytes (1.66x) with this.
A reader calculates a normal from the face's corners when asked, using no extra memory, at the cost
of reading those corners.

### Two ways to store per-corner data

**Problem:** A normal or UV exists per corner, which is far more than per vertex.

**Technique:** The encoder tries both forms and keeps the smaller:
- **Each distinct value once**, with corners reaching back using the same rank trick as corners. Wins
  on flat shading: 2.8x smaller than per-corner storage on a flat-shaded test mesh.
- **One value per vertex**, with a mask for corners that disagree. Wins on smooth surfaces: 3.1x
  smaller than per-corner on a smooth test mesh, where only 36 of 31,488 corners disagreed.

- **One value per stored position**, for vertices split without differing, which tools often leave behind. Repeats of a
  position already say which earlier one they match, so a normal costs nothing more there. Corners that disagree go in the
  same mask. `minotaur` file 14,600 to 12,736 bytes, `samurai` 22,968 to 20,124. It adds one lookup on a random read and
  is not combined with UV prediction yet.

Any corner still reads directly in every form.

### UVs on a tiny grid

**Technique:** UVs become whole numbers of 1 / 2^16 steps: at most 1/32 of a texel on a 4K texture, and 1/16 on 8K. Going from 2^20 saved about 5% on textured models.
Reading is one divide. Layers keep their names, and tiles beyond 0-1 work.

### Flags and creases cost almost nothing when unused

**Technique:** Sharp, seam and flat flags are ordinary columns, so an all-zero block stores no data at all,
and a flag that is never set has no section. Creases are 16 bits.

### UVs predicted from position

**Problem:** UVs cost as much as positions on textured models, yet across a surface patch a UV is close to a flat
function of where the vertex is.

**Technique:** Each block of 64 vertices may store three whole numbers, and the stored value is only the difference from
`(x * cx + y * cy + z * cz) >> shift`, in grid steps, where each stream picks a shift of 10 or 16, because `BoomBox`
wants the shorter numbers and `Duck` needs the finer ones. The arithmetic is exact, so every reader agrees. The encoder fits each
block, and uses it only when it saves more than it costs. It costs up to six more column reads per random UV.

**Numbers:** `Duck` UVs 8,343 to about 3,700 bytes (2.3x), `WaterBottle` 1.45x, `CesiumMan` 1.35x. No effect on models
whose UVs jump around, such as `DamagedHelmet`, where it stays off. We have not seen this elsewhere, though we have not
surveyed every format. Tried on normals too, with the cheaper numbers as well: larger on six of seven models.

### Corners one step apart share a normal

**Problem:** Corners of one smooth vertex often land one step apart on the 12-bit grid from rounding alone, and each
one was stored as an exception.

**Technique:** Corners of a vertex within one step take the first corner's value, moving a normal at most about 0.05°.
**Numbers:** `BoomBox` normals 14,579 to about 11,000 bytes, and `DamagedHelmet`'s whole file from 193,568 to 184,796.

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

### Weights adding up to 1 lose their last value

**Problem:** Weights are the largest part of a rigged mesh, and normalized weights say the same thing twice.

**Technique:** When every vertex's weights already add up to 1, they go on a 12-bit grid with spare steps given by largest
remainder, so each row adds up exactly, sorted largest first. Each row then stores no last value. Any other mesh keeps its
weights in order on the 16-bit grid.

**Numbers:** `CesiumMan` weights 19,232 to 10,550 bytes (1.82x). `benchmarks/quality/weight_test.py` poses every bone at up to
69° and reports the worst move: 0.0044% of the mesh's size on `CesiumMan`, 0.0028% on `RiggedFigure`. A 16-bit grid is
1.3x larger, 10 bits gains 13% more at 4x the error, 8 bits 27% more at 16x.

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

### Strings on any domain

A string attribute stores each distinct string once in its own name table, and its column holds indices. A label repeated
across a mesh costs a few bits per element, and any element still reads directly.

### One map for layers that split alike

A UV layer in the shared form whose corners split exactly as layer 0's do stores no repeat map of its own, only a flag.
Lightmap and detail layers often follow the same seams.

## Scenes

### Text, surfaces and metaballs

They are saved as the mesh they make, so they look right anywhere.

### Point clouds and hair as meshes with no faces

**Problem:** Both are mostly positions and per-point attributes, which mesh columns already store well.

**Technique:** A point cloud is a mesh of loose points. Hair is a mesh of wires, one run of edges per curve. Any reader can
use them as they are, and every column technique applies. A property names what they were, so Blender rebuilds the real
thing. Curve-level attributes sit in a value tree, since a mesh has no such domain.

### Volumes

A volume keeps its settings on its object. Its grid file links by path, or is stored whole in an optional extension
when embedding is on. Its grids are not parsed. If the saved path is gone on import, the stored file is written out and used.

### Meshes that change shape over time

An object with a modifier that can change its topology is walked through the scene's frames at export. Each new vertex,
face or corner count stores a whole mesh, with a stepped track naming which one applies when. Blender cannot key a swap, so
its import ignores the track, and other readers use it. Export takes longer for such objects, and a same-count
change is not noticed.

### Sparse keys

A multi-component
track may be sparse: change flags sit before its values, and a component that did not change costs one flag. Reading such a
key counts flags, so read them in key order.

### Other image encodings

An image lists other encodings of itself, each with the quality rule that made it.
These live in value trees and point at ordinary images, so they need no new layout. ZPM ships no texture encoder, so nothing
in the Blender plugin writes image variants yet.

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

**Limits:** a vertex parent uses the mesh as stored, and a bone keeping its parent's shear is only exact without shear.

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

**Problem:** Custom properties and application settings have no fixed shape, and a
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

## The packed form

Saved files hold each mesh's geometry packed: decoded once, start to end, then read like the direct form. On 24 meshes,
at Draco's own 20-bit precision, it is 0.991x Draco's size and smaller on 21 of 24, 7.81x smaller than meshopt,
and decodes 2.3x to 15.6x faster than Draco. [The comparison](../benchmarks/format-compare/README.md) has every mesh.

### Static-table rANS, with raw low bits

Every stream is rANS with one fixed table of symbol frequencies per context, stored in the stream. A number is
coded as its bit length; only that costs a table lookup, and the bits below its top bit are copied raw. 10-bit
tables made streams no larger than 12-bit and decode faster, since a table fits in cache. Tables are bit-packed:
symbol gaps and frequencies as Exp-Golomb codes, the last frequency implied, and stream lengths as varints. That cut
small files by up to a quarter: `11375` went from 648 to 476 bytes.

### Faces walked across open edges

The walk keeps only open edges: directed edges with a face on one side so far. Each popped edge costs one bit-sized
symbol saying whether a face lies across it, and that face starts with the edge's two positions known. Its other
corners are almost always a new position, or one closing an open edge into its last or out of its first corner.
Faces of one material stay together. Connectivity then costs 1.2 to 2.5 bits per triangle on the meshes checked.
The encoder writes the walk under three context models and keeps the smallest: the symbol before; that plus the one
before it and which candidates exist, which saved 20% of connectivity on `DamagedHelmet`; or face counts, below.

### Corners named by steps along the boundary

A corner that no candidate names is still almost always on the open boundary, two or three edges along from the
face's last corner or back from its first. Naming it by that count instead of its distance from the newest position
cut connectivity on the big scans by 11% (`stanford-bunny`) to 22% (`lucy`), level with Draco's on most of them.

### Face counts, as valence coders use

The third model stores each new position's count of faces, keyed on the count before. A reader then knows how many
faces each position still needs, and keys every corner and gate on that: once a position needs one more face, that
face nearly always closes its fan. This is what valence-driven coders like Draco's rely on. On regular meshes it pays,
`stanford-bunny` connectivity 8.2 to 6.3 KB; on irregular scans the counts cost more than they save, so the encoder
keeps whichever model is smaller.

### Counts as symbols, not numbers

Steps, face sizes and face counts are never negative, so they are stored as symbols, not as signed numbers with raw
low bits. That alone saved a raw bit on most of them, and took `lucy` and `xyzrgb_dragon` past Draco.

### Splits as a choice per corner

The walk runs over welded positions, so a corner only says which vertex at its position it uses. Vertices stay
exactly as Blender has them. A second model guesses the vertex across the face's gate first, which a corner reached
through a gate nearly always uses; the encoder keeps whichever model is smaller.

### Draco's grid for positions

The direct form's grid step is a power of two, so it can be up to twice as fine as the 1 / 2^21 bound needs, which
costs up to 3 bits a position. Packed positions use the largest size over 2^20, as Draco does, rounded down to 6
significant bits so saving again finds the same step. That made the big scans 4% to 5% smaller, and makes the
comparison with Draco like for like.

### Each stream picks its own number form and contexts

Positions, normals and UVs are each written with plain or fine numbers, fine putting a number's second bit in its
symbol too, and kept in whichever is smaller. Positions also try contexts keyed on the earlier axes of the same
position: a large residual on one axis predicts large ones on the others. That made `stanford-bunny` 2.5% smaller and
`teapot` 9%, and is skipped where it does not pay.

### Predictions from what is already decoded, in one pass

Positions use parallelograms across the gate a face came through. Normals use the summed face normals of the
decoded geometry, around each welded position on smooth meshes or each vertex on meshes with hard edges; the
encoder writes both and keeps the smaller, which made the whole `04263` file 2.5x smaller. UVs use the triangle's shape in space.
Each stream reads corners in walk order with no tables of neighbours, which is what makes it fast: the first
decoder built neighbour tables and was only about as fast as Draco.

### Same integers in every reader

All prediction uses IEEE doubles, integer cross products and rounding halves to even, so C++ and Python agree to the
bit, proved by checksum on every mesh.

### What it costs

A reader must decode before reading anything, so packed files lose the direct form's reads in place. Saving takes
up to 17 s for one mesh in Python. The C# reader reads only the direct form.

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
| Corners reaching back to an earlier corner, not a vertex | 10% to 30% larger on most of 6 meshes, equal at best | Far references still set each block's width |
| Depth-first walk | Positions up to 1.1x smaller, corners larger | Only 4% on `stanford-bunny`, worse on others |
| Plane fit per block for positions | Under 1.1% | Blocks span too much of the model |
| Deltas inside a block for positions | 1% to 3% larger | Same reason |
| Entropy coding every stream in blocks | 1.21x, still 2.05x larger than Draco, simulated | Slows every read for little; only connectivity earned it |
| Predicting normals from their face's normal | 7% smaller at best, larger on others | Smooth normals are too far from the face |
| Choosing face order by whole file size | Only `samurai` changed, by 1.4%, at three times the encode time | The estimate already picks the best of the three orders |
| More face order candidates: other patch sizes and start orders | Best was 0.6% on `suzanne`, larger on the rest | The strip order is already best |
| A cache of recent vertices for strips | 0 to 6%, about the same as a better distance code, and adds state | Most old vertices are not recent |
| Linking a face to one before the last | Under 0.3% | Faces with no link rarely share an edge with a recent face |
| Coding a no-link face's vertices relative to its first | 7% larger on those faces | Distances from the newest are already short |
| Strip blocks of 768 corners | About 0.35%, no mesh changes sides | Random corner and edge reads 2x slower |
| Whole-mesh adaptive binary coding, simulated | 1.2x to 1.3x larger than Draco | Its connectivity and UV prediction were weak; the packed form replaced both |
| Packed normals corrected from decoded neighbours | Larger on every mesh tried | The geometry prediction already holds what neighbours add |
| Packed decoding with tables of neighbours | About Draco's speed | Rebuilt as one pass in walk order, several times faster |
| Packed positions from every parallelogram already decoded, averaged | 2% smaller to 4% larger | The walk decodes a position as soon as one parallelogram exists, so there is rarely a second |
| Packed position contexts from the gate edge's length, or the vertex's other axes | Within 1%, often larger | Residuals are already coded close to their entropy |
| Finer number bins: bit length plus the next bit as the symbol | Under 0.5% | Raw low bits are nearly uniform already |
| One vertex list for all blendshape targets (union mask) | 3.2x larger on a test face rig of 30 local targets | Every target then stores every vertex |
| Branch-free strip vertices | Whole reads about 1.3x slower | The branch predicts better than expected |
| Smaller blocks with their own widths and bases | About 1% at best, often larger | The width reflects real variation, not outliers |
| Merging exact duplicate vertices | 1.31x on samurai | Vertices must stay as Blender has them |

## How far compression could go

Measured 2026-11-02 on seven meshes, in bytes. Parallelogram prediction with each 384-corner block on its own, then ideal
order-0 entropy per 1,024 values, with table costs estimated. That is optimistic for any real entropy coder.

| Mesh | ZPM now | Positions, normals and UVs: now | With entropy | Draco |
| --- | ---: | ---: | ---: | ---: |
| BoomBox | 51,676 | 45,712 | 38,995 | 32,446 |
| Avocado | 6,428 | 5,564 | 4,707 | 4,680 |
| Duck | 31,476 | 26,990 | 25,871 | 23,536 |
| DamagedHelmet | 178,768 | 168,490 | 146,958 | 98,931 |

Parallelogram prediction with bit packing alone is larger than today's columns on most streams. Entropy coding would beat
meshopt on these four, but it means decoding up to 1,024 values to read one, and it does not reach Draco on these once corners
are added. None of this is built.

A second test went further: whole-mesh decoding, averaged parallelogram prediction on welded positions, seam twins as flags,
normals predicted from geometry and an adaptive binary coder, with corners in strip order. Exact model costs, in bytes:

| Mesh | ZPM now | Whole mesh, adaptive | Blocks of 2,048 values, adaptive | Draco | Meshopt |
| --- | ---: | ---: | ---: | ---: | ---: |
| Avocado | 6,428 | 5,666 | 5,666 | 4,680 | 6,255 |
| suzanne | 5,488 | 5,864 | 5,864 | 5,230 | 4,988 |
| BoomBox | 51,676 | 41,091 | 44,678 | 32,446 | 51,417 |
| Duck | 31,476 | 27,983 | 29,823 | 23,536 | 35,146 |
| samurai | 19,312 | 17,898 | 18,701 | 13,705 | 36,377 |

That did not reach Draco. The packed form, built since with better connectivity and UV prediction, about ties it:
see [The packed form](#the-packed-form).

## What a 3D file should not hold

Removed on 2026-11-02, as Blender's own state and not 3D data: viewport display, locks, render visibility and other object
settings; bone collections and display; scene frame range and active camera; worlds; speakers and light probes; node graphs;
modifiers, constraints, drivers and control shapes; bake records. The armature link stays, so skinning works. Object settings
keep only delta transforms and an empty's shape, and cameras and lights only what changes the image. A rig comes back with
its bones, weights and animation, but no controllers.

## Where ZPM stands against Draco on size

The direct form is about 1.7x larger than Draco: a format that reads any value alone gives up predicting from what
came before. The packed form decodes in order like Draco does. At the same precision it is 0.991x Draco in total
and smaller on 21 of 24. Draco is still smaller on BoomBox 1.008x, DamagedHelmet 1.057x, happy 1.008x.

## What we can and can't claim

- **Can say:** on these 17 meshes, smaller than every earlier ZPM experiment on all but tiny meshes,
  with zero allocations when reading.
- **Can say:** saved files are 7.8x smaller than meshopt on these 24 meshes, smaller than Draco on 21 of them at
  the same precision and 0.991x Draco in total, and decode 2x to 16x faster than Draco in native code.
- **Can't say:** smaller than Draco on every mesh, or faster than meshopt at decoding a whole mesh. It has been measured: see
  [the comparison](../benchmarks/format-compare/README.md). The direct form wins on reading part of a mesh and on memory.
- **Not new inventions:** the building blocks above are known. What's unusual is combining them in a
  mesh file so nothing needs decoding. We have not surveyed every format, so say "unusual", not "first".
- **Tiny meshes get bigger:** a mesh of a few vertices costs more, because of the directory and tail.
