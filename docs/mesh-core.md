# Mesh-core contract

Status: prototype 0.1. Updated 2026-10-31. Two envelopes: ZMC1 (RAW32, COMPACT) below,
and [ZMD1](#zmd1-direct-read-layout) (DIRECT). Readers choose by magic.

This implementation uses the existing **ZMC1 benchmark envelope**, with a `.zpm`
file extension in Blender. This is not a frozen final ZPM container. Readers
must inspect magic and version, not infer layout from a filename.

## Packet layout

Little-endian. A 64-byte header is followed by four contiguous streams.

| Header field | Type | Meaning |
| --- | --- | --- |
| Magic | 4 bytes | `ZMC1` |
| Version | UInt32 | `1` |
| Vertex, face, corner, edge counts | 4 × UInt32 | Exact element counts |
| Position mode | UInt32 | `0`: raw Float32 XYZ |
| Position block size | UInt32 | Reserved in aligned profiles; writer uses 1024 |
| Face mode | UInt32 | `0`: raw UInt32; `4`: aligned compact IDs |
| Edge mode | UInt32 | `0`: raw UInt32; `3`: aligned compact IDs |
| Uniform face arity | UInt32 | `0`: explicit face sizes; otherwise shared size ≥ 3 |
| Four stream lengths | 4 × UInt32 | Position, face-size, corner, edge byte lengths |
| Raw flag | UInt32 | `1`: RAW32; `0`: COMPACT |

| Stream | Contents |
| --- | --- |
| Positions | `vertex_count × 3` Float32 values |
| Face sizes | Absent for shared arity; otherwise `face_count` UInt32 values |
| Corners | `corner_count` vertex IDs, preserving face order and winding |
| Edges | `edge_count × 2` vertex IDs, preserving endpoint order |

RAW32 always uses UInt32 IDs and explicit face sizes. COMPACT uses UInt16 IDs
when vertex count ≤ 65,536, otherwise UInt32. UInt16 narrows integer storage,
not position precision. No welding, triangulation, reordering or quantization.

Other experimental benchmark profiles are **not supported** by this reader.
They fail with a clear error. The format API supports arbitrary index topology;
the Blender adapter additionally rejects self-loop edges, duplicate edges,
repeated vertices inside a face and missing explicit face-boundary edges.
Loose edges and nonmanifold edges remain valid.

## ZMD1 direct-read layout

The file is its own runtime form. Any vertex, corner, face or edge is read by index from the file
bytes, with no decode step, no allocation and nothing else touched. Positions and IDs stay exact.
Not frozen: the layout may change while the format is in development.

Little-endian. A 48-byte header, one UInt32 entry per section, the sections (each 4-byte aligned),
then 32 zero bytes so readers can load past the last value.

| Offset | Field |
| ---: | --- |
| 0 | Magic `ZMD1` |
| 4 | Vertex, face, corner, edge, unique-position counts (5 × UInt32) |
| 24 | Axis modes X, Y, Z (3 bytes): 0 bits, 1 palette, 2 signed palette, 3 constant |
| 27 | Corner mode: 0 single column, 1 relative to first corner (arity 3 or 4) |
| 28 | Edge mode: 0 endpoints, 1 corner reference |
| 29 | Uniform arity, or 0 for mixed faces |
| 30 | Flags: 1 repeated positions, 2 loose edges, 4 canonical edges (a ≤ b) |
| 32 | Axis arguments (3 × UInt32): palette size or constant bits |
| 44 | Loose edge count |

A table entry is a file offset, with bit 0 set when a column stores slopes.
Sections appear in this order, each only if its mode needs it:
repeat mask, repeat IDs, axis X, Y, Z (palette then indices), corner columns, face starts,
then either endpoint columns (a, b − a) or loose mask, loose a, loose b, references, flips.

**Column.** N UInt32 values in blocks of 64: `value[j] = base + slope × j + residual[j]`, with
wrapping arithmetic. Stored as bases (UInt32 per block), optional slopes (Int8), widths (byte,
0–32), one UInt32 payload offset per 8 blocks, then residuals packed low bit first. A full block's
payload is exactly `width × 8` bytes, so a block's offset is its group's offset plus the sum of the
widths before it, which one 64-bit multiply computes.

**Rank mask.** One UInt64 per 64 elements, then one UInt32 per word counting unmarked elements
before it. Popcount gives any element's rank in constant time.

**Positions.** Repeated positions are removed: a marked vertex looks up its unique ID, and an
unmarked one's ID is its rank. Each axis then stores one of: float bits mapped so their order follows
their value (so nearby values have small residuals), a sorted palette plus indices, a palette of
magnitudes with the sign in the index's low bit (a mirrored model shares values), or one constant.

**Mixed faces.** One bit per corner marks each face's first corner, with one UInt32 per 64 faces
to find it. This also answers corner-to-next-corner without a stored face table.

**Edges.** An edge shared with a face stores one corner reference: its endpoints are that corner
and the next. A manifold edge has two such corners, and the encoder picks whichever fits its block
in fewer bits. Blender writes a ≤ b, so endpoint order then costs nothing; otherwise a flip column
stores it. Edges on no face go through a rank mask to their own columns.

**Untrusted files.** Opening reads only the header and table. Call validation before reading
untrusted bytes: it checks every width, offset and mask, and proves IDs in range from block
descriptors, reading a block's values only when its descriptor cannot prove it. After that no read
can leave the file or return a missing ID. Fuzzing covers this.

The reference reader is `benchmarks/direct-read/reference/ZpmMeshView.cs`.
The Python API expands whole files into a `Mesh` for editing; it has no by-index reader.

## Validation and memory

Lengths and modes are checked before allocating decoded arrays. Indices and
face totals are validated before constructing Blender data. The default read
limit is 512 MiB for the packet and retained decoded buffers, configurable by
API callers. It is not a peak RAM budget. Input bytes, decoded arrays, temporary
integer conversion buffers and Blender's own mesh can coexist.

Writers validate before opening the destination. They write a temporary file
in the destination directory, then replace the destination atomically. This
avoids leaving an existing file half-written after an encode or write failure.
