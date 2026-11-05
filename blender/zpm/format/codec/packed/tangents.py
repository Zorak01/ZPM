# SPDX-License-Identifier: GPL-3.0-or-later

"""Tangents: 12-bit directions with a sign, predicted the way they were made.

Tangents come from MikkTSpace, which follows how positions run along U. So each face
corner gives the direction its triangle's positions move as U rises, laid flat against
the corner's normal and weighted by the corner's angle; corners at one position with one
UV, one normal and one UV winding form a group and add theirs up. MikkTSpace gives a
group one tangent, so a corner names a tangent already seen at its vertex, its group's
first; or it writes whether its sign is the guessed one and two residuals, the second
keyed on the first's size when that is smaller.
"""

import math

from ...errors import FormatError
from ...storage.rans import SymbolReader, smallest_written
from ..quantize import decode_normal, encode_normal
from .corner_values import (
    MAX_SEEN_CONTEXT,
    PAIR_KINDS,
    ChoiceStep,
    Lookup,
    NumberStep,
    SeenLookup,
    SymbolStep,
    estimated_bytes,
    pair_context,
    step_counts,
    write_steps,
)
from .layout import HISTORY_MAX, Layout, magnitude

# The tangent sign sits above a direction's 12 bits in its second value.
_SIGN_SHIFT = 12
_DIRECTION_MASK = (1 << _SIGN_SHIFT) - 1
_FINE_LEVELS = 3
# The form byte: the fine level in bits 0 and 1, pairing in bit 2.
_FINE_MASK = 3
_PAIRED_SHIFT = 2
_FORM_BITS = 3
_HISTORY_STEP = 3
_HISTORY_KINDS = HISTORY_MAX // _HISTORY_STEP + 1
# Choices take MAX_SEEN_CONTEXT + 1 contexts, then as many again when the group's own
# tangent is offered first.
_GROUP_CHOICES = MAX_SEEN_CONTEXT + 1
_SIGN_CONTEXT = 2 * _GROUP_CHOICES
_VALUE_FIRST = _SIGN_CONTEXT + 1
_FIRST_PAIRED = _VALUE_FIRST + 2 * _HISTORY_KINDS
_CONTEXTS = _FIRST_PAIRED + PAIR_KINDS
# Terms of arcsin(h) = h + h^3 / 6 + 3 h^5 / 40, which keeps the angle to sqrt and sums.
_ARCSIN_CUBE = 1 / 6
_ARCSIN_FIFTH = 3 / 40


def encode_tangents(
    layout: Layout, corners: list, pairs: list, rows: list, normals: list, uvs: list
) -> bytes:
    """Return the tangents in their smallest fine level, a first byte naming it.

    pairs holds each corner's tangent as encode_tangent stores it; rows each welded
    position's grid row; normals each corner's 12-bit normal, or is empty; uvs each
    corner's stored UV pair from the tangents' layer.
    """
    guesses, groups = _guesses(layout, rows, normals, uvs)
    seen_tangents = _SeenTangents(layout, corners, groups)
    history = [0, 0]
    steps = []
    for corner, pair in enumerate(pairs):
        context, seen = seen_tangents.options(corner)
        if seen:
            at = seen.index(pair) + 1 if pair in seen else 0
            steps.append(ChoiceStep(context, at))
            if at:
                seen_tangents.record(corner, pair)
                continue
        guess, negative = guesses[corner]
        steps.append(
            SymbolStep(_SIGN_CONTEXT, int((pair[1] >> _SIGN_SHIFT) != negative))
        )
        for part, value in enumerate((pair[0], pair[1] & _DIRECTION_MASK)):
            difference = value - guess[part]
            steps.append(NumberStep(*_value_contexts(part, history), difference))
            history[part] = magnitude(difference)
        seen_tangents.record(corner, pair)
    counts = step_counts(steps)
    forms = [(fine, paired) for paired in (False, True) for fine in range(_FINE_LEVELS)]
    return smallest_written(
        forms,
        lambda form: estimated_bytes(counts, _CONTEXTS, *form),
        lambda form: bytes((form[0] | form[1] << _PAIRED_SHIFT,))
        + write_steps(steps, _CONTEXTS, *form),
    )


def read_tangents(
    layout: Layout, data: bytes, corners: list, rows: list, normals: list, uvs: list
) -> list:
    """Return each corner's tangent pair. Raises FormatError on a bad stream."""
    if not data or (data[0] & _FINE_MASK) >= _FINE_LEVELS or data[0] >> _FORM_BITS:
        raise FormatError("Packed tangents name no known form")
    fine, paired = data[0] & _FINE_MASK, bool(data[0] >> _PAIRED_SHIFT)
    reader = SymbolReader(data[1:], _CONTEXTS, fine=fine)
    guesses, groups = _guesses(layout, rows, normals, uvs)
    seen_tangents = _SeenTangents(layout, corners, groups)
    history = [0, 0]
    pairs = []
    for corner in range(len(corners)):
        context, seen = seen_tangents.options(corner)
        if seen:
            at = reader.read_choice(context)
            if not 0 <= at <= len(seen):
                raise FormatError("A packed tangent names a missing value")
            if at:
                pairs.append(seen[at - 1])
                seen_tangents.record(corner, pairs[-1])
                continue
        guess, negative = guesses[corner]
        sign = negative ^ reader.read(_SIGN_CONTEXT)
        if sign > 1:
            raise FormatError("A packed tangent has an unknown sign")
        values = []
        for part in range(2):
            plain, paired_context = _value_contexts(part, history)
            difference = reader.read_number(paired_context if paired else plain)
            values.append(guess[part] + difference)
            history[part] = magnitude(difference)
        if not all(0 <= value <= _DIRECTION_MASK for value in values):
            raise FormatError("A packed tangent is out of range")
        pairs.append((values[0], values[1] | sign << _SIGN_SHIFT))
        seen_tangents.record(corner, pairs[-1])
    return pairs


def _value_contexts(part: int, history: list) -> tuple:
    """Return a residual's context written plainly, then written in pairs."""
    plain = _VALUE_FIRST + part * _HISTORY_KINDS + history[part] // _HISTORY_STEP
    return plain, pair_context(_FIRST_PAIRED, part, history, plain)


class _SeenTangents:
    """Tangents seen at each vertex, with the corner's group's own tangent first."""

    def __init__(self, layout: Layout, corners: list, groups: list):
        self._seen = SeenLookup(layout, corners, Lookup.VERTEX, _CONTEXTS)
        self._groups = groups
        self._group_tangent = {}

    def options(self, corner: int) -> tuple:
        """Return (context, the tangents this corner may name)."""
        context, seen = self._seen.options(corner)
        known = self._group_tangent.get(self._groups[corner])
        if known is None or known not in seen:
            return context, seen
        return context + _GROUP_CHOICES, [
            known,
            *(item for item in seen if item != known),
        ]

    def record(self, corner: int, pair: tuple) -> None:
        """Note the tangent a corner uses."""
        self._seen.record(corner, pair)
        self._group_tangent.setdefault(self._groups[corner], pair)


def _guesses(layout: Layout, rows: list, normals: list, uvs: list) -> tuple:
    """Return (each corner's guessed 12-bit direction and sign bit, its group)."""
    welded = layout.welded
    starts = layout.starts
    count = len(welded)
    units = _unit_normals(layout, rows, normals)
    keys = [None] * count
    totals = {}
    negative = [0] * count
    for face in range(len(starts) - 1):
        first, end = starts[face], starts[face + 1]
        size = end - first
        for place in range(size):
            corner = first + place
            after = first + (place + 1) % size
            before = first + (place - 1) % size
            direction, flipped = _corner_tangent(
                rows, welded, uvs, units[corner], (corner, after, before)
            )
            negative[corner] = flipped
            # As MikkTSpace does, corners whose UVs wind opposite ways stay apart.
            normal = normals[corner] if normals else None
            keys[corner] = (welded[corner], uvs[corner], normal, flipped)
            total = totals.setdefault(keys[corner], [0.0, 0.0, 0.0])
            for axis in range(3):
                total[axis] += direction[axis]
    guesses = []
    for corner in range(count):
        flat = _flattened(totals[keys[corner]], units[corner])
        guesses.append((encode_normal(*flat), negative[corner]))
    return guesses, keys


def _unit_normals(layout: Layout, rows: list, normals: list) -> list:
    """Return each corner's decoded normal, or with none stored, its face's own from
    the face's first three corners."""
    if normals:
        return [decode_normal(*pair) for pair in normals]
    units = []
    starts = layout.starts
    for face in range(len(starts) - 1):
        first = starts[face]
        a, b, c = (rows[layout.welded[at]] for at in (first, first + 1, first + 2))
        u = [b[axis] - a[axis] for axis in range(3)]
        v = [c[axis] - a[axis] for axis in range(3)]
        cross = (
            u[1] * v[2] - u[2] * v[1],
            u[2] * v[0] - u[0] * v[2],
            u[0] * v[1] - u[1] * v[0],
        )
        length = math.sqrt(sum(float(part) * part for part in cross)) or 1.0
        units += [tuple(part / length for part in cross)] * (starts[face + 1] - first)
    return units


def _corner_tangent(rows, welded, uvs, normal: tuple, triangle: tuple) -> tuple:
    """Return (the corner's weighted, flattened direction along U, its sign bit)."""
    corner, after, before = triangle
    origin = rows[welded[corner]]
    first = [rows[welded[after]][axis] - origin[axis] for axis in range(3)]
    second = [rows[welded[before]][axis] - origin[axis] for axis in range(3)]
    du1, dv1 = uvs[after][0] - uvs[corner][0], uvs[after][1] - uvs[corner][1]
    du2, dv2 = uvs[before][0] - uvs[corner][0], uvs[before][1] - uvs[corner][1]
    side = 1 if du1 * dv2 - du2 * dv1 >= 0 else -1
    along = [
        float((first[axis] * dv2 - second[axis] * dv1) * side) for axis in range(3)
    ]
    flat = _flattened(along, normal)
    length = math.sqrt(flat[0] * flat[0] + flat[1] * flat[1] + flat[2] * flat[2])
    # MikkTSpace weighs a corner by its angle with both edges laid flat on the normal.
    weight = _corner_angle(_flattened(first, normal), _flattened(second, normal))
    if not length:
        return (0.0, 0.0, 0.0), int(side < 0)
    return tuple(part / length * weight for part in flat), int(side < 0)


def _flattened(vector, normal: tuple) -> tuple:
    """Return the vector less its part along the unit normal."""
    along = vector[0] * normal[0] + vector[1] * normal[1] + vector[2] * normal[2]
    return tuple(vector[axis] - along * normal[axis] for axis in range(3))


def _corner_angle(first: list, second: list) -> float:
    """Return the angle between two edges, near enough, using only sqrt and sums."""
    first_length = math.sqrt(sum(float(part) * part for part in first))
    second_length = math.sqrt(sum(float(part) * part for part in second))
    if not first_length or not second_length:
        return 0.0
    dot = sum(float(a) * b for a, b in zip(first, second, strict=True))
    cosine = max(-1.0, min(1.0, dot / (first_length * second_length)))
    half = math.sqrt((1.0 - cosine) / 2.0)
    squared = half * half
    return 2.0 * half * (1.0 + squared * (_ARCSIN_CUBE + squared * _ARCSIN_FIFTH))
