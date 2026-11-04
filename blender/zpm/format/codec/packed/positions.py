# SPDX-License-Identifier: GPL-3.0-or-later

"""Positions: grid integers predicted from parallelograms across the walk's gates.

Welded positions in the order they first appear, each axis a number added to a
prediction from where the position first appears in its face.
"""

import math
import struct
from dataclasses import dataclass
from enum import IntEnum
from typing import NamedTuple

from ...container.layout import AXIS_COUNT
from ...errors import FormatError
from ...storage.rans import SymbolReader, SymbolWriter
from .layout import Layout, magnitude

STEP = struct.Struct("<f")
_FINE_LEVELS = 3
_HISTORY_CONTEXTS = 4
_HISTORY_STEP = 4
_HISTORY_MIN_ROWS = 4000
_REACH_KINDS = 8
_OWN_KINDS = 6
_OWN_STEP = 3
_FACING_KINDS = 5
_FACING_LEVELS = 4


class Keying(IntEnum):
    """What keys each axis's context."""

    # Its own last size, on meshes of 4,000 positions or more.
    OWN = 0
    # Also the earlier axes of the same position.
    ACROSS_AXES = 1
    # Also how much the parallelogram's triangle faces along the axis: a prediction
    # misses most across the surface.
    FACING = 2


@dataclass(frozen=True)
class PositionForm:
    """How a positions stream is written, stored in one byte after the grid step.

    fine is the fine level, in bits 0 and 1; keying is in bits 2 and 3; shrink, in bit
    4, predicts the far corner a quarter of the way back toward the shared edge's
    midpoint, since on irregular scans a full parallelogram overshoots.
    """

    fine: int
    keying: Keying
    shrink: bool = False

    def to_byte(self) -> int:
        """Return the byte this form is stored as."""
        return self.fine | self.keying << 2 | int(self.shrink) << 4

    @classmethod
    def from_byte(cls, byte: int) -> "PositionForm":
        """Return the form a byte names. Raises FormatError for one that names none."""
        fine, keying = byte & 3, byte >> 2 & 3
        if fine >= _FINE_LEVELS or keying >= len(Keying) or byte >> 5:
            raise FormatError("Packed positions name no known number form")
        return cls(fine, Keying(keying), bool(byte >> 4))

    def contexts(self) -> int:
        """Return how many contexts a stream in this form uses."""
        if self.keying == Keying.FACING:
            return _FACING_KINDS * _OWN_KINDS
        if self.keying == Keying.ACROSS_AXES:
            return _REACH_KINDS * _OWN_KINDS
        return _HISTORY_CONTEXTS


class PositionRows(NamedTuple):
    """Positions as read: the grid step, then grid rows of welded positions and of
    vertices on no face."""

    step: float
    rows: list
    extras: list


def grid_rows(positions, exponent: int, step: float) -> list:
    """Return each position's grid integers: on step, or on 2^exponent when it is 0."""
    if step:
        return [
            tuple(round(value / step) for value in positions[at : at + AXIS_COUNT])
            for at in range(0, len(positions), AXIS_COUNT)
        ]
    return [
        tuple(
            round(math.ldexp(value, -exponent))
            for value in positions[at : at + AXIS_COUNT]
        )
        for at in range(0, len(positions), AXIS_COUNT)
    ]


def encode_positions(layout: Layout, rows: list, extras: list, step: float) -> bytes:
    """Return the step, then positions in whichever form is smallest.

    Every fine level and keying is tried on one set of predictions; shrinking is tried
    only with the form that won, which keeps saving time down.
    """
    uses_history = layout.position_count >= _HISTORY_MIN_ROWS
    plain = _residuals(layout, rows, shrink=False)
    best = min(
        (
            _written(plain, extras, PositionForm(fine, keying), uses_history)
            for fine in range(_FINE_LEVELS)
            for keying in Keying
        ),
        key=len,
    )
    won = PositionForm.from_byte(best[0])
    shrunk = PositionForm(won.fine, won.keying, shrink=True)
    shrunk_bytes = _written(
        _residuals(layout, rows, shrink=True), extras, shrunk, uses_history
    )
    return STEP.pack(step) + min(best, shrunk_bytes, key=len)


def read_positions(layout: Layout, data: bytes, extra: int) -> PositionRows:
    """Return the positions a stream holds, with extra vertices on no face.

    Raises FormatError for a bad step or form, or a stream that runs out.
    """
    if len(data) <= STEP.size:
        raise FormatError("Packed positions are truncated")
    (step,) = STEP.unpack_from(data)
    if not (step == 0 or math.isfinite(step) and step > 0):
        raise FormatError("Packed positions have a bad grid step")
    form = PositionForm.from_byte(data[STEP.size])
    reader = SymbolReader(data[STEP.size + 1 :], form.contexts(), fine=form.fine)
    uses_history = layout.position_count >= _HISTORY_MIN_ROWS
    predictor = _Predictor(layout, form.shrink)
    history = [0] * AXIS_COUNT
    rows = []
    for corner, first, at, face in layout.corners():
        if layout.welded[corner] != len(rows):
            continue
        guess = predictor.guess(rows, first, at, face)
        facing = predictor.facing() if form.keying == Keying.FACING else None
        reach = []
        values = []
        for axis in range(AXIS_COUNT):
            own = history[axis]
            widest = max(reach) if reach else own
            share = None if facing is None else facing[axis]
            context = _context(form.keying, uses_history, own, widest, share)
            difference = reader.read_number(context)
            values.append(guess[axis] + difference)
            history[axis] = magnitude(difference)
            reach.append(history[axis])
        rows.append(tuple(values))
        predictor.previous = rows[-1]
    previous = predictor.previous
    extras = []
    for _ in range(extra):
        row = tuple(
            previous[axis] + reader.read_number(0) for axis in range(AXIS_COUNT)
        )
        extras.append(row)
        previous = row
    return PositionRows(step, rows, extras)


class _Residual(NamedTuple):
    """One axis of one position, with what any keying needs for its context."""

    value: int
    own: int
    widest: int
    facing: int | None


class _Residuals(NamedTuple):
    """Every position's residuals under one prediction, and the last position."""

    values: list
    last: tuple


def _residuals(layout: Layout, rows: list, shrink: bool) -> _Residuals:
    predictor = _Predictor(layout, shrink)
    history = [0] * AXIS_COUNT
    values = []
    made = 0
    for corner, first, at, face in layout.corners():
        position = layout.welded[corner]
        if position != made:
            continue
        made += 1
        guess = predictor.guess(rows, first, at, face)
        facing = predictor.facing()
        row = rows[position]
        reach = []
        for axis in range(AXIS_COUNT):
            difference = row[axis] - guess[axis]
            own = history[axis]
            widest = max(reach) if reach else own
            share = None if facing is None else facing[axis]
            values.append(_Residual(difference, own, widest, share))
            history[axis] = magnitude(difference)
            reach.append(history[axis])
        predictor.previous = row
    return _Residuals(values, predictor.previous)


def _written(
    residuals: _Residuals, extras: list, form: PositionForm, uses_history: bool
) -> bytes:
    writer = SymbolWriter(form.contexts(), fine=form.fine)
    for residual in residuals.values:
        context = _context(
            form.keying, uses_history, residual.own, residual.widest, residual.facing
        )
        writer.write_number(context, residual.value)
    previous = residuals.last
    for row in extras:
        for axis in range(AXIS_COUNT):
            writer.write_number(0, row[axis] - previous[axis])
        previous = row
    return bytes((form.to_byte(),)) + writer.to_bytes()


def _context(keying: Keying, uses_history: bool, own: int, widest: int, facing) -> int:
    """Return an axis's context from its own last size, the widest earlier axis of the
    same position, and how much the prediction's triangle faces along it."""
    own_kind = min(own // _OWN_STEP, _OWN_KINDS - 1)
    if keying == Keying.FACING:
        return (0 if facing is None else facing + 1) * _OWN_KINDS + own_kind
    if keying == Keying.ACROSS_AXES:
        return min(widest // 2, _REACH_KINDS - 1) * _OWN_KINDS + own_kind
    context = own // _HISTORY_STEP if uses_history else 0
    return min(context, _HISTORY_CONTEXTS - 1)


class _Predictor:
    """Parallelogram predictions in walk order, shared by both directions."""

    def __init__(self, layout: Layout, shrink: bool):
        self._layout = layout
        self._shrink = shrink
        self._triangle = None
        self.previous = (0,) * AXIS_COUNT

    def guess(self, rows: list, first: int, at: int, face: int) -> tuple:
        """Return a corner's predicted position from those before it in the walk."""
        third = _third_corner(self._layout, first, at, face)
        self._triangle = None
        if at == 0:
            return self.previous
        welded = self._layout.welded
        if at == 1:
            return rows[welded[first]]
        a = rows[welded[first + at - 1]]
        if third is None:
            return a
        b = rows[welded[first]]
        c = rows[welded[third]]
        self._triangle = (a, b, c)
        if self._shrink:
            return tuple(
                (7 * (a[i] + b[i]) - 6 * c[i] + 4) // 8 for i in range(AXIS_COUNT)
            )
        return tuple(a[i] + b[i] - c[i] for i in range(AXIS_COUNT))

    def facing(self) -> list | None:
        """Return how much the last guess's triangle faces along each axis, or None."""
        return None if self._triangle is None else _facing(*self._triangle)


def _third_corner(layout: Layout, first: int, at: int, face: int) -> int | None:
    """Return the corner a parallelogram subtracts, or None if it has none."""
    if at < 2:
        return None
    if at > 2:
        return first + 1
    gate = layout.gates[face]
    return None if gate is None else gate.opposite


def _facing(a: tuple, b: tuple, c: tuple) -> list | None:
    """Return, per axis, a quarter step of how much triangle abc faces along it, up to
    3, or None for a triangle with no area. Integers only, so readers agree."""
    ux, uy, uz = a[0] - c[0], a[1] - c[1], a[2] - c[2]
    vx, vy, vz = b[0] - c[0], b[1] - c[1], b[2] - c[2]
    normal = (abs(uy * vz - uz * vy), abs(uz * vx - ux * vz), abs(ux * vy - uy * vx))
    total = sum(normal)
    if not total:
        return None
    return [min(_FACING_LEVELS * part // total, _FACING_LEVELS - 1) for part in normal]
