# SPDX-License-Identifier: GPL-3.0-or-later

"""Splits: which of a welded position's vertices each corner uses.

Corners in walk order. The first corner at a position makes a new vertex; later ones
read a choice, 0 for a new vertex, else one already at that position.
"""

from enum import IntEnum
from typing import NamedTuple

from ...errors import FormatError
from ...storage.rans import SymbolReader, SymbolWriter
from .layout import Layout

_COUNT_KINDS = 4
_GUESS_KINDS = 2
_CHOICE_KINDS = 4
_SEAM_KINDS = 2
_THROUGH_GATE_KINDS = 2
_BASE_CONTEXTS = _COUNT_KINDS * _GUESS_KINDS


class SplitModel(IntEnum):
    """How a corner names its vertex. The stream's first byte names one."""

    # By when the vertex was made, from 1.
    BY_ORDER = 0
    # First a guess, the vertex across the face's gate or the one last used here,
    # then the rest, newest first.
    GATE_GUESS = 1
    # First the vertex of the corner across any edge the face closed. Seams run in
    # chains, so this also keys on the face's last choice, whether the vertex across
    # began at a seam, and whether the corner came in through the gate.
    ACROSS_GUESS = 2


class VertexSplits(NamedTuple):
    """Each corner's vertex, and each vertex's welded position."""

    corners: list
    position_of: list


def encode_splits(layout: Layout, corners: list) -> bytes:
    """Return the splits under whichever model makes them smaller."""
    return min((_written(layout, corners, model) for model in SplitModel), key=len)


def read_splits(data: bytes, layout: Layout) -> VertexSplits:
    """Return the vertices the splits name. Raises FormatError for a bad model or
    choice."""
    if not data or data[0] >= len(SplitModel):
        raise FormatError("Packed splits name no known model")
    model = SplitModel(data[0])
    reader = SymbolReader(data[1:], _contexts(model))
    corners = [0] * len(layout.welded)
    position_of = []
    splits = _Splits(layout, model)
    for corner, _first, at, face in layout.corners():
        choice = splits.options(corner, at, face)
        index = None
        vertex = None
        if choice is not None:
            context, options = choice
            index = reader.read_choice(context)
            if not 0 <= index <= len(options):
                raise FormatError("A packed corner names a missing vertex")
            if index:
                vertex = options[index - 1]
        if vertex is None:
            vertex = len(position_of)
            position_of.append(layout.welded[corner])
        corners[corner] = vertex
        splits.record(corner, vertex, index)
    return VertexSplits(corners, position_of)


def _written(layout: Layout, corners: list, model: SplitModel) -> bytes:
    writer = SymbolWriter(_contexts(model))
    splits = _Splits(layout, model)
    for corner, _first, at, face in layout.corners():
        vertex = corners[corner]
        choice = splits.options(corner, at, face)
        index = None
        if choice is not None:
            context, options = choice
            index = options.index(vertex) + 1 if vertex in options else 0
            writer.write_choice(context, index)
        splits.record(corner, vertex, index)
    return bytes((model,)) + writer.to_bytes()


def _contexts(model: SplitModel) -> int:
    if model == SplitModel.ACROSS_GUESS:
        return _BASE_CONTEXTS * _CHOICE_KINDS * _SEAM_KINDS * _THROUGH_GATE_KINDS
    return _BASE_CONTEXTS


class _Splits:
    """What a corner may name, read and written alike.

    Only corners recorded so far are consulted, so writer and reader see the same.
    """

    def __init__(self, layout: Layout, model: SplitModel):
        self._layout = layout
        self._model = model
        self._vertex_of = {}
        self._seen = {}
        self._recent = {}
        self._face = None
        self._face_choice = 0
        self._seams = set()

    def options(self, corner: int, at: int, face: int) -> tuple | None:
        """Return (context, the vertices this corner may name), or None if none yet."""
        position = self._layout.welded[corner]
        known = self._seen.get(position)
        if not known:
            return None
        if self._model == SplitModel.BY_ORDER:
            return min(len(known), _COUNT_KINDS - 1), known
        if self._model == SplitModel.ACROSS_GUESS:
            return self._across_options(corner, at, face, position, known)
        gate = self._layout.gates[face]
        guessed = gate is not None and at < 2
        guess = self._vertex_of[gate.corner(at)] if guessed else self._recent[position]
        return self._guess_context(known, guessed), self._guess_first(guess, known)

    def record(self, corner: int, vertex: int, index: int | None) -> None:
        """Note the vertex a corner uses; index is the choice made, or None if none."""
        position = self._layout.welded[corner]
        known = self._seen.setdefault(position, [])
        if vertex not in known:
            known.append(vertex)
        self._recent[position] = vertex
        self._vertex_of[corner] = vertex
        if index is not None:
            self._face_choice = 1 if index == 0 else 3 if index == 1 else 2
            if index == 0:
                self._seams.add(vertex)

    def _across_options(self, corner, at, face, position, known) -> tuple:
        if face != self._face:
            self._face, self._face_choice = face, 0
        # A face may close its own edge, across to a corner not read yet.
        guess = self._vertex_of.get(self._layout.across[corner])
        guessed = guess is not None
        if not guessed:
            guess = self._recent[position]
        seam = 1 if guessed and guess in self._seams else 0
        through = 1 if self._layout.gates[face] is not None and at < 2 else 0
        context = self._guess_context(known, guessed)
        context += _BASE_CONTEXTS * (self._face_choice * _SEAM_KINDS + seam)
        context += _BASE_CONTEXTS * _CHOICE_KINDS * _SEAM_KINDS * through
        return context, self._guess_first(guess, known)

    @staticmethod
    def _guess_context(known: list, guessed: bool) -> int:
        return min(len(known), _COUNT_KINDS - 1) + (_COUNT_KINDS if guessed else 0)

    @staticmethod
    def _guess_first(guess: int, known: list) -> list:
        return [guess, *(vertex for vertex in reversed(known) if vertex != guess)]
