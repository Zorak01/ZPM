# SPDX-License-Identifier: GPL-3.0-or-later

"""Removing animation keys a reader would not miss."""

from ..container.layout import TrackValue
from ..scene import Track

_RELATIVE_TOLERANCE = 1e-5


def _fits_line(times: list, values: list, first: int, last: int, tolerance: float):
    span = times[last] - times[first]
    slope = (values[last] - values[first]) / span if span else 0.0
    return all(
        abs(values[first] + slope * (times[key] - times[first]) - values[key])
        <= tolerance
        for key in range(first + 1, last)
    )


def reduce_track(track: Track) -> Track:
    """Drop keys that lie on the line between their neighbours.

    Only for one-component float tracks whose keys are all linear. No value moves more
    than 1 / 100,000 of the track's own range, and first and last keys always stay.
    """
    is_linear = not track.key_modes and not track.handles
    if track.value_type != TrackValue.FLOAT or track.components != 1 or not is_linear:
        return track
    times, values = track.times, track.values
    if len(times) < 3:
        return track
    tolerance = (max(values) - min(values)) * _RELATIVE_TOLERANCE
    kept = [0]
    at = 0
    while at < len(times) - 1:
        last = at + 1
        while last + 1 < len(times) and _fits_line(
            times, values, at, last + 1, tolerance
        ):
            last += 1
        kept.append(last)
        at = last
    track.times = [times[key] for key in kept]
    track.values = [values[key] for key in kept]
    return track
