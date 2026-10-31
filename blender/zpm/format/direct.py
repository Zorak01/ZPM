# SPDX-License-Identifier: GPL-3.0-or-later

"""ZMD1 direct-read files: small, exact, and readable by index without decoding.

This module writes the same bytes as the benchmark reference and expands a file
into a Mesh for editing. Runtime readers index the file instead; see
docs/mesh-core.md and benchmarks/direct-read/reference/ZpmMeshView.cs.
"""

import struct
import sys
from array import array

from .model import FormatError, Mesh

MAGIC = b"ZMD1"
HEADER_BYTES = 48
TAIL_BYTES = 32
BLOCK_SIZE = 64
BLOCKS_PER_GROUP = 8
MAX_WIDTH = 32
MAX_SLOPE = 127
MAX_UNIFORM_ARITY = 255
MAX_RELATIVE_ARITY = 4
FACES_PER_SELECT = 64
U32 = 0xFFFFFFFF
SIGN_BIT = 0x80000000
MAGNITUDE = 0x7FFFFFFF

AXIS_BITS, AXIS_PALETTE, AXIS_SIGNED_PALETTE, AXIS_CONSTANT = range(4)
CORNERS_SINGLE, CORNERS_RELATIVE = range(2)
EDGES_ENDPOINTS, EDGES_REFERENCE = range(2)
FLAG_REPEATS, FLAG_LOOSE, FLAG_CANONICAL = 1, 2, 4

_HEADER = struct.Struct("<4s5I3B4Bx4I")


def _align(length: int) -> int:
    return (length + 3) & ~3


def _size_of(sections: list) -> int:
    return sum(_align(len(section[0])) + 4 for section in sections)


def _to_ordered(bits: int) -> int:
    """Map Float32 bits to an order that follows numeric value; exact for all bits."""
    return bits ^ U32 if bits & SIGN_BIT else bits ^ SIGN_BIT


def _from_ordered(ordered: int) -> int:
    return ordered ^ SIGN_BIT if ordered & SIGN_BIT else ordered ^ U32


def _measure(values: list, start: int, length: int, slope: int) -> tuple:
    residuals = [values[start + j] - slope * j for j in range(length)]
    low = min(residuals)
    span = max(residuals) - low
    return low, (span.bit_length() if span < (1 << MAX_WIDTH) else sys.maxsize)


def _clamp_slope(value: int) -> int:
    return max(-MAX_SLOPE, min(MAX_SLOPE, value))


def _fit_block(values: list, start: int, length: int) -> tuple:
    low, width = _measure(values, start, length, 0)
    best = 0
    if length < 3 or width == 0:
        return best, low, width
    span = values[start + length - 1] - values[start]
    quotient = abs(span) // (length - 1)
    middle = length // 2
    after = start + middle + 1 if middle + 1 < length else start + 1
    candidates = (
        _clamp_slope(quotient if span >= 0 else -quotient),
        _clamp_slope(values[start + 1] - values[start]),
        _clamp_slope(values[after] - values[start + middle]),
    )
    for slope in candidates:
        if slope == 0 or slope == best:
            continue
        sloped_low, sloped_width = _measure(values, start, length, slope)
        if sloped_width < width:
            width, low, best = sloped_width, sloped_low, slope
    return best, low, width


def _build_column(values: list) -> tuple:
    """Fit each 64-value block with the slope needing the fewest residual bits."""
    count = len(values)
    blocks = (count + BLOCK_SIZE - 1) // BLOCK_SIZE
    bases = array("I")
    slopes = array("b")
    widths = bytearray()
    groups = array("I")
    payload = bytearray()
    for block in range(blocks):
        start = block * BLOCK_SIZE
        length = min(BLOCK_SIZE, count - start)
        slope, low, width = _fit_block(values, start, length)
        if width > MAX_WIDTH:
            raise FormatError("Value range exceeds 32 bits")
        bases.append(low & U32)
        slopes.append(slope)
        widths.append(width)
        if block % BLOCKS_PER_GROUP == 0:
            groups.append(len(payload))
        packed = 0
        if width:
            for j in range(length):
                packed |= (values[start + j] - slope * j - low) << (j * width)
        payload += packed.to_bytes((length * width + 7) // 8, "little")
    has_slopes = any(slopes)
    head = bytearray(_bytes(bases))
    if has_slopes:
        head += _bytes(slopes)
    head += widths
    head += bytes(_align(len(head)) - len(head))
    return bytes(head) + _bytes(groups) + bytes(payload), has_slopes


def _bytes(buffer: array) -> bytes:
    if sys.byteorder == "little":
        return buffer.tobytes()
    swapped = array(buffer.typecode, buffer)
    swapped.byteswap()
    return swapped.tobytes()


def _build_rank_mask(marks: list) -> tuple:
    words = array("Q")
    ranks = array("I")
    clear = 0
    for start in range(0, len(marks), BLOCK_SIZE):
        ranks.append(clear)
        word = 0
        for j, marked in enumerate(marks[start : start + BLOCK_SIZE]):
            if marked:
                word |= 1 << j
            else:
                clear += 1
        words.append(word)
    return _bytes(words) + _bytes(ranks), False


def _build_axis(rows: list, axis: int, count: int, palettes: bool) -> tuple:
    """Return (mode, argument, sections), the smallest legal layout of one axis."""
    column = [rows[i * 3 + axis] for i in range(count)]
    if len(set(column)) == 1:
        return AXIS_CONSTANT, rows[axis], []
    direct = _build_column([_to_ordered(bits) for bits in column])
    if palettes:
        negative = any(bits & SIGN_BIT for bits in column)
        ordered = sorted({bits & MAGNITUDE for bits in column})
        lookup = {value: index for index, value in enumerate(ordered)}
        if negative:
            indices = [lookup[b & MAGNITUDE] * 2 + (b >> 31) for b in column]
        else:
            indices = [lookup[b & MAGNITUDE] for b in column]
        palette = _build_column(ordered)
        mapped = _build_column(indices)
        if _align(len(palette[0])) + _align(len(mapped[0])) < _align(len(direct[0])):
            mode = AXIS_SIGNED_PALETTE if negative else AXIS_PALETTE
            return mode, len(ordered), [palette, mapped]
    return AXIS_BITS, 0, [direct]


def _build_positions(bits: list, count: int) -> tuple:
    """Return (sections, modes, arguments, unique count, flags)."""
    direct_axes = [_build_axis(bits, axis, count, True) for axis in range(3)]
    best = [section for axis in direct_axes for section in axis[2]]
    axes = direct_axes
    unique = count
    flags = 0
    seen = {}
    rows = []
    marks = [False] * count
    repeat_ids = []
    for vertex in range(count):
        key = tuple(bits[vertex * 3 : vertex * 3 + 3])
        known = seen.get(key)
        if known is None:
            seen[key] = len(seen)
            rows.extend(key)
        else:
            marks[vertex] = True
            repeat_ids.append(known)
    if repeat_ids:
        layered_axes = [_build_axis(rows, axis, len(seen), True) for axis in range(3)]
        layered = [_build_rank_mask(marks), _build_column(repeat_ids)]
        layered += [section for axis in layered_axes for section in axis[2]]
        if _size_of(layered) < _size_of(best):
            best = layered
            axes = layered_axes
            unique = len(seen)
            flags = FLAG_REPEATS
    return best, [a[0] for a in axes], [a[1] for a in axes], unique, flags


def _build_corners(corners: list, faces: int, arity: int) -> tuple:
    chosen = [_build_column(corners)]
    mode = CORNERS_SINGLE
    if 3 <= arity <= MAX_RELATIVE_ARITY:
        relative = [_build_column(corners[0::arity])]
        for j in range(1, arity):
            relative.append(
                _build_column(
                    [corners[f * arity + j] - corners[f * arity] for f in range(faces)]
                )
            )
        if _size_of(relative) < _size_of(chosen):
            chosen = relative
            mode = CORNERS_RELATIVE
    return chosen, mode


def _build_face_starts(sizes: list, corner_count: int) -> tuple:
    words = [0] * ((corner_count + 63) // 64)
    selects = array("I")
    at = 0
    for face, size in enumerate(sizes):
        if face % FACES_PER_SELECT == 0:
            selects.append(at)
        words[at >> 6] |= 1 << (at & 63)
        at += size
    return _bytes(array("Q", words)) + _bytes(selects), False


def _pick_block_references(candidates: list) -> list:
    """An edge has a corner on each side; pick whichever sits nearest its block."""
    picked = [options[0] for options in candidates]
    for start in range(0, len(picked), BLOCK_SIZE):
        end = min(start + BLOCK_SIZE, len(picked))
        for _ in range(2):
            middle = sorted(picked[start:end])[(end - start) // 2]
            for i in range(start, end):
                best = picked[i]
                for corner in candidates[i]:
                    if abs(corner - middle) < abs(best - middle):
                        best = corner
                picked[i] = best
    return picked


def _build_edges(edges: list, corners: list, sizes: list) -> tuple:
    """Return (sections, mode, flags, loose count)."""
    count = len(edges) // 2
    chosen = [
        _build_column(edges[0::2]),
        _build_column([edges[i * 2 + 1] - edges[i * 2] for i in range(count)]),
    ]
    if not corners:
        return chosen, EDGES_ENDPOINTS, 0, 0
    following = [0] * len(corners)
    at = 0
    for size in sizes:
        for j in range(size):
            following[at + j] = at + (j + 1) % size
        at += size
    table = {}
    for corner, vertex in enumerate(corners):
        other = corners[following[corner]]
        key = (vertex, other) if vertex < other else (other, vertex)
        table.setdefault(key, []).append(corner)
    candidates = []
    marks = [False] * count
    loose_a = []
    loose_b = []
    canonical = True
    attached = []
    for edge in range(count):
        a, b = edges[edge * 2], edges[edge * 2 + 1]
        options = table.get((a, b) if a < b else (b, a))
        if options is None:
            marks[edge] = True
            loose_a.append(a)
            loose_b.append(b)
        else:
            candidates.append(options)
            attached.append(a)
            canonical = canonical and a <= b
    referenced = []
    if loose_a:
        referenced += [
            _build_rank_mask(marks),
            _build_column(loose_a),
            _build_column(loose_b),
        ]
    if candidates:
        first = [options[0] for options in candidates]
        nearest = _pick_block_references(candidates)
        first_column = _build_column(first)
        nearest_column = _build_column(nearest)
        use_first = len(first_column[0]) <= len(nearest_column[0])
        picked = first if use_first else nearest
        referenced.append(first_column if use_first else nearest_column)
        if not canonical:
            referenced.append(
                _build_column(
                    [
                        0 if corners[reference] == attached[i] else 1
                        for i, reference in enumerate(picked)
                    ]
                )
            )
    if _size_of(referenced) < _size_of(chosen):
        flags = (FLAG_LOOSE if loose_a else 0) | (FLAG_CANONICAL if canonical else 0)
        return referenced, EDGES_REFERENCE, flags, len(loose_a)
    return chosen, EDGES_ENDPOINTS, 0, 0


def encode(mesh: Mesh) -> bytes:
    """Encode validated buffers into the smallest layout this format allows."""
    mesh.validate()
    bits = array("I")
    bits.frombytes(mesh.positions.tobytes())
    if sys.byteorder != "little":
        bits.byteswap()
    bits = bits.tolist()
    corners = list(mesh.corners)
    edges = list(mesh.edges)
    faces = mesh.face_count
    sizes = [mesh.arity] * faces if mesh.arity else list(mesh.face_sizes)
    vertices = mesh.vertex_count
    if max(vertices, faces, len(corners), mesh.edge_count) > U32:
        raise FormatError("Mesh exceeds the file's 32-bit size limit")

    sections = []
    flags = 0
    modes = [0, 0, 0]
    arguments = [0, 0, 0]
    unique = 0
    if vertices:
        positions, modes, arguments, unique, flags = _build_positions(bits, vertices)
        sections += positions
    arity = 0
    corner_mode = CORNERS_SINGLE
    if corners:
        if sizes[0] <= MAX_UNIFORM_ARITY and all(size == sizes[0] for size in sizes):
            arity = sizes[0]
        columns, corner_mode = _build_corners(corners, faces, arity)
        sections += columns
        if not arity:
            sections.append(_build_face_starts(sizes, len(corners)))
    edge_mode = EDGES_ENDPOINTS
    loose = 0
    if edges:
        columns, edge_mode, edge_flags, loose = _build_edges(edges, corners, sizes)
        sections += columns
        flags |= edge_flags

    header = _HEADER.pack(
        MAGIC,
        vertices,
        faces,
        len(corners),
        mesh.edge_count,
        unique,
        *modes,
        corner_mode,
        edge_mode,
        arity,
        flags,
        *arguments,
        loose,
    )
    offset = HEADER_BYTES + len(sections) * 4
    table = array("I")
    body = bytearray()
    for data, has_slopes in sections:
        offset = _align(offset)
        body += bytes(offset - HEADER_BYTES - len(sections) * 4 - len(body))
        table.append(offset | (1 if has_slopes else 0))
        body += data
        offset += len(data)
    return header + _bytes(table) + bytes(body) + bytes(TAIL_BYTES)


class _Reader:
    """Walks the section table, rejecting any layout a writer could not produce."""

    def __init__(self, data: bytes, table_end: int):
        self.data = data
        self.table = HEADER_BYTES
        self.table_end = table_end
        self.at = table_end
        self.limit = len(data) - TAIL_BYTES

    def _entry(self) -> tuple:
        if self.table + 4 > self.table_end:
            raise FormatError("File section table is too short")
        entry = int.from_bytes(self.data[self.table : self.table + 4], "little")
        self.table += 4
        offset = entry & ~1
        if offset != _align(self.at):
            raise FormatError("File section is not where the table says")
        return offset, entry & 1

    def _take(self, offset: int, length: int) -> bytes:
        end = offset + length
        if end > self.limit:
            raise FormatError("File section runs past the end of the file")
        return self.data[offset:end]

    def column(self, count: int) -> list:
        offset, has_slopes = self._entry()
        blocks = (count + BLOCK_SIZE - 1) // BLOCK_SIZE
        groups = (blocks + BLOCKS_PER_GROUP - 1) // BLOCKS_PER_GROUP
        at = offset
        bases = array("I")
        bases.frombytes(self._take(at, blocks * 4))
        if sys.byteorder != "little":
            bases.byteswap()
        at += blocks * 4
        slopes = None
        if has_slopes:
            slopes = array("b")
            slopes.frombytes(self._take(at, blocks))
            at += blocks
        widths = self._take(at, blocks)
        at = _align(at + blocks)
        group_table = array("I")
        group_table.frombytes(self._take(at, groups * 4))
        if sys.byteorder != "little":
            group_table.byteswap()
        at += groups * 4
        values = []
        running = 0
        for block in range(blocks):
            width = widths[block]
            if width > MAX_WIDTH:
                raise FormatError("Column width is out of range")
            if block % BLOCKS_PER_GROUP == 0:
                if group_table[block // BLOCKS_PER_GROUP] != running:
                    raise FormatError("Column offsets do not add up")
            length = min(BLOCK_SIZE, count - block * BLOCK_SIZE)
            size = (length * width + 7) // 8
            packed = int.from_bytes(self._take(at + running, size), "little")
            running += size
            base = bases[block]
            slope = slopes[block] if slopes else 0
            mask = (1 << width) - 1
            values.extend(
                (base + slope * j + ((packed >> (j * width)) & mask)) & U32
                for j in range(length)
            )
        self.at = at + running
        return values

    def raw(self, length: int) -> bytes:
        offset, has_slopes = self._entry()
        if has_slopes:
            raise FormatError("File section has an invalid flag")
        result = self._take(offset, length)
        self.at = offset + length
        return result

    def rank_mask(self, count: int, clear_count: int) -> list:
        words = (count + 63) // 64
        data = self.raw(words * 12)
        masks = struct.unpack_from(f"<{words}Q", data)
        ranks = struct.unpack_from(f"<{words}I", data, words * 8)
        marks = []
        clear = 0
        for word in range(words):
            if ranks[word] != clear:
                raise FormatError("Rank mask does not add up")
            used = min(64, count - word * 64)
            if used < 64 and masks[word] >> used:
                raise FormatError("Rank mask has stray bits")
            for j in range(used):
                marked = bool(masks[word] >> j & 1)
                marks.append(marked)
                clear += not marked
        if clear != clear_count:
            raise FormatError("Rank mask does not match its count")
        return marks

    def finish(self) -> None:
        if self.table != self.table_end or self.at != self.limit:
            raise FormatError("File stream lengths do not match the header")


def _read_axis(reader: _Reader, mode: int, argument: int, count: int) -> list:
    if mode == AXIS_CONSTANT:
        return [argument] * count
    if mode == AXIS_BITS:
        return [_from_ordered(value) for value in reader.column(count)]
    if mode not in (AXIS_PALETTE, AXIS_SIGNED_PALETTE):
        raise FormatError("Unknown position layout")
    palette = reader.column(argument)
    indices = reader.column(count)
    try:
        if mode == AXIS_PALETTE:
            return [palette[index] for index in indices]
        return [palette[i >> 1] | ((i & 1) << 31) for i in indices]
    except IndexError as error:
        raise FormatError("Position palette index is out of range") from error


def _count_sections(modes, corner_mode, arity, corners, edges, edge_mode, flags, loose):
    total = 2 if flags & FLAG_REPEATS else 0
    for mode in modes:
        total += {AXIS_BITS: 1, AXIS_PALETTE: 2, AXIS_SIGNED_PALETTE: 2}.get(mode, 0)
    if corners:
        total += arity if corner_mode == CORNERS_RELATIVE else 1
        total += 0 if arity else 1
    if edges:
        if edge_mode == EDGES_ENDPOINTS:
            total += 2
        else:
            total += 3 if loose else 0
            if edges > loose:
                total += 1 if flags & FLAG_CANONICAL else 2
    return total


def decode(data: bytes, max_bytes: int) -> Mesh:
    """Expand a whole ZMD1 file into a validated Mesh, for editing tools."""
    if len(data) < HEADER_BYTES + TAIL_BYTES:
        raise FormatError("Packet is truncated")
    (
        magic,
        vertices,
        faces,
        corner_count,
        edge_count,
        unique,
        *modes,
        corner_mode,
        edge_mode,
        arity,
        flags,
        arg_x,
        arg_y,
        arg_z,
        loose,
    ) = _HEADER.unpack_from(data)
    if magic != MAGIC or flags & ~(FLAG_REPEATS | FLAG_LOOSE | FLAG_CANONICAL):
        raise FormatError("Unsupported ZPM mesh packet or version")
    if corner_mode > CORNERS_RELATIVE or edge_mode > EDGES_REFERENCE:
        raise FormatError("Unknown stream layout")
    if unique > vertices or loose > edge_count:
        raise FormatError("Invalid element counts")
    width = 2 if vertices <= 65536 else 4
    expanded = vertices * 12 + corner_count * width + edge_count * 2 * width + faces * 4
    if expanded > max_bytes:
        raise FormatError("Decoded mesh exceeds the configured size limit")
    if corner_count == 0 and faces:
        raise FormatError("Faces have no corners")
    if arity and arity * faces != corner_count:
        raise FormatError("Invalid uniform face layout")
    if corner_mode == CORNERS_RELATIVE and not 3 <= arity <= MAX_RELATIVE_ARITY:
        raise FormatError("Invalid uniform face layout")
    if not flags & FLAG_REPEATS and unique != vertices:
        raise FormatError("Invalid element counts")
    sections = _count_sections(
        modes if vertices else (),
        corner_mode,
        arity,
        corner_count,
        edge_count,
        edge_mode,
        flags,
        loose,
    )
    reader = _Reader(data, HEADER_BYTES + sections * 4)

    bits = []
    if vertices:
        marks = None
        if flags & FLAG_REPEATS:
            marks = reader.rank_mask(vertices, unique)
            repeat_ids = reader.column(vertices - unique)
        axes = [
            _read_axis(reader, mode, argument, unique)
            for mode, argument in zip(modes, (arg_x, arg_y, arg_z), strict=True)
        ]
        rows = list(zip(*axes, strict=True))
        if marks is None:
            bits = [value for row in rows for value in row]
        else:
            repeats = iter(repeat_ids)
            next_unique = 0
            try:
                for marked in marks:
                    if marked:
                        bits.extend(rows[next(repeats)])
                    else:
                        bits.extend(rows[next_unique])
                        next_unique += 1
            except IndexError as error:
                raise FormatError("Repeated position ID is out of range") from error

    corners = []
    sizes = []
    if corner_count:
        if corner_mode == CORNERS_RELATIVE:
            columns = [reader.column(faces) for _ in range(arity)]
            corners = [0] * corner_count
            corners[0::arity] = columns[0]
            for j in range(1, arity):
                corners[j::arity] = [
                    (first + delta) & U32
                    for first, delta in zip(columns[0], columns[j], strict=True)
                ]
        else:
            corners = reader.column(corner_count)
        if not arity:
            words = (corner_count + 63) // 64
            selects = (faces + FACES_PER_SELECT - 1) // FACES_PER_SELECT
            starts = reader.raw(words * 8 + selects * 4)
            packed = int.from_bytes(starts[: words * 8], "little")
            if packed >> corner_count or not packed & 1:
                raise FormatError("Face starts are invalid")
            positions = [i for i in range(corner_count) if packed >> i & 1]
            if len(positions) != faces:
                raise FormatError("Face starts do not match the face count")
            expected = positions[0::FACES_PER_SELECT]
            if list(struct.unpack_from(f"<{selects}I", starts, words * 8)) != expected:
                raise FormatError("Face select table is invalid")
            sizes = [
                end - start
                for start, end in zip(
                    positions, positions[1:] + [corner_count], strict=True
                )
            ]

    edges = []
    if edge_count:
        if edge_mode == EDGES_ENDPOINTS:
            first = reader.column(edge_count)
            delta = reader.column(edge_count)
            edges = [0] * (edge_count * 2)
            edges[0::2] = first
            edges[1::2] = [
                (a + d) & U32 for a, d in zip(first, delta, strict=True)
            ]
        else:
            edges = _read_reference_edges(
                reader, edge_count, loose, flags, corners, arity, sizes
            )
    reader.finish()

    positions_out = array("f")
    raw_bits = array("I", bits)
    if sys.byteorder != "little":
        raw_bits.byteswap()
    positions_out.frombytes(raw_bits.tobytes())
    code = "H" if width == 2 else "I"
    try:
        result = Mesh(
            positions_out,
            array("I", sizes),
            array(code, corners),
            array(code, edges),
            arity=arity,
        )
    except OverflowError as error:
        raise FormatError("Topology references a missing vertex") from error
    result.validate()
    return result


def _read_reference_edges(reader, edge_count, loose, flags, corners, arity, sizes):
    marks = None
    if flags & FLAG_LOOSE:
        marks = reader.rank_mask(edge_count, edge_count - loose)
        loose_a = reader.column(loose)
        loose_b = reader.column(loose)
    elif loose:
        raise FormatError("Loose edge count has no mask")
    attached = edge_count - loose
    if attached == 0:
        references = flips = []
    else:
        if not corners:
            raise FormatError("Edges reference corners that do not exist")
        references = reader.column(attached)
        flips = None if flags & FLAG_CANONICAL else reader.column(attached)
    following = [0] * len(corners)
    at = 0
    for size in [arity] * (len(corners) // arity) if arity else sizes:
        for j in range(size):
            following[at + j] = at + (j + 1) % size
        at += size
    edges = []
    next_attached = 0
    next_loose = 0
    for edge in range(edge_count):
        if marks is not None and marks[edge]:
            edges += (loose_a[next_loose], loose_b[next_loose])
            next_loose += 1
            continue
        reference = references[next_attached]
        if reference >= len(corners):
            raise FormatError("Edge references a missing corner")
        a, b = corners[reference], corners[following[reference]]
        flipped = a > b if flips is None else flips[next_attached] != 0
        edges += (b, a) if flipped else (a, b)
        next_attached += 1
    return edges
