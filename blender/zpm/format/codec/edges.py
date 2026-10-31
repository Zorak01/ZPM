# SPDX-License-Identifier: GPL-3.0-or-later

"""Edges: endpoints, or a reference to a corner on the edge when that is smaller."""

from ..container.layout import BLOCK_SIZE, UINT32_MAX, SectionKind
from ..container.sections import (
    Section,
    SectionReader,
    column_section,
    stored_bytes,
)
from ..errors import FormatError
from ..processing.faces import next_corners
from ..storage.column import encode_column
from ..storage.rank_mask import encode_rank_mask, read_rank_mask
from ..storage.select_bits import encode_select_bits, read_select_bits

_REFERENCE_PASSES = 2


def _nearest_references(candidates: list) -> list:
    """An edge has a corner on each side; pick whichever sits nearest its block."""
    picked = [options[0] for options in candidates]
    for start in range(0, len(picked), BLOCK_SIZE):
        end = min(start + BLOCK_SIZE, len(picked))
        for _ in range(_REFERENCE_PASSES):
            middle = sorted(picked[start:end])[(end - start) // 2]
            for i in range(start, end):
                picked[i] = min(
                    candidates[i],
                    key=lambda corner: (abs(corner - middle), corner != picked[i]),
                )
    return picked


def _encode_first_use(pairs: list, corners: list, following: list) -> Section | None:
    """Mark the corner first reaching each edge, if edges are stored in that order.

    Costs one bit per corner. Returns None unless every pair is lower ID first and in
    exactly the order the corners first reach them.
    """
    reached = {}
    for corner, vertex in enumerate(corners):
        other = corners[following[corner]]
        reached.setdefault((min(vertex, other), max(vertex, other)), corner)
    if pairs != list(reached):
        return None
    data = encode_select_bits(list(reached.values()), len(corners))
    return Section(SectionKind.EDGE_FIRST_USE, data)


def _encode_references(edges: list, corners: list, sizes: list) -> list:
    following = next_corners(sizes, len(corners))
    corners_of_edge = {}
    for corner, vertex in enumerate(corners):
        other = corners[following[corner]]
        key = (vertex, other) if vertex < other else (other, vertex)
        corners_of_edge.setdefault(key, []).append(corner)
    edge_count = len(edges) // 2
    candidates = []
    attached_pairs = []
    loose_marks = [False] * edge_count
    loose_first = []
    loose_second = []
    for edge in range(edge_count):
        a, b = edges[edge * 2], edges[edge * 2 + 1]
        options = corners_of_edge.get((a, b) if a < b else (b, a))
        if options is None:
            loose_marks[edge] = True
            loose_first.append(a)
            loose_second.append(b)
        else:
            candidates.append(options)
            attached_pairs.append((a, b))
    sections = []
    if loose_first:
        sections += [
            Section(SectionKind.EDGE_LOOSE_MASK, encode_rank_mask(loose_marks)),
            column_section(SectionKind.EDGE_LOOSE_FIRST, loose_first),
            column_section(SectionKind.EDGE_LOOSE_SECOND, loose_second),
        ]
    if not candidates:
        return sections
    first_use = _encode_first_use(attached_pairs, corners, following)
    if first_use is not None:
        return [*sections, first_use]
    picked = min(
        ([options[0] for options in candidates], _nearest_references(candidates)),
        key=lambda values: len(encode_column(values)[0]),
    )
    sections.append(column_section(SectionKind.EDGE_CORNERS, picked))
    flips = [
        0 if corners[reference] == pair[0] else 1
        for reference, pair in zip(picked, attached_pairs, strict=True)
    ]
    lower_first = [
        1 if corners[reference] > corners[following[reference]] else 0
        for reference in picked
    ]
    if flips != lower_first:
        sections.append(column_section(SectionKind.EDGE_FLIPS, flips))
    return sections


def encode_edges(edges: list, corners: list, sizes: list) -> list:
    """Return the smallest legal form of the edge list."""
    firsts = edges[0::2]
    spans = [second - first for first, second in zip(firsts, edges[1::2], strict=True)]
    endpoints = [
        column_section(SectionKind.EDGE_FIRST, firsts),
        column_section(SectionKind.EDGE_SPAN, spans),
    ]
    if not corners:
        return endpoints
    referenced = _encode_references(edges, corners, sizes)
    return (
        referenced if stored_bytes(referenced) < stored_bytes(endpoints) else endpoints
    )


def _read_references(sections: SectionReader, attached: int, corner_count: int) -> list:
    if sections.has(SectionKind.EDGE_FIRST_USE):
        data = sections.raw(SectionKind.EDGE_FIRST_USE)
        return read_select_bits(data, corner_count, attached)
    if attached:
        return sections.column(SectionKind.EDGE_CORNERS, attached)
    return []


def read_edges(
    sections: SectionReader, edge_count: int, corners: list, sizes: list
) -> list:
    """Return flat endpoint pairs, one pair per edge."""
    if not edge_count:
        return []
    if sections.has(SectionKind.EDGE_FIRST):
        first = sections.column(SectionKind.EDGE_FIRST, edge_count)
        spans = sections.column(SectionKind.EDGE_SPAN, edge_count)
        edges = [0] * (edge_count * 2)
        edges[0::2] = first
        edges[1::2] = [
            (start + span) & UINT32_MAX
            for start, span in zip(first, spans, strict=True)
        ]
        return edges
    marks = [False] * edge_count
    loose_first = loose_second = []
    if sections.has(SectionKind.EDGE_LOOSE_MASK):
        marks = read_rank_mask(sections.raw(SectionKind.EDGE_LOOSE_MASK), edge_count)
        loose_first = sections.column(SectionKind.EDGE_LOOSE_FIRST, sum(marks))
        loose_second = sections.column(SectionKind.EDGE_LOOSE_SECOND, sum(marks))
    attached = edge_count - sum(marks)
    references = _read_references(sections, attached, len(corners))
    flips = (
        sections.column(SectionKind.EDGE_FLIPS, attached)
        if sections.has(SectionKind.EDGE_FLIPS)
        else None
    )
    if references and max(references) >= len(corners):
        raise FormatError("An edge references a missing corner")
    following = next_corners(sizes, len(corners))
    edges = []
    next_attached = 0
    next_loose = 0
    for marked in marks:
        if marked:
            edges += (loose_first[next_loose], loose_second[next_loose])
            next_loose += 1
            continue
        reference = references[next_attached]
        a, b = corners[reference], corners[following[reference]]
        flipped = a > b if flips is None else flips[next_attached] != 0
        edges += (b, a) if flipped else (a, b)
        next_attached += 1
    return edges
