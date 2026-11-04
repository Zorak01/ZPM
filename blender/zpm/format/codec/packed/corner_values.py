# SPDX-License-Identifier: GPL-3.0-or-later

"""What normals and UVs share: looking up values already seen, and keying pairs.

Both are a pair of integers per corner. A corner first looks for its value among
those already seen; only a new value is predicted and its residuals written.
"""

from collections import Counter
from enum import IntEnum
from typing import NamedTuple

from ...storage.rans import SymbolCounter, SymbolWriter
from .layout import Layout

# A lookup's choice context counts the values seen, up to this.
MAX_SEEN_CONTEXT = 2
# Contexts a pair's second part takes from its first part's size.
PAIR_KINDS = 8


class Lookup(IntEnum):
    """Where a corner looks for values already seen."""

    POSITION = 0
    VERTEX = 1
    # Its vertex's if it has any, else its position's. On meshes split at seams a
    # vertex has one value, so repeats are nearly free without repeating the seams
    # splits already hold.
    VERTEX_THEN_POSITION = 2


def lookup_contexts(lookup: Lookup) -> int:
    """Return the extra choice contexts a lookup needs past a stream's own."""
    return MAX_SEEN_CONTEXT + 1 if lookup == Lookup.VERTEX_THEN_POSITION else 0


class ChoiceStep(NamedTuple):
    """A lookup's choice, as recorded once for every number form."""

    context: int
    choice: int

    def write(self, writer: SymbolWriter, paired: bool) -> None:
        writer.write_choice(self.context, self.choice)

    def count(self, counter: SymbolCounter, paired: bool, times: int) -> None:
        counter.add_choice(self.context, self.choice, times)


class SymbolStep(NamedTuple):
    """A plain symbol, as recorded once for every number form."""

    context: int
    symbol: int

    def write(self, writer: SymbolWriter, paired: bool) -> None:
        writer.write(self.context, self.symbol)

    def count(self, counter: SymbolCounter, paired: bool, times: int) -> None:
        counter.add(self.context, self.symbol, times)


class NumberStep(NamedTuple):
    """A residual with its context as written plainly and as written in pairs."""

    plain: int
    paired: int
    value: int

    def write(self, writer: SymbolWriter, paired: bool) -> None:
        writer.write_number(self.paired if paired else self.plain, self.value)

    def count(self, counter: SymbolCounter, paired: bool, times: int) -> None:
        counter.add_number(self.paired if paired else self.plain, self.value, times)


def write_steps(steps: list, contexts: int, fine: bool, paired: bool) -> bytes:
    """Return a stream holding recorded steps, in one number form."""
    writer = SymbolWriter(contexts, fine=fine)
    for step in steps:
        step.write(writer, paired)
    return writer.to_bytes()


def step_counts(steps: list) -> Counter:
    """Return how many times each distinct step occurs, for estimating forms."""
    return Counter(steps)


def estimated_bytes(counts: Counter, contexts: int, fine: bool, paired: bool) -> float:
    """Return about how many bytes write_steps would return for these steps."""
    counter = SymbolCounter(contexts, fine=fine)
    for step, times in counts.items():
        step.count(counter, paired, times)
    return counter.estimated_bytes()


def pair_context(first_context: int, part: int, history: list, plain: int) -> int:
    """Return the context of a paired value's part: the second keys on the size of the
    first, from first_context up; the first keeps plain."""
    if part:
        return first_context + min(history[0] // 2, PAIR_KINDS - 1)
    return plain


class SeenLookup:
    """The values already seen where each corner looks.

    vertex_contexts is where a VERTEX_THEN_POSITION lookup's vertex choices start.
    """

    def __init__(
        self, layout: Layout, corners: list, lookup: Lookup, vertex_contexts: int
    ):
        self._welded = layout.welded
        self._corners = corners
        self._lookup = lookup
        self._vertex_contexts = vertex_contexts
        self._by_position = {}
        self._by_vertex = {}

    def options(self, corner: int) -> tuple:
        """Return (context, the values this corner may name); the list may be empty."""
        position = self._by_position.setdefault(self._welded[corner], [])
        vertex = self._by_vertex.setdefault(self._corners[corner], [])
        if self._lookup == Lookup.VERTEX:
            return min(len(vertex), MAX_SEEN_CONTEXT), vertex
        if self._lookup == Lookup.VERTEX_THEN_POSITION and vertex:
            return min(len(vertex), MAX_SEEN_CONTEXT) + self._vertex_contexts, vertex
        return min(len(position), MAX_SEEN_CONTEXT), position

    def record(self, corner: int, value: tuple) -> None:
        """Add a corner's value to each list it is not yet in."""
        for seen in (
            self._by_position[self._welded[corner]],
            self._by_vertex[self._corners[corner]],
        ):
            if value not in seen:
                seen.append(value)
