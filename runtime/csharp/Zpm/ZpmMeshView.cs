// SPDX-License-Identifier: GPL-3.0-or-later
using System;
using System.Runtime.CompilerServices;

namespace Zpm;

/// <summary>
/// Reads a ZPM mesh straight from its file bytes. The view holds pointers and never allocates,
/// copies or decodes: any vertex, corner, face or edge is one call away. The bytes must stay
/// alive and pinned while the view is used.
/// </summary>
public unsafe struct ZpmMeshView
{
    uint _vertexCount, _faceCount, _cornerCount, _edgeCount, _uniqueCount, _arity;
    byte* _limit;

    bool _hasRepeats;
    ZpmRankMask _repeats;
    ZpmColumn _repeatIds;
    ZpmAxis _x, _y, _z;

    bool _hasRelativeCorners;
    ZpmColumn _corners, _offset1, _offset2, _offset3;
    bool _hasOffset1, _hasOffset2, _hasOffset3;
    bool _hasBackDistances, _hasFaceStarts;
    ZpmRankMask _cornerRepeats;
    ZpmColumn _backDistances;
    ZpmSelectBits _faceStarts;

    bool _hasEndpoints, _hasCorners, _hasSpan, _hasLooseMask, _hasLooseFirst, _hasLooseSecond, _hasEdgeCorners, _hasFlips, _hasFirstUse;
    ZpmColumn _edgeFirst, _edgeSpan;
    ZpmRankMask _looseMask;
    ZpmColumn _looseFirst, _looseSecond;
    ZpmColumn _edgeCorners, _edgeFlips;
    ZpmSelectBits _edgeFirstUse;

    public readonly uint vertexCount => _vertexCount;
    public readonly uint faceCount => _faceCount;
    public readonly uint cornerCount => _cornerCount;
    public readonly uint edgeCount => _edgeCount;

    /// <summary>The corner count shared by every face, or 0 when faces differ.</summary>
    public readonly uint arity => _arity;

    /// <summary>
    /// Reads the header and directory and points the view at each section, without reading mesh
    /// data. Returns false when the file is malformed. Call Validate before reading untrusted bytes.
    /// </summary>
    public static bool TryOpen(byte* data, long length, out ZpmMeshView view)
    {
        view = default;
        if (length < ZpmLayout.FileHeaderBytes + ZpmLayout.TailBytes || length > uint.MaxValue)
            return false;
        if (Unsafe.ReadUnaligned<uint>(data) != ZpmLayout.Magic
            || Unsafe.ReadUnaligned<ushort>(data + 4) != ZpmLayout.Version
            || Unsafe.ReadUnaligned<uint>(data + 8) != length)
            return false;
        uint sections = Unsafe.ReadUnaligned<ushort>(data + 6);
        long directoryEnd = ZpmLayout.FileHeaderBytes + (long)sections * ZpmLayout.DirectoryEntryBytes;
        long limit = length - ZpmLayout.TailBytes;
        if (directoryEnd > limit)
            return false;
        view._limit = data + limit;

        // Mesh info is the lowest kind, so it is read first and every later section can use its counts.
        int previousKind = 0;
        long previousOffset = directoryEnd;
        for (uint i = 0; i < sections; i++)
        {
            byte* entry = data + ZpmLayout.FileHeaderBytes + i * ZpmLayout.DirectoryEntryBytes;
            int kind = Unsafe.ReadUnaligned<ushort>(entry);
            var flags = (ZpmSectionFlags)Unsafe.ReadUnaligned<ushort>(entry + 2);
            uint offset = Unsafe.ReadUnaligned<uint>(entry + 4);
            if (kind <= previousKind || offset < previousOffset || (offset & 3) != 0 || offset + (long)ZpmLayout.ColumnCountBytes > limit)
                return false;
            if (i == 0 && (ZpmSectionKind)kind != ZpmSectionKind.MeshInfo)
                return false;
            if (!view.TryBindSection((ZpmSectionKind)kind, flags, data + offset, limit - offset))
                return false;
            previousKind = kind;
            previousOffset = offset;
        }
        return sections != 0 && view.HasEverySection();
    }

    bool TryBindSection(ZpmSectionKind kind, ZpmSectionFlags flags, byte* section, long available)
    {
        switch (kind)
        {
            case ZpmSectionKind.MeshInfo:
                return available >= ZpmLayout.MeshInfoBytes && TryReadMeshInfo(section);
            case ZpmSectionKind.PositionRepeatMask:
                _hasRepeats = true;
                _repeats = ZpmRankMask.Bind(section, _vertexCount);
                return true;
            case ZpmSectionKind.PositionRepeatIds:
                _repeatIds = ZpmColumn.Bind(section, flags);
                return _hasRepeats;
            case ZpmSectionKind.CornerIds:
                _corners = ZpmColumn.Bind(section, flags);
                _hasCorners = true;
                return true;
            case ZpmSectionKind.CornerFirst:
                _corners = ZpmColumn.Bind(section, flags);
                _hasCorners = true;
                _hasRelativeCorners = true;
                return true;
            case ZpmSectionKind.CornerOffset1:
                _offset1 = ZpmColumn.Bind(section, flags);
                _hasOffset1 = true;
                return true;
            case ZpmSectionKind.CornerOffset2:
                _offset2 = ZpmColumn.Bind(section, flags);
                _hasOffset2 = true;
                return true;
            case ZpmSectionKind.CornerOffset3:
                _offset3 = ZpmColumn.Bind(section, flags);
                _hasOffset3 = true;
                return true;
            case ZpmSectionKind.FaceStarts:
                _faceStarts = ZpmSelectBits.Bind(section, _cornerCount);
                _hasFaceStarts = true;
                return true;
            case ZpmSectionKind.CornerRepeatMask:
                _cornerRepeats = ZpmRankMask.Bind(section, _cornerCount);
                _hasCorners = true;
                return true;
            case ZpmSectionKind.CornerBackDistances:
                _backDistances = ZpmColumn.Bind(section, flags);
                _hasBackDistances = true;
                return true;
            case ZpmSectionKind.EdgeFirstUse:
                _edgeFirstUse = ZpmSelectBits.Bind(section, _cornerCount);
                _hasFirstUse = true;
                return true;
            case ZpmSectionKind.EdgeFirst:
                _edgeFirst = ZpmColumn.Bind(section, flags);
                _hasEndpoints = true;
                return true;
            case ZpmSectionKind.EdgeSpan:
                _edgeSpan = ZpmColumn.Bind(section, flags);
                _hasSpan = true;
                return true;
            case ZpmSectionKind.EdgeLooseMask:
                _looseMask = ZpmRankMask.Bind(section, _edgeCount);
                _hasLooseMask = true;
                return true;
            case ZpmSectionKind.EdgeLooseFirst:
                _looseFirst = ZpmColumn.Bind(section, flags);
                _hasLooseFirst = true;
                return true;
            case ZpmSectionKind.EdgeLooseSecond:
                _looseSecond = ZpmColumn.Bind(section, flags);
                _hasLooseSecond = true;
                return true;
            case ZpmSectionKind.EdgeCorners:
                _edgeCorners = ZpmColumn.Bind(section, flags);
                _hasEdgeCorners = true;
                return true;
            case ZpmSectionKind.EdgeFlips:
                _edgeFlips = ZpmColumn.Bind(section, flags);
                _hasFlips = true;
                return true;
            default:
                return TryBindAxisSection(kind, flags, section);
        }
    }

    bool TryBindAxisSection(ZpmSectionKind kind, ZpmSectionFlags flags, byte* section)
    {
        int value = (int)kind;
        int axisIndex = value & 3;
        if (axisIndex >= ZpmLayout.AxisCount)
            return false;
        ref ZpmAxis axis = ref axisIndex == 0 ? ref _x : ref axisIndex == 1 ? ref _y : ref _z;
        switch ((ZpmSectionKind)(value & ~3))
        {
            case ZpmSectionKind.PositionBitsX:
                axis.BindBits(section, flags);
                return true;
            case ZpmSectionKind.PositionConstantX:
                axis.BindConstant(section);
                return true;
            case ZpmSectionKind.PositionPaletteX:
                axis.BindPalette(section, flags);
                return true;
            case ZpmSectionKind.PositionIndicesX:
                axis.BindIndices(section, flags);
                return true;
            default:
                return false;
        }
    }

    bool TryReadMeshInfo(byte* info)
    {
        _vertexCount = Unsafe.ReadUnaligned<uint>(info);
        _faceCount = Unsafe.ReadUnaligned<uint>(info + 4);
        _cornerCount = Unsafe.ReadUnaligned<uint>(info + 8);
        _edgeCount = Unsafe.ReadUnaligned<uint>(info + 12);
        _uniqueCount = Unsafe.ReadUnaligned<uint>(info + 16);
        _arity = Unsafe.ReadUnaligned<uint>(info + 20);
        if (_uniqueCount > _vertexCount)
            return false;
        return _arity == 0 ? _cornerCount != 0 || _faceCount == 0 : (ulong)_arity * _faceCount == _cornerCount;
    }

    /// <summary>Checks the sections present are a complete, legal set for this mesh's counts.</summary>
    readonly bool HasEverySection()
    {
        if (_vertexCount != 0 && (!_x.isBound || !_y.isBound || !_z.isBound))
            return false;
        if (!_hasRepeats && _uniqueCount != _vertexCount)
            return false;
        if (_cornerCount != 0)
        {
            if (!_hasCorners || (_arity == 0 && !_hasFaceStarts))
                return false;
            if (_hasRelativeCorners && (_arity < 3 || _arity > ZpmLayout.MaxRelativeArity
                || !_hasOffset1 || !_hasOffset2 || (_arity == 4 && !_hasOffset3)))
                return false;
        }
        if (_edgeCount != 0)
        {
            if (_hasEndpoints != _hasSpan || (!_hasEndpoints && !_hasEdgeCorners && !_hasFirstUse && !_hasLooseMask))
                return false;
            if (_hasLooseMask && (!_hasLooseFirst || !_hasLooseSecond))
                return false;
        }
        return true;
    }

    /// <summary>
    /// Proves every read stays inside the file and every ID is in range. Reads block descriptors,
    /// and a block's values only when its descriptor cannot prove it. Returns false on a malformed file.
    /// </summary>
    public readonly bool Validate()
    {
        return ValidatePositions() && ValidateCorners() && ValidateEdges();
    }

    readonly bool ValidatePositions()
    {
        if (_hasRepeats)
        {
            if (!_repeats.Validate(_limit, _uniqueCount) || _repeatIds.count != _vertexCount - _uniqueCount
                || !_repeatIds.ValidateLayout(_limit) || !_repeatIds.ValidateBelow(_uniqueCount))
                return false;
        }
        return _vertexCount == 0
            || (_x.Validate(_limit, _uniqueCount) && _y.Validate(_limit, _uniqueCount) && _z.Validate(_limit, _uniqueCount));
    }

    readonly bool ValidateCorners()
    {
        if (_cornerCount == 0)
            return true;
        if (_hasBackDistances)
        {
            if (!ValidateBackDistances())
                return false;
        }
        else if (_hasRelativeCorners)
        {
            if (_corners.count != _faceCount || !_corners.ValidateLayout(_limit) || !_corners.ValidateBelow(_vertexCount)
                || !ValidateOffsets(_offset1) || !ValidateOffsets(_offset2) || (_arity == 4 && !ValidateOffsets(_offset3)))
                return false;
        }
        else if (_corners.count != _cornerCount || !_corners.ValidateLayout(_limit) || !_corners.ValidateBelow(_vertexCount))
            return false;
        return _arity != 0 || ValidateFaceStarts();
    }

    readonly bool ValidateOffsets(in ZpmColumn offsets) =>
        offsets.count == _faceCount && offsets.ValidateLayout(_limit) && offsets.ValidateSumBelow(_corners, _vertexCount);

    /// <summary>Walks every distance: only a running count can prove none reaches before vertex 0.</summary>
    readonly bool ValidateBackDistances()
    {
        uint repeated = _backDistances.count;
        if (repeated > _cornerCount || _cornerCount - repeated > _vertexCount
            || !_cornerRepeats.Validate(_limit, _cornerCount - repeated) || !_backDistances.ValidateLayout(_limit))
            return false;
        uint newest = 0;
        uint next = 0;
        for (uint corner = 0; corner < _cornerCount; corner++)
        {
            if (!_cornerRepeats.IsSet(corner))
                newest++;
            else if (_backDistances.Get(next++) >= newest)
                return false;
        }
        return true;
    }

    readonly bool ValidateFaceStarts() => _faceStarts.Validate(_limit, _faceCount) && _faceStarts.IsSet(0);

    readonly bool ValidateEdges()
    {
        if (_edgeCount == 0)
            return true;
        if (_hasEndpoints)
        {
            return _edgeFirst.count == _edgeCount && _edgeSpan.count == _edgeCount
                && _edgeFirst.ValidateLayout(_limit) && _edgeSpan.ValidateLayout(_limit)
                && _edgeFirst.ValidateBelow(_vertexCount) && _edgeSpan.ValidateSumBelow(_edgeFirst, _vertexCount);
        }
        uint looseCount = 0;
        if (_hasLooseMask)
        {
            looseCount = _looseFirst.count;
            if (looseCount > _edgeCount || _looseSecond.count != looseCount
                || !_looseMask.Validate(_limit, _edgeCount - looseCount)
                || !_looseFirst.ValidateLayout(_limit) || !_looseSecond.ValidateLayout(_limit)
                || !_looseFirst.ValidateBelow(_vertexCount) || !_looseSecond.ValidateBelow(_vertexCount))
                return false;
        }
        uint attached = _edgeCount - looseCount;
        if (attached == 0)
            return true;
        if (_cornerCount == 0)
            return false;
        if (_hasFirstUse)
            return _edgeFirstUse.Validate(_limit, attached);
        if (!_hasEdgeCorners || _edgeCorners.count != attached
            || !_edgeCorners.ValidateLayout(_limit) || !_edgeCorners.ValidateBelow(_cornerCount))
            return false;
        return !_hasFlips || (_edgeFlips.count == attached && _edgeFlips.ValidateLayout(_limit) && _edgeFlips.ValidateBelow(2));
    }

    /// <summary>Returns one vertex position. Touches only the blocks that hold it.</summary>
    [MethodImpl(MethodImplOptions.AggressiveInlining)]
    public readonly ZpmVertex GetVertex(uint index)
    {
        uint unique = index;
        if (_hasRepeats)
            unique = _repeats.IsSet(index) ? _repeatIds.Get(_repeats.GetSetRank(index)) : _repeats.GetClearRank(index);
        return new ZpmVertex(_x.GetBits(unique), _y.GetBits(unique), _z.GetBits(unique));
    }

    /// <summary>Writes a range of positions to the caller's buffer as XYZ bit patterns.</summary>
    public readonly void GetVertices(uint first, uint length, uint* destination)
    {
        if (!_hasRepeats)
        {
            _x.CopyTo(first, length, destination);
            _y.CopyTo(first, length, destination + 1);
            _z.CopyTo(first, length, destination + 2);
            return;
        }
        if (first == 0 && length == _vertexCount)
        {
            ExpandAllVertices(destination);
            return;
        }
        uint end = first + length;
        for (uint vertex = first; vertex < end;)
        {
            if (_repeats.IsSet(vertex))
            {
                uint unique = _repeatIds.Get(_repeats.GetSetRank(vertex));
                destination[0] = _x.GetBits(unique);
                destination[1] = _y.GetBits(unique);
                destination[2] = _z.GetBits(unique);
                destination += 3;
                vertex++;
                continue;
            }
            uint run = Math.Min(_repeats.GetClearRun(vertex), end - vertex);
            uint start = _repeats.GetClearRank(vertex);
            _x.CopyTo(start, run, destination);
            _y.CopyTo(start, run, destination + 1);
            _z.CopyTo(start, run, destination + 2);
            destination += run * 3;
            vertex += run;
        }
    }

    /// <summary>
    /// Writes each unique position once at the start of the buffer, then spreads them out from the
    /// end. A unique position's slot is never past the vertices that use it, so nothing is overwritten
    /// before it is read and no second buffer is needed.
    /// </summary>
    readonly void ExpandAllVertices(uint* destination)
    {
        uint unique = _uniqueCount;
        _x.CopyTo(0, unique, destination);
        _y.CopyTo(0, unique, destination + 1);
        _z.CopyTo(0, unique, destination + 2);
        uint repeated = _vertexCount - unique;
        for (uint vertex = _vertexCount; repeated != 0;)
        {
            vertex--;
            uint source = _repeats.IsSet(vertex) ? _repeatIds.Get(--repeated) : --unique;
            uint* from = destination + source * 3;
            uint* to = destination + vertex * 3;
            to[0] = from[0];
            to[1] = from[1];
            to[2] = from[2];
        }
    }

    /// <summary>
    /// Fills the caller's buffers with the whole mesh, the fastest way to read everything.
    /// Edges resolve through the corners just written, so each costs one array read.
    /// </summary>
    public readonly void GetMesh(uint* positions, uint* faceSizes, uint* corners, uint* edges)
    {
        GetVertices(0, _vertexCount, positions);
        GetCorners(0, _cornerCount, corners);
        GetFaceSizes(0, _faceCount, faceSizes);
        if (_hasEndpoints || _hasLooseMask || _edgeCount == 0)
        {
            GetEdges(0, _edgeCount, edges);
            return;
        }
        if (_hasFirstUse)
        {
            uint reached = _edgeFirstUse.Select(0);
            for (uint i = 0; i < _edgeCount; i++, reached = _edgeFirstUse.GetNextSet(reached))
                edges[i * 2] = reached;
        }
        else
            _edgeCorners.CopyTo(0, _edgeCount, edges, 2);
        for (uint i = 0; i < _edgeCount; i++)
        {
            uint corner = edges[i * 2];
            uint from = corners[corner];
            uint to = corners[GetNextCorner(corner)];
            bool flipped = _hasFlips ? _edgeFlips.Get(i) != 0 : from > to;
            edges[i * 2] = flipped ? to : from;
            edges[i * 2 + 1] = flipped ? from : to;
        }
    }

    /// <summary>Returns the vertex ID of one face corner.</summary>
    [MethodImpl(MethodImplOptions.AggressiveInlining)]
    public readonly uint GetCorner(uint index)
    {
        if (_hasBackDistances)
        {
            uint newCount = _cornerRepeats.GetClearRank(index);
            return _cornerRepeats.IsSet(index) ? newCount - 1 - _backDistances.Get(index - newCount) : newCount;
        }
        if (!_hasRelativeCorners)
            return _corners.Get(index);
        uint face = index / _arity;
        uint position = index - face * _arity;
        uint first = _corners.Get(face);
        switch (position)
        {
            case 0: return first;
            case 1: return first + _offset1.Get(face);
            case 2: return first + _offset2.Get(face);
            default: return first + _offset3.Get(face);
        }
    }

    /// <summary>Writes a range of corner vertex IDs to the caller's buffer.</summary>
    public readonly void GetCorners(uint first, uint length, uint* destination)
    {
        if (_hasBackDistances)
        {
            GetBackDistanceCorners(first, length, destination);
            return;
        }
        if (!_hasRelativeCorners)
        {
            _corners.CopyTo(first, length, destination, 1);
            return;
        }
        uint arity = _arity;
        if (first % arity != 0 || length % arity != 0)
        {
            for (uint i = 0; i < length; i++)
                destination[i] = GetCorner(first + i);
            return;
        }
        uint firstFace = first / arity;
        uint faces = length / arity;
        int stride = (int)arity;
        _corners.CopyTo(firstFace, faces, destination, stride);
        _offset1.CopyTo(firstFace, faces, destination + 1, stride);
        _offset2.CopyTo(firstFace, faces, destination + 2, stride);
        if (arity == 4)
            _offset3.CopyTo(firstFace, faces, destination + 3, stride);
        uint* face = destination;
        for (uint i = 0; i < faces; i++, face += stride)
        {
            for (uint j = 1; j < arity; j++)
                face[j] += face[0];
        }
    }

    /// <summary>
    /// Whole-mesh reads unpack the distances at the end of the buffer and resolve them forward.
    /// The read position never falls behind the write position, so no second buffer is needed.
    /// </summary>
    readonly void GetBackDistanceCorners(uint first, uint length, uint* destination)
    {
        if (first != 0 || length != _cornerCount)
        {
            for (uint i = 0; i < length; i++)
                destination[i] = GetCorner(first + i);
            return;
        }
        uint repeated = _backDistances.count;
        uint* distance = destination + length - repeated;
        _backDistances.CopyTo(0, repeated, distance, 1);
        uint newest = 0;
        for (uint i = 0; i < length; i++)
            destination[i] = _cornerRepeats.IsSet(i) ? newest - 1 - *distance++ : newest++;
    }

    /// <summary>Returns the index of a face's first corner. Constant time for mixed faces too.</summary>
    [MethodImpl(MethodImplOptions.AggressiveInlining)]
    public readonly uint GetFaceStart(uint face) => _arity != 0 ? face * _arity : _faceStarts.Select(face);

    /// <summary>Returns how many corners a face has.</summary>
    public readonly uint GetFaceSize(uint face)
    {
        if (_arity != 0)
            return _arity;
        uint start = _faceStarts.Select(face);
        return _faceStarts.GetNextSet(start) - start;
    }

    /// <summary>Fills the caller's buffer with a face's vertex IDs and returns how many it wrote.</summary>
    public readonly uint GetFace(uint face, uint* corners)
    {
        uint start = GetFaceStart(face);
        uint size = _arity != 0 ? _arity : _faceStarts.GetNextSet(start) - start;
        for (uint i = 0; i < size; i++)
            corners[i] = GetCorner(start + i);
        return size;
    }

    /// <summary>Writes the corner count of each face in a range to the caller's buffer.</summary>
    public readonly void GetFaceSizes(uint first, uint length, uint* destination)
    {
        if (_arity != 0)
        {
            for (uint i = 0; i < length; i++)
                destination[i] = _arity;
            return;
        }
        uint start = length == 0 ? 0 : _faceStarts.Select(first);
        for (uint i = 0; i < length; i++)
        {
            uint end = _faceStarts.GetNextSet(start);
            destination[i] = end - start;
            start = end;
        }
    }

    [MethodImpl(MethodImplOptions.AggressiveInlining)]
    readonly uint GetNextCorner(uint corner)
    {
        uint next = corner + 1;
        if (_arity != 0)
            return next % _arity == 0 ? next - _arity : next;
        if (next < _cornerCount && !_faceStarts.IsSet(next))
            return next;
        return _faceStarts.GetLastSetAtOrBefore(corner);
    }

    /// <summary>Returns one edge's vertex IDs in source order.</summary>
    [MethodImpl(MethodImplOptions.AggressiveInlining)]
    public readonly ZpmEdge GetEdge(uint index)
    {
        if (_hasEndpoints)
        {
            uint a = _edgeFirst.Get(index);
            return new ZpmEdge(a, a + _edgeSpan.Get(index));
        }
        return GetReferencedEdge(index);
    }

    readonly ZpmEdge GetReferencedEdge(uint index)
    {
        uint attached = index;
        if (_hasLooseMask)
        {
            if (_looseMask.IsSet(index))
            {
                uint rank = _looseMask.GetSetRank(index);
                return new ZpmEdge(_looseFirst.Get(rank), _looseSecond.Get(rank));
            }
            attached = _looseMask.GetClearRank(index);
        }
        uint corner = _hasFirstUse ? _edgeFirstUse.Select(attached) : _edgeCorners.Get(attached);
        uint from = GetCorner(corner);
        uint to = GetCorner(GetNextCorner(corner));
        bool flipped = _hasFlips ? _edgeFlips.Get(attached) != 0 : from > to;
        return flipped ? new ZpmEdge(to, from) : new ZpmEdge(from, to);
    }

    /// <summary>Writes a range of edges to the caller's buffer as ID pairs.</summary>
    public readonly void GetEdges(uint first, uint length, uint* destination)
    {
        if (_hasEndpoints)
        {
            _edgeFirst.CopyTo(first, length, destination, 2);
            _edgeSpan.CopyTo(first, length, destination + 1, 2);
            for (uint i = 0; i < length; i++)
                destination[i * 2 + 1] += destination[i * 2];
            return;
        }
        for (uint i = 0; i < length; i++)
        {
            ZpmEdge edge = GetReferencedEdge(first + i);
            destination[i * 2] = edge.a;
            destination[i * 2 + 1] = edge.b;
        }
    }
}
