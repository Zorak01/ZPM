// SPDX-License-Identifier: GPL-3.0-or-later
using System.Runtime.CompilerServices;

namespace Zpm;

/// <summary>
/// One pair of stored values per corner, such as a normal or a UV. The file holds either each
/// distinct pair once with corners reaching back to it, or one pair per vertex with the corners
/// that disagree stored apart. Any corner reads directly. Allocates nothing.
/// </summary>
public unsafe struct ZpmCornerStream
{
    bool _isShared, _hasExceptions, _isBound;
    ZpmBackReferences _ids;
    ZpmColumn _a, _b;
    ZpmRankMask _exceptions;
    ZpmColumn _exceptionA, _exceptionB;

    /// <summary>Returns false until a section has provided this stream.</summary>
    public readonly bool isBound => _isBound;

    /// <summary>Points at one section of the stream. Returns false for a role that does not exist.</summary>
    public bool TryBindRole(int role, byte* section, ZpmSectionFlags flags, uint cornerCount)
    {
        _isBound = true;
        switch ((ZpmStreamRole)role)
        {
            case ZpmStreamRole.RepeatMask:
                _isShared = true;
                _ids.BindMask(section, cornerCount);
                return true;
            case ZpmStreamRole.Distances:
                _ids.BindDistances(section, flags);
                return true;
            case ZpmStreamRole.ValuesA:
            case ZpmStreamRole.VertexA:
                _a = ZpmColumn.Bind(section, flags);
                return true;
            case ZpmStreamRole.ValuesB:
            case ZpmStreamRole.VertexB:
                _b = ZpmColumn.Bind(section, flags);
                return true;
            case ZpmStreamRole.ExceptionMask:
                _hasExceptions = true;
                _exceptions = ZpmRankMask.Bind(section, cornerCount);
                return true;
            case ZpmStreamRole.ExceptionA:
                _exceptionA = ZpmColumn.Bind(section, flags);
                return true;
            case ZpmStreamRole.ExceptionB:
                _exceptionB = ZpmColumn.Bind(section, flags);
                return true;
            default:
                return false;
        }
    }

    /// <summary>Returns one corner's stored pair. Needs the corner's vertex, which the view supplies.</summary>
    [MethodImpl(MethodImplOptions.AggressiveInlining)]
    public readonly void GetPair(uint corner, uint vertex, out uint a, out uint b)
    {
        if (_isShared)
        {
            uint id = _ids.Get(corner);
            a = _a.Get(id);
            b = _b.Get(id);
            return;
        }
        if (_hasExceptions && _exceptions.IsSet(corner))
        {
            uint rank = _exceptions.GetSetRank(corner);
            a = _exceptionA.Get(rank);
            b = _exceptionB.Get(rank);
            return;
        }
        a = _a.Get(vertex);
        b = _b.Get(vertex);
    }

    /// <summary>
    /// Fills the caller's buffer with every corner's stored pair: the fastest way to read them all.
    /// corners holds each corner's vertex ID, as GetCorners writes them.
    /// </summary>
    public readonly void CopyAllPairs(uint* corners, uint cornerCount, uint* pairs)
    {
        if (_isShared)
        {
            uint ids = _ids.idCount;
            _a.CopyTo(0, ids, pairs, 2);
            _b.CopyTo(0, ids, pairs + 1, 2);
            _ids.ExpandAll(pairs, 2);
            return;
        }
        for (uint corner = 0; corner < cornerCount; corner++)
            GetPair(corner, corners[corner], out pairs[corner * 2], out pairs[corner * 2 + 1]);
    }

    /// <summary>Checks the stream fits the file and every lookup stays in range.</summary>
    public readonly bool Validate(byte* limit, uint cornerCount, uint vertexCount)
    {
        if (!_a.ValidateLayout(limit) || !_b.ValidateLayout(limit) || _a.count != _b.count)
            return false;
        if (_isShared)
            return _ids.Validate(limit, cornerCount) && _ids.idCount == _a.count;
        if (_a.count < vertexCount)
            return false;
        if (!_hasExceptions)
            return true;
        uint exceptionCount = _exceptionA.count;
        return exceptionCount <= cornerCount && _exceptionB.count == exceptionCount
            && _exceptions.Validate(limit, cornerCount - exceptionCount)
            && _exceptionA.ValidateLayout(limit) && _exceptionB.ValidateLayout(limit);
    }
}
