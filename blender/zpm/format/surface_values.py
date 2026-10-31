# SPDX-License-Identifier: GPL-3.0-or-later

"""Stored forms of surface values: 12-bit normal directions and grid UVs."""

import math

from .layout import CREASE_MAX, NORMAL_BITS, UV_GRID_BITS

_NORMAL_MAX = (1 << NORMAL_BITS) - 1
_UV_SCALE = 1 << UV_GRID_BITS
_INT32_MAX = 2**31 - 1


def _fold(u: float, v: float) -> tuple:
    return (
        (1 - abs(v)) * (1 if u >= 0 else -1),
        (1 - abs(u)) * (1 if v >= 0 else -1),
    )


def encode_normal(x: float, y: float, z: float) -> tuple:
    """Return a direction as two 12-bit values, folding the sphere onto a square."""
    if not (math.isfinite(x) and math.isfinite(y) and math.isfinite(z)):
        x, y, z = 0.0, 0.0, 1.0
    total = abs(x) + abs(y) + abs(z)
    if total == 0:
        x, y, z, total = 0.0, 0.0, 1.0, 1.0
    u, v = x / total, y / total
    if z < 0:
        u, v = _fold(u, v)
    return round((u * 0.5 + 0.5) * _NORMAL_MAX), round((v * 0.5 + 0.5) * _NORMAL_MAX)


def decode_normal(a: int, b: int) -> tuple:
    """Return the unit direction two stored values stand for."""
    u, v = a / _NORMAL_MAX * 2 - 1, b / _NORMAL_MAX * 2 - 1
    z = 1 - abs(u) - abs(v)
    if z < 0:
        u, v = _fold(u, v)
    length = math.sqrt(u * u + v * v + z * z)
    return u / length, v / length, z / length


def encode_uv(value: float) -> int:
    """Return a UV coordinate on a grid of 1 / 2^20. Non-finite values become 0."""
    if not math.isfinite(value):
        return 0
    return max(-_INT32_MAX, min(_INT32_MAX, round(value * _UV_SCALE)))


def decode_uv(stored: int) -> float:
    return stored / _UV_SCALE


def encode_crease(value: float) -> int:
    """Return a crease as 16 bits. Non-finite values become 0; range is 0 to 1."""
    if not math.isfinite(value):
        return 0
    return max(0, min(CREASE_MAX, round(value * CREASE_MAX)))


def decode_crease(stored: int) -> float:
    return stored / CREASE_MAX
