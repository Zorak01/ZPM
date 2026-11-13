# SPDX-License-Identifier: GPL-3.0-or-later

"""Skin weights as one stream, vertex by vertex in packed order.

Neighbouring vertices share rows, so a vertex first names a row among the last few
seen. A new row names its groups among those used lately, and each weight but the last,
which the others leave, as a change from a guess: the group's last weight, kept between
an even share of what is left and what the row still allows. Weights are whole steps
of 1 / WEIGHT_TOTAL, adding up to exactly WEIGHT_TOTAL in every row.
"""

from enum import IntEnum
from typing import NamedTuple

from ...container.layout import WEIGHT_TOTAL
from ...errors import FormatError
from ...storage.rans import SymbolReader, SymbolWriter

# Rows a vertex may name again, most recent first.
_RECENT_ROWS = 32


class _Context(IntEnum):
    ROW = 0
    SIZE = 1
    RECENT_GROUP = 2
    NEW_GROUP = 3
    FIRST_WEIGHT = 4
    LATER_WEIGHT = 5


class WeightRows(NamedTuple):
    """Each vertex's weights: offsets into groups and steps, plus a final total."""

    offsets: list
    groups: list
    steps: list


def encode_weights(rows: WeightRows) -> bytes:
    """Return the stream for rows whose steps add up to WEIGHT_TOTAL each."""
    writer = SymbolWriter(len(_Context))
    state = _State()
    for vertex in range(len(rows.offsets) - 1):
        start, end = rows.offsets[vertex], rows.offsets[vertex + 1]
        row = (tuple(rows.groups[start:end]), tuple(rows.steps[start:end]))
        if row in state.recent:
            writer.write_choice(_Context.ROW, state.recent.index(row) + 1)
        else:
            writer.write_choice(_Context.ROW, 0)
            _write_row(writer, state, row)
        state.use(row)
    return writer.to_bytes()


def read_weights(data: bytes, vertex_count: int, group_count: int) -> WeightRows:
    """Return the rows of vertex_count vertices over group_count groups. Raises
    FormatError for a damaged stream."""
    reader = SymbolReader(data, len(_Context))
    state = _State()
    offsets, groups, steps = [0], [], []
    for _ in range(vertex_count):
        at = reader.read_choice(_Context.ROW)
        if at > len(state.recent):
            raise FormatError("Packed weights name a missing row")
        row = state.recent[at - 1] if at else _read_row(reader, state, group_count)
        state.use(row)
        groups += row[0]
        steps += row[1]
        offsets.append(len(steps))
    return WeightRows(offsets, groups, steps)


class _State:
    """What writer and reader both remember: recent rows, groups and weights."""

    def __init__(self):
        self.recent = []
        self.groups = []
        self.last_step = {}

    def use(self, row: tuple) -> None:
        if row in self.recent:
            self.recent.remove(row)
        self.recent.insert(0, row)
        del self.recent[_RECENT_ROWS:]

    def use_group(self, group: int, step: int) -> None:
        if group in self.groups:
            self.groups.remove(group)
        self.groups.insert(0, group)
        self.last_step[group] = step

    def guess(self, group: int, place: int, size: int, written: list) -> int:
        """Return the guessed step of a row's weight at place, after those written."""
        left = WEIGHT_TOTAL - sum(written)
        ceiling = min(left, written[-1]) if written else left
        share = -(-left // (size - place))
        return min(max(self.last_step.get(group, ceiling // 2), share), ceiling)


def _write_row(writer: SymbolWriter, state: _State, row: tuple) -> None:
    groups, steps = row
    writer.write_choice(_Context.SIZE, len(groups) - 1)
    for place, group in enumerate(groups):
        if group in state.groups:
            writer.write_choice(_Context.RECENT_GROUP, state.groups.index(group) + 1)
        else:
            writer.write_choice(_Context.RECENT_GROUP, 0)
            writer.write_choice(_Context.NEW_GROUP, group)
        if place < len(groups) - 1:
            guess = state.guess(group, place, len(groups), list(steps[:place]))
            context = _Context.LATER_WEIGHT if place else _Context.FIRST_WEIGHT
            writer.write_number(context, steps[place] - guess)
        state.use_group(group, steps[place])


def _read_row(reader: SymbolReader, state: _State, group_count: int) -> tuple:
    size = reader.read_choice(_Context.SIZE) + 1
    if size > group_count:
        raise FormatError("A packed weight row has more weights than groups")
    groups, steps = [], []
    for place in range(size):
        at = reader.read_choice(_Context.RECENT_GROUP)
        if at > len(state.groups):
            raise FormatError("Packed weights name a missing recent group")
        group = state.groups[at - 1] if at else reader.read_choice(_Context.NEW_GROUP)
        if group >= group_count:
            raise FormatError("Packed weights name a missing group")
        if place < size - 1:
            guess = state.guess(group, place, size, steps)
            context = _Context.LATER_WEIGHT if place else _Context.FIRST_WEIGHT
            step = guess + reader.read_number(context)
        else:
            step = WEIGHT_TOTAL - sum(steps)
        if not 0 <= step <= WEIGHT_TOTAL - sum(steps):
            raise FormatError("Packed weights add up to more than 1")
        groups.append(group)
        steps.append(step)
        state.use_group(group, step)
    return tuple(groups), tuple(steps)
