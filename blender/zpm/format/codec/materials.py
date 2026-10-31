# SPDX-License-Identifier: GPL-3.0-or-later

"""Material slots and which slot each face uses.

Reordering keeps a material's faces together, so most blocks of the column hold one
value and take no data bytes.
"""

from array import array

from ..container.layout import SectionKind
from ..container.sections import Section, SectionReader, column_section
from ..mesh import Mesh
from ..storage.names import encode_names, read_names


def encode_materials(mesh: Mesh) -> list:
    """Return slot names, and face slots unless every face uses the first slot."""
    sections = []
    if mesh.material_slots:
        names = encode_names(mesh.material_slots)
        sections.append(Section(SectionKind.MATERIAL_SLOT_NAMES, names))
    if any(mesh.face_materials):
        slots = list(mesh.face_materials)
        sections.append(column_section(SectionKind.FACE_MATERIALS, slots))
    return sections


def read_materials(sections: SectionReader, face_count: int) -> dict:
    """Return Mesh fields for material slots; absent ones come back empty."""
    names = []
    if sections.has(SectionKind.MATERIAL_SLOT_NAMES):
        names = read_names(sections.raw(SectionKind.MATERIAL_SLOT_NAMES))
    slots = []
    if sections.has(SectionKind.FACE_MATERIALS):
        slots = sections.column(SectionKind.FACE_MATERIALS, face_count)
    return {"material_slots": names, "face_materials": array("I", slots)}
