# Mesh-core contract

Status: prototype 0.1. Updated 2026-11-03. Not frozen: the layout may change during development.

ZPM has one mesh layout and no export options. Files use the `.zpm` extension. Readers must
inspect the magic and version, not infer layout from a filename.

A mesh is stored in one of two forms, told apart by which sections it has:

- **Packed**, what saving writes. The smallest form: a reader decodes it once, start to end, into
  the direct form or into its own arrays. See [Packed sections](#packed-sections).
- **Direct**, its own runtime form. Any vertex, corner, face or edge is read by index from the file
  bytes, with no decode step, no allocation and nothing else touched.

Saving falls back to direct for a mesh the packed form cannot hold: one with no faces (point clouds,
hair), holes, no grid, edges that are not exactly those its faces reach plus loose ones, or vertices
a vertex parent names by number.

## What saving changes

The file holds the same shape, not always the same bytes as the source.

- **Normals become 12-bit directions.** The largest error measured was about 0.06°. Normals that were
  not authored, and are within 0.1° of their own face's, are not stored: readers calculate them.
- **UVs snap to a grid of 1 / 2^20**, about 1/256 of a texel on a 4K texture. Non-finite values become 0.
- **Creases and weights become 16 bits.** Float colors snap to a grid of 1 / 2^16. RGBA8 colors stay exact.
- **Tangents become 12-bit directions,** with their sign kept. Blender recalculates its own on import.
- **Positions snap to a grid.** The step is a power of two, at most 1 / 2^20 of the model's largest
  size, so no point moves more than 1 / 2^21 of it: about 1 micron on a 2 m character. Equal
  positions stay equal, so shared points cannot crack apart. Snapping twice changes nothing. A mesh
  with a non-finite position is stored with its bits unchanged. A packed mesh uses a coarser step
  when it can, the largest size over 2^20 rounded down to 6 significant bits, as Draco's 20-bit
  grid: the same 1 / 2^21 bound, rounding from the original positions. `snap_as_saved` gives a
  mesh as saving stores it.
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
| File header | 16 bytes: magic `ZPM\0`, version (UInt16), section count (UInt16), file length (UInt64) |
| Directory | 8 bytes per section: kind (UInt16), flags (Byte), layer (Byte), file offset (UInt32). A file past 4 GiB uses a UInt64 offset, 12 bytes per section |
| Sections | Each padded with zeros to 4-byte alignment, contiguous, in rising kind then layer order |
| Tail | 32 zero bytes, so readers can load whole words past the last value |

A section's length is the distance to the next offset. A section is present only if the mesh needs
it, so its presence is the layout choice: the file has no separate mode fields.

Flag values: `1` the column stores slopes, `2` a palette index holds a sign in its low bit, `4` block offsets are UInt16,
`8` a stream holds one pair per stored position rather than per vertex, `16` a stream's predictions use shift 10, not 16, `32` a UV layer uses layer 0's repeat map.

A column is its count and lowest base (two UInt32), its lowest width and bits per base (two bytes), each block's base as a
rise above the lowest, packed to the bit, slopes when flagged, each block's width as a 4-bit rise above the lowest, padding
to 4 bytes, one offset per 16 blocks except the first, padding to 4 bytes, then the packed values. A rank mask is 8 bytes
per 64 elements, plus a count of 2 bytes, or 4 when it covers more than 65,535 elements, padded to 4 bytes.

An old strip vertex is a clear bit, then its distance plus 8 as an Exp-Golomb code: as many clear bits as it has bits past
its lowest four, a set bit, then its bits below the top one. Thirteen clear bits and a set bit mean 32 whole bits follow.

## Sections

| Kind | Name | Type | Holds |
| ---: | --- | --- | --- |
| 1 | `MESH_INFO` | 8 × 32-bit | Vertex, face, corner, edge and unique-position counts; shared face size or 0; position grid exponent; flags (1 = authored normals, 2 = normals calculated from faces) |
| 2 | `POSITION_REPEAT_MASK` | Rank mask | Marks vertices that repeat an earlier position |
| 3 | `POSITION_BACK_DISTANCES` | Column | How far each marked vertex reaches back from the newest unique position |
| 12–14 | `POSITION_GRID_X/Y/Z` | Column | Position as a whole number of grid steps |
| 15 | `VERTEX_CREASES` | Column | 16-bit crease per vertex |
| 16–18 | `POSITION_BITS_X/Y/Z` | Column | Float bits, mapped so their order follows their value. Only for meshes with no grid |
| 20–22 | `POSITION_CONSTANT_X/Y/Z` | UInt32 | The one value this axis holds |
| 24–26 | `POSITION_PALETTE_X/Y/Z` | Column | Sorted grid values, without their sign |
| 28–30 | `POSITION_INDICES_X/Y/Z` | Column | Palette index of each unique position |
| 32 | `CORNER_IDS` | Column | Vertex ID of each corner |
| 33 | `CORNER_FIRST` | Column | Vertex ID of each face's first corner |
| 34–36 | `CORNER_OFFSET_1/2/3` | Column | Each later corner, relative to the first |
| 37 | `CORNER_REPEAT_MASK` | Rank mask | Marks corners that reuse an earlier vertex. Unmarked ones use the next new vertex |
| 38 | `CORNER_BACK_DISTANCES` | Column | How far each marked corner reaches back from the newest vertex |
| 40 | `FACE_STARTS` | Select bits | Marks each face's first corner |
| 39 | `CORNER_SHARP` | Column | 1 where a corner is marked sharp |
| 41 | `FACE_FLAT` | Column | 1 where a face is shaded flat |
| 42 | `FACE_MATERIALS` | Column | Material slot per face; absent when every face uses slot 0 |
| 43 | `MATERIAL_SLOT_NAMES` | Name table | Slot names; an empty name is an empty slot |
| 44 | `FACE_HOLE_STARTS` | Column | Rising corner indices, each the first corner of a hole ring. Requires edges as endpoints and stored normals |
| 45 | `CORNER_SMOOTHING_GROUPS` | Column | Smoothing group per corner; 0 means none |
| 48 | `EDGE_FIRST` | Column | First vertex ID of each edge |
| 49 | `EDGE_SPAN` | Column | Second vertex ID, relative to the first |
| 50 | `EDGE_LOOSE_MASK` | Rank mask | Marks edges on no face |
| 51–52 | `EDGE_LOOSE_FIRST/SECOND` | Column | Both vertex IDs of each marked edge |
| 53 | `EDGE_CORNERS` | Column | A corner on this edge: its endpoints are that corner and the next |
| 54 | `EDGE_FLIPS` | Column | 1 where the edge runs against that corner. Absent means lower ID first |
| 55 | `EDGE_FIRST_USE` | Select bits | Marks the corner that first reaches each edge, in edge order |
| 56–57 | `EDGE_SHARP`, `EDGE_SEAM` | Column | 1 where an edge is marked |
| 58 | `EDGE_CREASES` | Column | 16-bit crease per edge |
| 59 | `CREASE_CHAIN_OFFSETS` | Column | Where each chain's vertices start, plus a final total |
| 60 | `CREASE_CHAIN_VERTICES` | Column | Vertex IDs along each chain; neighbours share an edge |
| 78 | `UV_SEAMS` | Column, layered by UV layer | A layer's own seams, only when they differ from `EDGE_SEAM` |
| 95 | `TANGENT_METHOD` | Name table | One name: what made the tangents and its version |
| 64–72 | `NORMAL_*` | Corner stream | Two 12-bit values per corner |
| 79 | `UV_NAMES` | UInt32 count, then length + UTF-8 | Layer names; a name's position is its layer number |
| 80–88 | `UV_*` | Corner stream, one per layer | Two grid integers per corner |
| 96–104 | `TANGENT_*` | Corner stream, layered by UV layer | A 12-bit direction per corner, with the bitangent sign as bit 12 of the second value |
| 110 | `COLOR_NAMES` | Name table | Layer names; a name's position is its layer number |
| 111 | `COLOR_FORMATS` | UInt32 per layer | 1 = per corner, 2 = float channels, 4 = sRGB color channels |
| 112–120 | `COLOR_RG_*` | Corner stream, one per layer | RGBA8: red + green and blue + alpha, as two 16-bit values. Float: red and green |
| 128–136 | `COLOR_BA_*` | Corner stream, one per float layer | Float blue and alpha |
| 144 | `GROUP_NAMES` | Name table | Vertex group names; a weight's group is an index here |
| 145 | `WEIGHT_OFFSETS` | Column | Where each vertex's weights start, plus a final total |
| 146–147 | `WEIGHT_GROUPS`, `WEIGHT_VALUES` | Column | A group index and a 16-bit value per weight |
| 148 | `WEIGHT_SET_OF_VERTEX` | Column | Which row of the weight tables each vertex uses, present when sharing rows is smaller. `WEIGHT_OFFSETS` then has one entry per row plus a total |
| 160 | `ATTRIBUTE_NAMES` | Name table | Custom attribute names |
| 161 | `ATTRIBUTE_FORMATS` | UInt32 per attribute | Domain, type << 8, components << 16, meaning << 24 |
| 162 | `ATTRIBUTE_VALUES` | Column, layered by attribute | All of component 0, then component 1 and so on. Floats as ordered bits, so exact |
| 163 | `ATTRIBUTE_STRINGS` | Name table, layered by attribute | A string attribute's distinct strings; its values index them |
| 170 | `SHAPE_NAMES` | Name table | Blendshape names, in evaluation order |
| 171 | `SHAPE_GROUP_NAMES` | Name table | Vertex group limiting each target; empty for none |
| 172 | `SHAPE_INFO` | 32 bytes per target | Reference, in-between-of, value, minimum, maximum, frame, in-between weight, flags (1 relative, 2 muted, interpolation << 8) |
| 173 | `SHAPE_STARTS` | Column | Where each target's run starts, plus a final total |
| 174 | `SHAPE_VERTICES` | Column | Rising vertex IDs each target changes |
| 185, 186 | `CORNER_STRIP_STARTS`, `CORNER_STRIP_NEWEST` | Column | Per block of 384 corners: where its bits start (with a final total), and its next new vertex |
| 187 | `CORNER_STRIP_BITS` | Bits | Faces of up to 32 corners, low bits first. See `storage/corner_strips.py`. Replaces other corner forms when smaller |
| 184 | `SHAPE_LIST_STARTS` | Column | Where each target's list starts, present when targets share lists |
| 175–177 | `SHAPE_POSITION_X/Y/Z` | Column | Signed grid steps from the base shape, or ordered bits of the target with no grid |
| 178–183 | `SHAPE_NORMAL_*`, `SHAPE_TANGENT_*` | Column | Optional signed changes on a 1 / 2^16 grid |

### Packed sections

A packed mesh holds these in place of the direct position, corner, edge, normal and UV sections. Everything else
it holds, from tangents to blendshapes, uses the sections above. `codec/packed.py` is the reference.

| Kind | Name | Holds |
| ---: | --- | --- |
| 278 | `PACKED_CONNECTIVITY` | One byte, the context model, then faces as a walk over welded positions |
| 279 | `PACKED_SPLITS` | One byte, the model, then which vertex at its position each corner uses |
| 280 | `PACKED_POSITIONS` | Grid step (Float32; 0 for 2^ the grid exponent), a form byte (bit 0 fine numbers, bit 1 contexts across axes), then grid integers of each welded position, then of vertices on no face |
| 281 | `PACKED_NORMALS` | A byte (bit 0 predicts around vertices, not positions; bit 1 fine numbers), then the stream |
| 282 | `PACKED_UVS` | Layered by UV layer, named by `UV_NAMES`: a byte (1 for fine numbers), then the stream |
| 283 | `PACKED_LOOSE_EDGES` | Column: both vertex IDs of each edge on no face |

Each stream is static-table rANS: a state (UInt32), then word count and raw byte count as varints (7 bits a byte, low
first, top bit set on all but the last); then the tables, bit-packed low bits first and padded to a byte; then the
words (UInt16), read from the first, then the raw bytes. A table is its entry count, then, if any, a 4-bit k, each
entry's symbol as its gap from the one before (the first from −1) less one, then every entry's frequency but the
last, less one, as an order-k Exp-Golomb code; the last takes what remains of 1,024. Counts and gaps are order-0 codes.
An order-k code of v is as many clear bits as v + 2^k has bits past k + 1, a set bit, then its bits below the top one,
low first. A symbol takes the state's low 10 bits as its slot; the state becomes
frequency × (state >> 10) + slot − the symbol's first slot, and takes the next word whenever it falls below 65,536. A
number is its zigzag value's bit length as a symbol, then the bits below the top one, raw, low bits first. In a fine
stream a number of two bits or more is instead 2 × its length − 2 + its second bit, then the bits below that. A choice
is one symbol, or 200 then a number to add to 200; counts that cannot be negative are choices.

**Connectivity.** Faces arrive as a walk. The reader keeps open edges: directed edges with a face on one side so far,
and a stack of them, newest last. It reads a face with nothing known, then pops edges: any no longer open is skipped;
otherwise a gate symbol says whether a face lies across it. If so, that face starts with the edge's two positions
reversed, else the edge closes. When the stack empties and faces remain, a new face with nothing known starts. A
face's size is a choice when `MESH_INFO`'s shared size is 0. Each later corner is a symbol: 0 a new position; 1 the
newest position closing an open edge into the face's last corner; 2 the one out of its first corner; 3 an older one
into the last; 4 far, a number counting back from the newest position; 5 a choice plus one of steps forward along open
edges from the last corner, taking the newest edge at each step; 6 the same backward from the first corner. A face's
corners are its ring in this order, which sets winding and first corner. Contexts count symbols 4 and up as one.

Model 0 keys a corner on the symbol before and whether it ends its face; model 1 adds the symbol before that and
whether open edges lead into the last corner and out of the first. Model 2 follows each new position with a choice,
its count of corners in all faces, keyed on the count before it up to 9 (starting from 6), and keys a corner on how
many faces its face's first and last positions still need (none, 0, 1, 2, or 3 and up) with whether it is last and
the open edges as in model 1, and a gate on what each end still needs, up to 2. Model 0 and 1 gates share one context.

**Splits.** Corners in order. The first corner at a position makes a new vertex. Later ones read a choice: 0 for a
new vertex, else a vertex already at that position, from 1. Model 0 counts them in the order they were made. Model 1
first names a guess: for a face's first two corners, the vertex at the matching corner of the face across its gate,
else the vertex last used at this position; then the others, newest first. Contexts are the vertex count, up to 3,
plus 4 when the guess came from a gate.

**Positions.** Welded positions in the order they first appear, each axis a number added to a prediction from where
the position first appears in its face. At the first corner: the last position read. At the second: the first
corner's. At the third, reached through a gate: the two gate corners added, minus the third corner of the face the
gate came from; with no gate, the corner before. At later corners: the corner before plus the first minus the second.
With 4,000 positions or more, each axis's number takes a context from the size of the last, a quarter of its bit
length up to 3. Across axes, the context is instead half the largest bit length so far among this position's earlier
axes, or for the first axis its own last, up to 7, times 6, plus a third of its own last bit length up to 5.

**Normals and UVs.** Corners in order. A corner whose key, position or vertex, already holds values reads a choice:
0 for new values, or which of them. New values are numbers added to a prediction: for normals, the 12-bit direction
of the summed face normals around the key, with integer cross products, or the key's last value; for UVs, from the
triangle's shape in space, with a symbol picking the side when the walk does not settle it. All prediction uses IEEE
doubles and rounds halves to even, so every reader gets the same integers.

### Scene files

Every saved file is a scene. It has the same header, directory and tail as a mesh file, and always holds
`SCENE_MESH_STARTS`. Each mesh in it is a complete mesh file, so a mesh opens with no copy using the mesh reader.
Tables hold fixed records of 4-byte fields, read in place. Links are indices, with `0xFFFFFFFF` meaning none.

| Kind | Section | Holds |
| --- | --- | --- |
| 4 | `SPACE` | Float32 meters per unit, up axis, forward axis, two zero bytes. Absent means 1 m, +Z up, -Y forward |
| 200, 201 | `SCENE_MESH_STARTS`, `SCENE_MESHES` | UInt64 start per mesh plus a total, then the mesh files joined |
| 202, 203 | `OBJECT_NAMES`, `OBJECT_INFO` | 164 bytes per object: type, data, parent, parent bone, rotation mode, flags, instance collection, skin armature, skin flags, parent type, three parent vertices, location, rotation, scale, parent inverse, first pose channel, pose channel count |
| 204 | `POSE_CHANNELS` | 48 bytes per channel: bone, location, rotation, scale, rotation mode |
| 205–208 | `COLLECTION_*` | Names, parents, member starts, members |
| 209, 210 | `MATERIAL_NAMES`, `MATERIAL_INFO` | 64 bytes per material: base color, metallic, roughness, emission, strength, normal scale, alpha cutoff, alpha mode, flags, first slot, slot count |
| 211, 212 | `TEXTURE_SLOTS`, `TEXTURE_UV_NAMES` | 28 bytes per slot: image, sampler, offset, rotation, scale; and its UV layer name |
| 213 | `SAMPLERS` | 12 bytes per sampler: filter, wrap U, wrap V |
| 214–216 | `IMAGE_NAMES`, `IMAGE_COLOR_SPACES`, `IMAGE_INFO` | 24 bytes per image: width, height, channels, flags, first tile, tile count |
| 217, 218 | `IMAGE_TILES`, `IMAGE_PATHS` | 8 bytes per tile: UDIM number, payload; and its path |
| 219, 220 | `IMAGE_PAYLOAD_STARTS`, `IMAGE_PAYLOADS` | UInt64 starts, then original encoded image bytes, each distinct file once |
| 221, 222 | `LIGHT_NAMES`, `LIGHT_INFO` | 56 bytes per light: type, unit, color, power, flags, area shape, radius, spot angle, spot blend, sun angle, area size |
| 223, 224 | `ARMATURE_NAMES`, `ARMATURE_BONE_STARTS` | Names and where each armature's bones start |
| 225, 226 | `BONE_NAMES`, `BONE_INFO` | 48 bytes per bone: parent, flags (1 connected, 2 deform, 4 inherits rotation, scale inheritance << 8), head, tail, roll, envelope distance, head radius, tail radius |
| 227, 228 | `SKIN_STARTS`, `SKIN_BONES` | For each object, the bone for each of its mesh's vertex groups |
| 229 | `TEXTURE_ROLES` | What each texture slot feeds, as a name. `base_color`, `metallic`, `roughness`, `normal`, `emission` and `alpha` are shared; any other name is allowed |
| 230 | `VALUE_STRINGS` | Name table shared by value trees and animation |
| 231 | `VALUE_NODES` | 16 bytes per node: key string, type, and a and b (the value, or the start and count of an array or of children, always later in the table) |
| 232 | `VALUE_POOL` | Column holding all arrays; floats as ordered bits |
| 233 | `VALUE_OWNERS` | 12 bytes per tree, sorted: resource kind, index, root node |
| 234 | `CAMERA_NAMES` | Camera names; settings live in value trees |
| 235, 236 | `CURVE_NAMES`, `CURVE_INFO` | 8 bytes per curve: first spline, spline count |
| 237 | `SPLINE_INFO` | 32 bytes per spline: type, flags, order, resolution, first point, point count, first knot, knot count |
| 238–243 | `CURVE_POINT_*` | Exact columns: X, Y, Z, weight, radius, tilt |
| 244–250 | `CURVE_HANDLE_*` | Exact left and right handles, and two handle types per point |
| 251 | `CURVE_KNOTS` | Exact stored knots, when not calculated |
| 252, 253 | `LATTICE_NAMES`, `LATTICE_INFO` | 20 bytes per lattice: U, V, W, first point, point count |
| 254–260 | `LATTICE_*` | Exact columns: rest XYZ, deformed XYZ, weight |
| 261, 262 | `CLIP_NAMES`, `CLIP_INFO` | 20 bytes per clip: first track, track count, frames per second, start, end |
| 263 | `TRACK_INFO` | 52 bytes per track: owner kind, owner, path string, array index, value type, target kind, components, flags, time set, key count, first value, first mode, first handle |
| 264, 265 | `TIME_STARTS`, `TIMES` | Distinct lists of key times |
| 266–268 | `KEY_VALUES`, `KEY_MODES`, `KEY_HANDLES` | Key columns. Modes hold mode, left handle type << 8, right handle type << 16 |
| 272–274 | `OBJECT_ORDER`, `OBJECT_CHILD_STARTS`, `OBJECT_CHILDREN` | Objects with parents before children, and each object's children. Absent when nothing has a parent |
| 275–277 | `BONE_ORDER`, `BONE_CHILD_STARTS`, `BONE_CHILDREN` | The same for bones, by index across all armatures |
| 269–271 | `EXTENSION_NAMES`, `EXTENSION_INFO`, `EXTENSION_PAYLOADS` | Name, then version, flags (1 = required), start, length, then the bytes |

Section kinds from `0x8000` up are optional: a reader skips any it does not know. A required extension a reader
does not know makes it refuse the file.

Unknown kinds are refused. Each axis uses grid integers, a constant, a palette with indices, or bits.
Sections for data a mesh lacks, or that is all zero, are absent.
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

**Name table.** A UInt32 count, then a UInt32 length and UTF-8 bytes per name.

**Rank mask.** One UInt64 per 64 elements, then one UInt32 per word counting unmarked elements
before it. A popcount gives any element's rank in constant time.

**Corner stream.** Nine possible sections, in this order: repeat mask, distances, values A and B, vertex
A and B, exception mask, exception A and B. The first form stores each distinct pair once, and corners
reach back to it. The second stores one pair per vertex, plus the corners that disagree.

**Select bits.** One bit per element, then one UInt32 per 64 set bits giving that bit's position, so
the Nth set bit is found by reading a few words.

## What makes it small

- **Corners store how far they reach back.** Vertices are numbered by first use, so a corner either
  uses the next new vertex or an earlier one. The newest vertex at any corner is just its rank in the
  mask, so this needs no running state and any corner still reads directly.
- **Edges can cost one bit per corner.** Stored in the order faces first reach them, an edge is the
  Nth marked corner.

- **Positions are grid integers.** After snapping, a coordinate is a whole number of steps, so the
  jump in float bits between -1 and +1 is gone. Reading is one multiply.
- **Repeated positions** are stored once. An unmarked vertex's unique ID is its rank in the mask, and
  a marked vertex stores how far back it reaches.
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
| `blender/zpm/format/codec/mesh_codec.py` | The one writer, and expanding a file into a `Mesh` for editing |
| `blender/zpm/format/processing/` | Snapping, reordering and the geometry check |
| `blender/zpm/format/container/file_map.py` | Names every byte of a file |
| `blender/zpm/format/codec/quantize.py` | Stored forms of directions, UVs, colors, creases and weights |
| `runtime/csharp/Zpm/ZpmMeshView.cs` | Runtime reader: by index, no allocation |

## Limits

One mesh stays under 4 GiB, because its element counts are 32-bit, but a scene file can be larger, as far as tested below 4 GiB. `decode` and `load` default to a 512 MiB limit on the file and the
expanded buffers, configurable by API callers. It is not a peak RAM budget.

The format API supports arbitrary index topology. The Blender adapter additionally rejects self-loop
edges, duplicate edges, repeated vertices inside a face and missing face-boundary edges.
Loose edges and nonmanifold edges remain valid.

Writers validate before opening the destination. They write a temporary file in the destination
directory, then replace the destination atomically.
