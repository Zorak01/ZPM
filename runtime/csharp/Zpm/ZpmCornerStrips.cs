// SPDX-License-Identifier: GPL-3.0-or-later
using System.Runtime.CompilerServices;

namespace Zpm;

/// <summary>
/// Face corners as strips in small blocks. Reading a corner decodes its block from the start to that
/// face, onto the stack: nothing outside the block, and no allocation. One random read costs far more
/// than an in-place column, so read many with CopyRange or a ZpmCornerCursor.
/// </summary>
public unsafe struct ZpmCornerStrips
{
    public const int MaxFaceCorners = 32;
    internal const uint CornersPerBlock = 384;
    const uint TriangleCorners = 3;
    const uint StartEdge = 2;
    const int DistanceOrder = 3;
    const int EscapeZeros = 13;
    const int WideBits = 32;
    const int WindowBits = 64;
    const int WindowRefillBits = 32;

    ZpmColumn _starts, _newest;
    ZpmSelectBits _faceStarts;
    byte* _bits;
    bool _hasStarts, _hasNewest;
    ulong _bitCount;
    uint _arity, _cornerCount;

    /// <summary>Whether its sections are all present.</summary>
    public readonly bool isComplete => _hasStarts && _hasNewest && _bits != null;

    /// <summary>Points at one section. Returns whether it was a strip section.</summary>
    public bool TryBindSection(ZpmSectionKind kind, ZpmSectionFlags flags, byte* section, long available)
    {
        switch (kind)
        {
            case ZpmSectionKind.CornerStripStarts: _starts = ZpmColumn.Bind(section, flags); _hasStarts = true; return true;
            case ZpmSectionKind.CornerStripNewest: _newest = ZpmColumn.Bind(section, flags); _hasNewest = true; return true;
            case ZpmSectionKind.CornerStripBits: _bits = section; _bitCount = (ulong)available * 8; return true;
            default: return false;
        }
    }

    /// <summary>Says how faces are laid out: one shared size, or the marks on their first corners.</summary>
    public void SetFaces(uint arity, in ZpmSelectBits faceStarts, uint cornerCount)
    {
        _arity = arity;
        _faceStarts = faceStarts;
        _cornerCount = cornerCount;
    }

    [MethodImpl(MethodImplOptions.AggressiveInlining)]
    readonly uint ReadBits(ref ulong position, int width)
    {
        ulong raw = Unsafe.ReadUnaligned<ulong>(_bits + (position >> 3));
        uint value = (uint)((raw >> (int)(position & 7)) & ((1UL << width) - 1));
        position += (ulong)width;
        return value;
    }

    [MethodImpl(MethodImplOptions.AggressiveInlining)]
    readonly uint ReadVertex(ref ZpmStripState state)
    {
        if (ReadBits(ref state.position, 1) != 0)
            return state.nextNew++;
        return state.nextNew - 1 - ReadDistance(ref state.position);
    }

    /// <summary>Reads an old vertex's distance in one load: its code fits a word, unless it escapes.</summary>
    [MethodImpl(MethodImplOptions.AggressiveInlining)]
    readonly uint ReadDistance(ref ulong position)
    {
        ulong raw = Unsafe.ReadUnaligned<ulong>(_bits + (position >> 3)) >> (int)(position & 7);
        int zeros = System.Numerics.BitOperations.TrailingZeroCount(raw);
        if (zeros >= EscapeZeros)
        {
            position += EscapeZeros + 1;
            return ReadBits(ref position, WideBits);
        }
        int width = zeros + DistanceOrder;
        uint low = (uint)(raw >> (zeros + 1)) & ((1u << width) - 1);
        position += (ulong)(zeros + 1 + width);
        return ((1u << width) | low) - (1u << DistanceOrder);
    }

    [MethodImpl(MethodImplOptions.AggressiveInlining)]
    readonly uint GetFaceEnd(uint start) => _arity != 0 ? start + _arity : _faceStarts.GetNextSet(start);

    /// <summary>Returns the first corner of the first face in a block.</summary>
    readonly uint GetBlockStart(uint block)
    {
        uint corner = block * CornersPerBlock;
        if (_arity != 0)
            return (corner + _arity - 1) / _arity * _arity;
        return corner == 0 || _faceStarts.IsSet(corner) ? corner : _faceStarts.GetNextSet(corner);
    }

    /// <summary>Returns the first corner of the face holding a corner.</summary>
    [MethodImpl(MethodImplOptions.AggressiveInlining)]
    readonly uint GetFaceStartOf(uint corner) => _arity != 0 ? corner - corner % _arity : _faceStarts.GetLastSetAtOrBefore(corner);

    /// <summary>Sets the state to a block's first face, reading all its vertices.</summary>
    public readonly void StartBlock(uint block, out ZpmStripState state)
    {
        state = default;
        state.position = _starts.Get(block);
        state.nextNew = _newest.Get(block);
        state.lastEdge = StartEdge;
        state.faceStart = GetBlockStart(block);
        state.faceSize = GetFaceEnd(state.faceStart) - state.faceStart;
        ReadVertices(ref state, 0);
    }

    [MethodImpl(MethodImplOptions.AggressiveInlining)]
    readonly void ReadVertices(ref ZpmStripState state, uint from)
    {
        for (uint i = from; i < state.faceSize; i++)
            state.vertices[i] = ReadVertex(ref state);
    }

    [MethodImpl(MethodImplOptions.AggressiveInlining)]
    static uint GetPreferredEdge(uint size, uint lastEdge) => size == TriangleCorners ? TriangleCorners - lastEdge : size / 2;

    /// <summary>Returns the edge of the previous face this one continues from, or 0 for no link.</summary>
    [MethodImpl(MethodImplOptions.AggressiveInlining)]
    readonly uint ReadLink(uint previousSize, uint lastEdge, ref ulong position)
    {
        uint preferred = GetPreferredEdge(previousSize, lastEdge);
        if (ReadBits(ref position, 1) == 0)
            return preferred;
        if (ReadBits(ref position, 1) != 0)
            return 0;
        int width = previousSize == TriangleCorners ? 0 : System.Numerics.BitOperations.Log2(previousSize - 3) + 1;
        uint index = width == 0 ? 0 : ReadBits(ref position, width);
        return index + 1 < preferred ? index + 1 : index + 2;
    }

    /// <summary>Moves the state from the face it holds to the next face.</summary>
    [MethodImpl(MethodImplOptions.AggressiveInlining)]
    public readonly void Advance(ref ZpmStripState state)
    {
        uint previousSize = state.faceSize;
        uint edge = ReadLink(previousSize, state.lastEdge, ref state.position);
        uint start = state.faceStart + previousSize;
        state.faceStart = start;
        state.faceSize = GetFaceEnd(start) - start;
        if (edge == 0)
        {
            state.lastEdge = StartEdge;
            ReadVertices(ref state, 0);
            return;
        }
        uint first = state.vertices[edge + 1 == previousSize ? 0 : edge + 1];
        uint second = state.vertices[edge];
        state.lastEdge = edge <= StartEdge ? edge : StartEdge;
        state.vertices[0] = first;
        state.vertices[1] = second;
        ReadVertices(ref state, 2);
    }

    /// <summary>Returns one corner's vertex. See the type's note on what a random read costs.</summary>
    public readonly uint Get(uint corner)
    {
        uint faceStart = GetFaceStartOf(corner);
        StartBlock(faceStart / CornersPerBlock, out ZpmStripState state);
        while (state.faceStart < faceStart)
            Advance(ref state);
        return state.vertices[corner - faceStart];
    }

    /// <summary>Fills the caller's buffer with a range of corners, in one pass per block touched.</summary>
    public readonly void CopyRange(uint first, uint length, uint* destination)
    {
        if (length == 0)
            return;
        if (_arity == TriangleCorners && first == 0 && length == _cornerCount)
        {
            CopyAllTriangles(destination);
            return;
        }
        if (first == 0 && length == _cornerCount)
        {
            CopyAllFaces(destination);
            return;
        }
        uint end = first + length;
        uint faceStart = GetFaceStartOf(first);
        uint block = faceStart / CornersPerBlock;
        StartBlock(block, out ZpmStripState state);
        while (state.faceStart < faceStart)
            Advance(ref state);
        while (true)
        {
            uint from = state.faceStart < first ? first - state.faceStart : 0;
            for (uint i = from; i < state.faceSize && state.faceStart + i < end; i++)
                destination[state.faceStart + i - first] = state.vertices[i];
            uint next = state.faceStart + state.faceSize;
            if (next >= end)
                return;
            uint nextBlock = next / CornersPerBlock;
            if (nextBlock != block)
            {
                block = nextBlock;
                StartBlock(block, out state);
            }
            else
                Advance(ref state);
        }
    }

    /// <summary>The whole mesh with faces of any size. The previous face is already in the output, so no state is copied.</summary>
    readonly void CopyAllFaces(uint* destination)
    {
        ulong position = 0;
        uint nextNew = 0;
        uint block = uint.MaxValue;
        uint previousSize = 0, lastEdge = StartEdge;
        for (uint start = 0; start < _cornerCount;)
        {
            uint size = GetFaceEnd(start) - start;
            uint* face = destination + start;
            uint from = 0;
            uint edge = 0;
            if (start / CornersPerBlock != block)
                block = start / CornersPerBlock;
            else
                edge = ReadLink(previousSize, lastEdge, ref position);
            if (edge != 0)
            {
                uint* previous = face - previousSize;
                face[0] = previous[edge + 1 == previousSize ? 0 : edge + 1];
                face[1] = previous[edge];
                from = 2;
            }
            lastEdge = edge != 0 && edge <= StartEdge ? edge : StartEdge;
            for (uint i = from; i < size; i++)
                face[i] = ReadTriangleVertex(ref position, ref nextNew);
            previousSize = size;
            start += size;
        }
    }

    /// <summary>The whole mesh when every face is a triangle, kept apart because it is the hot path.</summary>
    readonly void CopyAllTriangles(uint* destination)
    {
        uint blocks = (_cornerCount - 1) / CornersPerBlock + 1;
        ulong position = 0;
        uint nextNew = 0;
        byte* bits = _bits;
        ulong window = 0;
        int windowBits = 0;
        for (uint block = 0; block < blocks; block++)
        {
            uint a = ReadWindowVertex(bits, ref position, ref window, ref windowBits, ref nextNew);
            uint b = ReadWindowVertex(bits, ref position, ref window, ref windowBits, ref nextNew);
            uint c = ReadWindowVertex(bits, ref position, ref window, ref windowBits, ref nextNew);
            destination[0] = a;
            destination[1] = b;
            destination[2] = c;
            destination += TriangleCorners;
            uint faces = System.Math.Min(CornersPerBlock, _cornerCount - block * CornersPerBlock) / TriangleCorners;
            uint lastEdge = StartEdge;
            for (uint face = 1; face < faces; face++)
            {
                uint edge = TriangleCorners - lastEdge;
                if (windowBits < WindowRefillBits)
                    Refill(bits, ref position, ref window, ref windowBits);
                uint link = (uint)window & 3;
                if ((link & 1) == 0)
                {
                    window >>= 1;
                    windowBits--;
                }
                else
                {
                    window >>= 2;
                    windowBits -= 2;
                    if (link == 3)
                    {
                        lastEdge = StartEdge;
                        a = ReadWindowVertex(bits, ref position, ref window, ref windowBits, ref nextNew);
                        b = ReadWindowVertex(bits, ref position, ref window, ref windowBits, ref nextNew);
                        c = ReadWindowVertex(bits, ref position, ref window, ref windowBits, ref nextNew);
                        goto write;
                    }
                    edge = lastEdge;
                }
                lastEdge = edge;
                if (edge == 1)
                    a = c;
                else
                    b = c;
                c = ReadWindowVertex(bits, ref position, ref window, ref windowBits, ref nextNew);
                write:
                destination[0] = a;
                destination[1] = b;
                destination[2] = c;
                destination += TriangleCorners;
            }
        }
    }

    /// <summary>Tops the window up to at least 56 bits. position counts bits already loaded.</summary>
    [MethodImpl(MethodImplOptions.AggressiveInlining)]
    static void Refill(byte* bits, ref ulong position, ref ulong window, ref int windowBits)
    {
        window |= (Unsafe.ReadUnaligned<ulong>(bits + (position >> 3)) >> (int)(position & 7)) << windowBits;
        int added = (WindowBits - 1 - windowBits) & ~7;
        position += (ulong)added;
        windowBits += added;
    }

    /// <summary>Reads one vertex from a bit window: one load per several reads, rather than one per read.</summary>
    [MethodImpl(MethodImplOptions.AggressiveInlining)]
    static uint ReadWindowVertex(byte* bits, ref ulong position, ref ulong window, ref int windowBits, ref uint nextNew)
    {
        if (windowBits < WindowRefillBits)
            Refill(bits, ref position, ref window, ref windowBits);
        if ((window & 1) != 0)
        {
            window >>= 1;
            windowBits--;
            return nextNew++;
        }
        ulong code = window >> 1;
        int zeros = System.Numerics.BitOperations.TrailingZeroCount(code);
        if (zeros >= EscapeZeros)
        {
            position -= (ulong)windowBits;
            position += 1 + EscapeZeros + 1;
            uint wide = (uint)(Unsafe.ReadUnaligned<ulong>(bits + (position >> 3)) >> (int)(position & 7));
            position += WideBits;
            window = 0;
            windowBits = 0;
            return nextNew - 1 - wide;
        }
        int width = zeros + DistanceOrder;
        uint low = (uint)(code >> (zeros + 1)) & ((1u << width) - 1);
        int used = 1 + zeros + 1 + width;
        window >>= used;
        windowBits -= used;
        return nextNew - 1 - (((1u << width) | low) - (1u << DistanceOrder));
    }

    [MethodImpl(MethodImplOptions.AggressiveInlining)]
    readonly uint ReadTriangleVertex(ref ulong position, ref uint nextNew)
    {
        if (ReadBits(ref position, 1) != 0)
            return nextNew++;
        return nextNew - 1 - ReadDistance(ref position);
    }

    /// <summary>
    /// Decodes everything once with every check: only that proves each read stays in the file, each ID
    /// exists, and each block starts where it says. Afterwards the unchecked reads above are safe.
    /// </summary>
    public readonly bool Validate(byte* limit, uint vertexCount)
    {
        if (!isComplete || _cornerCount == 0 || _bits > limit || (ulong)(limit - _bits) * 8 < _bitCount
            || !_starts.ValidateLayout(limit) || !_newest.ValidateLayout(limit))
            return false;
        uint blocks = GetFaceStartOf(_cornerCount - 1) / CornersPerBlock + 1;
        if (_starts.count != (ulong)blocks + 1 || _newest.count != blocks)
            return false;
        ulong position = 0;
        uint nextNew = 0;
        uint block = uint.MaxValue;
        uint previousSize = 0, lastEdge = StartEdge;
        for (uint start = 0; start < _cornerCount;)
        {
            uint size = GetFaceEnd(start) - start;
            if (size < TriangleCorners || size > MaxFaceCorners)
                return false;
            uint own = size;
            uint faceBlock = start / CornersPerBlock;
            if (faceBlock != block)
            {
                block = faceBlock;
                if (_starts.Get(block) != position || _newest.Get(block) != nextNew)
                    return false;
                lastEdge = StartEdge;
            }
            else if (!ValidateLink(previousSize, ref lastEdge, ref position, ref own))
                return false;
            for (uint v = 0; v < own; v++)
            {
                if (!ValidateVertex(vertexCount, ref position, ref nextNew))
                    return false;
            }
            previousSize = size;
            start += size;
        }
        return _starts.Get(blocks) == position;
    }

    readonly bool ValidateLink(uint previousSize, ref uint lastEdge, ref ulong position, ref uint own)
    {
        if (position + 2 > _bitCount)
            return false;
        uint preferred = GetPreferredEdge(previousSize, lastEdge);
        if (ReadBits(ref position, 1) == 0)
        {
            lastEdge = preferred <= StartEdge ? preferred : StartEdge;
            own -= 2;
            return true;
        }
        if (ReadBits(ref position, 1) != 0)
        {
            lastEdge = StartEdge;
            return true;
        }
        int width = previousSize == TriangleCorners ? 0 : System.Numerics.BitOperations.Log2(previousSize - 3) + 1;
        if (position + (ulong)width > _bitCount)
            return false;
        uint index = width == 0 ? 0 : ReadBits(ref position, width);
        uint edge = index + 1 < preferred ? index + 1 : index + 2;
        if (edge >= previousSize)
            return false;
        lastEdge = edge <= StartEdge ? edge : StartEdge;
        own -= 2;
        return true;
    }

    readonly bool ValidateVertex(uint vertexCount, ref ulong position, ref uint nextNew)
    {
        if (position + 1 > _bitCount)
            return false;
        if (ReadBits(ref position, 1) != 0)
            return nextNew++ < vertexCount;
        if (position >= _bitCount)
            return false;
        ulong raw = Unsafe.ReadUnaligned<ulong>(_bits + (position >> 3)) >> (int)(position & 7);
        int zeros = System.Numerics.BitOperations.TrailingZeroCount(raw);
        if (zeros > EscapeZeros)
            return false;
        int codeBits = zeros == EscapeZeros ? EscapeZeros + 1 + WideBits : 2 * zeros + 1 + DistanceOrder;
        if (position + (ulong)codeBits > _bitCount)
            return false;
        uint distance = ReadDistance(ref position);
        return nextNew != 0 && distance <= nextNew - 1;
    }
}
