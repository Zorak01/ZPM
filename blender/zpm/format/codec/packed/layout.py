# SPDX-License-Identifier: GPL-3.0-or-later

"""What every packed stream reads from the walk, shared by writing and reading."""

from typing import NamedTuple

# Residual sizes are bit lengths, capped here before they key a context.
HISTORY_MAX = 14


def magnitude(value: int) -> int:
    """Return a residual's bit length, capped at HISTORY_MAX."""
    return min(abs(value).bit_length(), HISTORY_MAX)


class Gate(NamedTuple):
    """An open edge the walk may cross: u to v, as the face it came from holds it."""

    u: int
    v: int
    corner_u: int
    corner_v: int
    # The face's next corner after v, opposite the edge in a triangle.
    corner_after: int


class GateCorners(NamedTuple):
    """The corners of the face a gate came from, for a face reached through it.

    first and second hold this face's first two positions; opposite is the third
    corner, across the shared edge.
    """

    first: int
    second: int
    opposite: int

    def corner(self, place: int) -> int:
        """Return the corner holding this face's first (0) or second (1) position."""
        return self.second if place else self.first


class Layout(NamedTuple):
    """Connectivity as every other stream reads it, in walk order.

    welded is each corner's welded position; starts each face's first corner, then a
    final total; gates each face's GateCorners, or None for a face no gate reached;
    across each corner's earlier corner at the same position over an edge its face
    closed, or None.
    """

    welded: list
    starts: list
    gates: list
    position_count: int
    across: list

    def corners(self):
        """Yield (corner, its face's first corner, its place in the face, the face)."""
        starts = self.starts
        for face in range(len(starts) - 1):
            first = starts[face]
            for corner in range(first, starts[face + 1]):
                yield corner, first, corner - first, face
