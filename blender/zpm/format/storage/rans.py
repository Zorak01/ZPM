# SPDX-License-Identifier: GPL-3.0-or-later

"""Static-table rANS: fast entropy coding for streams that are read start to end.

Each context holds its own table of symbol frequencies, summing to 1024. A number is
stored as its zigzag bit length, as a symbol, then the bits below its top bit, raw. Only
the lengths cost a table lookup, so decoding stays fast. The encoder works from the end
of the stream, so a reader takes symbols in the order they were written.

    State (UInt32), word count, raw byte count (UInt32 each)
    Per context: entry count (Byte), then symbol (Byte) and frequency (UInt16) per entry
    Words (UInt16), then raw bytes
"""

import struct

from ..errors import FormatError

_PROBABILITY_BITS = 10
_TOTAL = 1 << _PROBABILITY_BITS
_SLOT_MASK = _TOTAL - 1
_LOWEST_STATE = 1 << 16
_WORD_BITS = 16
_WORD_MASK = (1 << _WORD_BITS) - 1
_HEAD = struct.Struct("<III")
_ENTRY = struct.Struct("<BH")
_MAX_SYMBOL = 255


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

    def __init__(self, context_count: int):
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
        self.write(context, length)
        if length > 1:
            self._pending |= (stored & ((1 << (length - 1)) - 1)) << self._pending_bits
            self._pending_bits += length - 1
            while self._pending_bits >= 8:
                self._raw.append(self._pending & 255)
                self._pending >>= 8
                self._pending_bits -= 8

    def to_bytes(self) -> bytes:
        tables = [_frequencies(counts) if counts else {} for counts in self._counts]
        starts = []
        table_bytes = bytearray()
        for table in tables:
            at = 0
            start = {}
            table_bytes.append(len(table))
            for symbol in sorted(table):
                start[symbol] = at
                at += table[symbol]
                table_bytes += _ENTRY.pack(symbol, table[symbol])
            starts.append(start)
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
            _HEAD.pack(state, len(words), len(raw))
            + bytes(table_bytes)
            + struct.pack(f"<{len(words)}H", *words)
            + raw
        )


class SymbolReader:
    """Takes symbols and numbers back. Raises FormatError if they run out."""

    def __init__(self, data: bytes, context_count: int):
        if len(data) < _HEAD.size:
            raise FormatError("A packed stream is truncated")
        self._state, word_count, raw_bytes = _HEAD.unpack_from(data)
        at = _HEAD.size
        self._slots = []
        for _ in range(context_count):
            if at >= len(data):
                raise FormatError("A packed stream is truncated")
            entries = data[at]
            at += 1
            slots = None
            if entries:
                slots = []
                for _ in range(entries):
                    if at + _ENTRY.size > len(data):
                        raise FormatError("A packed stream is truncated")
                    symbol, frequency = _ENTRY.unpack_from(data, at)
                    at += _ENTRY.size
                    start = len(slots)
                    slots += [(symbol, frequency, start)] * frequency
                if len(slots) != _TOTAL:
                    raise FormatError("A packed table does not add up")
            self._slots.append(slots)
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
        length = self.read(context)
        if length <= 1:
            return _from_zigzag(length)
        extra = length - 1
        while self._pending_bits < extra:
            if self._next_raw >= len(self._raw):
                raise FormatError("A packed stream ran out of bits")
            self._pending |= self._raw[self._next_raw] << self._pending_bits
            self._next_raw += 1
            self._pending_bits += 8
        low = self._pending & ((1 << extra) - 1)
        self._pending >>= extra
        self._pending_bits -= extra
        return _from_zigzag(1 << extra | low)
