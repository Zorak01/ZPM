# SPDX-License-Identifier: GPL-3.0-or-later

"""Corner streams: one pair of integers per corner, in whichever form is smaller.

The shared form stores each distinct pair once, with corners reaching back to it. The
vertex form stores one pair per vertex, plus the corners that disagree. Any corner
still reads directly in either form. A stream's sections take fixed places after its
first kind, in StreamRole order.
"""

from ..container.layout import SectionKind, StreamRole
from ..container.sections import (
    Section,
    SectionReader,
    column_section,
    stored_bytes,
)
from ..errors import FormatError
from .back_references import resolve_back_references
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
    pairs: list, first_kind: SectionKind, role_a: StreamRole, layer: int
) -> list:
    return [
        column_section(
            SectionKind(first_kind + role_a + part),
            [pair[part] for pair in pairs],
            layer=layer,
        )
        for part in range(2)
    ]


def _encode_by_vertex(
    pairs: list, corners: list, vertex_count: int, first_kind: SectionKind, layer: int
) -> list:
    by_vertex = [None] * vertex_count
    marks = []
    exceptions = []
    for pair, vertex in zip(pairs, corners, strict=True):
        if by_vertex[vertex] is None:
            by_vertex[vertex] = pair
        marks.append(by_vertex[vertex] != pair)
        if marks[-1]:
            exceptions.append(pair)
    filled = [pair if pair is not None else (0, 0) for pair in by_vertex]
    sections = encode_vertex_pairs(filled, first_kind, layer)
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


def encode_vertex_pairs(pairs: list, first_kind: SectionKind, layer: int = 0) -> list:
    """Return a stream holding exactly one pair per vertex."""
    return _encode_pair_columns(pairs, first_kind, StreamRole.VERTEX_A, layer)


def encode_corner_pairs(
    pairs: list,
    corners: list,
    vertex_count: int,
    first_kind: SectionKind,
    layer: int = 0,
) -> list:
    """Return a stream holding one pair per corner, in its smaller form."""
    shared = _encode_shared(pairs, first_kind, layer)
    by_vertex = _encode_by_vertex(pairs, corners, vertex_count, first_kind, layer)
    return by_vertex if stored_bytes(by_vertex) < stored_bytes(shared) else shared


def has_stream(sections: SectionReader, first_kind: SectionKind, layer: int = 0):
    """Return whether the file holds this stream, in either form."""
    return sections.has(_kind(first_kind, StreamRole.REPEAT_MASK), layer) or (
        sections.has(_kind(first_kind, StreamRole.VERTEX_A), layer)
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
    sections: SectionReader, first_kind: SectionKind, corners: list, layer: int = 0
) -> list:
    """Return one stored pair per corner, from either form."""
    if sections.has(_kind(first_kind, StreamRole.REPEAT_MASK), layer):
        return _read_shared(sections, first_kind, corners, layer)
    by_vertex = read_vertex_pairs(sections, first_kind, layer)
    if corners and max(corners) >= len(by_vertex):
        raise FormatError("A corner stream does not cover every vertex")
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


def _read_shared(
    sections: SectionReader, first_kind: SectionKind, corners: list, layer: int
) -> list:
    marks = read_rank_mask(
        sections.raw(_kind(first_kind, StreamRole.REPEAT_MASK), layer), len(corners)
    )
    repeats = sum(marks)
    distances = sections.column(_kind(first_kind, StreamRole.DISTANCES), repeats, layer)
    ids = resolve_back_references(marks, distances)
    count = len(corners) - repeats
    values_a = sections.column(_kind(first_kind, StreamRole.VALUES_A), count, layer)
    values_b = sections.column(_kind(first_kind, StreamRole.VALUES_B), count, layer)
    return [(values_a[index], values_b[index]) for index in ids]
