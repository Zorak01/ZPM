// SPDX-License-Identifier: GPL-3.0-or-later
namespace Zpm;

/// <summary>Clips, tracks and their key columns. Any key is one call away, and nothing is decoded.</summary>
public unsafe struct ZpmAnimation
{
    const int HandleFields = 4;
    const uint ByteMask = 255;

    ZpmSceneTable<ZpmClip> _clips;
    ZpmSceneTable<ZpmTrack> _tracks;
    ZpmSceneTable<uint> _timeStarts;
    ZpmFloatColumn _times, _handles;
    ZpmColumn _values, _modes;
    bool _hasTimes, _hasValues, _hasModes, _hasHandles;

    /// <summary>How many clips the file holds.</summary>
    public readonly uint clipCount => _clips.count;

    /// <summary>Points at one section, if it is an animation kind. Returns whether it was, and sets isValid.</summary>
    public bool TryBindSection(ZpmSectionKind kind, ZpmSectionFlags flags, byte* section, long length, out bool isValid)
    {
        isValid = true;
        switch (kind)
        {
            case ZpmSectionKind.ClipInfo: isValid = _clips.TryBind(section, length); return true;
            case ZpmSectionKind.TrackInfo: isValid = _tracks.TryBind(section, length); return true;
            case ZpmSectionKind.TimeStarts: isValid = _timeStarts.TryBind(section, length); return true;
            case ZpmSectionKind.Times: _times.Bind(section, flags); _hasTimes = true; return true;
            case ZpmSectionKind.KeyValues: _values = ZpmColumn.Bind(section, flags); _hasValues = true; return true;
            case ZpmSectionKind.KeyModes: _modes = ZpmColumn.Bind(section, flags); _hasModes = true; return true;
            case ZpmSectionKind.KeyHandles: _handles.Bind(section, flags); _hasHandles = true; return true;
            default: return false;
        }
    }

    /// <summary>Returns one clip. The index must be below clipCount.</summary>
    public readonly ref readonly ZpmClip GetClip(uint clip) => ref _clips[clip];

    /// <summary>Returns one track. Indices come from a clip's firstTrack.</summary>
    public readonly ref readonly ZpmTrack GetTrack(uint track) => ref _tracks[track];

    /// <summary>Returns the time of one key, in frames.</summary>
    public readonly float GetKeyTime(in ZpmTrack track, uint key) => _times.Get(_timeStarts[track.timeSet] + key);

    /// <summary>Returns one Float component of one key.</summary>
    public readonly float GetKeyFloat(in ZpmTrack track, uint key, uint component = 0) =>
        System.BitConverter.UInt32BitsToSingle(ZpmAxis.OrderedToBits(GetKeyStored(track, key, component)));

    /// <summary>
    /// Returns one key's stored value: an Int, a Bool, a Reference index or a string index.
    /// One call on a plain track. A sparse track counts its change flags, so walk those in key order.
    /// </summary>
    public readonly uint GetKeyStored(in ZpmTrack track, uint key, uint component = 0)
    {
        uint element = key * track.components + component;
        if ((track.flags & ZpmTrackFlags.Sparse) == 0)
            return _values.Get(track.firstValue + element);
        while (_values.Get(track.firstValue + element) == 0)
            element -= track.components;
        uint changedBefore = 0;
        for (uint at = 0; at < element; at++)
            changedBefore += _values.Get(track.firstValue + at);
        return _values.Get(track.firstValue + (uint)((ulong)track.keyCount * track.components) + changedBefore);
    }

    /// <summary>Returns how a key reaches the next one.</summary>
    public readonly ZpmKeyMode GetKeyMode(in ZpmTrack track, uint key) =>
        track.firstMode == ZpmLayout.NoIndex ? ZpmKeyMode.Linear : (ZpmKeyMode)(_modes.Get(track.firstMode + key) & ByteMask);

    /// <summary>Returns one field of a key's handles: left time, left value, right time, right value.</summary>
    public readonly float GetKeyHandle(in ZpmTrack track, uint key, uint field) => _handles.Get(track.firstHandle + key * HandleFields + field);

    /// <summary>Walks every track and proves its keys stay in range, including what reference keys name.</summary>
    public readonly bool Validate(byte* limit, in ZpmSceneView scene, uint stringCount)
    {
        if ((_hasValues && !_values.ValidateLayout(limit)) || (_hasModes && !_modes.ValidateLayout(limit))
            || (_hasHandles && !_handles.Validate(limit, _handles.count)))
            return false;
        uint times = _hasTimes ? _times.count : 0;
        if (_hasTimes && !_times.Validate(limit, times))
            return false;
        for (uint i = 1; i < _timeStarts.count; i++)
        {
            if (_timeStarts[i] < _timeStarts[i - 1])
                return false;
        }
        if (_timeStarts.count != 0 && (_timeStarts[0] != 0 || _timeStarts[_timeStarts.count - 1] > times))
            return false;
        for (uint i = 0; i < _clips.count; i++)
        {
            if ((ulong)_clips[i].firstTrack + _clips[i].trackCount > _tracks.count)
                return false;
        }
        for (uint i = 0; i < _tracks.count; i++)
        {
            if (!ValidateTrack(_tracks[i], scene, stringCount))
                return false;
        }
        return true;
    }

    readonly bool ValidateTrack(in ZpmTrack track, in ZpmSceneView scene, uint stringCount)
    {
        if (track.ownerKind > ZpmResourceKind.Camera || track.targetKind > ZpmResourceKind.Camera || track.valueType > ZpmTrackValue.Reference
            || track.owner >= scene.GetResourceCount(track.ownerKind) || track.path >= stringCount
            || (ulong)track.timeSet + 1 >= _timeStarts.count
            || _timeStarts[track.timeSet + 1] - _timeStarts[track.timeSet] != track.keyCount)
            return false;
        uint values = _hasValues ? _values.count : 0;
        ulong elements = (ulong)track.keyCount * track.components;
        if ((ulong)track.firstValue + elements > values)
            return false;
        ulong storedFrom = track.firstValue;
        ulong storedCount = elements;
        if ((track.flags & ZpmTrackFlags.Sparse) != 0)
        {
            storedFrom += elements;
            storedCount = 0;
            for (ulong at = 0; at < elements; at++)
            {
                uint flag = _values.Get(track.firstValue + (uint)at);
                if (flag > 1 || (at < track.components && flag == 0))
                    return false;
                storedCount += flag;
            }
            if (storedFrom + storedCount > values)
                return false;
        }
        if (track.firstMode != ZpmLayout.NoIndex && (!_hasModes || (ulong)track.firstMode + track.keyCount > _modes.count))
            return false;
        if (track.firstHandle != ZpmLayout.NoIndex && (!_hasHandles || (ulong)track.firstHandle + (ulong)track.keyCount * HandleFields > _handles.count))
            return false;
        if (track.valueType != ZpmTrackValue.Reference && track.valueType != ZpmTrackValue.String)
            return true;
        uint limit = track.valueType == ZpmTrackValue.String ? stringCount : scene.GetResourceCount(track.targetKind);
        for (ulong at = 0; at < storedCount; at++)
        {
            if (_values.Get((uint)(storedFrom + at)) >= limit)
                return false;
        }
        return true;
    }
}
