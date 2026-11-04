# SPDX-License-Identifier: GPL-3.0-or-later

"""Clips and tracks. Any field of any resource can animate, addressed by a path.

Tracks with the same key times share one list. Values sit in one column, so a held
value costs almost nothing, and any key is readable by index. Times and values stay
exact. A track whose keys are all linear stores no modes, and handles only exist on
tracks that have them. A sparse track's values start with one change flag per component
of every key, then only the changed values.
"""

from ..container.layout import (
    CLIP_INFO,
    KEY_LEFT_HANDLE_SHIFT,
    KEY_RIGHT_HANDLE_SHIFT,
    NO_INDEX,
    TRACK_INFO,
    KeyMode,
    ResourceKind,
    SectionKind,
    TrackFlags,
    TrackValue,
    bits_to_ordered,
    float_bits,
    float_from_bits,
    ordered_to_bits,
    to_signed,
)
from ..container.sections import Section, SectionReader, column_section
from ..errors import FormatError
from ..processing.snapping import snap_track
from ..scene import Clip, Scene, Track
from .packed.animation import encode_tracks, read_tracks
from .scene_tables import (
    checked_enum,
    float_column,
    indices_section,
    names_section,
    read_float_column,
    read_name_list,
    read_records,
    read_starts,
)
from .scene_values import StringTable

_HANDLE_FIELDS = 4
_BYTE_MASK = 255


def _stored_value(track: Track, value, strings: StringTable) -> int:
    if track.value_type == TrackValue.FLOAT:
        return bits_to_ordered(float_bits(value))
    if track.value_type == TrackValue.STRING:
        return strings.add(value)
    return int(value) & 0xFFFFFFFF


def _read_value(track: Track, stored: int, strings: list):
    if track.value_type == TrackValue.FLOAT:
        return float_from_bits(ordered_to_bits(stored))
    if track.value_type == TrackValue.STRING:
        if stored >= len(strings):
            raise FormatError("A key names a missing string")
        return strings[stored]
    if track.value_type == TrackValue.BOOL:
        return bool(stored)
    if track.value_type == TrackValue.INT:
        return to_signed(stored)
    return stored


def _pack_mode(track: Track, key: int) -> int:
    left = track.left_handle_types[key] if track.left_handle_types else 0
    right = track.right_handle_types[key] if track.right_handle_types else 0
    return (
        track.key_modes[key]
        | left << KEY_LEFT_HANDLE_SHIFT
        | right << KEY_RIGHT_HANDLE_SHIFT
    )


def encode_animation(
    scene: Scene, strings: StringTable, *, packed: bool = False
) -> list:
    """Return every clip and track, with identical key times stored once.

    Packed, the tracks are one stream read once, and float values snap to their grid:
    see snap_track. Otherwise every key reads by index.
    """
    if not scene.clips:
        return []
    sections = _clip_sections(scene)
    tracks = [track for clip in scene.clips for track in clip.tracks]
    if not tracks:
        return sections
    if packed:
        snapped = [snap_track(track) for track in tracks]
        data = encode_tracks(snapped, scene, strings)
        return [*sections, Section(SectionKind.PACKED_TRACKS, data)]
    return sections + _track_sections(tracks, strings)


def _clip_sections(scene: Scene) -> list:
    clip_records = bytearray()
    first_track = 0
    for clip in scene.clips:
        clip_records += CLIP_INFO.pack(
            first_track,
            len(clip.tracks),
            clip.frames_per_second,
            clip.start,
            clip.end,
        )
        first_track += len(clip.tracks)
    return [
        *names_section(SectionKind.CLIP_NAMES, [clip.name for clip in scene.clips]),
        Section(SectionKind.CLIP_INFO, bytes(clip_records)),
    ]


def _track_sections(tracks: list, strings: StringTable) -> list:
    time_sets = {}
    values, modes, handles = [], [], []
    track_records = bytearray()
    for track in tracks:
        first_mode = len(modes) if track.key_modes else NO_INDEX
        first_handle = len(handles) if track.handles else NO_INDEX
        first_value = len(values)
        values += [int(is_changed) for is_changed in track.changed]
        values += [_stored_value(track, v, strings) for v in track.values]
        if track.key_modes:
            modes += [_pack_mode(track, key) for key in range(len(track.times))]
        handles += track.handles
        time_set = time_sets.setdefault(tuple(track.times), len(time_sets))
        track_records += TRACK_INFO.pack(
            track.owner_kind,
            track.owner,
            strings.add(track.path),
            track.array_index,
            track.value_type,
            track.target_kind,
            track.components,
            track.flags | (TrackFlags.SPARSE if track.changed else 0),
            time_set,
            len(track.times),
            first_value,
            first_mode,
            first_handle,
        )
    starts = [0]
    for times in time_sets:
        starts.append(starts[-1] + len(times))
    sections = [
        Section(SectionKind.TRACK_INFO, bytes(track_records)),
        *indices_section(SectionKind.TIME_STARTS, starts),
        float_column(SectionKind.TIMES, [t for times in time_sets for t in times]),
        column_section(SectionKind.KEY_VALUES, values),
    ]
    if modes:
        sections.append(column_section(SectionKind.KEY_MODES, modes))
    if handles:
        sections.append(float_column(SectionKind.KEY_HANDLES, handles))
    return sections


class _KeyColumns:
    def __init__(self, sections: SectionReader):
        kind = SectionKind.TIME_STARTS
        count = len(sections.raw(kind)) // 4 - 1 if sections.has(kind) else 0
        self.starts = read_starts(sections, kind, count)
        self.times = read_float_column(sections, SectionKind.TIMES, self.starts[-1])
        self.values = self._column(sections, SectionKind.KEY_VALUES)
        self.modes = self._column(sections, SectionKind.KEY_MODES)
        kind = SectionKind.KEY_HANDLES
        count = sections.count_of(kind) if sections.has(kind) else 0
        self.handles = read_float_column(sections, kind, count)

    @staticmethod
    def _column(sections: SectionReader, kind) -> list:
        return (
            sections.column(kind, sections.count_of(kind)) if sections.has(kind) else []
        )


def _read_track(values: tuple, columns: _KeyColumns, strings: list) -> Track:
    (
        owner_kind,
        owner,
        path,
        array_index,
        value_type,
        target_kind,
        components,
        flags,
        time_set,
        key_count,
        first_value,
        first_mode,
        first_handle,
    ) = values
    if time_set + 1 >= len(columns.starts) or path >= len(strings):
        raise FormatError("A track links to something missing")
    start = columns.starts[time_set]
    times = columns.times[start : columns.starts[time_set + 1]]
    stored_count = key_count * components
    if len(times) != key_count or first_value + stored_count > len(columns.values):
        raise FormatError("A track's keys do not match its columns")
    changed = []
    if TrackFlags(flags) & TrackFlags.SPARSE:
        marks = columns.values[first_value : first_value + stored_count]
        if any(mark > 1 for mark in marks) or not all(marks[:components]):
            raise FormatError("A sparse track has wrong change flags")
        changed = [bool(mark) for mark in marks]
        first_value += stored_count
        stored_count = sum(marks)
        if first_value + stored_count > len(columns.values):
            raise FormatError("A track's keys do not match its columns")
    track = Track(
        checked_enum(ResourceKind, owner_kind),
        owner,
        strings[path],
        times,
        [],
        array_index,
        checked_enum(TrackValue, value_type),
        checked_enum(ResourceKind, target_kind),
        components,
        TrackFlags(flags) & ~TrackFlags.SPARSE,
        changed=changed,
    )
    track.values = [
        _read_value(track, stored, strings)
        for stored in columns.values[first_value : first_value + stored_count]
    ]
    if first_mode != NO_INDEX:
        if first_mode + key_count > len(columns.modes):
            raise FormatError("A track's modes run past their column")
        packed = columns.modes[first_mode : first_mode + key_count]
        track.key_modes = [checked_enum(KeyMode, v & _BYTE_MASK) for v in packed]
        track.left_handle_types = [
            v >> KEY_LEFT_HANDLE_SHIFT & _BYTE_MASK for v in packed
        ]
        track.right_handle_types = [
            v >> KEY_RIGHT_HANDLE_SHIFT & _BYTE_MASK for v in packed
        ]
    if first_handle != NO_INDEX:
        end = first_handle + key_count * _HANDLE_FIELDS
        if end > len(columns.handles):
            raise FormatError("A track's handles run past their column")
        track.handles = columns.handles[first_handle:end]
    return track


def read_animation(sections: SectionReader, strings: list, scene: Scene) -> list:
    """Return every clip. scene supplies the objects and armatures packed bone paths
    name. Raises FormatError for anything malformed."""
    names = read_name_list(sections, SectionKind.CLIP_NAMES)
    if not names:
        return []
    infos = read_records(sections, SectionKind.CLIP_INFO, CLIP_INFO, len(names))
    if sections.has(SectionKind.PACKED_TRACKS):
        count = sum(info[1] for info in infos)
        data = sections.raw(SectionKind.PACKED_TRACKS)
        try:
            tracks = read_tracks(data, count, scene, strings)
        except (IndexError, ValueError, OverflowError) as error:
            raise FormatError("Packed animation is malformed") from error
    else:
        tracks = _read_tracks(sections, strings)
    clips = []
    for name, (first, count, rate, start, end) in zip(names, infos, strict=True):
        if first + count > len(tracks):
            raise FormatError(f"Clip {name} has missing tracks")
        clips.append(Clip(name, rate, start, end, tracks[first : first + count]))
    return clips


def _read_tracks(sections: SectionReader, strings: list) -> list:
    kind = SectionKind.TRACK_INFO
    track_count = (
        len(sections.raw(kind)) // TRACK_INFO.size if sections.has(kind) else 0
    )
    records = read_records(sections, kind, TRACK_INFO, track_count)
    columns = _KeyColumns(sections)
    return [_read_track(values, columns, strings) for values in records]
