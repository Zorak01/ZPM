# SPDX-License-Identifier: GPL-3.0-or-later

"""Predicting per-vertex values from position, block by block.

Texture coordinates change smoothly across a surface patch, so inside one block a value
is close to a flat function of the vertex's position. Each block may store three fixed
point numbers, and the stream's column then holds only the small difference. Everything
is whole-number arithmetic, so every reader gets exactly the same result.

    prediction = (x * cx + y * cy + z * cz) >> shift

A stream picks one of two shifts: fewer bits cost less, but a tiny grid step needs more.
"""

from ..container.layout import BLOCK_SIZE

_INT32_LIMIT = 2**31 - 1
_MIN_BLOCK_VALUES = 8
_COEFFICIENT_BYTES = 12
_FLATNESS_GUARD = 1.0


def predict(coefficients: tuple, position: tuple, shift: int) -> int:
    """Return the prediction at one position, from its grid steps."""
    return (
        position[0] * coefficients[0]
        + position[1] * coefficients[1]
        + position[2] * coefficients[2]
    ) >> shift


def _solve(matrix: list, target: list) -> tuple | None:
    """Solve a 3x3 system. Returns None when it has no single answer."""
    a, b, c = matrix
    det = (
        a[0] * (b[1] * c[2] - b[2] * c[1])
        - a[1] * (b[0] * c[2] - b[2] * c[0])
        + a[2] * (b[0] * c[1] - b[1] * c[0])
    )
    if abs(det) < 1e-6:
        return None
    x, y, z = target
    return (
        (
            x * (b[1] * c[2] - b[2] * c[1])
            - a[1] * (y * c[2] - b[2] * z)
            + a[2] * (y * c[1] - b[1] * z)
        )
        / det,
        (
            a[0] * (y * c[2] - b[2] * z)
            - x * (b[0] * c[2] - b[2] * c[0])
            + a[2] * (b[0] * z - y * c[0])
        )
        / det,
        (
            a[0] * (b[1] * z - y * c[1])
            - a[1] * (b[0] * z - y * c[0])
            + x * (b[0] * c[1] - b[1] * c[0])
        )
        / det,
    )


def _fit_block(values: list, rows: list, shift: int) -> tuple:
    """Return fixed point coefficients for one block, or zeros if a fit costs more."""
    count = len(values)
    if count < _MIN_BLOCK_VALUES:
        return (0, 0, 0)
    means = [sum(row[axis] for row in rows) / count for axis in range(3)]
    mean = sum(values) / count
    centered = [[row[axis] - means[axis] for axis in range(3)] for row in rows]
    # The small extra on the diagonal keeps a perfectly flat patch solvable.
    matrix = [
        [
            sum(row[i] * row[j] for row in centered)
            + (_FLATNESS_GUARD if i == j else 0)
            for j in range(3)
        ]
        for i in range(3)
    ]
    target = [
        sum(
            row[i] * (value - mean) for row, value in zip(centered, values, strict=True)
        )
        for i in range(3)
    ]
    solved = _solve(matrix, target)
    if solved is None:
        return (0, 0, 0)
    coefficients = tuple(
        max(-_INT32_LIMIT, min(_INT32_LIMIT, round(part * (1 << shift))))
        for part in solved
    )
    residuals = [
        value - predict(coefficients, row, shift)
        for value, row in zip(values, rows, strict=True)
    ]
    before = (max(values) - min(values)).bit_length()
    after = (max(residuals) - min(residuals)).bit_length()
    saved = (before - after) * count // 8
    return coefficients if saved > _COEFFICIENT_BYTES else (0, 0, 0)


def fit_blocks(values: list, positions: list, shift: int) -> list:
    """Return one coefficient triple per block of values."""
    return [
        _fit_block(
            values[start : start + BLOCK_SIZE],
            positions[start : start + BLOCK_SIZE],
            shift,
        )
        for start in range(0, len(values), BLOCK_SIZE)
    ]


def predictions(blocks: list, positions: list, shift: int) -> list:
    """Return the prediction for every value."""
    return [
        predict(blocks[index // BLOCK_SIZE], position, shift)
        for index, position in enumerate(positions)
    ]
