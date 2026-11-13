# SPDX-License-Identifier: GPL-3.0-or-later

"""Corner streams: one pair of integers per corner, in whichever form is smaller.

A UV layer in the shared form whose corners split exactly as layer 0's do stores no map
of its own.

The shared form stores each distinct pair once, with corners reaching back to it. The
vertex form stores one pair per vertex, plus the corners that disagree. The position
form is the vertex form with one pair per distinct position, for vertices split without
differing. Any corner still reads directly in every form. A stream's sections take fixed
places after its first kind, in StreamRole order.
"""

from dataclasses import replace

from ..container.layout import (
    BLOCK_SIZE,
    FIT_COARSE_SHIFT,
    FIT_SHIFT,
    UINT32_MAX,
    SectionFlags,
    SectionKind,
    StreamRole,
    to_signed,
)
from ..container.sections import (
    Section,
    SectionReader,
    column_section,
    stored_bytes,
)
from ..errors import FormatError
from .back_references import resolve_back_references
from .position_fit import fit_blocks, predictions
from .rank_mask import encode_rank_mask, read_rank_mask


def _kind(first_kind: SectionKind, role: StreamRole) -> SectionKind:
    return SectionKind(first_kind + role)


def _encode_shared(pairs: list, first_kind: SectionKind, layer: int) -> list:
    known = {}
    marks = []
    distances = []
    for pair in pairs:
        index = known.get(pair)
        if index is None:
            known[pair] = len(known)
            marks.append(False)
        else:
            marks.append(True)
            distances.append(len(known) - 1 - index)
    return [
        Section(
            _kind(first_kind, StreamRole.REPEAT_MASK),
            encode_rank_mask(marks),
            layer=layer,
        ),
        column_section(_kind(first_kind, StreamRole.DISTANCES), distances, layer=layer),
        *_encode_pair_columns(list(known), first_kind, StreamRole.VALUES_A, layer),
    ]


def _encode_pair_columns(
    pairs: list,
    first_kind: SectionKind,
    role_a: StreamRole,
    layer: int,
    extra: SectionFlags = SectionFlags.NONE,
) -> list:
    return [
        column_section(
            SectionKind(first_kind + role_a + part),
            [pair[part] for pair in pairs],
            extra,
            layer,
        )
        for part in range(2)
    ]


def _split_by_vertex(pairs: list, corners: list, vertex_count: int) -> tuple:
    """Return (each vertex's pair or None, corner marks, the corners that disagree)."""
    by_vertex = [None] * vertex_count
    marks = []
    exceptions = []
    for pair, vertex in zip(pairs, corners, strict=True):
        if by_vertex[vertex] is None:
            by_vertex[vertex] = pair
        marks.append(by_vertex[vertex] != pair)
        if marks[-1]:
            exceptions.append(pair)
    return by_vertex, marks, exceptions


def _encode_by_vertex(
    pairs: list, corners: list, vertex_count: int, first_kind: SectionKind, layer: int
) -> list:
    by_vertex, marks, exceptions = _split_by_vertex(pairs, corners, vertex_count)
    filled = [pair if pair is not None else (0, 0) for pair in by_vertex]
    return [
        *encode_vertex_pairs(filled, first_kind, layer),
        *_encode_exceptions(marks, exceptions, first_kind, layer),
    ]


def _encode_by_position(
    pairs: list, corners: list, position_ids: list, first_kind: SectionKind, layer: int
) -> list:
    shared_ids = [position_ids[vertex] for vertex in corners]
    by_id, marks, exceptions = _split_by_vertex(
        pairs, shared_ids, max(position_ids) + 1
    )
    filled = [pair if pair is not None else (0, 0) for pair in by_id]
    return [
        *_encode_pair_columns(
            filled, first_kind, StreamRole.VERTEX_A, layer, SectionFlags.BY_POSITION
        ),
        *_encode_exceptions(marks, exceptions, first_kind, layer),
    ]


def _encode_exceptions(marks, exceptions, first_kind, layer) -> list:
    sections = []
    if exceptions:
        sections += [
            Section(
                _kind(first_kind, StreamRole.EXCEPTION_MASK),
                encode_rank_mask(marks),
                layer=layer,
            ),
            *_encode_pair_columns(
                exceptions, first_kind, StreamRole.EXCEPTION_A, layer
            ),
        ]
    return sections


def encode_fitted_pairs(
    pairs: list,
    corners: list,
    positions: list,
    first_kind: SectionKind,
    layer: int = 0,
) -> list:
    """Return the vertex form, predicting each vertex's pair from its grid position.

    positions holds one (x, y, z) in grid steps per vertex.
    """
    return min(
        (
            _encode_fitted(pairs, corners, positions, first_kind, layer, shift)
            for shift in (FIT_SHIFT, FIT_COARSE_SHIFT)
        ),
        key=stored_bytes,
    )


def _encode_fitted(
    pairs: list,
    corners: list,
    positions: list,
    first_kind: SectionKind,
    layer: int,
    shift: int,
) -> list:
    by_vertex, marks, exceptions = _split_by_vertex(pairs, corners, len(positions))
    first = next((pair for pair in by_vertex if pair is not None), (0, 0))
    known = [pair if pair is not None else first for pair in by_vertex]
    sections = []
    residuals = []
    for part in range(2):
        values = [pair[part] for pair in known]
        blocks = fit_blocks(values, positions, shift)
        guessed = predictions(blocks, positions, shift)
        residuals.append(
            [value - guess for value, guess in zip(values, guessed, strict=True)]
        )
        for axis in range(3):
            kind = SectionKind(first_kind + StreamRole.FIT_A_X + part * 3 + axis)
            column = [block[axis] for block in blocks]
            extra = (
                SectionFlags.COARSE_FIT
                if shift == FIT_COARSE_SHIFT
                else SectionFlags.NONE
            )
            sections.append(column_section(kind, column, extra, layer))
    return [
        *sections,
        *encode_vertex_pairs(
            list(zip(residuals[0], residuals[1], strict=True)), first_kind, layer
        ),
        *_encode_exceptions(marks, exceptions, first_kind, layer),
    ]


def _read_fit(sections, first_kind, part: int, layer: int, blocks: int) -> list:
    axes = [
        sections.column(
            SectionKind(first_kind + StreamRole.FIT_A_X + part * 3 + axis),
            blocks,
            layer,
        )
        for axis in range(3)
    ]
    return [
        tuple(to_signed(value) for value in triple)
        for triple in zip(*axes, strict=True)
    ]


def encode_vertex_pairs(pairs: list, first_kind: SectionKind, layer: int = 0) -> list:
    """Return a stream holding exactly one pair per vertex."""
    return _encode_pair_columns(pairs, first_kind, StreamRole.VERTEX_A, layer)


def encode_corner_pairs(
    pairs: list,
    corners: list,
    vertex_count: int,
    first_kind: SectionKind,
    layer: int = 0,
    position_ids: list | None = None,
) -> list:
    """Return a stream holding one pair per corner, in its smallest form.

    position_ids holds each vertex's stored position, when the file stores repeats.
    """
    forms = [
        _encode_shared(pairs, first_kind, layer),
        _encode_by_vertex(pairs, corners, vertex_count, first_kind, layer),
    ]
    if position_ids:
        forms.append(
            _encode_by_position(pairs, corners, position_ids, first_kind, layer)
        )
    return min(forms, key=stored_bytes)


def share_repeat_map(stream: list, source: list, first_kind: SectionKind) -> list:
    """Return a stream without its map, when source holds exactly the same one."""
    map_kinds = (
        _kind(first_kind, StreamRole.REPEAT_MASK),
        _kind(first_kind, StreamRole.DISTANCES),
    )
    own = {s.kind: s.data for s in stream if s.kind in map_kinds}
    theirs = {s.kind: s.data for s in source if s.kind in map_kinds}
    if not own or own != theirs:
        return stream
    values_a = _kind(first_kind, StreamRole.VALUES_A)
    return [
        replace(s, flags=s.flags | SectionFlags.SHARED_MAP) if s.kind == values_a else s
        for s in stream
        if s.kind not in map_kinds
    ]


def has_stream(sections: SectionReader, first_kind: SectionKind, layer: int = 0):
    """Return whether the file holds this stream, in any form."""
    return any(
        sections.has(_kind(first_kind, role), layer)
        for role in (StreamRole.REPEAT_MASK, StreamRole.VALUES_A, StreamRole.VERTEX_A)
    )


def read_vertex_pairs(
    sections: SectionReader, first_kind: SectionKind, layer: int = 0
) -> list:
    """Return the stream's one pair per vertex."""
    kind_a = _kind(first_kind, StreamRole.VERTEX_A)
    values_a = sections.column(kind_a, sections.count_of(kind_a, layer), layer)
    values_b = sections.column(
        _kind(first_kind, StreamRole.VERTEX_B), len(values_a), layer
    )
    return list(zip(values_a, values_b, strict=True))


def read_corner_pairs(
    sections: SectionReader,
    first_kind: SectionKind,
    corners: list,
    layer: int = 0,
    positions: list | None = None,
    position_ids: list | None = None,
) -> list:
    """Return one stored pair per corner, from any form.

    positions holds each vertex's grid steps, needed only when a stream predicts from
    them. position_ids holds each vertex's stored position, needed only by the position
    form. Raises FormatError when either is needed and missing.
    """
    if sections.has(_kind(first_kind, StreamRole.VALUES_A), layer):
        return _read_shared(sections, first_kind, corners, layer)
    by_vertex = read_vertex_pairs(sections, first_kind, layer)
    if sections.flags(_kind(first_kind, StreamRole.VERTEX_A), layer) & (
        SectionFlags.BY_POSITION
    ):
        by_vertex = _spread_by_position(by_vertex, position_ids)
    if corners and max(corners) >= len(by_vertex):
        raise FormatError("A corner stream does not cover every vertex")
    if positions is not None and sections.has(
        SectionKind(first_kind + StreamRole.FIT_A_X), layer
    ):
        by_vertex = _add_predictions(sections, first_kind, layer, by_vertex, positions)
    pairs = [by_vertex[vertex] for vertex in corners]
    if sections.has(_kind(first_kind, StreamRole.EXCEPTION_MASK), layer):
        marks = read_rank_mask(
            sections.raw(_kind(first_kind, StreamRole.EXCEPTION_MASK), layer),
            len(corners),
        )
        count = sum(marks)
        values_a = iter(
            sections.column(_kind(first_kind, StreamRole.EXCEPTION_A), count, layer)
        )
        values_b = iter(
            sections.column(_kind(first_kind, StreamRole.EXCEPTION_B), count, layer)
        )
        for corner, marked in enumerate(marks):
            if marked:
                pairs[corner] = (next(values_a), next(values_b))
    return pairs


def _spread_by_position(by_id: list, position_ids: list | None) -> list:
    if not position_ids or max(position_ids) != len(by_id) - 1:
        raise FormatError("A stream follows positions the file does not repeat")
    return [by_id[index] for index in position_ids]


def _add_predictions(sections, first_kind, layer, by_vertex, positions) -> list:
    if positions is None or len(positions) != len(by_vertex):
        raise FormatError("A stream predicts from positions it cannot have")
    blocks = (len(by_vertex) + BLOCK_SIZE - 1) // BLOCK_SIZE
    first_fit = SectionKind(first_kind + StreamRole.FIT_A_X)
    is_coarse = sections.flags(first_fit, layer) & SectionFlags.COARSE_FIT
    shift = FIT_COARSE_SHIFT if is_coarse else FIT_SHIFT
    guessed = [
        predictions(
            _read_fit(sections, first_kind, part, layer, blocks), positions, shift
        )
        for part in range(2)
    ]
    return [
        (
            (pair[0] + guessed[0][index]) & UINT32_MAX,
            (pair[1] + guessed[1][index]) & UINT32_MAX,
        )
        for index, pair in enumerate(by_vertex)
    ]


def _read_shared(
    sections: SectionReader, first_kind: SectionKind, corners: list, layer: int
) -> list:
    values_a = _kind(first_kind, StreamRole.VALUES_A)
    map_layer = layer
    if sections.flags(values_a, layer) & SectionFlags.SHARED_MAP:
        if not layer:
            raise FormatError("The first layer has no map to share")
        map_layer = 0
    marks = read_rank_mask(
        sections.raw(_kind(first_kind, StreamRole.REPEAT_MASK), map_layer),
        len(corners),
    )
    repeats = sum(marks)
    distances = sections.column(
        _kind(first_kind, StreamRole.DISTANCES), repeats, map_layer
    )
    ids = resolve_back_references(marks, distances)
    count = len(corners) - repeats
    values_a = sections.column(_kind(first_kind, StreamRole.VALUES_A), count, layer)
    values_b = sections.column(_kind(first_kind, StreamRole.VALUES_B), count, layer)
    return [(values_a[index], values_b[index]) for index in ids]
