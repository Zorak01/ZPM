# Mesh-core contract

Status: prototype 0.1, aligned profiles only. Updated 2026-10-30.

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

## Validation and memory

Lengths and modes are checked before allocating decoded arrays. Indices and
face totals are validated before constructing Blender data. The default read
limit is 512 MiB for the packet and retained decoded buffers, configurable by
API callers. It is not a peak RAM budget. Input bytes, decoded arrays, temporary
integer conversion buffers and Blender's own mesh can coexist.

Writers validate before opening the destination. They write a temporary file
in the destination directory, then replace the destination atomically. This
avoids leaving an existing file half-written after an encode or write failure.
