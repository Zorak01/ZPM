# SPDX-License-Identifier: GPL-3.0-or-later

"""UVs: grid integers predicted from the triangle's shape in space.

A face's first corner predicts from the last UV, its second from the first, and later
ones from where the triangle's shape puts them, with a side symbol when the walk does
not already say which side of the edge they fall.
"""

import math
from dataclasses import dataclass

from ...errors import FormatError
from ...storage.rans import SymbolReader, smallest_written
from .corner_values import (
    MAX_SEEN_CONTEXT,
    PAIR_KINDS,
    ChoiceStep,
    Lookup,
    NumberStep,
    SeenLookup,
    SymbolStep,
    estimated_bytes,
    lookup_contexts,
    pair_context,
    step_counts,
    write_steps,
)
from .layout import HISTORY_MAX, Layout, magnitude

_HISTORY_STEP = 4
_HISTORY_KINDS = HISTORY_MAX // _HISTORY_STEP + 1
_SIDE_CONTEXT = MAX_SEEN_CONTEXT + 1
_VALUE_FIRST = _SIDE_CONTEXT + 1
_CONTEXTS = _VALUE_FIRST + _HISTORY_KINDS
# From the last UV, from the face's first corner, or from shape.
_PREDICTION_KINDS = 3


@dataclass(frozen=True)
class UvForm:
    """How a UV stream is written, stored in its first byte.

    fine, bit 0, uses fine numbers; lookup is bits 1 and 2; paired, bit 3, keys a UV's
    second part on its first, and its first on how it was predicted.
    """

    fine: bool
    lookup: Lookup
    paired: bool = False

    def to_byte(self) -> int:
        """Return the byte this form is stored as."""
        return int(self.fine) | self.lookup << 1 | int(self.paired) << 3

    @classmethod
    def from_byte(cls, byte: int) -> "UvForm":
        """Return the form a byte names. Raises FormatError for one that names none."""
        lookup = byte >> 1 & 3
        if lookup >= len(Lookup) or byte >> 4:
            raise FormatError("Packed UVs name no known form")
        return cls(bool(byte & 1), Lookup(lookup), bool(byte >> 3))

    def contexts(self) -> int:
        """Return how many contexts a stream in this form uses."""
        paired = PAIR_KINDS + _PREDICTION_KINDS * _HISTORY_KINDS if self.paired else 0
        return _first_paired(self.lookup) + paired


def encode_uvs(layout: Layout, corners: list, pairs: list, rows: list) -> bytes:
    """Return one UV layer in whichever form is smallest.

    Every number form and lookup is tried, each lookup worked out once, and only
    forms whose estimated size could win are written; pairing is tried only with the
    form that won, which keeps saving time down.
    """
    steps = {lookup: _steps(layout, corners, pairs, rows, lookup) for lookup in Lookup}
    counts = {lookup: step_counts(recorded) for lookup, recorded in steps.items()}

    def estimate(form: UvForm) -> float:
        recorded = counts[form.lookup]
        return estimated_bytes(recorded, form.contexts(), form.fine, form.paired)

    forms = [UvForm(fine, lookup) for fine in (False, True) for lookup in Lookup]
    best = smallest_written(forms, estimate, lambda form: _written(steps, form))
    won = UvForm.from_byte(best[0])
    paired = UvForm(won.fine, won.lookup, paired=True)
    return min(best, _written(steps, paired), key=len)


def read_uvs(layout: Layout, data: bytes, corners: list, rows: list) -> list:
    """Return each corner's UV grid pair. Raises FormatError on a bad stream."""
    if not data:
        raise FormatError("Packed UVs are truncated")
    form = UvForm.from_byte(data[0])
    reader = SymbolReader(data[1:], form.contexts(), fine=form.fine)
    values = [None] * len(layout.welded)
    history = [0, 0]
    last = (0, 0)
    seen_uvs = SeenLookup(layout, corners, form.lookup, _CONTEXTS)

    def side_of(_candidates):
        side = reader.read(_SIDE_CONTEXT)
        if side > 1:
            raise FormatError("A packed UV has an unknown side")
        return side

    for corner, first, at, face in layout.corners():
        context, seen = seen_uvs.options(corner)
        if seen:
            choice = reader.read_choice(context)
            if not 0 <= choice <= len(seen):
                raise FormatError("A packed UV names a missing value")
            if choice:
                values[corner] = seen[choice - 1]
                seen_uvs.record(corner, values[corner])
                continue
        base = (
            seen[-1]
            if seen and at == 0
            else _base(layout, rows, values, corner, first, at, face, last, side_of)
        )
        pair = []
        for part in range(2):
            plain, paired = _contexts(form.lookup, part, history, at)
            difference = reader.read_number(paired if form.paired else plain)
            pair.append(round(base[part]) + difference)
            history[part] = magnitude(difference)
        values[corner] = last = tuple(pair)
        seen_uvs.record(corner, last)
    return values


def _written(steps: dict, form: UvForm) -> bytes:
    data = write_steps(steps[form.lookup], form.contexts(), form.fine, form.paired)
    return bytes((form.to_byte(),)) + data


def _steps(layout, corners, pairs, rows, lookup: Lookup) -> list:
    """Return what writing one UV layer takes, looked up one way, for any form."""
    steps = []
    values = [None] * len(layout.welded)
    history = [0, 0]
    last = (0, 0)
    seen_uvs = SeenLookup(layout, corners, lookup, _CONTEXTS)
    for corner, first, at, face in layout.corners():
        context, seen = seen_uvs.options(corner)
        pair = pairs[corner]
        values[corner] = pair
        if seen:
            choice = seen.index(pair) + 1 if pair in seen else 0
            steps.append(ChoiceStep(context, choice))
            if choice:
                seen_uvs.record(corner, pair)
                continue

        def side_of(candidates, actual=pair):
            misses = [
                abs(candidate[0] - actual[0]) + abs(candidate[1] - actual[1])
                for candidate in candidates
            ]
            side = 1 if misses[1] < misses[0] else 0
            steps.append(SymbolStep(_SIDE_CONTEXT, side))
            return side

        base = (
            seen[-1]
            if seen and at == 0
            else _base(layout, rows, values, corner, first, at, face, last, side_of)
        )
        for part in range(2):
            difference = pair[part] - round(base[part])
            steps.append(NumberStep(*_contexts(lookup, part, history, at), difference))
            history[part] = magnitude(difference)
        seen_uvs.record(corner, pair)
        last = pair
    return steps


def _first_paired(lookup: Lookup) -> int:
    return _CONTEXTS + lookup_contexts(lookup)


def _contexts(lookup: Lookup, part: int, history: list, at: int) -> tuple:
    """Return a residual's context written plainly, then written in pairs: a pair's
    first part keys on how it was predicted, its second on the first's size."""
    plain = _VALUE_FIRST + history[part] // _HISTORY_STEP
    first = _first_paired(lookup)
    if part:
        return plain, pair_context(first, part, history, plain)
    kind = min(at, _PREDICTION_KINDS - 1)
    return plain, first + PAIR_KINDS + kind * _HISTORY_KINDS + history[
        0
    ] // _HISTORY_STEP


def _base(layout, rows, values, corner, first, at, face, last, side_of) -> tuple:
    """Return the prediction. side_of chooses when nothing else says which side."""
    if at == 0:
        return last
    if at == 1:
        return values[first]
    a, b = first + at - 1, first
    candidates = _candidates(layout, rows, values, corner, a, b)
    far = _far_corner(layout, values, first, at, face)
    if far is None:
        return candidates[side_of(candidates)]
    ua, ub, uf = values[a], values[b], values[far]
    across = float(ub[0] - ua[0]) * float(uf[1] - ua[1]) - float(ub[1] - ua[1]) * float(
        uf[0] - ua[0]
    )
    return candidates[1 if across > 0.0 else 0]


def _candidates(layout: Layout, rows, values, corner, a, b) -> tuple:
    """Return the two UVs the triangle's shape in space allows for this corner."""
    welded = layout.welded
    pa, pb, pv = rows[welded[a]], rows[welded[b]], rows[welded[corner]]
    ua, ub = values[a], values[b]
    ex, ey, ez = float(pb[0] - pa[0]), float(pb[1] - pa[1]), float(pb[2] - pa[2])
    qx, qy, qz = float(pv[0] - pa[0]), float(pv[1] - pa[1]), float(pv[2] - pa[2])
    length = ex * ex + ey * ey + ez * ez
    if length == 0.0:
        return (float(ua[0]), float(ua[1])), (float(ua[0]), float(ua[1]))
    du, dv = float(ub[0] - ua[0]), float(ub[1] - ua[1])
    along = (ex * qx + ey * qy + ez * qz) / length
    rx, ry, rz = qx - along * ex, qy - along * ey, qz - along * ez
    away = math.sqrt((rx * rx + ry * ry + rz * rz) / length)
    base_u, base_v = float(ua[0]) + along * du, float(ua[1]) + along * dv
    return (
        (base_u - away * dv, base_v + away * du),
        (base_u + away * dv, base_v - away * du),
    )


def _far_corner(layout: Layout, values, first, at, face) -> int | None:
    """Return a corner known to lie across this corner's edge, or None."""
    if at > 2:
        return first + 1
    gate = layout.gates[face]
    if gate is None:
        return None
    same_edge = (
        values[gate.first] == values[first] and values[gate.second] == values[first + 1]
    )
    return gate.opposite if same_edge else None
