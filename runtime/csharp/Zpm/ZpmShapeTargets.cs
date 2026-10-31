// SPDX-License-Identifier: GPL-3.0-or-later
using System.Runtime.CompilerServices;

namespace Zpm;

/// <summary>
/// The file's blendshape tables and columns. Targets share one list of rising vertex IDs with one
/// start per target, so finding a vertex in a target is a binary search over that target's run.
/// </summary>
public unsafe struct ZpmShapeTargets
{
    const uint RelativeFlag = 1;
    const uint MutedFlag = 2;
    const int InterpolationShift = 8;
    const int InfoFields = 8;

    ZpmNames _names, _groups;
    byte* _info;
    ZpmColumn _starts, _vertices, _x, _y, _z, _normalX, _normalY, _normalZ, _tangentX, _tangentY, _tangentZ;
    bool _hasGroups, _hasStarts, _hasVertices, _hasX, _hasY, _hasZ, _hasNormalX, _hasNormalY, _hasNormalZ, _hasTangentX, _hasTangentY, _hasTangentZ;

    /// <summary>How many targets the file holds.</summary>
    public readonly uint count => _names.count;

    /// <summary>Whether targets carry normal changes.</summary>
    public readonly bool hasNormalOffsets => _hasNormalX;

    /// <summary>Whether targets carry tangent changes.</summary>
    public readonly bool hasTangentOffsets => _hasTangentX;

    /// <summary>Points at one section, if it is a blendshape kind. Returns whether it was.</summary>
    public bool TryBindSection(ZpmSectionKind kind, ZpmSectionFlags flags, byte* section)
    {
        switch (kind)
        {
            case ZpmSectionKind.ShapeNames: _names = ZpmNames.Bind(section); return true;
            case ZpmSectionKind.ShapeGroupNames: _groups = ZpmNames.Bind(section); _hasGroups = true; return true;
            case ZpmSectionKind.ShapeInfo: _info = section; return true;
            case ZpmSectionKind.ShapeStarts: _starts = ZpmColumn.Bind(section, flags); _hasStarts = true; return true;
            case ZpmSectionKind.ShapeVertices: _vertices = ZpmColumn.Bind(section, flags); _hasVertices = true; return true;
            case ZpmSectionKind.ShapePositionX: _x = ZpmColumn.Bind(section, flags); _hasX = true; return true;
            case ZpmSectionKind.ShapePositionY: _y = ZpmColumn.Bind(section, flags); _hasY = true; return true;
            case ZpmSectionKind.ShapePositionZ: _z = ZpmColumn.Bind(section, flags); _hasZ = true; return true;
            case ZpmSectionKind.ShapeNormalX: _normalX = ZpmColumn.Bind(section, flags); _hasNormalX = true; return true;
            case ZpmSectionKind.ShapeNormalY: _normalY = ZpmColumn.Bind(section, flags); _hasNormalY = true; return true;
            case ZpmSectionKind.ShapeNormalZ: _normalZ = ZpmColumn.Bind(section, flags); _hasNormalZ = true; return true;
            case ZpmSectionKind.ShapeTangentX: _tangentX = ZpmColumn.Bind(section, flags); _hasTangentX = true; return true;
            case ZpmSectionKind.ShapeTangentY: _tangentY = ZpmColumn.Bind(section, flags); _hasTangentY = true; return true;
            case ZpmSectionKind.ShapeTangentZ: _tangentZ = ZpmColumn.Bind(section, flags); _hasTangentZ = true; return true;
            default: return false;
        }
    }

    /// <summary>Returns a target's facts. The index must be below count.</summary>
    public readonly ZpmShapeTarget GetTarget(uint target)
    {
        byte* at = _info + target * ZpmLayout.ShapeInfoBytes;
        uint flags = Unsafe.ReadUnaligned<uint>(at + 28);
        return new ZpmShapeTarget(
            Unsafe.ReadUnaligned<uint>(at),
            Unsafe.ReadUnaligned<uint>(at + 4),
            Unsafe.ReadUnaligned<float>(at + 8),
            Unsafe.ReadUnaligned<float>(at + 12),
            Unsafe.ReadUnaligned<float>(at + 16),
            Unsafe.ReadUnaligned<float>(at + 20),
            Unsafe.ReadUnaligned<float>(at + 24),
            (flags & RelativeFlag) != 0,
            (flags & MutedFlag) != 0,
            (ZpmShapeInterpolation)(flags >> InterpolationShift));
    }

    /// <summary>Finds a target's UTF-8 name. Returns false when out of range.</summary>
    public readonly bool TryGetName(uint target, out byte* utf8, out uint length) => _names.TryGet(target, out utf8, out length);

    /// <summary>Finds the name of the vertex group limiting a target. Returns false when it has none.</summary>
    public readonly bool TryGetVertexGroupName(uint target, out byte* utf8, out uint length)
    {
        utf8 = null;
        length = 0;
        return _hasGroups && _groups.TryGet(target, out utf8, out length) && length != 0;
    }

    /// <summary>Returns where a target's run starts in the shared columns.</summary>
    [MethodImpl(MethodImplOptions.AggressiveInlining)]
    public readonly uint GetStart(uint target) => _starts.Get(target);

    /// <summary>Returns how many vertices a target changes.</summary>
    public readonly uint GetAffectedCount(uint target) => _starts.Get(target + 1) - _starts.Get(target);

    /// <summary>Returns the vertex at one place in the shared columns.</summary>
    [MethodImpl(MethodImplOptions.AggressiveInlining)]
    public readonly uint GetVertexAt(uint at) => _vertices.Get(at);

    /// <summary>Returns the stored position values at one place: grid steps, or ordered bits with no grid.</summary>
    [MethodImpl(MethodImplOptions.AggressiveInlining)]
    public readonly void GetStoredPosition(uint at, out uint x, out uint y, out uint z)
    {
        x = _x.Get(at);
        y = _y.Get(at);
        z = _z.Get(at);
    }

    /// <summary>Returns a normal change at one place. The file must have them.</summary>
    public readonly ZpmNormal GetNormalOffsetAt(uint at) => new(
        (int)_normalX.Get(at) / ZpmLayout.OffsetGridScale,
        (int)_normalY.Get(at) / ZpmLayout.OffsetGridScale,
        (int)_normalZ.Get(at) / ZpmLayout.OffsetGridScale);

    /// <summary>Returns a tangent change at one place. The file must have them.</summary>
    public readonly ZpmNormal GetTangentOffsetAt(uint at) => new(
        (int)_tangentX.Get(at) / ZpmLayout.OffsetGridScale,
        (int)_tangentY.Get(at) / ZpmLayout.OffsetGridScale,
        (int)_tangentZ.Get(at) / ZpmLayout.OffsetGridScale);

    /// <summary>Finds a vertex in a target's run, by binary search. Returns false when it does not move.</summary>
    public readonly bool TryFindVertex(uint target, uint vertex, out uint at)
    {
        uint low = _starts.Get(target), high = _starts.Get(target + 1);
        while (low < high)
        {
            uint middle = low + (high - low) / 2;
            uint found = _vertices.Get(middle);
            if (found == vertex)
            {
                at = middle;
                return true;
            }
            if (found < vertex)
                low = middle + 1;
            else
                high = middle;
        }
        at = 0;
        return false;
    }

    /// <summary>Walks every start, vertex and target entry: only reading them can prove they are in range.</summary>
    public readonly bool Validate(byte* limit, uint vertexCount)
    {
        bool anyColumn = _hasStarts || _hasVertices || _hasX || _hasY || _hasZ || _hasGroups || _info != null
            || _hasNormalX || _hasNormalY || _hasNormalZ || _hasTangentX || _hasTangentY || _hasTangentZ;
        uint targets = _names.count;
        if (targets == 0)
            return !anyColumn && _names.Validate(limit);
        if (!_names.Validate(limit) || !_hasStarts || !_hasVertices || !_hasX || !_hasY || !_hasZ || _info == null
            || (ulong)(limit - _info) < (ulong)targets * ZpmLayout.ShapeInfoBytes)
            return false;
        if (_hasGroups && (!_groups.Validate(limit) || _groups.count != targets))
            return false;
        if (_starts.count != (ulong)targets + 1 || !_starts.ValidateLayout(limit) || !_vertices.ValidateLayout(limit))
            return false;
        uint total = _vertices.count;
        if (!ValidateChannel(limit, total, true, _hasX, _x) || !ValidateChannel(limit, total, true, _hasY, _y) || !ValidateChannel(limit, total, true, _hasZ, _z)
            || !ValidateOptional(limit, total, _hasNormalX, _hasNormalY, _hasNormalZ, _normalX, _normalY, _normalZ)
            || !ValidateOptional(limit, total, _hasTangentX, _hasTangentY, _hasTangentZ, _tangentX, _tangentY, _tangentZ))
            return false;
        uint start = _starts.Get(0);
        if (start != 0)
            return false;
        for (uint target = 0; target < targets; target++)
        {
            uint end = _starts.Get(target + 1);
            if (end < start || end > total || !ValidateTargetEntry(target, targets))
                return false;
            uint previous = 0;
            for (uint at = start; at < end; at++)
            {
                uint vertex = _vertices.Get(at);
                if (vertex >= vertexCount || (at != start && vertex <= previous))
                    return false;
                previous = vertex;
            }
            start = end;
        }
        return start == total;
    }

    readonly bool ValidateTargetEntry(uint target, uint targets)
    {
        ZpmShapeTarget entry = GetTarget(target);
        return entry.reference < targets && entry.interpolation <= ZpmShapeInterpolation.BSpline
            && (entry.inBetweenOf == ZpmShapeTarget.NoTarget || entry.inBetweenOf < targets);
    }

    static bool ValidateChannel(byte* limit, uint total, bool isRequired, bool isPresent, in ZpmColumn column) =>
        isPresent ? column.count == total && column.ValidateLayout(limit) : !isRequired;

    static bool ValidateOptional(byte* limit, uint total, bool hasX, bool hasY, bool hasZ, in ZpmColumn x, in ZpmColumn y, in ZpmColumn z) =>
        hasX == hasY && hasY == hasZ
        && ValidateChannel(limit, total, false, hasX, x) && ValidateChannel(limit, total, false, hasY, y) && ValidateChannel(limit, total, false, hasZ, z);
}
