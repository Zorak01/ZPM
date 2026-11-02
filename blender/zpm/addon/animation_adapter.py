# SPDX-License-Identifier: GPL-3.0-or-later

"""Blender actions to and from clips and tracks."""

from array import array

import bpy
from bpy_extras import anim_utils

from ..format import Clip, Track
from ..format.container.layout import HandleType, KeyMode, ResourceKind, TrackValue
from ..format.processing.animation import reduce_track

NODE_TREE_PREFIX = "node_tree."
WORLD_PREFIX = "world."
_MODES = {mode.name: mode for mode in KeyMode}
_HANDLES = {kind.name: kind for kind in HandleType}
_KEY_FIELDS = 2
_HANDLE_FIELDS = 4


def _capture_track(curve, kind: ResourceKind, owner: int, prefix: str) -> Track:
    points = curve.keyframe_points
    count = len(points)
    values = array("f", [0.0]) * (count * _KEY_FIELDS)
    points.foreach_get("co", values)
    left = array("f", [0.0]) * (count * _KEY_FIELDS)
    right = array("f", [0.0]) * (count * _KEY_FIELDS)
    points.foreach_get("handle_left", left)
    points.foreach_get("handle_right", right)
    track = Track(
        kind,
        owner,
        prefix + curve.data_path,
        list(values[0::_KEY_FIELDS]),
        list(values[1::_KEY_FIELDS]),
        curve.array_index,
    )
    modes = [_MODES[point.interpolation] for point in points]
    if any(mode != KeyMode.LINEAR for mode in modes):
        track.key_modes = modes
        track.left_handle_types = [_HANDLES[p.handle_left_type] for p in points]
        track.right_handle_types = [_HANDLES[p.handle_right_type] for p in points]
    if any(mode == KeyMode.BEZIER for mode in modes):
        track.handles = [
            v
            for key in range(count)
            for v in (*left[key * 2 : key * 2 + 2], *right[key * 2 : key * 2 + 2])
        ]
    return reduce_track(_without_held_keys(track))


def _without_held_keys(track: Track) -> Track:
    """A track holding one value needs one key. The result reads back the same."""
    if len(set(track.values)) > 1 or len(track.values) < 2:
        return track
    track.times = track.times[:1]
    track.values = track.values[:1]
    track.key_modes = track.key_modes[:1]
    track.left_handle_types = track.left_handle_types[:1]
    track.right_handle_types = track.right_handle_types[:1]
    track.handles = track.handles[:_HANDLE_FIELDS]
    return track


def _curves_of(block) -> tuple:
    """Return (action, its curves for this block), or (None, ())."""
    animation = block.animation_data
    if animation is None or animation.action is None:
        return None, ()
    channels = anim_utils.action_get_channelbag_for_slot(
        animation.action, animation.action_slot
    )
    return animation.action, channels.fcurves if channels is not None else ()


class ClipCapture:
    """Collects actions as clips, one clip per action."""

    def __init__(self, frames_per_second: float):
        self._rate = frames_per_second
        self._clips = {}

    @property
    def clips(self) -> list:
        return list(self._clips.values())

    def add(self, block, kind: ResourceKind, owner: int, prefix: str = "") -> None:
        action, curves = _curves_of(block)
        if action is None:
            return
        clip = self._clips.setdefault(
            action.name,
            Clip(
                action.name,
                self._rate,
                action.frame_range[0],
                action.frame_range[1],
            ),
        )
        clip.tracks += [_capture_track(curve, kind, owner, prefix) for curve in curves]


def _apply_track(curve, track: Track) -> None:
    points = curve.keyframe_points
    points.add(len(track.times))
    flat = array(
        "f", [v for pair in zip(track.times, track.values, strict=True) for v in pair]
    )
    points.foreach_set("co", flat)
    modes = {mode: name for name, mode in _MODES.items()}
    kinds = {kind: name for name, kind in _HANDLES.items()}
    for key, point in enumerate(points):
        point.interpolation = (
            modes[track.key_modes[key]] if track.key_modes else "LINEAR"
        )
        if track.left_handle_types:
            point.handle_left_type = kinds[HandleType(track.left_handle_types[key])]
            point.handle_right_type = kinds[HandleType(track.right_handle_types[key])]
        if track.handles:
            at = key * _HANDLE_FIELDS
            point.handle_left = track.handles[at : at + 2]
            point.handle_right = track.handles[at + 2 : at + 4]
    curve.update()


def apply_clips(clips: list, owner_block, created: list) -> None:
    """Create an action per clip and owner. owner_block finds a track's data block."""
    for clip in clips:
        by_owner = {}
        for track in clip.tracks:
            # Blender cannot key a swap of data. The modifiers making it are kept.
            if track.value_type == TrackValue.REFERENCE:
                continue
            path = track.path.removeprefix(WORLD_PREFIX)
            in_node_tree = path.startswith(NODE_TREE_PREFIX)
            key = (track.owner_kind, track.owner, in_node_tree)
            by_owner.setdefault(key, []).append(track)
        for (kind, owner, in_node_tree), tracks in by_owner.items():
            block = owner_block(kind, owner)
            if block is not None and in_node_tree:
                block = block.node_tree
            if block is None:
                continue
            action = bpy.data.actions.new(clip.name)
            created.append(action)
            block.animation_data_create().action = action
            for track in tracks:
                curve = action.fcurve_ensure_for_datablock(
                    block,
                    track.path.removeprefix(WORLD_PREFIX).removeprefix(
                        NODE_TREE_PREFIX
                    ),
                    index=track.array_index,
                )
                _apply_track(curve, track)
