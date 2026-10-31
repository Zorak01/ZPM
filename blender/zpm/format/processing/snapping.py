# SPDX-License-Identifier: GPL-3.0-or-later

"""The barely noticeable value changes a file makes, all in one place."""

import math
from array import array
from dataclasses import replace

from ..codec.quantize import (
    decode_color_channel,
    decode_normal,
    decode_tangent,
    decode_unit,
    decode_uv,
    encode_color_channel,
    encode_normal,
    encode_tangent,
    encode_unit,
    encode_uv,
)
from ..container.layout import AXIS_COUNT, MAX_GRID_VALUE, NO_GRID
from ..mesh import ColorLayer, Mesh
from .faces import corner_normals_from_faces, face_lists

GRID_BITS = 20
_FACE_NORMAL_MIN_COSINE = math.cos(math.radians(0.1))
_TANGENT_WIDTH = 4


def grid_exponent(positions: array) -> int:
    """Return E where the position grid step is 2^E, or NO_GRID if none fits.

    A mesh has no grid when a position is not finite, when it has no size, or when
    its values sit so far from the origin that grid integers would not fit a Float32.
    """
    if not positions or not all(math.isfinite(value) for value in positions):
        return NO_GRID
    extent = max(
        max(positions[axis::AXIS_COUNT]) - min(positions[axis::AXIS_COUNT])
        for axis in range(AXIS_COUNT)
    )
    if extent == 0:
        return NO_GRID
    exponent = math.frexp(extent)[1] - 1 - GRID_BITS
    largest = max(abs(value) for value in positions)
    return NO_GRID if math.ldexp(largest, -exponent) >= MAX_GRID_VALUE else exponent


def normals_match_faces(mesh: Mesh) -> bool:
    """Return whether readers can calculate this mesh's normals from its faces.

    True when normals were not authored, no face has holes, and each is within 0.1
    degrees of its face's.
    Walks the whole mesh.
    """
    if mesh.has_custom_normals or mesh.hole_starts or not mesh.corner_normals:
        return False
    derived = corner_normals_from_faces(mesh.positions, face_lists(mesh))
    if derived is None:
        return False
    stored = mesh.corner_normals
    return all(
        derived[at] * stored[at]
        + derived[at + 1] * stored[at + 1]
        + derived[at + 2] * stored[at + 2]
        >= _FACE_NORMAL_MIN_COSINE
        for at in range(0, len(stored), AXIS_COUNT)
    )


def _snap_groups(values: array, width: int, encode, decode) -> array:
    return array(
        "f",
        [
            part
            for at in range(0, len(values), width)
            for part in decode(*encode(*values[at : at + width]))
        ],
    )


def _snap_color(layer: ColorLayer) -> ColorLayer:
    if not layer.is_float:
        return layer
    snapped = [decode_color_channel(encode_color_channel(v)) for v in layer.values]
    return ColorLayer(array("f", snapped), layer.is_corner, layer.is_srgb)


def _distinct_seams(mesh: Mesh) -> dict:
    shared = bytes(mesh.seam_edges) or bytes(mesh.edge_count)
    return {
        name: flags for name, flags in mesh.uv_seams.items() if bytes(flags) != shared
    }


def snap_mesh(mesh: Mesh) -> Mesh:
    """Return the mesh as the file will hold it.

    Positions move to a grid of at most 1 / 2^20 of the model's largest size, so no
    point moves more than 1 / 2^21 of it. Equal positions stay equal, so shared points
    cannot crack apart. The step is a power of two, so snapping again changes nothing.
    Normals and tangents become 12-bit directions, or normals their faces' when those
    match. UVs go on a 1 / 2^20 grid, float colors on 1 / 2^16, creases and weights to
    16 bits. RGBA8 colors stay exact. A UV layer's seams are dropped when they equal
    the mesh's own.
    """
    changes = {
        "corner_normals": _snap_groups(
            mesh.corner_normals, AXIS_COUNT, encode_normal, decode_normal
        ),
        "tangent_layers": {
            name: _snap_groups(layer, _TANGENT_WIDTH, encode_tangent, decode_tangent)
            for name, layer in mesh.tangent_layers.items()
        },
        "uv_seams": _distinct_seams(mesh),
        "uv_layers": {
            name: array("f", [decode_uv(encode_uv(value)) for value in layer])
            for name, layer in mesh.uv_layers.items()
        },
        "color_layers": {
            name: _snap_color(layer) for name, layer in mesh.color_layers.items()
        },
    }
    for name in ("edge_creases", "vertex_creases", "weight_values"):
        changes[name] = array(
            "f", [decode_unit(encode_unit(value)) for value in getattr(mesh, name)]
        )
    exponent = grid_exponent(mesh.positions)
    if exponent != NO_GRID:
        step = math.ldexp(1.0, exponent)
        changes["positions"] = array(
            "f", [round(value / step) * step for value in mesh.positions]
        )
    snapped = replace(mesh, **changes)
    if normals_match_faces(snapped):
        snapped.corner_normals = corner_normals_from_faces(
            snapped.positions, face_lists(snapped)
        )
    return snapped
