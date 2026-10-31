// SPDX-License-Identifier: GPL-3.0-or-later
using System.Numerics;
using System.Runtime.CompilerServices;
using System.Runtime.Intrinsics.X86;

namespace Zpm.DirectRead;

/// <summary>One vertex position as exact Float32 bit patterns.</summary>
public readonly struct ZpmVertex
{
    public readonly uint xBits, yBits, zBits;

    public ZpmVertex(uint xBits, uint yBits, uint zBits)
    {
        this.xBits = xBits;
        this.yBits = yBits;
        this.zBits = zBits;
    }

    public float x => BitConverter.UInt32BitsToSingle(xBits);
    public float y => BitConverter.UInt32BitsToSingle(yBits);
    public float z => BitConverter.UInt32BitsToSingle(zBits);
}

/// <summary>One edge as its two vertex IDs, in source order.</summary>
public readonly struct ZpmEdge
{
    public readonly uint a, b;

    public ZpmEdge(uint a, uint b)
    {
        this.a = a;
        this.b = b;
    }
}

public enum ZpmAxisMode : byte { Bits = 0, Palette = 1, SignedPalette = 2, Constant = 3 }
public enum ZpmCornerMode : byte { Single = 0, RelativeToFirst = 1 }
public enum ZpmEdgeMode : byte { Endpoints = 0, CornerReference = 1 }

[Flags]
public enum ZpmFlags : byte { None = 0, RepeatedPositions = 1, LooseEdges = 2, CanonicalEdges = 4 }

/// <summary>Fixed file layout values shared by the view and the encoder.</summary>
public static class ZpmLayout
{
    public const uint Magic = 0x31444D5A; // "ZMD1"
    public const int HeaderBytes = 48;
    public const int TailBytes = 32;
    public const int MaxUniformArity = 255;
    public const int MaxRelativeArity = 4;
    public const int FacesPerSelect = 64;

    public const int VertexCountAt = 4, FaceCountAt = 8, CornerCountAt = 12, EdgeCountAt = 16, UniqueCountAt = 20;
    public const int AxisModesAt = 24, CornerModeAt = 27, EdgeModeAt = 28, ArityAt = 29, FlagsAt = 30;
    public const int AxisArgumentsAt = 32, LooseCountAt = 44;
}

/// <summary>One position axis, stored as bits, through a palette, or as one constant value.</summary>
public unsafe struct ZpmAxis
{
    public ZpmAxisMode mode;
    public uint argument;
    public ZpmColumn values;
    public ZpmColumn indices;

    const uint SignBit = 0x80000000u;
    const int SignShift = 31;

    /// <summary>Maps Float32 bits to an order that follows numeric value. Exact for every pattern.</summary>
    [MethodImpl(MethodImplOptions.AggressiveInlining)]
    public static uint BitsToOrdered(uint bits) => bits ^ ((uint)((int)bits >> SignShift) | SignBit);

    [MethodImpl(MethodImplOptions.AggressiveInlining)]
    public static uint OrderedToBits(uint ordered) => ordered ^ ((uint)((int)~ordered >> SignShift) | SignBit);

    [MethodImpl(MethodImplOptions.AggressiveInlining)]
    public readonly uint GetBits(uint index)
    {
        switch (mode)
        {
            case ZpmAxisMode.Bits:
                return OrderedToBits(values.Get(index));
            case ZpmAxisMode.Palette:
                return values.Get(indices.Get(index));
            case ZpmAxisMode.SignedPalette:
                uint signed = indices.Get(index);
                return values.Get(signed >> 1) | (signed << SignShift);
            default:
                return argument;
        }
    }

    /// <summary>Writes a range of this axis to the caller's buffer, every three elements.</summary>
    public readonly void CopyTo(uint first, uint length, uint* destination)
    {
        const int Stride = 3;
        switch (mode)
        {
            case ZpmAxisMode.Bits:
                values.CopyTo(first, length, destination, Stride);
                for (uint i = 0; i < length; i++, destination += Stride)
                    *destination = OrderedToBits(*destination);
                break;
            case ZpmAxisMode.Palette:
                indices.CopyTo(first, length, destination, Stride);
                for (uint i = 0; i < length; i++, destination += Stride)
                    *destination = values.Get(*destination);
                break;
            case ZpmAxisMode.SignedPalette:
                indices.CopyTo(first, length, destination, Stride);
                for (uint i = 0; i < length; i++, destination += Stride)
                {
                    uint signed = *destination;
                    *destination = values.Get(signed >> 1) | (signed << SignShift);
                }
                break;
            default:
                for (uint i = 0; i < length; i++, destination += Stride)
                    *destination = argument;
                break;
        }
    }

    public readonly bool Validate(byte* limit, uint count)
    {
        switch (mode)
        {
            case ZpmAxisMode.Bits:
                return values.count == count && values.ValidateLayout(limit);
            case ZpmAxisMode.Palette:
            case ZpmAxisMode.SignedPalette:
                ulong bound = mode == ZpmAxisMode.Palette ? argument : (ulong)argument * 2;
                return values.ValidateLayout(limit) && indices.ValidateLayout(limit) && indices.ValidateBelow(bound);
            default:
                return true;
        }
    }
}

/// <summary>
/// Reads a ZMD1 mesh straight from its file bytes. The view holds pointers and never allocates,
/// copies or decodes: any vertex, corner, face or edge is one call away. The bytes must stay
/// alive and pinned while the view is used.
/// </summary>
public unsafe struct ZpmMeshView : IZpmMeshReader
{
    uint _vertexCount, _faceCount, _cornerCount, _edgeCount, _uniqueCount, _looseCount, _arity;
    ZpmCornerMode _cornerMode;
    ZpmEdgeMode _edgeMode;
    ZpmFlags _flags;
    byte* _limit;

    ZpmRankMask _repeats;
    ZpmColumn _repeatIds;
    ZpmAxis _x, _y, _z;
    ZpmColumn _corners0, _corners1, _corners2, _corners3;
    ulong* _faceStarts;
    uint* _faceSelect;
    ZpmColumn _edgesA, _edgesB;
    ZpmColumn _edgeRefs, _edgeFlips;
    ZpmRankMask _loose;

    public readonly uint vertexCount => _vertexCount;
    public readonly uint faceCount => _faceCount;
    public readonly uint cornerCount => _cornerCount;
    public readonly uint edgeCount => _edgeCount;
    public readonly uint uniqueCount => _uniqueCount;
    public readonly uint arity => _arity;
    public readonly bool hasUniformFaces => _arity != 0;

    /// <summary>
    /// Reads the header and points the view at each stream, touching only the first bytes of the
    /// file. Returns false when the header is malformed. Call Validate before reading untrusted bytes.
    /// </summary>
    public static bool TryOpen(byte* data, long length, out ZpmMeshView view)
    {
        view = default;
        if (length < ZpmLayout.HeaderBytes + ZpmLayout.TailBytes || length > uint.MaxValue)
            return false;
        if (Unsafe.ReadUnaligned<uint>(data) != ZpmLayout.Magic)
            return false;
        view._vertexCount = Unsafe.ReadUnaligned<uint>(data + ZpmLayout.VertexCountAt);
        view._faceCount = Unsafe.ReadUnaligned<uint>(data + ZpmLayout.FaceCountAt);
        view._cornerCount = Unsafe.ReadUnaligned<uint>(data + ZpmLayout.CornerCountAt);
        view._edgeCount = Unsafe.ReadUnaligned<uint>(data + ZpmLayout.EdgeCountAt);
        view._uniqueCount = Unsafe.ReadUnaligned<uint>(data + ZpmLayout.UniqueCountAt);
        view._looseCount = Unsafe.ReadUnaligned<uint>(data + ZpmLayout.LooseCountAt);
        view._cornerMode = (ZpmCornerMode)data[ZpmLayout.CornerModeAt];
        view._edgeMode = (ZpmEdgeMode)data[ZpmLayout.EdgeModeAt];
        view._arity = data[ZpmLayout.ArityAt];
        view._flags = (ZpmFlags)data[ZpmLayout.FlagsAt];
        view._limit = data + length - ZpmLayout.TailBytes;
        if (view._uniqueCount > view._vertexCount || view._looseCount > view._edgeCount)
            return false;
        if (view._cornerMode > ZpmCornerMode.RelativeToFirst || view._edgeMode > ZpmEdgeMode.CornerReference)
            return false;
        if ((view._flags & ~(ZpmFlags.RepeatedPositions | ZpmFlags.LooseEdges | ZpmFlags.CanonicalEdges)) != 0)
            return false;

        uint table = ZpmLayout.HeaderBytes;
        uint unique = view._uniqueCount;
        if ((view._flags & ZpmFlags.RepeatedPositions) != 0)
        {
            if (!TryNext(data, length, ref table, out uint mask) || !TryNext(data, length, ref table, out uint ids))
                return false;
            view._repeats = ZpmRankMask.Bind(data, mask, view._vertexCount);
            view._repeatIds = ZpmColumn.Bind(data, ids, view._vertexCount - unique);
        }
        else if (unique != view._vertexCount)
            return false;

        if (view._vertexCount != 0
            && (!TryBindAxis(data, length, 0, unique, ref table, out view._x)
                || !TryBindAxis(data, length, 1, unique, ref table, out view._y)
                || !TryBindAxis(data, length, 2, unique, ref table, out view._z)))
            return false;

        if (view._cornerCount != 0)
        {
            if (view._cornerMode == ZpmCornerMode.RelativeToFirst)
            {
                uint arity = view._arity;
                if (arity < 3 || arity > ZpmLayout.MaxRelativeArity || (ulong)arity * view._faceCount != view._cornerCount)
                    return false;
                if (!TryNext(data, length, ref table, out uint a) || !TryNext(data, length, ref table, out uint b) || !TryNext(data, length, ref table, out uint c))
                    return false;
                view._corners0 = ZpmColumn.Bind(data, a, view._faceCount);
                view._corners1 = ZpmColumn.Bind(data, b, view._faceCount);
                view._corners2 = ZpmColumn.Bind(data, c, view._faceCount);
                if (arity == 4)
                {
                    if (!TryNext(data, length, ref table, out uint d))
                        return false;
                    view._corners3 = ZpmColumn.Bind(data, d, view._faceCount);
                }
            }
            else
            {
                if (!TryNext(data, length, ref table, out uint a))
                    return false;
                view._corners0 = ZpmColumn.Bind(data, a, view._cornerCount);
            }
            if (view._arity == 0)
            {
                if (!TryNext(data, length, ref table, out uint starts))
                    return false;
                view._faceStarts = (ulong*)(data + starts);
                view._faceSelect = (uint*)(data + starts + (ulong)ZpmRankMask.WordCountOf(view._cornerCount) * 8);
            }
            else if ((ulong)view._arity * view._faceCount != view._cornerCount)
                return false;
        }
        else if (view._faceCount != 0)
            return false;

        if (view._edgeCount != 0)
        {
            if (view._edgeMode == ZpmEdgeMode.Endpoints)
            {
                if (!TryNext(data, length, ref table, out uint a) || !TryNext(data, length, ref table, out uint b))
                    return false;
                view._edgesA = ZpmColumn.Bind(data, a, view._edgeCount);
                view._edgesB = ZpmColumn.Bind(data, b, view._edgeCount);
            }
            else
            {
                uint looseCount = view._looseCount;
                uint attached = view._edgeCount - looseCount;
                if ((view._flags & ZpmFlags.LooseEdges) != 0)
                {
                    if (!TryNext(data, length, ref table, out uint mask) || !TryNext(data, length, ref table, out uint looseA) || !TryNext(data, length, ref table, out uint looseB))
                        return false;
                    view._loose = ZpmRankMask.Bind(data, mask, view._edgeCount);
                    view._edgesA = ZpmColumn.Bind(data, looseA, looseCount);
                    view._edgesB = ZpmColumn.Bind(data, looseB, looseCount);
                }
                else if (looseCount != 0)
                    return false;
                if (attached != 0)
                {
                    if (!TryNext(data, length, ref table, out uint refs))
                        return false;
                    view._edgeRefs = ZpmColumn.Bind(data, refs, attached);
                    if ((view._flags & ZpmFlags.CanonicalEdges) == 0)
                    {
                        if (!TryNext(data, length, ref table, out uint flips))
                            return false;
                        view._edgeFlips = ZpmColumn.Bind(data, flips, attached);
                    }
                }
            }
        }
        return true;
    }

    static bool TryNext(byte* data, long length, ref uint table, out uint entry)
    {
        entry = 0;
        if (table + 4 > length)
            return false;
        entry = Unsafe.ReadUnaligned<uint>(data + table);
        table += 4;
        return (entry & ~1u) < length;
    }

    static bool TryBindAxis(byte* data, long length, int axis, uint count, ref uint table, out ZpmAxis result)
    {
        result = default;
        result.mode = (ZpmAxisMode)data[ZpmLayout.AxisModesAt + axis];
        result.argument = Unsafe.ReadUnaligned<uint>(data + ZpmLayout.AxisArgumentsAt + axis * 4);
        switch (result.mode)
        {
            case ZpmAxisMode.Bits:
                if (!TryNext(data, length, ref table, out uint bits))
                    return false;
                result.values = ZpmColumn.Bind(data, bits, count);
                return true;
            case ZpmAxisMode.Palette:
            case ZpmAxisMode.SignedPalette:
                if (!TryNext(data, length, ref table, out uint palette) || !TryNext(data, length, ref table, out uint indices))
                    return false;
                result.values = ZpmColumn.Bind(data, palette, result.argument);
                result.indices = ZpmColumn.Bind(data, indices, count);
                return true;
            case ZpmAxisMode.Constant:
                return true;
            default:
                return false;
        }
    }

    /// <summary>
    /// Proves every read stays inside the file and every ID is in range. Reads block descriptors,
    /// not the mesh: cost grows with blocks (one per 64 values). Returns false on a malformed file.
    /// </summary>
    public readonly bool Validate()
    {
        byte* limit = _limit;
        if ((_flags & ZpmFlags.RepeatedPositions) != 0)
        {
            uint repeated = _vertexCount - _uniqueCount;
            if (!_repeats.Validate(limit, _uniqueCount) || !_repeatIds.ValidateLayout(limit) || !_repeatIds.ValidateBelow(_uniqueCount))
                return false;
            if (_repeatIds.count != repeated)
                return false;
        }
        if (_vertexCount != 0 && (!_x.Validate(limit, _uniqueCount) || !_y.Validate(limit, _uniqueCount) || !_z.Validate(limit, _uniqueCount)))
            return false;
        if (_cornerCount != 0)
        {
            if (_cornerMode == ZpmCornerMode.RelativeToFirst)
            {
                if (!_corners0.ValidateLayout(limit) || !_corners1.ValidateLayout(limit) || !_corners2.ValidateLayout(limit)
                    || !_corners0.ValidateBelow(_vertexCount) || !ValidateSum(_corners0, _corners1) || !ValidateSum(_corners0, _corners2))
                    return false;
                if (_arity == 4 && (!_corners3.ValidateLayout(limit) || !ValidateSum(_corners0, _corners3)))
                    return false;
            }
            else if (!_corners0.ValidateLayout(limit) || !_corners0.ValidateBelow(_vertexCount))
                return false;
            if (_arity == 0 && !ValidateFaceStarts(limit))
                return false;
        }
        if (_edgeCount != 0)
        {
            if (_edgeMode == ZpmEdgeMode.Endpoints)
            {
                return _edgesA.ValidateLayout(limit) && _edgesB.ValidateLayout(limit)
                    && _edgesA.ValidateBelow(_vertexCount) && ValidateSum(_edgesA, _edgesB);
            }
            uint attached = _edgeCount - _looseCount;
            if ((_flags & ZpmFlags.LooseEdges) != 0)
            {
                if (!_loose.Validate(limit, attached) || !_edgesA.ValidateLayout(limit) || !_edgesB.ValidateLayout(limit)
                    || !_edgesA.ValidateBelow(_vertexCount) || !_edgesB.ValidateBelow(_vertexCount))
                    return false;
            }
            if (attached != 0)
            {
                if (_cornerCount == 0 || !_edgeRefs.ValidateLayout(limit) || !_edgeRefs.ValidateBelow(_cornerCount))
                    return false;
                if ((_flags & ZpmFlags.CanonicalEdges) == 0 && (!_edgeFlips.ValidateLayout(limit) || !_edgeFlips.ValidateBelow(2)))
                    return false;
            }
        }
        return true;
    }

    /// <summary>Proves first + delta is a valid vertex ID for each element of two same-length columns.</summary>
    readonly bool ValidateSum(ZpmColumn first, ZpmColumn deltas)
    {
        uint blocks = first.blockCount;
        for (uint block = 0; block < blocks; block++)
        {
            first.GetBlockRange(block, false, out long firstLow, out long firstHigh);
            deltas.GetBlockRange(block, true, out long low, out long high);
            if (firstLow + low >= 0 && firstHigh + high < _vertexCount)
                continue;
            uint start = block * ZpmColumn.BlockSize;
            uint end = Math.Min(start + ZpmColumn.BlockSize, first.count);
            for (uint i = start; i < end; i++)
            {
                if (first.Get(i) + deltas.Get(i) >= _vertexCount)
                    return false;
            }
        }
        return true;
    }

    readonly bool ValidateFaceStarts(byte* limit)
    {
        uint words = ZpmRankMask.WordCountOf(_cornerCount);
        uint selects = (uint)(((ulong)_faceCount + ZpmLayout.FacesPerSelect - 1) / ZpmLayout.FacesPerSelect);
        if ((byte*)_faceStarts >= limit || (ulong)(limit - (byte*)_faceStarts) < (ulong)words * 8 + (ulong)selects * 4)
            return false;
        if ((_faceStarts[0] & 1) == 0)
            return false;
        uint spare = (uint)(words * 64UL - _cornerCount);
        if (spare != 0 && _faceStarts[words - 1] >> (int)(64 - spare) != 0)
            return false;
        uint seen = 0;
        for (uint word = 0; word < words; word++)
        {
            ulong bits = _faceStarts[word];
            uint next = seen + (uint)BitOperations.PopCount(bits);
            while (bits != 0)
            {
                if (seen % ZpmLayout.FacesPerSelect == 0)
                {
                    uint position = word * 64 + (uint)BitOperations.TrailingZeroCount(bits);
                    if (seen / ZpmLayout.FacesPerSelect >= selects || _faceSelect[seen / ZpmLayout.FacesPerSelect] != position)
                        return false;
                }
                bits &= bits - 1;
                seen++;
            }
            if (next != seen)
                return false;
        }
        return seen == _faceCount;
    }

    /// <summary>Returns one vertex position. Touches only the blocks that hold it.</summary>
    [MethodImpl(MethodImplOptions.AggressiveInlining)]
    public readonly ZpmVertex GetVertex(uint index)
    {
        uint unique = index;
        if ((_flags & ZpmFlags.RepeatedPositions) != 0)
            unique = _repeats.IsSet(index) ? _repeatIds.Get(_repeats.GetSetRank(index)) : _repeats.GetClearRank(index);
        return new ZpmVertex(_x.GetBits(unique), _y.GetBits(unique), _z.GetBits(unique));
    }

    /// <summary>Writes a range of positions to the caller's buffer as XYZ bit patterns.</summary>
    public readonly void GetVertices(uint first, uint length, uint* destination)
    {
        if ((_flags & ZpmFlags.RepeatedPositions) == 0)
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
        if (_edgeMode == ZpmEdgeMode.Endpoints || (_flags & ZpmFlags.LooseEdges) != 0 || _edgeCount == 0)
        {
            GetEdges(0, _edgeCount, edges);
            return;
        }
        _edgeRefs.CopyTo(0, _edgeCount, edges, 2);
        bool canonical = (_flags & ZpmFlags.CanonicalEdges) != 0;
        for (uint i = 0; i < _edgeCount; i++)
        {
            uint corner = edges[i * 2];
            uint from = corners[corner];
            uint to = corners[GetNextCorner(corner)];
            bool flipped = canonical ? from > to : _edgeFlips.Get(i) != 0;
            edges[i * 2] = flipped ? to : from;
            edges[i * 2 + 1] = flipped ? from : to;
        }
    }

    /// <summary>Returns the vertex ID of one face corner.</summary>
    [MethodImpl(MethodImplOptions.AggressiveInlining)]
    public readonly uint GetCorner(uint index)
    {
        if (_cornerMode == ZpmCornerMode.Single)
            return _corners0.Get(index);
        uint face = index / _arity;
        uint position = index - face * _arity;
        uint first = _corners0.Get(face);
        switch (position)
        {
            case 0: return first;
            case 1: return first + _corners1.Get(face);
            case 2: return first + _corners2.Get(face);
            default: return first + _corners3.Get(face);
        }
    }

    /// <summary>Writes a range of corner vertex IDs to the caller's buffer.</summary>
    public readonly void GetCorners(uint first, uint length, uint* destination)
    {
        if (_cornerMode == ZpmCornerMode.Single)
        {
            _corners0.CopyTo(first, length, destination, 1);
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
        _corners0.CopyTo(firstFace, faces, destination, stride);
        _corners1.CopyTo(firstFace, faces, destination + 1, stride);
        _corners2.CopyTo(firstFace, faces, destination + 2, stride);
        if (arity == 4)
            _corners3.CopyTo(firstFace, faces, destination + 3, stride);
        uint* face = destination;
        for (uint i = 0; i < faces; i++, face += stride)
        {
            for (uint j = 1; j < arity; j++)
                face[j] += face[0];
        }
    }

    /// <summary>Returns the index of a face's first corner. Constant time for mixed faces too.</summary>
    [MethodImpl(MethodImplOptions.AggressiveInlining)]
    public readonly uint GetFaceStart(uint face)
    {
        if (_arity != 0)
            return face * _arity;
        return SelectFaceStart(face);
    }

    readonly uint SelectFaceStart(uint face)
    {
        uint position = _faceSelect[face / ZpmLayout.FacesPerSelect];
        uint remaining = face % ZpmLayout.FacesPerSelect;
        uint word = position >> 6;
        ulong bits = _faceStarts[word] & (~0UL << (int)(position & 63));
        while (true)
        {
            uint available = (uint)BitOperations.PopCount(bits);
            if (remaining < available)
                return word * 64 + SelectBit(bits, remaining);
            remaining -= available;
            bits = _faceStarts[++word];
        }
    }

    [MethodImpl(MethodImplOptions.AggressiveInlining)]
    static uint SelectBit(ulong bits, uint rank)
    {
        if (Bmi2.X64.IsSupported)
            return (uint)BitOperations.TrailingZeroCount(Bmi2.X64.ParallelBitDeposit(1UL << (int)rank, bits));
        for (; rank != 0; rank--)
            bits &= bits - 1;
        return (uint)BitOperations.TrailingZeroCount(bits);
    }

    /// <summary>Returns how many corners a face has.</summary>
    public readonly uint GetFaceSize(uint face)
    {
        if (_arity != 0)
            return _arity;
        uint start = SelectFaceStart(face);
        return GetFaceEnd(start) - start;
    }

    readonly uint GetFaceEnd(uint start)
    {
        uint next = start + 1;
        if (next >= _cornerCount)
            return _cornerCount;
        uint word = next >> 6;
        ulong bits = _faceStarts[word] & (~0UL << (int)(next & 63));
        uint words = ZpmRankMask.WordCountOf(_cornerCount);
        while (bits == 0)
        {
            if (++word == words)
                return _cornerCount;
            bits = _faceStarts[word];
        }
        return word * 64 + (uint)BitOperations.TrailingZeroCount(bits);
    }

    /// <summary>Fills the caller's buffer with a face's vertex IDs and returns how many it wrote.</summary>
    public readonly uint GetFace(uint face, uint* corners)
    {
        uint start = GetFaceStart(face);
        uint size = _arity != 0 ? _arity : GetFaceEnd(start) - start;
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
        uint start = length == 0 ? 0 : SelectFaceStart(first);
        for (uint i = 0; i < length; i++)
        {
            uint end = GetFaceEnd(start);
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
        if (next < _cornerCount && (_faceStarts[next >> 6] >> (int)(next & 63) & 1) == 0)
            return next;
        return GetPreviousFaceStart(corner);
    }

    readonly uint GetPreviousFaceStart(uint corner)
    {
        uint word = corner >> 6;
        ulong bits = _faceStarts[word] & (~0UL >> (int)(63 - (corner & 63)));
        while (bits == 0)
            bits = _faceStarts[--word];
        return word * 64 + 63 - (uint)BitOperations.LeadingZeroCount(bits);
    }

    /// <summary>Returns one edge's vertex IDs in source order.</summary>
    [MethodImpl(MethodImplOptions.AggressiveInlining)]
    public readonly ZpmEdge GetEdge(uint index)
    {
        if (_edgeMode == ZpmEdgeMode.Endpoints)
        {
            uint a = _edgesA.Get(index);
            return new ZpmEdge(a, a + _edgesB.Get(index));
        }
        return GetReferencedEdge(index);
    }

    readonly ZpmEdge GetReferencedEdge(uint index)
    {
        uint attached = index;
        if ((_flags & ZpmFlags.LooseEdges) != 0)
        {
            if (_loose.IsSet(index))
            {
                uint rank = _loose.GetSetRank(index);
                return new ZpmEdge(_edgesA.Get(rank), _edgesB.Get(rank));
            }
            attached = _loose.GetClearRank(index);
        }
        uint corner = _edgeRefs.Get(attached);
        uint from = GetCorner(corner);
        uint to = GetCorner(GetNextCorner(corner));
        bool flipped = (_flags & ZpmFlags.CanonicalEdges) != 0 ? from > to : _edgeFlips.Get(attached) != 0;
        return flipped ? new ZpmEdge(to, from) : new ZpmEdge(from, to);
    }

    /// <summary>Writes a range of edges to the caller's buffer as ID pairs.</summary>
    public readonly void GetEdges(uint first, uint length, uint* destination)
    {
        if (_edgeMode == ZpmEdgeMode.Endpoints)
        {
            _edgesA.CopyTo(first, length, destination, 2);
            _edgesB.CopyTo(first, length, destination + 1, 2);
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

/// <summary>What the benchmark calls on any direct reader, so it runs the same code on each.</summary>
public unsafe interface IZpmMeshReader
{
    uint vertexCount { get; }
    uint faceCount { get; }
    uint cornerCount { get; }
    uint edgeCount { get; }
    bool Validate();
    ZpmVertex GetVertex(uint index);
    uint GetCorner(uint index);
    uint GetFace(uint face, uint* corners);
    ZpmEdge GetEdge(uint index);
    void GetVertices(uint first, uint length, uint* destination);
    void GetCorners(uint first, uint length, uint* destination);
    void GetEdges(uint first, uint length, uint* destination);
    void GetFaceSizes(uint first, uint length, uint* destination);
    void GetMesh(uint* positions, uint* faceSizes, uint* corners, uint* edges);
}
