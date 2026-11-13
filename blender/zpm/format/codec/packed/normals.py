# SPDX-License-Identifier: GPL-3.0-or-later

"""Normals: 12-bit directions predicted from the decoded geometry.

A new value is predicted from the summed face normals around its key, a welded position
on smooth meshes or a vertex on meshes with hard edges, or from the key's last value.
"""

from dataclasses import dataclass

from ...errors import FormatError
from ...storage.rans import SymbolReader, smallest_written
from ..quantize import encode_normal
from .corner_values import (
    MAX_SEEN_CONTEXT,
    PAIR_KINDS,
    ChoiceStep,
    Lookup,
    NumberStep,
    SeenLookup,
    estimated_bytes,
    lookup_contexts,
    pair_context,
    step_counts,
    write_steps,
)
from .layout import HISTORY_MAX, Layout, magnitude

_HISTORY_STEP = 3
_HISTORY_KINDS = HISTORY_MAX // _HISTORY_STEP + 1
_VALUE_FIRST = MAX_SEEN_CONTEXT + 1
_CONTEXTS = _VALUE_FIRST + 2 * _HISTORY_KINDS


@dataclass(frozen=True)
class NormalForm:
    """How a normals stream is written, stored in its first byte.

    by_vertex, bit 0, predicts around vertices, not welded positions; fine, bit 1, uses
    fine numbers; lookup is bits 2 and 3; paired, bit 4, keys a normal's second part on
    its first.
    """

    by_vertex: bool
    fine: bool
    lookup: Lookup
    paired: bool = False

    def to_byte(self) -> int:
        """Return the byte this form is stored as."""
        return (
            int(self.by_vertex)
            | int(self.fine) << 1
            | self.lookup << 2
            | int(self.paired) << 4
        )

    @classmethod
    def from_byte(cls, byte: int) -> "NormalForm":
        """Return the form a byte names. Raises FormatError for one that names none."""
        lookup = byte >> 2 & 3
        if lookup >= len(Lookup) or byte >> 5:
            raise FormatError("Packed normals name no known prediction")
        return cls(bool(byte & 1), bool(byte >> 1 & 1), Lookup(lookup), bool(byte >> 4))

    def contexts(self) -> int:
        """Return how many contexts a stream in this form uses."""
        return _first_paired(self.lookup) + (PAIR_KINDS if self.paired else 0)


def encode_normals(layout: Layout, corners: list, pairs: list, rows: list) -> bytes:
    """Return the normals in whichever form is smallest.

    Every prediction, number form and lookup is tried, each prediction and lookup
    worked out once, and only forms whose estimated size could win are written;
    pairing is tried only with the form that won, which keeps saving time down.
    """
    predictions = {
        by_vertex: _predictions(layout, corners, rows, by_vertex)
        for by_vertex in (False, True)
    }
    steps = {
        (by_vertex, lookup): _steps(
            layout, corners, pairs, predictions[by_vertex], lookup
        )
        for by_vertex in (False, True)
        for lookup in Lookup
    }
    counts = {key: step_counts(recorded) for key, recorded in steps.items()}

    def estimate(form: NormalForm) -> float:
        recorded = counts[form.by_vertex, form.lookup]
        return estimated_bytes(recorded, form.contexts(), form.fine, form.paired)

    forms = [
        NormalForm(by_vertex, fine, lookup)
        for by_vertex in (False, True)
        for fine in (False, True)
        for lookup in Lookup
    ]
    best = smallest_written(forms, estimate, lambda form: _written(steps, form))
    won = NormalForm.from_byte(best[0])
    paired = NormalForm(won.by_vertex, won.fine, won.lookup, paired=True)
    return min(best, _written(steps, paired), key=len)


def read_normals(layout: Layout, data: bytes, corners: list, rows: list) -> list:
    """Return each corner's 12-bit normal pair. Raises FormatError on a bad stream."""
    if not data:
        raise FormatError("Packed normals are truncated")
    form = NormalForm.from_byte(data[0])
    reader = SymbolReader(data[1:], form.contexts(), fine=form.fine)
    keys, guesses = _predictions(layout, corners, rows, form.by_vertex)
    history = [0, 0]
    seen_normals = SeenLookup(layout, corners, form.lookup, _CONTEXTS)
    pairs = []
    for corner, key in enumerate(keys):
        context, seen = seen_normals.options(corner)
        if seen:
            at = reader.read_choice(context)
            if not 0 <= at <= len(seen):
                raise FormatError("A packed normal names a missing value")
            if at:
                pairs.append(seen[at - 1])
                seen_normals.record(corner, pairs[-1])
                continue
        base = seen[-1] if seen else guesses[key]
        values = []
        for part in range(2):
            plain, paired = _contexts(form.lookup, seen, part, history)
            difference = reader.read_number(paired if form.paired else plain)
            values.append(base[part] + difference)
            history[part] = magnitude(difference)
        pairs.append(tuple(values))
        seen_normals.record(corner, pairs[-1])
    return pairs


def _written(steps: dict, form: NormalForm) -> bytes:
    recorded = steps[(form.by_vertex, form.lookup)]
    data = write_steps(recorded, form.contexts(), form.fine, form.paired)
    return bytes((form.to_byte(),)) + data


def _predictions(layout: Layout, corners: list, rows: list, by_vertex: bool) -> tuple:
    """Return (each corner's key, each key's predicted 12-bit normal)."""
    keys, key_rows = _keys(layout, corners, rows, by_vertex)
    return keys, _vertex_normals(layout.starts, keys, key_rows)


def _steps(layout, corners, pairs, predictions: tuple, lookup: Lookup) -> list:
    """Return what writing the normals takes, predicted one way, for any number form."""
    keys, guesses = predictions
    history = [0, 0]
    seen_normals = SeenLookup(layout, corners, lookup, _CONTEXTS)
    steps = []
    for corner, key in enumerate(keys):
        context, seen = seen_normals.options(corner)
        pair = pairs[corner]
        if seen:
            at = seen.index(pair) + 1 if pair in seen else 0
            steps.append(ChoiceStep(context, at))
            if at:
                seen_normals.record(corner, pair)
                continue
        base = seen[-1] if seen else guesses[key]
        for part in range(2):
            difference = pair[part] - base[part]
            steps.append(
                NumberStep(*_contexts(lookup, seen, part, history), difference)
            )
            history[part] = magnitude(difference)
        seen_normals.record(corner, pair)
    return steps


def _first_paired(lookup: Lookup) -> int:
    return _CONTEXTS + lookup_contexts(lookup)


def _contexts(lookup: Lookup, seen: list, part: int, history: list) -> tuple:
    """Return a residual's context written plainly, then written in pairs."""
    plain = (
        _VALUE_FIRST + (_HISTORY_KINDS if seen else 0) + history[part] // _HISTORY_STEP
    )
    return plain, pair_context(_first_paired(lookup), part, history, plain)


def _keys(layout: Layout, corners: list, rows: list, by_vertex: bool) -> tuple:
    """Return (each corner's key, each key's grid row) for this way of predicting.

    Smooth meshes predict best around a welded position, since a UV seam does not
    bend the surface; meshes with hard edges predict best around each vertex.
    """
    if not by_vertex:
        return layout.welded, rows
    vertex_rows = [None] * (max(corners) + 1 if corners else 0)
    for vertex, position in zip(corners, layout.welded, strict=True):
        vertex_rows[vertex] = rows[position]
    return corners, vertex_rows


def _vertex_normals(starts: list, keys: list, rows: list) -> list:
    """Return each key's 12-bit direction from the areas of faces around it."""
    totals = [[0, 0, 0] for _ in rows]
    for face in range(len(starts) - 1):
        first = starts[face]
        a = keys[first]
        pa = rows[a]
        for at in range(first + 1, starts[face + 1] - 1):
            b, c = keys[at], keys[at + 1]
            pb, pc = rows[b], rows[c]
            ux, uy, uz = pb[0] - pa[0], pb[1] - pa[1], pb[2] - pa[2]
            vx, vy, vz = pc[0] - pa[0], pc[1] - pa[1], pc[2] - pa[2]
            normal = (uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx)
            for key in (a, b, c):
                total = totals[key]
                for axis in range(3):
                    total[axis] += normal[axis]
    return [encode_normal(float(x), float(y), float(z)) for x, y, z in totals]
