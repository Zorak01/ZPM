# SPDX-License-Identifier: GPL-3.0-or-later

"""Face corners and face sizes."""

from ..container.layout import (
    MAX_RELATIVE_ARITY,
    UINT32_MAX,
    SectionKind,
)
from ..container.sections import (
    Section,
    SectionReader,
    column_section,
    stored_bytes,
)
from ..errors import FormatError
from ..storage.back_references import (
    resolve_back_references,
    split_back_references,
)
from ..storage.rank_mask import encode_rank_mask, read_rank_mask
from ..storage.select_bits import encode_select_bits, read_select_bits


def _encode_relative(corners: list, face_count: int, arity: int) -> list:
    first = corners[0::arity]
    sections = [column_section(SectionKind.CORNER_FIRST, first)]
    for position in range(1, arity):
        offsets = [
            corners[face * arity + position] - first[face] for face in range(face_count)
        ]
        kind = SectionKind(SectionKind.CORNER_FIRST + position)
        sections.append(column_section(kind, offsets))
    return sections


def encode_corners(corners: list, face_count: int, arity: int) -> list:
    """Return the smallest legal form of the corner list."""
    best = [column_section(SectionKind.CORNER_IDS, corners)]
    if 3 <= arity <= MAX_RELATIVE_ARITY:
        relative = _encode_relative(corners, face_count, arity)
        if stored_bytes(relative) < stored_bytes(best):
            best = relative
    split = split_back_references(corners)
    if split is not None:
        marks, distances = split
        by_distance = [
            Section(SectionKind.CORNER_REPEAT_MASK, encode_rank_mask(marks)),
            column_section(SectionKind.CORNER_BACK_DISTANCES, distances),
        ]
        if stored_bytes(by_distance) < stored_bytes(best):
            best = by_distance
    return best


def encode_face_starts(sizes: list, corner_count: int) -> Section:
    """Return the section marking each face's first corner, for mixed face sizes."""
    starts = []
    at = 0
    for size in sizes:
        starts.append(at)
        at += size
    return Section(SectionKind.FACE_STARTS, encode_select_bits(starts, corner_count))


def read_corners(
    sections: SectionReader, corner_count: int, face_count: int, arity: int
) -> list:
    """Return one vertex ID per corner."""
    if not corner_count:
        return []
    if sections.has(SectionKind.CORNER_REPEAT_MASK):
        marks = read_rank_mask(
            sections.raw(SectionKind.CORNER_REPEAT_MASK), corner_count
        )
        kind = SectionKind.CORNER_BACK_DISTANCES
        distances = sections.column(kind, sections.count_of(kind))
        return resolve_back_references(marks, distances)
    if not sections.has(SectionKind.CORNER_FIRST):
        return sections.column(SectionKind.CORNER_IDS, corner_count)
    if not 3 <= arity <= MAX_RELATIVE_ARITY:
        raise FormatError("Invalid uniform face layout")
    first = sections.column(SectionKind.CORNER_FIRST, face_count)
    corners = [0] * corner_count
    corners[0::arity] = first
    for position in range(1, arity):
        kind = SectionKind(SectionKind.CORNER_FIRST + position)
        corners[position::arity] = [
            (start + offset) & UINT32_MAX
            for start, offset in zip(
                first, sections.column(kind, face_count), strict=True
            )
        ]
    return corners


def read_face_sizes(
    sections: SectionReader, corner_count: int, face_count: int
) -> list:
    """Return the corner count of each face, from the marks on their first corners."""
    data = sections.raw(SectionKind.FACE_STARTS)
    starts = read_select_bits(data, corner_count, face_count)
    if not starts or starts[0] != 0:
        raise FormatError("Face starts are invalid")
    ends = starts[1:] + [corner_count]
    return [end - start for start, end in zip(starts, ends, strict=True)]
