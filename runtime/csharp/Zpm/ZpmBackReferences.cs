// SPDX-License-Identifier: GPL-3.0-or-later
using System.Runtime.CompilerServices;

namespace Zpm;

/// <summary>
/// One ID per element, for IDs numbered by first use. An element either takes the next new ID or
/// stores how far back it reaches from the newest one. The newest ID at any element is its rank
/// in a mask, so any element reads directly with no running state.
/// </summary>
public unsafe struct ZpmBackReferences
{
    ZpmRankMask _mask;
    ZpmColumn _distances;
    uint _count;

    const uint DistanceBlock = 64;
    const int DistanceBufferLength = 128;

    /// <summary>How many elements there are.</summary>
    public readonly uint count => _count;

    /// <summary>How many new IDs the elements introduce in total.</summary>
    public readonly uint idCount => _count - _distances.count;

    public void BindMask(byte* section, uint count)
    {
        _count = count;
        _mask = ZpmRankMask.Bind(section, count);
    }

    public void BindDistances(byte* section, ZpmSectionFlags flags) => _distances = ZpmColumn.Bind(section, flags);

    /// <summary>Returns the ID of one element.</summary>
    [MethodImpl(MethodImplOptions.AggressiveInlining)]
    public readonly uint Get(uint index)
    {
        uint newCount = _mask.GetClearRank(index);
        return _mask.IsSet(index) ? newCount - 1 - _distances.Get(index - newCount) : newCount;
    }

    /// <summary>Returns whether this element reuses an earlier ID.</summary>
    [MethodImpl(MethodImplOptions.AggressiveInlining)]
    public readonly bool IsRepeat(uint index) => _mask.IsSet(index);

    /// <summary>Returns how many elements from this index on, inside its mask word, take new IDs.</summary>
    [MethodImpl(MethodImplOptions.AggressiveInlining)]
    public readonly uint GetNewRun(uint index) => _mask.GetClearRun(index);

    /// <summary>
    /// Spreads values out in place: the buffer starts with one group of width values per ID and
    /// ends with one group per element. A group's slot is never past the elements using it, so
    /// working from the end overwrites nothing before it is read. Distances unpack a block at a
    /// time on the stack, because reading them one by one was most of the cost.
    /// </summary>
    public readonly void ExpandAll(uint* values, uint width)
    {
        uint newest = idCount;
        uint repeated = _distances.count;
        uint* distances = stackalloc uint[DistanceBufferLength];
        uint loadedFrom = uint.MaxValue;
        for (uint element = _count; repeated != 0;)
        {
            element--;
            uint isRepeat = _mask.IsSet(element) ? 1u : 0u;
            repeated -= isRepeat;
            if (repeated < loadedFrom)
            {
                loadedFrom = repeated & ~(DistanceBlock - 1);
                _distances.CopyTo(loadedFrom, System.Math.Min(DistanceBlock, _distances.count - loadedFrom), distances, 1);
            }
            // A new ID reads a distance it then ignores, so the path does not depend on an unpredictable mask.
            uint source = newest - 1 - (distances[repeated - loadedFrom] & (0u - isRepeat));
            newest -= 1 - isRepeat;
            uint* from = values + source * width;
            uint* to = values + element * width;
            for (uint part = 0; part < width; part++)
                to[part] = from[part];
        }
    }

    /// <summary>
    /// Writes every ID to the caller's buffer of count elements. Distances unpack at the buffer's
    /// end and resolve forward: the read position never falls behind the write position.
    /// </summary>
    public readonly void CopyAllTo(uint* destination)
    {
        uint repeated = _distances.count;
        uint* distance = destination + _count - repeated;
        _distances.CopyTo(0, repeated, distance, 1);
        uint newest = 0;
        for (uint i = 0; i < _count; i++)
            destination[i] = _mask.IsSet(i) ? newest - 1 - *distance++ : newest++;
    }

    /// <summary>Walks every distance: only a running count can prove none reaches before the first ID.</summary>
    public readonly bool Validate(byte* limit, ulong maxIds)
    {
        uint repeated = _distances.count;
        if (repeated > _count || _count - repeated > maxIds
            || !_mask.Validate(limit, _count - repeated) || !_distances.ValidateLayout(limit))
            return false;
        uint newest = 0;
        uint next = 0;
        for (uint i = 0; i < _count; i++)
        {
            if (!_mask.IsSet(i))
                newest++;
            else if (_distances.Get(next++) >= newest)
                return false;
        }
        return true;
    }
}
