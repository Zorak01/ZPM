# SPDX-License-Identifier: GPL-3.0-or-later

"""Units and axes. A mesh using the defaults stores nothing."""

import math

from ..container.layout import MESH_SPACE, SectionKind, SpaceAxis
from ..container.sections import Section, SectionReader
from ..errors import FormatError
from ..mesh import Mesh, MeshSpace

_AXIS_FAMILIES = len(SpaceAxis) // 2


def encode_space(mesh: Mesh) -> list:
    if mesh.space == MeshSpace():
        return []
    info = MESH_SPACE.pack(
        mesh.space.meters_per_unit, mesh.space.up, mesh.space.forward, 0
    )
    return [Section(SectionKind.MESH_SPACE, info)]


def read_space(sections: SectionReader) -> MeshSpace:
    """Return the file's space. Raises FormatError for an impossible one."""
    if not sections.has(SectionKind.MESH_SPACE):
        return MeshSpace()
    section = sections.raw(SectionKind.MESH_SPACE)
    if len(section) != MESH_SPACE.size:
        raise FormatError("Mesh space has the wrong size")
    meters, up, forward, padding = MESH_SPACE.unpack(section)
    if not math.isfinite(meters) or meters <= 0 or padding:
        raise FormatError("Mesh space is invalid")
    try:
        space = MeshSpace(meters, SpaceAxis(up), SpaceAxis(forward))
    except ValueError as error:
        raise FormatError("Mesh space names an unknown axis") from error
    if up % _AXIS_FAMILIES == forward % _AXIS_FAMILIES:
        raise FormatError("Up and forward cannot share an axis")
    return space
