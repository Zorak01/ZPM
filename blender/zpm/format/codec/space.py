# SPDX-License-Identifier: GPL-3.0-or-later

"""Units and axes of a scene. A scene using the defaults stores nothing."""

import math

from ..container.layout import SPACE, SectionKind, SpaceAxis
from ..container.sections import Section, SectionReader
from ..errors import FormatError
from ..scene import SceneSpace

_AXIS_FAMILIES = len(SpaceAxis) // 2


def encode_space(space: SceneSpace) -> list:
    if space == SceneSpace():
        return []
    info = SPACE.pack(space.meters_per_unit, space.up, space.forward, 0)
    return [Section(SectionKind.SPACE, info)]


def read_space(sections: SectionReader) -> SceneSpace:
    """Return the file's space. Raises FormatError for an impossible one."""
    if not sections.has(SectionKind.SPACE):
        return SceneSpace()
    section = sections.raw(SectionKind.SPACE)
    if len(section) != SPACE.size:
        raise FormatError("Scene space has the wrong size")
    meters, up, forward, padding = SPACE.unpack(section)
    if not math.isfinite(meters) or meters <= 0 or padding:
        raise FormatError("Scene space is invalid")
    try:
        space = SceneSpace(meters, SpaceAxis(up), SpaceAxis(forward))
    except ValueError as error:
        raise FormatError("Scene space names an unknown axis") from error
    if up % _AXIS_FAMILIES == forward % _AXIS_FAMILIES:
        raise FormatError("Up and forward cannot share an axis")
    return space
