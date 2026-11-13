# SPDX-License-Identifier: GPL-3.0-or-later

"""Packed animation: every track of every clip in one stream, read once.

Track headers are a few symbols each. A path that poses an armature's bone is stored as
the bone's index and the property, and rebuilt exactly. Tracks with the same key times
share one list, written where first used: each time as a step in whole frames plus an
exact correction. Float values on their track's grid are whole numbers of steps, each
written as a change from the key before; others, and every handle, keep their exact
bits.
"""

import math
from enum import IntEnum

from ...container.layout import (
    KeyMode,
    ObjectType,
    ResourceKind,
    TrackFlags,
    TrackValue,
    bits_to_ordered,
    float_bits,
    float_from_bits,
    ordered_to_bits,
)
from ...errors import FormatError
from ...processing.snapping import track_step
from ...scene import Track
from ...storage.rans import SymbolReader, SymbolWriter

_BONE_PROPERTIES = (
    "location",
    "rotation_quaternion",
    "rotation_euler",
    "rotation_axis_angle",
    "scale",
)
_BONE_PREFIX = 'pose.bones["'
_KNOWN_FLAGS = TrackFlags.BAKED | TrackFlags.SPHERICAL_ROTATION | TrackFlags.SPARSE
# A float32 exponent from the smallest step up, kept above 0 for an exact track.
_STEP_EXPONENT_OFFSET = 150


class _Context(IntEnum):
    OWNER_KIND = 0
    OWNER = 1
    PATH_KIND = 2
    BONE = 3
    PROPERTY = 4
    STRING = 5
    ARRAY_INDEX = 6
    VALUE_TYPE = 7
    TARGET_KIND = 8
    COMPONENTS = 9
    FLAGS = 10
    TIME_LIST = 11
    LIST_SIZE = 12
    LIST_FORM = 13
    FRAME_STEP = 14
    TIME_FIX = 15
    EXACT_TIME = 16
    HAS_MODES = 17
    HAS_HANDLES = 18
    STEP = 19
    FIRST_KEY = 20
    KEY_CHANGE = 21
    EXACT_VALUE = 22
    OTHER_VALUE = 23
    CHANGED = 24
    MODE = 25
    LEFT_TYPE = 26
    RIGHT_TYPE = 27
    HANDLE = 28


class _PathKind(IntEnum):
    STRING = 0
    BONE = 1


class _ListForm(IntEnum):
    FRAMES = 0
    EXACT = 1


def encode_tracks(tracks: list, scene, strings) -> bytes:
    """Return the packed stream of tracks, in clip order. strings takes other paths and
    string values. Float values must already be on their grid: see snap_track."""
    writer = _TrackWriter(scene, strings)
    for track in tracks:
        writer.write(track)
    return writer.to_bytes()


def read_tracks(data: bytes, count: int, scene, strings: list) -> list:
    """Return count tracks from a packed stream. scene supplies objects and armatures
    for bone paths. Raises FormatError for anything that names what is not there."""
    reader = _TrackReader(data, scene, strings)
    return [reader.read() for _ in range(count)]


def to_float32(value: float) -> float:
    """Return the Float32 nearest a value. Raises FormatError out of Float32's range."""
    try:
        return float_from_bits(float_bits(value))
    except OverflowError as error:
        raise FormatError("A packed float is out of range") from error


def _ordered(value: float) -> int:
    return bits_to_ordered(float_bits(value))


def _from_ordered(stored: int) -> float:
    if not 0 <= stored <= 0xFFFFFFFF:
        raise FormatError("A packed float is out of range")
    return float_from_bits(ordered_to_bits(stored))


def _bone_names(scene, owner_kind: int, owner: int) -> list | None:
    """Return the bone names a track's owner poses, or None if it poses none."""
    if owner_kind != ResourceKind.OBJECT or owner >= len(scene.objects):
        return None
    item = scene.objects[owner]
    if item.object_type != ObjectType.ARMATURE or item.data >= len(scene.armatures):
        return None
    return [bone.name for bone in scene.armatures[item.data].bones]


def _bone_path(name: str, prop: str) -> str:
    return f'{_BONE_PREFIX}{name}"].{prop}'


class _TrackWriter:
    def __init__(self, scene, strings):
        self._scene = scene
        self._strings = strings
        self._writer = SymbolWriter(len(_Context))
        self._lists = {}
        self._previous_owner = 0
        self._previous_bone = 0
        self._previous_list = 0
        self._bone_indices = {}

    def to_bytes(self) -> bytes:
        return self._writer.to_bytes()

    def write(self, track: Track) -> None:
        w = self._writer
        w.write(_Context.OWNER_KIND, track.owner_kind)
        w.write_number(_Context.OWNER, track.owner - self._previous_owner)
        self._previous_owner = track.owner
        self._write_path(track)
        w.write_choice(_Context.ARRAY_INDEX, track.array_index)
        w.write(_Context.VALUE_TYPE, track.value_type)
        w.write(_Context.TARGET_KIND, track.target_kind)
        w.write_choice(_Context.COMPONENTS, track.components)
        sparse = TrackFlags.SPARSE if track.changed else TrackFlags.NONE
        w.write_choice(_Context.FLAGS, track.flags | sparse)
        self._write_times(track.times)
        w.write(_Context.HAS_MODES, 1 if track.key_modes else 0)
        w.write(_Context.HAS_HANDLES, 1 if track.handles else 0)
        for flag in track.changed:
            w.write(_Context.CHANGED, int(flag))
        self._write_values(track)
        if track.key_modes:
            self._write_modes(track)
        if track.handles:
            self._write_handles(track)

    def _write_path(self, track: Track) -> None:
        index, prop = self._bone_of(track)
        if index is None:
            self._writer.write(_Context.PATH_KIND, _PathKind.STRING)
            self._writer.write_choice(_Context.STRING, self._strings.add(track.path))
            return
        self._writer.write(_Context.PATH_KIND, _PathKind.BONE)
        self._writer.write_number(_Context.BONE, index - self._previous_bone)
        self._writer.write(_Context.PROPERTY, prop)
        self._previous_bone = index

    def _bone_of(self, track: Track) -> tuple:
        """Return (bone index, property index) for a path posing one bone, or Nones."""
        key = (track.owner_kind, track.owner)
        if key not in self._bone_indices:
            names = _bone_names(self._scene, *key) or []
            indices = {}
            for index, name in enumerate(names):
                indices.setdefault(name, index)
            self._bone_indices[key] = indices
        indices = self._bone_indices[key]
        for prop_index, prop in enumerate(_BONE_PROPERTIES):
            suffix = f'"].{prop}'
            if not track.path.startswith(_BONE_PREFIX) or not track.path.endswith(
                suffix
            ):
                continue
            name = track.path[len(_BONE_PREFIX) : -len(suffix)]
            index = indices.get(name)
            if index is not None and _bone_path(name, prop) == track.path:
                return index, prop_index
        return None, None

    def _write_times(self, times: list) -> None:
        key = tuple(times)
        index = self._lists.get(key, len(self._lists))
        self._writer.write_number(_Context.TIME_LIST, index - self._previous_list)
        self._previous_list = index
        if key in self._lists:
            return
        self._lists[key] = index
        self._writer.write_choice(_Context.LIST_SIZE, len(times))
        exact = not all(math.isfinite(time) for time in times)
        self._writer.write(
            _Context.LIST_FORM, _ListForm.EXACT if exact else _ListForm.FRAMES
        )
        previous_frame = 0
        previous = 0
        for time in times:
            if exact:
                self._writer.write_number(
                    _Context.EXACT_TIME, _ordered(time) - previous
                )
                previous = _ordered(time)
                continue
            frame = round(time)
            self._writer.write_number(_Context.FRAME_STEP, frame - previous_frame)
            self._writer.write_number(
                _Context.TIME_FIX, _ordered(time) - _ordered(to_float32(frame))
            )
            previous_frame = frame

    def _write_values(self, track: Track) -> None:
        w = self._writer
        values = track.values
        if track.value_type == TrackValue.STRING:
            for value in values:
                w.write_choice(_Context.STRING, self._strings.add(value))
            return
        if track.value_type != TrackValue.FLOAT:
            previous = 0
            for value in values:
                w.write_number(_Context.OTHER_VALUE, int(value) - previous)
                previous = int(value)
            return
        step = track_step(track)
        if step and all(to_float32(round(v / step) * step) == v for v in values):
            w.write_choice(
                _Context.STEP, math.frexp(step)[1] - 1 + _STEP_EXPONENT_OFFSET
            )
            previous = 0
            for at, value in enumerate(values):
                whole = round(value / step)
                w.write_number(
                    _Context.KEY_CHANGE if at else _Context.FIRST_KEY, whole - previous
                )
                previous = whole
            return
        w.write_choice(_Context.STEP, 0)
        previous = 0
        for value in values:
            w.write_number(_Context.EXACT_VALUE, _ordered(value) - previous)
            previous = _ordered(value)

    def _write_modes(self, track: Track) -> None:
        for key, mode in enumerate(track.key_modes):
            self._writer.write(_Context.MODE, mode)
            left = track.left_handle_types[key] if track.left_handle_types else 0
            right = track.right_handle_types[key] if track.right_handle_types else 0
            self._writer.write(_Context.LEFT_TYPE, left)
            self._writer.write(_Context.RIGHT_TYPE, right)

    def _write_handles(self, track: Track) -> None:
        references = _handle_references(track)
        for handle, reference in zip(track.handles, references, strict=True):
            self._writer.write_number(
                _Context.HANDLE, _ordered(handle) - _ordered(reference)
            )


def _handle_references(track: Track) -> list:
    """Return what each handle float is written against: its key's time or value."""
    values = track.expanded_values()
    references = []
    for key, time in enumerate(track.times):
        value = values[key * track.components] if values else 0.0
        value = value if isinstance(value, float) else 0.0
        references += [time, value, time, value]
    return references


class _TrackReader:
    def __init__(self, data: bytes, scene, strings: list):
        self._scene = scene
        self._strings = strings
        self._reader = SymbolReader(data, len(_Context))
        self._lists = []
        self._previous_owner = 0
        self._previous_bone = 0
        self._previous_list = 0

    def read(self) -> Track:
        r = self._reader
        owner_kind = self._checked(ResourceKind, r.read(_Context.OWNER_KIND))
        owner = self._previous_owner + r.read_number(_Context.OWNER)
        if owner < 0:
            raise FormatError("A packed track names a missing owner")
        self._previous_owner = owner
        path = self._read_path(owner_kind, owner)
        array_index = r.read_choice(_Context.ARRAY_INDEX)
        value_type = self._checked(TrackValue, r.read(_Context.VALUE_TYPE))
        target_kind = self._checked(ResourceKind, r.read(_Context.TARGET_KIND))
        components = r.read_choice(_Context.COMPONENTS)
        stored_flags = r.read_choice(_Context.FLAGS)
        if stored_flags & ~_KNOWN_FLAGS:
            raise FormatError("A packed track has unknown flags")
        flags = TrackFlags(stored_flags)
        times = self._read_times()
        has_modes = r.read(_Context.HAS_MODES)
        has_handles = r.read(_Context.HAS_HANDLES)
        if components < 1:
            raise FormatError("A packed track has no components")
        track = Track(
            owner_kind,
            owner,
            path,
            times,
            [],
            array_index,
            value_type,
            target_kind,
            components,
            flags & ~TrackFlags.SPARSE,
        )
        count = len(times) * components
        if flags & TrackFlags.SPARSE:
            track.changed = [bool(r.read(_Context.CHANGED)) for _ in range(count)]
            count = sum(track.changed)
        track.values = self._read_values(value_type, count)
        if has_modes:
            self._read_modes(track)
        if has_handles:
            self._read_handles(track)
        return track

    @staticmethod
    def _checked(kind, value: int):
        try:
            return kind(value)
        except ValueError as error:
            raise FormatError(
                f"A packed track names an unknown {kind.__name__}"
            ) from error

    def _read_path(self, owner_kind: int, owner: int) -> str:
        r = self._reader
        kind = r.read(_Context.PATH_KIND)
        if kind == _PathKind.STRING:
            index = r.read_choice(_Context.STRING)
            if index >= len(self._strings):
                raise FormatError("A packed track names a missing path")
            return self._strings[index]
        if kind != _PathKind.BONE:
            raise FormatError("A packed track has an unknown path kind")
        names = _bone_names(self._scene, owner_kind, owner)
        index = self._previous_bone + r.read_number(_Context.BONE)
        prop = r.read(_Context.PROPERTY)
        if (
            names is None
            or not 0 <= index < len(names)
            or prop >= len(_BONE_PROPERTIES)
        ):
            raise FormatError("A packed track names a missing bone")
        self._previous_bone = index
        return _bone_path(names[index], _BONE_PROPERTIES[prop])

    def _read_times(self) -> list:
        r = self._reader
        index = self._previous_list + r.read_number(_Context.TIME_LIST)
        self._previous_list = index
        if 0 <= index < len(self._lists):
            return self._lists[index]
        if index != len(self._lists):
            raise FormatError("A packed track names a missing time list")
        size = r.read_choice(_Context.LIST_SIZE)
        form = r.read(_Context.LIST_FORM)
        times = []
        previous_frame = 0
        previous = 0
        for _ in range(size):
            if form == _ListForm.EXACT:
                previous += r.read_number(_Context.EXACT_TIME)
                times.append(_from_ordered(previous))
                continue
            previous_frame += r.read_number(_Context.FRAME_STEP)
            fix = r.read_number(_Context.TIME_FIX)
            times.append(_from_ordered(_ordered(to_float32(previous_frame)) + fix))
        self._lists.append(times)
        return times

    def _read_values(self, value_type: TrackValue, count: int) -> list:
        r = self._reader
        if value_type == TrackValue.STRING:
            indices = [r.read_choice(_Context.STRING) for _ in range(count)]
            if any(index >= len(self._strings) for index in indices):
                raise FormatError("A packed key names a missing string")
            return [self._strings[index] for index in indices]
        if value_type != TrackValue.FLOAT:
            values = []
            previous = 0
            for _ in range(count):
                previous += r.read_number(_Context.OTHER_VALUE)
                values.append(
                    bool(previous) if value_type == TrackValue.BOOL else previous
                )
            return values
        exponent = r.read_choice(_Context.STEP)
        values = []
        previous = 0
        if not exponent:
            for _ in range(count):
                previous += r.read_number(_Context.EXACT_VALUE)
                values.append(_from_ordered(previous))
            return values
        step = math.ldexp(1.0, exponent - _STEP_EXPONENT_OFFSET)
        for at in range(count):
            previous += r.read_number(_Context.KEY_CHANGE if at else _Context.FIRST_KEY)
            values.append(to_float32(previous * step))
        return values

    def _read_modes(self, track: Track) -> None:
        r = self._reader
        modes, lefts, rights = [], [], []
        for _ in track.times:
            modes.append(self._checked(KeyMode, r.read(_Context.MODE)))
            lefts.append(r.read(_Context.LEFT_TYPE))
            rights.append(r.read(_Context.RIGHT_TYPE))
        track.key_modes = modes
        track.left_handle_types = lefts
        track.right_handle_types = rights

    def _read_handles(self, track: Track) -> None:
        references = _handle_references(track)
        track.handles = [
            _from_ordered(
                _ordered(reference) + self._reader.read_number(_Context.HANDLE)
            )
            for reference in references
        ]
