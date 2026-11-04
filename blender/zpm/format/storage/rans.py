# SPDX-License-Identifier: GPL-3.0-or-later

"""Static-table rANS: fast entropy coding for streams that are read start to end.

Each context holds its own table of symbol frequencies, summing to 1024. A number is
stored as its zigzag bit length, as a symbol, then the bits below its top bit, raw. In a
fine stream a number of two bits or more also puts its second bit in the symbol: 2 × its
length − 2 + that bit, then the bits below it, raw. Only
the lengths cost a table lookup, so decoding stays fast. The encoder works from the end
of the stream, so a reader takes symbols in the order they were written.

    State (UInt32), then word count and raw byte count as varints
    Tables, bit-packed low bits first, padded to a byte
    Words (UInt16), then raw bytes

A varint is 7 bits per byte, low first, the top bit set on all but the last. Each
context's table is its entry count, then, if any, a 4-bit k, then per entry the
symbol's gap from the one before (the first from -1) less one, then all but the last
entry's frequency less one as an order-k Exp-Golomb code; the last takes what is left.
Counts and gaps are order-0 codes. An order-k code of v is as many clear bits as
v + 2^k has bits past k + 1, a set bit, then its bits below the top one, low first.
"""

import struct

from ..errors import FormatError

_PROBABILITY_BITS = 10
_TOTAL = 1 << _PROBABILITY_BITS
_SLOT_MASK = _TOTAL - 1
_LOWEST_STATE = 1 << 16
_WORD_BITS = 16
_WORD_MASK = (1 << _WORD_BITS) - 1
_STATE = struct.Struct("<I")
_MAX_SYMBOL = 255
_MAX_K = 15
_K_BITS = 4


def _varint(value: int) -> bytes:
    out = bytearray()
    while value >= 0x80:
        out.append(value & 0x7F | 0x80)
        value >>= 7
    out.append(value)
    return bytes(out)


def _read_varint(data: bytes, at: int) -> tuple:
    value = shift = 0
    while True:
        if at >= len(data) or shift > 28:
            raise FormatError("A packed stream is truncated")
        byte = data[at]
        at += 1
        value |= (byte & 0x7F) << shift
        shift += 7
        if byte < 0x80:
            return value, at


class _BitWriter:
    def __init__(self):
        self.out = bytearray()
        self.pending = 0
        self.count = 0

    def bits(self, value: int, count: int) -> None:
        self.pending |= value << self.count
        self.count += count
        while self.count >= 8:
            self.out.append(self.pending & 255)
            self.pending >>= 8
            self.count -= 8

    def golomb(self, value: int, k: int) -> None:
        stored = value + (1 << k)
        top = stored.bit_length() - 1
        self.bits(1 << (top - k), top - k + 1)
        self.bits(stored & ((1 << top) - 1), top)

    def to_bytes(self) -> bytes:
        if self.count:
            self.out.append(self.pending & 255)
        return bytes(self.out)


def _golomb_bits(value: int, k: int) -> int:
    top = (value + (1 << k)).bit_length() - 1
    return 2 * top - k + 1


class _BitReader:
    def __init__(self, data: bytes, at: int):
        self.data = data
        self.at = at
        self.pending = 0
        self.count = 0

    def bits(self, count: int) -> int:
        while self.count < count:
            if self.at >= len(self.data):
                raise FormatError("A packed table is truncated")
            self.pending |= self.data[self.at] << self.count
            self.at += 1
            self.count += 8
        value = self.pending & ((1 << count) - 1)
        self.pending >>= count
        self.count -= count
        return value

    def golomb(self, k: int) -> int:
        zeros = 0
        while not self.bits(1):
            zeros += 1
            if zeros > 24:
                raise FormatError("A packed table has a bad code")
        top = zeros + k
        return ((1 << top) | self.bits(top)) - (1 << k)


def _write_table(bits: _BitWriter, table: dict) -> None:
    symbols = sorted(table)
    bits.golomb(len(symbols), 0)
    if not symbols:
        return
    stored = [table[symbol] - 1 for symbol in symbols[:-1]]
    k = min(
        range(_MAX_K + 1),
        key=lambda k: sum(_golomb_bits(value, k) for value in stored),
    )
    bits.bits(k, _K_BITS)
    previous = -1
    for symbol in symbols:
        bits.golomb(symbol - previous - 1, 0)
        previous = symbol
    for value in stored:
        bits.golomb(value, k)


def _read_table(bits: _BitReader) -> list | None:
    """Return the table's slots, each (symbol, frequency, its first slot), or None."""
    count = bits.golomb(0)
    if not count:
        return None
    if count > _MAX_SYMBOL + 1:
        raise FormatError("A packed table has too many entries")
    k = bits.bits(_K_BITS)
    symbols = []
    previous = -1
    for _ in range(count):
        previous += bits.golomb(0) + 1
        if previous > _MAX_SYMBOL:
            raise FormatError("A packed table names a symbol out of range")
        symbols.append(previous)
    frequencies = [bits.golomb(k) + 1 for _ in range(count - 1)]
    frequencies.append(_TOTAL - sum(frequencies))
    if frequencies[-1] < 1:
        raise FormatError("A packed table does not add up")
    slots = []
    for symbol, frequency in zip(symbols, frequencies, strict=True):
        start = len(slots)
        slots += [(symbol, frequency, start)] * frequency
    return slots


def _to_zigzag(value: int) -> int:
    return value * 2 if value >= 0 else -value * 2 - 1


def _from_zigzag(stored: int) -> int:
    return stored >> 1 if stored & 1 == 0 else -(stored >> 1) - 1


def _frequencies(counts: dict) -> dict:
    """Return frequencies summing to the table's total, none of them zero."""
    total = sum(counts.values())
    scaled = {
        symbol: max(1, count * _TOTAL // total) for symbol, count in counts.items()
    }
    while sum(scaled.values()) > _TOTAL:
        scaled[max(scaled, key=scaled.get)] -= 1
    scaled[max(scaled, key=scaled.get)] += _TOTAL - sum(scaled.values())
    return scaled


class SymbolWriter:
    """Collects symbols and numbers, each under a context from 0 to context_count."""

    def __init__(self, context_count: int, *, fine: bool = False):
        self._fine = fine
        self._counts = [{} for _ in range(context_count)]
        self._symbols = []
        self._raw = bytearray()
        self._pending = 0
        self._pending_bits = 0

    def write(self, context: int, symbol: int) -> None:
        if not 0 <= symbol <= _MAX_SYMBOL:
            raise FormatError("A symbol is out of range")
        counts = self._counts[context]
        counts[symbol] = counts.get(symbol, 0) + 1
        self._symbols.append((context, symbol))

    def write_number(self, context: int, value: int) -> None:
        stored = _to_zigzag(value)
        length = stored.bit_length()
        if self._fine and length > 1:
            second = (stored >> (length - 2)) & 1
            self.write(context, 2 * length - 2 + second)
            self._write_raw(stored, length - 2)
            return
        self.write(context, length)
        if length > 1:
            self._write_raw(stored, length - 1)

    def _write_raw(self, value: int, count: int) -> None:
        self._pending |= (value & ((1 << count) - 1)) << self._pending_bits
        self._pending_bits += count
        while self._pending_bits >= 8:
            self._raw.append(self._pending & 255)
            self._pending >>= 8
            self._pending_bits -= 8

    def to_bytes(self) -> bytes:
        tables = [_frequencies(counts) if counts else {} for counts in self._counts]
        starts = []
        table_bits = _BitWriter()
        for table in tables:
            at = 0
            start = {}
            for symbol in sorted(table):
                start[symbol] = at
                at += table[symbol]
            starts.append(start)
            _write_table(table_bits, table)
        state = _LOWEST_STATE
        words = []
        limit_factor = (_LOWEST_STATE >> _PROBABILITY_BITS) << _WORD_BITS
        for context, symbol in reversed(self._symbols):
            frequency = tables[context][symbol]
            while state >= limit_factor * frequency:
                words.append(state & _WORD_MASK)
                state >>= _WORD_BITS
            state = (
                ((state // frequency) << _PROBABILITY_BITS)
                + state % frequency
                + starts[context][symbol]
            )
        words.reverse()
        raw = bytes(self._raw)
        if self._pending_bits:
            raw += bytes([self._pending & 255])
        return (
            _STATE.pack(state)
            + _varint(len(words))
            + _varint(len(raw))
            + table_bits.to_bytes()
            + struct.pack(f"<{len(words)}H", *words)
            + raw
        )


class SymbolReader:
    """Takes symbols and numbers back. Raises FormatError if they run out."""

    def __init__(self, data: bytes, context_count: int, *, fine: bool = False):
        self._fine = fine
        if len(data) < _STATE.size:
            raise FormatError("A packed stream is truncated")
        (self._state,) = _STATE.unpack_from(data)
        word_count, at = _read_varint(data, _STATE.size)
        raw_bytes, at = _read_varint(data, at)
        bits = _BitReader(data, at)
        self._slots = [_read_table(bits) for _ in range(context_count)]
        at = bits.at
        if at + word_count * 2 + raw_bytes > len(data):
            raise FormatError("A packed stream is truncated")
        self._words = struct.unpack_from(f"<{word_count}H", data, at)
        self._next_word = 0
        raw_start = at + word_count * 2
        self._raw = data[raw_start : raw_start + raw_bytes]
        self._next_raw = 0
        self._pending = 0
        self._pending_bits = 0

    def read(self, context: int) -> int:
        slots = self._slots[context]
        if slots is None:
            raise FormatError("A packed stream uses an empty table")
        slot = self._state & _SLOT_MASK
        symbol, frequency, start = slots[slot]
        state = frequency * (self._state >> _PROBABILITY_BITS) + slot - start
        if state < _LOWEST_STATE:
            if self._next_word >= len(self._words):
                raise FormatError("A packed stream ran out of words")
            state = (state << _WORD_BITS) | self._words[self._next_word]
            self._next_word += 1
        self._state = state
        return symbol

    def read_number(self, context: int) -> int:
        symbol = self.read(context)
        if symbol <= 1:
            return _from_zigzag(symbol)
        if self._fine:
            length = (symbol + 2) // 2
            extra = length - 2
            top = (2 | symbol & 1) << extra
        else:
            extra = symbol - 1
            top = 1 << extra
        return _from_zigzag(top | self._read_raw(extra))

    def _read_raw(self, count: int) -> int:
        while self._pending_bits < count:
            if self._next_raw >= len(self._raw):
                raise FormatError("A packed stream ran out of bits")
            self._pending |= self._raw[self._next_raw] << self._pending_bits
            self._next_raw += 1
            self._pending_bits += 8
        low = self._pending & ((1 << count) - 1)
        self._pending >>= count
        self._pending_bits -= count
        return low
