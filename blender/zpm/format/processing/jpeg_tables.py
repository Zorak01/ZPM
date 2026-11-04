# SPDX-License-Identifier: GPL-3.0-or-later

"""Baseline JPEGs re-coded with Huffman tables made for their own data.

Many JPEGs use the example tables from the standard. Re-coding the same symbols with
tables built from their counts, as the standard's Annex K.2 describes, gives a
smaller JPEG with exactly the same coefficients, so every decoder shows the same
pixels. Only a single-scan baseline JPEG with no restart markers is re-coded; anything
else, or anything that does not read back to the same symbols, is returned as given.
"""

import struct
from array import array
from typing import NamedTuple

_MARKER = 0xFF
_START_OF_IMAGE = b"\xff\xd8"
_BASELINE_FRAMES = (0xC0, 0xC1)
_HUFFMAN_TABLES = 0xC4
_START_OF_SCAN = 0xDA
_RESTART_INTERVAL = 0xDD
# Every other frame type: progressive, lossless, hierarchical or arithmetic coded.
_OTHER_FRAMES = frozenset(range(0xC2, 0xD0)) - {_HUFFMAN_TABLES, 0xC8, 0xCC}
_STANDALONE = frozenset({0x01, *range(0xD0, 0xD8)})
_SEGMENT_LENGTH = struct.Struct(">H")
_FRAME_SIZE = struct.Struct(">BHHB")
_COEFFICIENTS = 64
_BLOCK = 8
_LONGEST_CODE = 16
_SYMBOLS = 256
_SIXTEEN_ZEROS = 0xF0
_DC_CLASS = 0
_AC_CLASS = 1
_ONE_BITS = 0xFF
_BYTE_BITS = 8
# Bytes past the scan a 16-bit read may touch.
_PEEK_BYTES = 3
_WINDOW_MASK = (1 << 16) - 1


class _Symbols(NamedTuple):
    """Every coded symbol in order: its table as class * 16 + id, its value, and the
    raw bits after it, in flat arrays so a large image stays small in memory."""

    tables: array
    values: array
    extra_counts: array
    extras: array

    @classmethod
    def empty(cls) -> "_Symbols":
        return cls(array("B"), array("B"), array("B"), array("I"))


class _Jpeg(NamedTuple):
    head: list
    frame: list
    scan: list
    scan_segment: bytes
    entropy_start: int
    tables: dict
    size: tuple


def optimize_tables(data: bytes) -> bytes:
    """Return the JPEG re-coded with its own optimal Huffman tables when that is
    smaller and reads back to the same symbols, else data unchanged."""
    try:
        jpeg = _parse(data)
        if jpeg is None:
            return data
        symbols, end = _decode(data, jpeg)
        counts = _all_counts(symbols)
        tables = {
            key: _table_for(counts[_table_number(key)])
            for key in jpeg.tables
            if _table_number(key) in counts
        }
        recoded = _write(data, jpeg, tables, symbols, end)
        again = _parse(recoded)
        same = again is not None and _decode(recoded, again)[0] == symbols
    except (IndexError, KeyError, ValueError, struct.error):
        return data
    return recoded if same and len(recoded) < len(data) else data


def _parse(data: bytes) -> _Jpeg | None:
    """Return the parts before the entropy-coded data, or None for a JPEG this cannot
    re-code."""
    if not data.startswith(_START_OF_IMAGE):
        return None
    head, tables, frame, size = [], {}, None, None
    at = len(_START_OF_IMAGE)
    while True:
        if data[at] != _MARKER:
            return None
        marker = data[at + 1]
        if marker in _STANDALONE:
            at += 2
            continue
        (length,) = _SEGMENT_LENGTH.unpack_from(data, at + 2)
        body = data[at + 4 : at + 2 + length]
        segment = data[at : at + 2 + length]
        if marker in _OTHER_FRAMES or marker == _RESTART_INTERVAL:
            return None
        if marker == _HUFFMAN_TABLES:
            tables.update(_read_tables(body))
        else:
            head.append(segment)
        if marker in _BASELINE_FRAMES:
            _, height, width, count = _FRAME_SIZE.unpack_from(body)
            frame = [(body[6 + 3 * part], body[7 + 3 * part]) for part in range(count)]
            size = (width, height)
        if marker == _START_OF_SCAN:
            head.pop()
            scan = [(body[1 + 2 * part], body[2 + 2 * part]) for part in range(body[0])]
            if frame is None or len(scan) != len(frame):
                return None
            return _Jpeg(head, frame, scan, segment, at + 2 + length, tables, size)
        at += 2 + length


def _read_tables(body: bytes) -> dict:
    """Return each table in a DHT segment as {(class, id): {(length, code): value}}."""
    tables = {}
    at = 0
    while at < len(body):
        kind = body[at]
        lengths = body[at + 1 : at + 1 + _LONGEST_CODE]
        values = body[at + 1 + _LONGEST_CODE : at + 1 + _LONGEST_CODE + sum(lengths)]
        codes, code, index = {}, 0, 0
        for length in range(1, _LONGEST_CODE + 1):
            for _ in range(lengths[length - 1]):
                codes[length, code] = values[index]
                code += 1
                index += 1
            code <<= 1
        tables[kind >> 4, kind & 15] = codes
        at += 1 + _LONGEST_CODE + sum(lengths)
    return tables


def _scan_end(data: bytes, start: int) -> int:
    """Return where the entropy-coded data ends: at the first marker after start."""
    at = start
    while True:
        at = data.index(_MARKER, at)
        if data[at + 1]:
            return at
        at += 2


def _lookups(tables: dict) -> dict:
    """Return, for each table, what every 16-bit window starts with: its code length
    << 8 | its value, or 0 where no code starts it."""
    lookups = {}
    window = 1 << _LONGEST_CODE
    for key, codes in tables.items():
        lookup = [0] * window
        for (length, code), value in codes.items():
            first = code << (_LONGEST_CODE - length)
            span = 1 << (_LONGEST_CODE - length)
            lookup[first : first + span] = [length << _BYTE_BITS | value] * span
        lookups[_table_number(key)] = lookup
    return lookups


def _decode(data: bytes, jpeg: _Jpeg) -> tuple:
    """Return (every symbol in order, where the entropy-coded data ends)."""
    end = _scan_end(data, jpeg.entropy_start)
    stream = data[jpeg.entropy_start : end].replace(b"\xff\x00", b"\xff")
    limit = len(stream) * _BYTE_BITS
    stream += bytes((_ONE_BITS,)) * _PEEK_BYTES
    lookups = _lookups(jpeg.tables)
    sampling = dict(jpeg.frame)
    wide = max(factors >> 4 for factors in sampling.values())
    tall = max(factors & 15 for factors in sampling.values())
    across = -(-jpeg.size[0] // (_BLOCK * wide))
    down = -(-jpeg.size[1] // (_BLOCK * tall))
    units = []
    for component, selectors in jpeg.scan:
        factors = sampling[component]
        dc = _table_number((_DC_CLASS, selectors >> 4))
        ac = _table_number((_AC_CLASS, selectors & 15))
        units += [(dc, ac)] * ((factors >> 4) * (factors & 15))
    symbols = _Symbols.empty()
    adds = tuple(column.append for column in symbols)
    position = 0
    for _ in range(across * down):
        for dc, ac in units:
            position = _decode_block(stream, position, lookups, (dc, ac), adds)
    if position > limit:
        raise ValueError("The scan ends before its blocks do")
    return symbols, end


def _decode_block(stream, position, lookups, tables, adds) -> int:
    """Read one block's symbols from a bit position; return the position after.

    Runs once per block, so the 16-bit reads are written out here rather than called.
    """
    add_table, add_value, add_size, add_extra = adds
    place = 0
    for table in tables:
        lookup = lookups[table]
        while place < _COEFFICIENTS:
            at = position >> 3
            window = stream[at] << 16 | stream[at + 1] << 8 | stream[at + 2]
            entry = lookup[window >> (_BYTE_BITS - (position & 7)) & _WINDOW_MASK]
            if not entry:
                raise ValueError("A code no table holds")
            position += entry >> _BYTE_BITS
            value = entry & _ONE_BITS
            size = value if not place else value & 15
            extra = 0
            if size:
                at = position >> 3
                window = stream[at] << 16 | stream[at + 1] << 8 | stream[at + 2]
                window = window >> (_BYTE_BITS - (position & 7)) & _WINDOW_MASK
                extra = window >> (_LONGEST_CODE - size)
                position += size
            add_table(table)
            add_value(value)
            add_size(size)
            add_extra(extra)
            if not place:
                place = 1
                break
            if not size and value != _SIXTEEN_ZEROS:
                return position
            place += (value >> 4) + 1
    if place > _COEFFICIENTS:
        raise ValueError("A block runs past 64 coefficients")
    return position


def _table_number(key: tuple) -> int:
    return key[0] << 4 | key[1]


def _all_counts(symbols: _Symbols) -> dict:
    """Return each table's count of each value."""
    counts = {}
    for table, value in zip(symbols.tables, symbols.values, strict=True):
        found = counts.setdefault(table, {})
        found[value] = found.get(value, 0) + 1
    return counts


def _table_for(counts: dict) -> dict:
    """Return {value: (length, code)}, built as the standard's Annex K.2 builds it."""
    if not counts:
        return {}
    frequency = [0] * (_SYMBOLS + 1)
    for value, count in counts.items():
        frequency[value] = count
    # A reserved symbol keeps any code from being all ones.
    frequency[_SYMBOLS] = 1
    sizes = [0] * (_SYMBOLS + 1)
    others = [-1] * (_SYMBOLS + 1)
    while True:
        live = [at for at in range(_SYMBOLS + 1) if frequency[at]]
        if len(live) < 2:
            break
        first = min(live, key=lambda at: (frequency[at], -at))
        second = min(
            (at for at in live if at != first), key=lambda at: (frequency[at], -at)
        )
        frequency[first] += frequency[second]
        frequency[second] = 0
        for start in (first, second):
            at = start
            sizes[at] += 1
            while others[at] >= 0:
                at = others[at]
                sizes[at] += 1
            if start == first:
                others[at] = second
    return _assign_codes(_limited(sizes), counts)


def _limited(sizes: list) -> list:
    """Return how many codes have each length, no code longer than 16, the reserved
    symbol's code removed."""
    counts = [0] * (max(max(sizes), _LONGEST_CODE) + 1)
    for size in sizes:
        if size:
            counts[size] += 1
    for length in range(len(counts) - 1, _LONGEST_CODE, -1):
        while counts[length]:
            shorter = length - 2
            while not counts[shorter]:
                shorter -= 1
            counts[length] -= 2
            counts[length - 1] += 1
            counts[shorter + 1] += 2
            counts[shorter] -= 1
    longest = _LONGEST_CODE
    while not counts[longest]:
        longest -= 1
    counts[longest] -= 1
    return counts[: _LONGEST_CODE + 1]


def _assign_codes(counts: list, frequencies: dict) -> dict:
    order = sorted(frequencies, key=lambda value: (-frequencies[value], value))
    codes, code, index = {}, 0, 0
    for length in range(1, _LONGEST_CODE + 1):
        for _ in range(counts[length]):
            codes[order[index]] = (length, code)
            code += 1
            index += 1
        code <<= 1
    return codes


def _table_segment(tables: dict) -> bytes:
    body = bytearray()
    for (kind, number), codes in sorted(tables.items()):
        lengths = [0] * _LONGEST_CODE
        for length, _ in codes.values():
            lengths[length - 1] += 1
        values = sorted(codes, key=lambda value: codes[value])
        body.append(kind << 4 | number)
        body += bytes(lengths) + bytes(values)
    return (
        bytes((_MARKER, _HUFFMAN_TABLES)) + _SEGMENT_LENGTH.pack(len(body) + 2) + body
    )


def _write(
    data: bytes, jpeg: _Jpeg, tables: dict, symbols: _Symbols, end: int
) -> bytes:
    codes = {}
    for key, table in tables.items():
        row = [(0, 0)] * _SYMBOLS
        for value, length_and_code in table.items():
            row[value] = length_and_code
        codes[_table_number(key)] = row
    out = bytearray()
    buffer = 0
    count = 0
    for table, value, size, extra in zip(*symbols, strict=True):
        length, code = codes[table][value]
        buffer = buffer << (length + size) | code << size | extra
        count += length + size
        while count >= _BYTE_BITS:
            count -= _BYTE_BITS
            byte = buffer >> count & _ONE_BITS
            out.append(byte)
            if byte == _MARKER:
                out.append(0)
        buffer &= (1 << count) - 1
    if count:
        fill = _BYTE_BITS - count
        byte = (buffer << fill | (1 << fill) - 1) & _ONE_BITS
        out.append(byte)
        if byte == _MARKER:
            out.append(0)
    return b"".join(
        (
            _START_OF_IMAGE,
            *jpeg.head,
            _table_segment(tables),
            jpeg.scan_segment,
            bytes(out),
            data[end:],
        )
    )
