# Mesh-core contract

Status: prototype 0.1. Updated 2026-10-31. Not frozen: the layout may change during development.

ZPM has one mesh layout and no export options. Files use the `.zpm` extension. Readers must
inspect the magic and version, not infer layout from a filename.

The file is its own runtime form. Any vertex, corner, face or edge is read by index from the file
bytes, with no decode step, no allocation and nothing else touched.

## What saving changes

The file holds the same shape, not always the same bytes as the source.

- **Positions snap to a grid.** The step is a power of two, at most 1 / 2^20 of the model's largest
  size, so no point moves more than 1 / 2^21 of it: about 1 micron on a 2 m character. Equal
  positions stay equal, so shared points cannot crack apart. Snapping twice changes nothing. A mesh
  with a non-finite position is stored with its bits unchanged.
- **Elements may be reordered.** The encoder also tries walking faces across shared edges, starting
  from a space-filling curve, with vertices and edges in the order those faces reach them, and keeps
  whichever file is smaller.
  When it reorders, vertex IDs change and edges are stored lower ID first. Winding and each face's
  first corner are kept.

Triangles, quads and n-gons stay what they are. Nothing is welded or dropped.

## File structure

Little-endian. Every byte belongs to exactly one named region, and `tools/zpm_map.py FILE` prints them.

| Region | Contents |
| --- | --- |
| File header | 12 bytes: magic `ZPM\0`, version (UInt16), section count (UInt16), file length (UInt32) |
| Directory | 8 bytes per section: kind (UInt16), flags (UInt16), file offset (UInt32) |
| Sections | Each padded with zeros to 4-byte alignment, contiguous, in rising kind order |
| Tail | 32 zero bytes, so readers can load whole words past the last value |

A section's length is the distance to the next offset. A section is present only if the mesh needs
it, so its presence is the layout choice: the file has no separate mode fields.

Flag values: `1` the column stores slopes, `2` a palette index holds a sign in its low bit.

## Sections

| Kind | Name | Type | Holds |
| ---: | --- | --- | --- |
| 1 | `MESH_INFO` | 6 × UInt32 | Vertex, face, corner, edge and unique-position counts; shared face size or 0 |
| 2 | `POSITION_REPEAT_MASK` | Rank mask | Marks vertices that repeat an earlier position |
| 3 | `POSITION_REPEAT_IDS` | Column | Unique-position ID of each marked vertex |
| 16–18 | `POSITION_BITS_X/Y/Z` | Column | Float bits, mapped so their order follows their value |
| 20–22 | `POSITION_CONSTANT_X/Y/Z` | UInt32 | The one value this axis holds |
| 24–26 | `POSITION_PALETTE_X/Y/Z` | Column | Sorted values, without their sign |
| 28–30 | `POSITION_INDICES_X/Y/Z` | Column | Palette index of each unique position |
| 32 | `CORNER_IDS` | Column | Vertex ID of each corner |
| 33 | `CORNER_FIRST` | Column | Vertex ID of each face's first corner |
| 34–36 | `CORNER_OFFSET_1/2/3` | Column | Each later corner, relative to the first |
| 37 | `CORNER_REPEAT_MASK` | Rank mask | Marks corners that reuse an earlier vertex. Unmarked ones use the next new vertex |
| 38 | `CORNER_BACK_DISTANCES` | Column | How far each marked corner reaches back from the newest vertex |
| 40 | `FACE_STARTS` | Select bits | Marks each face's first corner |
| 48 | `EDGE_FIRST` | Column | First vertex ID of each edge |
| 49 | `EDGE_SPAN` | Column | Second vertex ID, relative to the first |
| 50 | `EDGE_LOOSE_MASK` | Rank mask | Marks edges on no face |
| 51–52 | `EDGE_LOOSE_FIRST/SECOND` | Column | Both vertex IDs of each marked edge |
| 53 | `EDGE_CORNERS` | Column | A corner on this edge: its endpoints are that corner and the next |
| 54 | `EDGE_FLIPS` | Column | 1 where the edge runs against that corner. Absent means lower ID first |
| 55 | `EDGE_FIRST_USE` | Select bits | Marks the corner that first reaches each edge, in edge order |

Unknown kinds are refused. Each axis uses bits, a constant, or a palette with indices.
Corners use `CORNER_IDS`, `CORNER_FIRST` with offsets when all faces have 3 or 4 corners, or a repeat
mask with back distances when vertices are numbered by first use.
Edges use `EDGE_FIRST` with `EDGE_SPAN`, or `EDGE_CORNERS` or `EDGE_FIRST_USE` with any loose-edge sections.

## Storage types

**Column.** A UInt32 count, then N UInt32 values in blocks of 64:
`value[j] = base + slope × j + residual[j]`, in wrapping arithmetic. Stored as bases (UInt32 per
block), slopes (Int8 per block, if flagged), widths (byte per block, 0–32), padding to 4 bytes, one
UInt32 payload offset per 8 blocks, then residuals packed low bit first. A full block's payload is
exactly `width × 8` bytes, so a block's offset is its group's offset plus the widths before it,
which one 64-bit multiply sums.

**Rank mask.** One UInt64 per 64 elements, then one UInt32 per word counting unmarked elements
before it. A popcount gives any element's rank in constant time.

**Select bits.** One bit per element, then one UInt32 per 64 set bits giving that bit's position, so
the Nth set bit is found by reading a few words.

## What makes it small

- **Corners store how far they reach back.** Vertices are numbered by first use, so a corner either
  uses the next new vertex or an earlier one. The newest vertex at any corner is just its rank in the
  mask, so this needs no running state and any corner still reads directly.
- **Edges can cost one bit per corner.** Stored in the order faces first reach them, an edge is the
  Nth marked corner.

- **Repeated positions** are stored once. An unmarked vertex's unique ID is its rank in the mask.
- **Signed palettes** store magnitudes, with the sign in the index, so a mirrored model shares values.
- **Edges** store one corner reference. A manifold edge has two such corners, and the encoder picks
  whichever fits its block in fewer bits. Blender writes the lower ID first, so order costs nothing.

## Untrusted files

Opening reads only the header and directory. Call validation before reading untrusted bytes. It checks
every width, offset and mask, and proves IDs in range from block descriptors, reading a block's
values only when its descriptor cannot prove it. After that no read can leave the file or return a
missing ID. Back distances are the one case read in full, because only a running count can prove
them. The benchmark fuzzes this on every run.

## Readers and writers

| Code | Does |
| --- | --- |
| `blender/zpm/format/encoder.py` | The one writer |
| `blender/zpm/format/optimize.py` | Position snapping, reordering, and the geometry check |
| `blender/zpm/format/decoder.py` | Expands a whole file into a `Mesh`, for editing tools |
| `blender/zpm/format/file_map.py` | Names every byte of a file |
| `runtime/csharp/Zpm/ZpmMeshView.cs` | Runtime reader: by index, no allocation |

## Limits

One mesh file stays under 4 GiB. `decode` and `load` default to a 512 MiB limit on the file and the
expanded buffers, configurable by API callers. It is not a peak RAM budget.

The format API supports arbitrary index topology. The Blender adapter additionally rejects self-loop
edges, duplicate edges, repeated vertices inside a face and missing face-boundary edges.
Loose edges and nonmanifold edges remain valid.

Writers validate before opening the destination. They write a temporary file in the destination
directory, then replace the destination atomically.
