# SPDX-License-Identifier: GPL-3.0-or-later

"""The barely noticeable value changes a file makes, all in one place."""

import math
from array import array
from dataclasses import replace
from typing import NamedTuple

from ..codec.quantize import (
    decode_color_channel,
    decode_normal,
    decode_offset,
    decode_tangent,
    decode_unit,
    decode_uv,
    encode_color_channel,
    encode_normal,
    encode_offset,
    encode_tangent,
    encode_unit,
    encode_uv,
)
from ..container.layout import (
    AXIS_COUNT,
    MAX_GRID_VALUE,
    NO_GRID,
    WEIGHT_TOTAL,
    TrackValue,
    float_bits,
    float_from_bits,
)
from ..mesh import ColorLayer, Mesh
from ..scene import Track
from .faces import corner_normals_from_faces, face_lists, has_face_normals

GRID_BITS = 20
# The packing grid's step keeps this many significant bits, so saving again finds it.
_PACKING_STEP_BITS = 12
# Grid integers stay this far inside Float32's precision, so a position read back
# always rounds to the same integer again.
_PACKING_HEADROOM_BITS = 21
# A packed float track's grid step is at most its range over 2^17.
_TRACK_GRID_BITS = 17
_FACE_NORMAL_MIN_COSINE = math.cos(math.radians(0.1))
_TANGENT_WIDTH = 4
_WEIGHT_SUM_TOLERANCE = 1e-3
_SAME_NORMAL_STEPS = 1


def grid_exponent(mesh: Mesh) -> int:
    """Return E where the position grid step is 2^E, or NO_GRID if none fits.

    A mesh has no grid when a position or shape target is not finite, when it has no
    size, or when its values sit so far from the origin that grid integers would not
    fit a Float32. The grid comes from the base shape; targets share it.
    """
    positions = mesh.positions
    buffers = [positions, *(target.positions for target in mesh.shape_targets)]
    if not positions or not all(
        math.isfinite(value) for buffer in buffers for value in buffer
    ):
        return NO_GRID
    extent = max(
        max(positions[axis::AXIS_COUNT]) - min(positions[axis::AXIS_COUNT])
        for axis in range(AXIS_COUNT)
    )
    if extent == 0:
        return NO_GRID
    exponent = math.frexp(extent)[1] - 1 - GRID_BITS
    largest = max(abs(value) for buffer in buffers for value in buffer)
    return NO_GRID if math.ldexp(largest, -exponent) >= MAX_GRID_VALUE else exponent


def normals_match_faces(mesh: Mesh) -> bool:
    """Return whether readers can calculate this mesh's normals from its faces.

    True when normals were not authored, no face has holes, and each is within 0.1
    degrees of its face's.
    Walks the whole mesh.
    """
    if mesh.has_custom_normals or mesh.hole_starts or not mesh.corner_normals:
        return False
    return has_face_normals(
        mesh.positions, face_lists(mesh), mesh.corner_normals, _FACE_NORMAL_MIN_COSINE
    )


def _as_float32(value: float) -> float:
    return float_from_bits(float_bits(value))


def _snap_to_grid(positions: array, step: float) -> array:
    return array("f", [round(value / step) * step for value in positions])


def _snap_offsets(mesh: Mesh, name: str) -> list:
    """Return each target's stored changes: all of them, or none if nothing changes."""
    empty = array("f", [0.0]) * len(mesh.positions)
    snapped = [
        array("f", [decode_offset(encode_offset(v)) for v in getattr(target, name)])
        or empty
        for target in mesh.shape_targets
    ]
    return (
        snapped
        if any(any(buffer) for buffer in snapped)
        else [array("f")] * len(snapped)
    )


def _snap_targets(mesh: Mesh, step: float | None) -> list:
    normals = _snap_offsets(mesh, "normal_offsets")
    tangents = _snap_offsets(mesh, "tangent_offsets")
    return [
        replace(
            target,
            positions=_snap_to_grid(target.positions, step)
            if step
            else target.positions,
            value=_as_float32(target.value),
            minimum=_as_float32(target.minimum),
            maximum=_as_float32(target.maximum),
            frame=_as_float32(target.frame),
            in_between_weight=_as_float32(target.in_between_weight),
            normal_offsets=normals[index],
            tangent_offsets=tangents[index],
        )
        for index, target in enumerate(mesh.shape_targets)
    ]


def _snap_groups(values: array, width: int, encode, decode) -> array:
    return array(
        "f",
        [
            part
            for at in range(0, len(values), width)
            for part in decode(*encode(*values[at : at + width]))
        ],
    )


def _snap_normals(mesh: Mesh) -> array:
    """Return 12-bit normals, with a vertex's corners one grid step apart made equal.

    Corners of a smooth vertex often land one step apart from rounding alone, and each
    would cost a stored exception. This moves a normal by at most about 0.05 degrees.
    Corners further apart are real splits and stay.
    """
    normals = mesh.corner_normals
    if not normals:
        return array("f")
    first_of_vertex = {}
    snapped = array("f")
    for corner, vertex in enumerate(mesh.corners):
        at = corner * AXIS_COUNT
        stored = encode_normal(*normals[at : at + AXIS_COUNT])
        first = first_of_vertex.setdefault(vertex, stored)
        is_close = (
            abs(stored[0] - first[0]) <= _SAME_NORMAL_STEPS
            and abs(stored[1] - first[1]) <= _SAME_NORMAL_STEPS
        )
        snapped.extend(decode_normal(*(first if is_close else stored)))
    return snapped


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


def _share_out(values: list) -> list:
    """Return whole steps adding up exactly, spares to the largest remainders."""
    total = sum(values)
    exact = [value / total * WEIGHT_TOTAL for value in values]
    steps = [int(value) for value in exact]
    spare = WEIGHT_TOTAL - sum(steps)
    by_remainder = sorted(range(len(values)), key=lambda at: steps[at] - exact[at])
    for at in by_remainder[:spare]:
        steps[at] += 1
    return steps


def _snap_weights(mesh: Mesh) -> dict:
    """Return weights on a 12-bit grid adding up to exactly 1, largest first.

    Only when every vertex has weights and they already add up to 1. Otherwise weights
    stay in their order on the 16-bit grid.
    """
    offsets = mesh.weight_offsets
    values = mesh.weight_values
    rows = [
        values[offsets[vertex] : offsets[vertex + 1]]
        for vertex in range(len(offsets) - 1)
    ]
    if not rows or any(abs(sum(row) - 1.0) > _WEIGHT_SUM_TOLERANCE for row in rows):
        return {
            "weight_values": array("f", [decode_unit(encode_unit(v)) for v in values])
        }
    groups = array("I")
    snapped = array("f")
    for vertex, row in enumerate(rows):
        steps = _share_out(list(row))
        first = offsets[vertex]
        order = sorted(
            range(len(row)), key=lambda at: (-steps[at], mesh.weight_groups[first + at])
        )
        groups.extend(mesh.weight_groups[first + at] for at in order)
        snapped.extend(steps[at] / WEIGHT_TOTAL for at in order)
    return {"weight_groups": groups, "weight_values": snapped}


def snap_mesh(mesh: Mesh) -> Mesh:
    """Return the mesh as the file will hold it.

    Positions and shape targets move to a grid of at most 1 / 2^20 of the model's
    largest size, so no point moves more than 1 / 2^21 of it. Equal positions stay
    equal, so shared points cannot crack apart. The step is a power of two, so
    snapping again changes nothing.
    Normals and tangents become 12-bit directions, or normals their faces' when those
    match, and a vertex's corners one step apart become equal. UVs go on a 1 /
    2^16 grid, float colors on 1 / 2^16, creases and weights to
    16 bits. RGBA8 colors stay exact. A UV layer's seams are dropped when they equal
    the mesh's own. Custom attributes stay exact.
    """
    changes = {
        "corner_normals": _snap_normals(mesh),
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
    for name in ("edge_creases", "vertex_creases"):
        changes[name] = array(
            "f", [decode_unit(encode_unit(value)) for value in getattr(mesh, name)]
        )
    changes.update(_snap_weights(mesh))
    exponent = grid_exponent(mesh)
    step = None if exponent == NO_GRID else math.ldexp(1.0, exponent)
    if step:
        changes["positions"] = _snap_to_grid(mesh.positions, step)
    changes["shape_targets"] = _snap_targets(mesh, step)
    snapped = replace(mesh, **changes)
    # Judged before snapping, which can turn a tiny face by far more than it moves it.
    if normals_match_faces(mesh):
        snapped.corner_normals = corner_normals_from_faces(
            snapped.positions, face_lists(snapped)
        )
    return snapped


class PackingGrid(NamedTuple):
    """A mesh as the packed form stores it, and the grid its positions sit on."""

    mesh: Mesh
    # The grid step, or 0 for the power-of-two grid of 2^exponent.
    step: float
    exponent: int
    # Whether readers calculate normals from faces, so none are stored.
    normals_from_faces: bool


def snap_for_packing(mesh: Mesh) -> PackingGrid:
    """Return the mesh as the packed form stores it.

    As snap_mesh, then positions on the packing grid when it is coarser: see
    packing_step. Shape targets keep their moves in steps of 2^exponent.
    """
    snapped = snap_mesh(mesh)
    from_faces = normals_match_faces(snapped)
    exponent = grid_exponent(snapped)
    step = packing_step(mesh.positions, exponent)
    snapped = _snap_to_step(snapped, mesh.positions, step, exponent, from_faces)
    return PackingGrid(snapped, step, exponent, from_faces)


def packing_step(positions, exponent: int) -> float:
    """Return the grid step packed positions use, or 0 for the power-of-two grid.

    The step is the model's largest size over 2^20, as Draco's 20-bit grid, rounded
    down to 12 significant bits, so no point moves more than 1 / 2^21 of that size: the
    same bound as the power-of-two grid, which can be up to twice as fine. Positions
    already exactly on a neighbouring step keep it, so saving again changes nothing.
    Kept only when coarser than the power-of-two grid and when positions read back
    from Float32 land on the same integers. Scans the positions up to four times.
    """
    if exponent == NO_GRID or not positions:
        return 0.0
    extent = max(
        max(positions[axis::AXIS_COUNT]) - min(positions[axis::AXIS_COUNT])
        for axis in range(AXIS_COUNT)
    )
    if extent <= 0:
        return 0.0
    scale = math.frexp(math.ldexp(extent, -GRID_BITS))[1] - _PACKING_STEP_BITS
    whole = math.floor(math.ldexp(extent, -GRID_BITS - scale))
    steps = [math.ldexp(value, scale) for value in (whole, whole - 1, whole + 1)]
    step = next((step for step in steps if _on_step(positions, step)), steps[0])
    largest = max(abs(value) for value in positions)
    if step <= math.ldexp(1.0, exponent):
        return 0.0
    return 0.0 if largest > math.ldexp(step, _PACKING_HEADROOM_BITS) else step


def _on_step(positions, step: float) -> bool:
    if step <= 0:
        return False
    snapped = array("f", [round(value / step) * step for value in positions])
    return snapped == array("f", positions)


def _snap_to_step(mesh: Mesh, source, step: float, exponent: int, from_faces: bool):
    """Return the mesh with positions on the packing grid, as a reader gets them back.

    Positions round from source, the positions before any snapping, so each moves at
    most half a step. Shape targets keep their moves in steps of 2^exponent, as their
    sections store them.
    """
    if not step:
        return mesh
    new = array("f", [round(value / step) * step for value in source])
    targets = [
        replace(
            target, positions=_target_on_step(mesh.positions, new, target, exponent)
        )
        for target in mesh.shape_targets
    ]
    snapped = replace(mesh, positions=new, shape_targets=targets)
    if from_faces:
        snapped.corner_normals = corner_normals_from_faces(new, face_lists(snapped))
    return snapped


def _target_on_step(old, new, target, exponent: int) -> array:
    """Return a shape target's positions over a base moved from old to new.

    A vertex the target changes comes back from the base's power-of-two grid point,
    as its section stores it; the rest follow the base.
    """
    unit = math.ldexp(1.0, exponent)
    moved = array("f", new)
    offsets = [
        buffer for buffer in (target.normal_offsets, target.tangent_offsets) if buffer
    ]
    for vertex in range(len(new) // AXIS_COUNT):
        slots = range(vertex * AXIS_COUNT, vertex * AXIS_COUNT + AXIS_COUNT)
        changed = any(target.positions[slot] != old[slot] for slot in slots)
        if not changed and not any(
            buffer[slot] for buffer in offsets for slot in slots
        ):
            continue
        for slot in slots:
            delta = round(target.positions[slot] / unit) - round(old[slot] / unit)
            moved[slot] = math.ldexp(round(new[slot] / unit) + delta, exponent)
    return moved


def snap_track(track: Track) -> Track:
    """Return a float track with its values on its grid, as the packed form stores them.

    The step is the largest power of two at most the track's range over 2^17, so no
    value moves more than 1 / 2^18 of the range: under the 1 / 100,000 of the range that
    dropping straight-line keys already allows. Values already on a neighbouring step
    keep it, so snapping again changes nothing. Other tracks, constant ones and ones
    holding a non-finite value come back unchanged.
    """
    step = track_step(track)
    if not step:
        return track
    values = array("f", [round(value / step) * step for value in track.values])
    return replace(track, values=list(values))


def track_step(track: Track) -> float:
    """Return the grid step a float track's values snap to, or 0 for exact values."""
    values = track.values
    if track.value_type != TrackValue.FLOAT or not values:
        return 0.0
    if not all(math.isfinite(value) for value in values):
        return 0.0
    extent = max(values) - min(values)
    if extent <= 0:
        return 0.0
    step = math.ldexp(1.0, math.frexp(extent)[1] - 1 - _TRACK_GRID_BITS)
    return next(
        (
            candidate
            for candidate in (step, step * 2, step / 2)
            if _on_step(values, candidate)
        ),
        step,
    )
