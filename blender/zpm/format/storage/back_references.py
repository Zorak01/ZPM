# SPDX-License-Identifier: GPL-3.0-or-later

"""Back references: IDs numbered by first use, stored as a mask and distances.

An element either takes the next new ID or reaches back from the newest one. The newest
ID at any element is its rank in the mask, so a reader needs no running state.
"""

from ..errors import FormatError


def split_back_references(ids: list) -> tuple | None:
    """Return (marks, distances), or None if the IDs are not numbered by first use."""
    newest = 0
    marks = []
    distances = []
    for value in ids:
        if value > newest:
            return None
        marks.append(value != newest)
        if value == newest:
            newest += 1
        else:
            distances.append(newest - 1 - value)
    return marks, distances


def resolve_back_references(marks: list, distances: list) -> list:
    """Return one ID per element. Raises FormatError if any reaches before the first."""
    remaining = iter(distances)
    ids = []
    newest = 0
    try:
        for marked in marks:
            if marked:
                ids.append(newest - 1 - next(remaining))
            else:
                ids.append(newest)
                newest += 1
    except StopIteration as error:
        raise FormatError("Back distances do not match their mask") from error
    if ids and min(ids) < 0:
        raise FormatError("A back distance reaches before the first ID")
    return ids
