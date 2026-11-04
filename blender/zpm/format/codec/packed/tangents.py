# SPDX-License-Identifier: GPL-3.0-or-later

"""Tangents: 12-bit directions with a sign, predicted the way they were made.

Tangents come from MikkTSpace, which follows how positions run along U. So each face
corner gives the direction its triangle's positions move as U rises, laid flat against
the corner's normal and weighted by the corner's angle; corners at one position with one
UV and one normal add theirs up. A corner then names a tangent already seen at its
vertex, or writes whether its sign is the guessed one and two residuals.
"""

import math

from ...errors import FormatError
from ...storage.rans import SymbolReader
from ..quantize import decode_normal, encode_normal
from .corner_values import (
    MAX_SEEN_CONTEXT,
    ChoiceStep,
    Lookup,
    NumberStep,
    SeenLookup,
    SymbolStep,
    write_steps,
)
from .layout import HISTORY_MAX, Layout, magnitude

# The tangent sign sits above a direction's 12 bits in its second value.
_SIGN_SHIFT = 12
_DIRECTION_MASK = (1 << _SIGN_SHIFT) - 1
_FINE_LEVELS = 3
_HISTORY_STEP = 3
_HISTORY_KINDS = HISTORY_MAX // _HISTORY_STEP + 1
_SIGN_CONTEXT = MAX_SEEN_CONTEXT + 1
_VALUE_FIRST = _SIGN_CONTEXT + 1
_CONTEXTS = _VALUE_FIRST + 2 * _HISTORY_KINDS
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
    guesses = _guesses(layout, rows, normals, uvs)
    seen_tangents = SeenLookup(layout, corners, Lookup.VERTEX, _CONTEXTS)
    history = [0, 0]
    steps = []
    for corner, pair in enumerate(pairs):
        context, seen = seen_tangents.options(corner)
        if seen:
            at = seen.index(pair) + 1 if pair in seen else 0
            steps.append(ChoiceStep(context, at))
            if at:
                continue
        guess, negative = guesses[corner]
        steps.append(
            SymbolStep(_SIGN_CONTEXT, int((pair[1] >> _SIGN_SHIFT) != negative))
        )
        for part, value in enumerate((pair[0], pair[1] & _DIRECTION_MASK)):
            difference = value - guess[part]
            context = _value_context(part, history)
            steps.append(NumberStep(context, context, difference))
            history[part] = magnitude(difference)
        seen_tangents.record(corner, pair)
    return min(
        (
            bytes((fine,)) + write_steps(steps, _CONTEXTS, fine, paired=False)
            for fine in range(_FINE_LEVELS)
        ),
        key=len,
    )


def read_tangents(
    layout: Layout, data: bytes, corners: list, rows: list, normals: list, uvs: list
) -> list:
    """Return each corner's tangent pair. Raises FormatError on a bad stream."""
    if not data or data[0] >= _FINE_LEVELS:
        raise FormatError("Packed tangents name no known form")
    reader = SymbolReader(data[1:], _CONTEXTS, fine=data[0])
    guesses = _guesses(layout, rows, normals, uvs)
    seen_tangents = SeenLookup(layout, corners, Lookup.VERTEX, _CONTEXTS)
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
                continue
        guess, negative = guesses[corner]
        sign = negative ^ reader.read(_SIGN_CONTEXT)
        if sign > 1:
            raise FormatError("A packed tangent has an unknown sign")
        values = []
        for part in range(2):
            difference = reader.read_number(_value_context(part, history))
            values.append(guess[part] + difference)
            history[part] = magnitude(difference)
        if not all(0 <= value <= _DIRECTION_MASK for value in values):
            raise FormatError("A packed tangent is out of range")
        pairs.append((values[0], values[1] | sign << _SIGN_SHIFT))
        seen_tangents.record(corner, pairs[-1])
    return pairs


def _value_context(part: int, history: list) -> int:
    return _VALUE_FIRST + part * _HISTORY_KINDS + history[part] // _HISTORY_STEP


def _guesses(layout: Layout, rows: list, normals: list, uvs: list) -> list:
    """Return each corner's (guessed 12-bit direction, guessed sign bit)."""
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
    return guesses


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
    weight = _corner_angle(first, second)
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
