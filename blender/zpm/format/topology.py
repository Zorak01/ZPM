# SPDX-License-Identifier: GPL-3.0-or-later

"""Facts about face topology that the encoder and decoder share."""


def next_corners(sizes: list, corner_count: int) -> list:
    """Return, for each corner, the index of the next corner around its face."""
    following = [0] * corner_count
    at = 0
    for size in sizes:
        for position in range(size):
            following[at + position] = at + (position + 1) % size
        at += size
    return following
