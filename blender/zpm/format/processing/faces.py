# SPDX-License-Identifier: GPL-3.0-or-later

"""Facts about faces that the codec and the mesh processing share."""

import math
from array import array

from ..mesh import Mesh

NO_AREA_NORMAL = (0.0, 0.0, 1.0)


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


def corner_normals_from_faces(positions: array, faces: list) -> array:
    """Return every corner's normal as its own face's. A face with no area points up."""
    normals = array("f")
    for face in faces:
        normal = _face_normal(positions, face) or NO_AREA_NORMAL
        normals.extend(normal * len(face))
    return normals


def has_face_normals(positions: array, faces: list, normals: array, cosine: float):
    """Return whether corners have their face's normal, ignoring faces with no area."""
    corner = 0
    for face in faces:
        normal = _face_normal(positions, face)
        if normal is not None:
            for at in range(corner * 3, (corner + len(face)) * 3, 3):
                dot = (
                    normal[0] * normals[at]
                    + normal[1] * normals[at + 1]
                    + normal[2] * normals[at + 2]
                )
                if dot < cosine:
                    return False
        corner += len(face)
    return True


def next_corners(sizes: list, corner_count: int) -> list:
    """Return, for each corner, the index of the next corner around its face."""
    following = [0] * corner_count
    at = 0
    for size in sizes:
        for position in range(size):
            following[at + position] = at + (position + 1) % size
        at += size
    return following
