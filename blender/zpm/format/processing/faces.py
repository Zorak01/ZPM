# SPDX-License-Identifier: GPL-3.0-or-later

"""Facts about faces that the codec and the mesh processing share."""

import math
from array import array

from ..mesh import Mesh


def face_lists(mesh: Mesh) -> list:
    """Return each face as a list of its vertex IDs, in file order."""
    sizes = [mesh.arity] * mesh.face_count if mesh.arity else mesh.face_sizes
    faces = []
    at = 0
    for size in sizes:
        faces.append(list(mesh.corners[at : at + size]))
        at += size
    return faces


def face_edges(face: list):
    """Yield a face's edges, each as (lower ID, higher ID)."""
    for position, vertex in enumerate(face):
        other = face[(position + 1) % len(face)]
        yield (min(vertex, other), max(vertex, other))


def _face_normal(positions: array, face: list) -> tuple | None:
    """Return a face's unit normal, summed around its corners; None if no area."""
    x = y = z = 0.0
    for position, vertex in enumerate(face):
        other = face[(position + 1) % len(face)]
        ax, ay, az = positions[vertex * 3 : vertex * 3 + 3]
        bx, by, bz = positions[other * 3 : other * 3 + 3]
        x += (ay - by) * (az + bz)
        y += (az - bz) * (ax + bx)
        z += (ax - bx) * (ay + by)
    length = math.sqrt(x * x + y * y + z * z)
    return (x / length, y / length, z / length) if length > 0 else None


def corner_normals_from_faces(positions: array, faces: list) -> array | None:
    """Return every corner's normal as its own face's; None if a face has no area."""
    normals = array("f")
    for face in faces:
        normal = _face_normal(positions, face)
        if normal is None:
            return None
        normals.extend(normal * len(face))
    return normals


def next_corners(sizes: list, corner_count: int) -> list:
    """Return, for each corner, the index of the next corner around its face."""
    following = [0] * corner_count
    at = 0
    for size in sizes:
        for position in range(size):
            following[at + position] = at + (position + 1) % size
        at += size
    return following
