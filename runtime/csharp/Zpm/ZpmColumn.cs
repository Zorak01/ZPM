// SPDX-License-Identifier: GPL-3.0-or-later
using System;
using System.Runtime.CompilerServices;
using System.Runtime.Intrinsics;
using System.Runtime.Intrinsics.X86;

namespace Zpm;

/// <summary>
/// A read-only array of UInt32 values stored in file bytes, readable by index without decoding.
/// value[i] = base + slope * j + residual[j], with j the position inside a 64-value block.
/// Costs one block lookup and one unaligned read per value. Allocates nothing.
/// </summary>
public unsafe struct ZpmColumn
{
    const int HeadBytes = 10;
    const int WidthFloorOffset = 8;
    const int BaseBitsOffset = 9;
    const uint WidthNibbleMask = 15;
    const int GroupWidthBytesShift = 3;
    const ulong EveryOtherNibble = 0x0F0F0F0F0F0F0F0FUL;
    const ulong SumNibbleBytes = 0x0101010101010101UL;
    const int TopByteShift = 56;
    const int BlockSize = 64;
    const int BlockShift = 6;
    const uint BlockMask = 63;
    const int GroupShift = 4;
    const uint GroupMask = 15;
    const int BitsPerByteShift = 3;
    const uint BitMask = 7;
    const int MaxWidth = 32;
    const uint WideValues = 8;
    const uint MaxWideWidth = 27;

    byte* _bases;
    sbyte* _slopes;
    byte* _widths;
    byte* _groups;
    byte* _payload;
    ulong _baseMask;
    uint _count, _lowestBase, _baseBits, _widthFloor, _groupBytes, _groupMask;

    /// <summary>How many values the column holds.</summary>
    public readonly uint count => _count;

    readonly uint blockCount => (uint)(((ulong)_count + BlockMask) >> BlockShift);

    /// <summary>Points at a column section. Touches only its count until validated.</summary>
    public static ZpmColumn Bind(byte* section, ZpmSectionFlags flags)
    {
        var column = new ZpmColumn
        {
            _count = Unsafe.ReadUnaligned<uint>(section),
            _lowestBase = Unsafe.ReadUnaligned<uint>(section + 4),
            _widthFloor = section[WidthFloorOffset],
            _baseBits = section[BaseBitsOffset],
        };
        column._baseMask = (1UL << (int)column._baseBits) - 1;
        bool hasShortGroups = (flags & ZpmSectionFlags.ShortGroups) != 0;
        column._groupBytes = hasShortGroups ? 2u : 4u;
        column._groupMask = hasShortGroups ? ushort.MaxValue : uint.MaxValue;
        uint blocks = column.blockCount;
        ulong offset = HeadBytes;
        column._bases = section + offset;
        offset += ((ulong)blocks * column._baseBits + BitMask) >> BitsPerByteShift;
        if ((flags & ZpmSectionFlags.HasSlopes) != 0)
        {
            column._slopes = (sbyte*)(section + offset);
            offset += blocks;
        }
        column._widths = section + offset;
        offset = (offset + (((ulong)blocks + 1) >> 1) + 3) & ~3UL;
        column._groups = section + offset;
        // The first group always starts at zero, so it is not stored.
        offset += (((Math.Max((ulong)blocks, 1) - 1) >> GroupShift) * column._groupBytes + 3) & ~3UL;
        column._payload = section + offset;
        return column;
    }

    [MethodImpl(MethodImplOptions.AggressiveInlining)]
    readonly uint GetBlockOffset(uint block)
    {
        uint group = block >> GroupShift;
        uint position = block & GroupMask;
        ulong widths = Unsafe.ReadUnaligned<ulong>(_widths + (group << GroupWidthBytesShift));
        ulong before = widths & ((1UL << (int)(position << 2)) - 1);
        ulong pairs = (before & EveryOtherNibble) + ((before >> 4) & EveryOtherNibble);
        uint rises = (uint)((pairs * SumNibbleBytes) >> TopByteShift);
        // A full block holds 64 values, so its payload is exactly width * 8 bytes.
        return GetGroupOffset(group) + ((rises + position * _widthFloor) << BitsPerByteShift);
    }

    [MethodImpl(MethodImplOptions.AggressiveInlining)]
    readonly uint GetWidth(uint block) => _widthFloor + (((uint)_widths[block >> 1] >> (int)((block & 1) << 2)) & WidthNibbleMask);

    [MethodImpl(MethodImplOptions.AggressiveInlining)]
    readonly uint GetGroupOffset(uint group)
    {
        // Group 0 reads harmless bytes before the table and masks them away, rather than branching.
        uint mask = group == 0 ? 0 : _groupMask;
        return Unsafe.ReadUnaligned<uint>(_groups + (long)group * _groupBytes - _groupBytes) & mask;
    }

    [MethodImpl(MethodImplOptions.AggressiveInlining)]
    readonly uint GetBase(uint block)
    {
        ulong bit = (ulong)block * _baseBits;
        ulong raw = Unsafe.ReadUnaligned<ulong>(_bases + (bit >> BitsPerByteShift));
        return _lowestBase + (uint)((raw >> (int)(bit & BitMask)) & _baseMask);
    }

    /// <summary>Returns one value. The index must be below count; nothing checks it here.</summary>
    [MethodImpl(MethodImplOptions.AggressiveInlining)]
    public readonly uint Get(uint index)
    {
        uint block = index >> BlockShift;
        uint position = index & BlockMask;
        uint width = GetWidth(block);
        uint bit = position * width;
        ulong raw = Unsafe.ReadUnaligned<ulong>(_payload + GetBlockOffset(block) + (bit >> BitsPerByteShift));
        uint residual = (uint)((raw >> (int)(bit & BitMask)) & ((1UL << (int)width) - 1));
        uint value = GetBase(block) + residual;
        return _slopes == null ? value : value + (uint)((int)_slopes[block] * (int)position);
    }

    /// <summary>Writes a range of values to the caller's buffer, every stride elements.</summary>
    public readonly void CopyTo(uint first, uint length, uint* destination, int stride)
    {
        while (length != 0)
        {
            uint block = first >> BlockShift;
            uint position = first & BlockMask;
            uint take = Math.Min((uint)BlockSize - position, length);
            CopyBlock(block, position, take, destination, stride);
            destination += take * stride;
            first += take;
            length -= take;
        }
    }

    [SkipLocalsInit]
    readonly void CopyBlock(uint block, uint position, uint take, uint* destination, int stride)
    {
        if (stride == 1)
        {
            CopyBlockTight(block, position, take, destination);
            return;
        }
        uint* values = stackalloc uint[BlockSize];
        CopyBlockTight(block, position, take, values);
        for (uint i = 0; i < take; i++, destination += stride)
            *destination = values[i];
    }

    /// <summary>Unpacks eight values per step with AVX2 where it exists; the plain loop gives the same result.</summary>
    readonly void CopyBlockTight(uint block, uint position, uint take, uint* destination)
    {
        uint width = GetWidth(block);
        uint slope = _slopes == null ? 0 : (uint)(int)_slopes[block];
        uint line = GetBase(block) + slope * position;
        if (width == 0)
        {
            for (uint i = 0; i < take; i++, destination++, line += slope)
                *destination = line;
            return;
        }
        byte* payload = _payload + GetBlockOffset(block);
        ulong mask = (1UL << (int)width) - 1;
        uint bit = position * width;
        if (Avx2.IsSupported && width <= MaxWideWidth && take >= WideValues)
        {
            // Eight values span exactly width bytes, so the lane offsets hold for the whole block.
            Vector256<uint> lanes = Vector256.Create(0u, 1, 2, 3, 4, 5, 6, 7);
            Vector256<uint> bits = lanes * width + Vector256.Create(bit & BitMask);
            Vector256<uint> low = Avx2.ShiftRightLogical(bits, 5);
            Vector256<uint> high = low + Vector256.Create(1u);
            Vector256<uint> shift = bits & Vector256.Create(31u);
            Vector256<uint> inverse = Vector256.Create(32u) - shift;
            Vector256<uint> residualMask = Vector256.Create((uint)mask);
            Vector256<uint> trend = Vector256.Create(line) + lanes * slope;
            Vector256<uint> step = Vector256.Create(slope * WideValues);
            byte* source = payload + (bit >> BitsPerByteShift);
            while (take >= WideValues)
            {
                Vector256<uint> words = Vector256.Load((uint*)source);
                Vector256<uint> residual = Avx2.ShiftRightLogicalVariable(Avx2.PermuteVar8x32(words, low), shift)
                    | Avx2.ShiftLeftLogicalVariable(Avx2.PermuteVar8x32(words, high), inverse);
                ((residual & residualMask) + trend).Store(destination);
                trend += step;
                source += width;
                destination += WideValues;
                bit += width * WideValues;
                line += slope * WideValues;
                take -= WideValues;
            }
        }
        for (uint i = 0; i < take; i++, destination++, line += slope, bit += width)
        {
            ulong raw = Unsafe.ReadUnaligned<ulong>(payload + (bit >> BitsPerByteShift));
            *destination = line + (uint)((raw >> (int)(bit & BitMask)) & mask);
        }
    }

    /// <summary>
    /// Checks widths and offsets against the file end, touching descriptors only.
    /// Returns false when the column is malformed. Required before reading untrusted bytes.
    /// </summary>
    public readonly bool ValidateLayout(byte* limit)
    {
        uint blocks = blockCount;
        // Check the descriptors fit first: their pointers came from an untrusted count.
        if (_payload > limit || _bases > limit || _baseBits > MaxWidth)
            return false;
        ulong running = 0;
        for (uint block = 0; block < blocks; block++)
        {
            uint width = GetWidth(block);
            if (width > MaxWidth)
                return false;
            if ((block & GroupMask) == 0 && GetGroupOffset(block >> GroupShift) != running)
                return false;
            bool last = block + 1 == blocks;
            uint values = last ? _count - (block << BlockShift) : BlockSize;
            running += last ? ((ulong)width * values + BitMask) >> BitsPerByteShift : (ulong)width << BitsPerByteShift;
            if (running > (ulong)(limit - _payload))
                return false;
        }
        return true;
    }

    /// <summary>
    /// Proves every value is below limit. Descriptors prove most blocks; a block they cannot
    /// prove is read value by value. Returns false when a value is out of range.
    /// </summary>
    public readonly bool ValidateBelow(ulong limit)
    {
        uint blocks = blockCount;
        for (uint block = 0; block < blocks; block++)
        {
            GetBlockRange(block, false, out long low, out long high);
            if (low >= 0 && (ulong)high < limit)
                continue;
            uint start = block << BlockShift;
            uint end = Math.Min(start + BlockSize, _count);
            for (uint i = start; i < end; i++)
            {
                if (Get(i) >= limit)
                    return false;
            }
        }
        return true;
    }

    /// <summary>Proves first[i] + this[i] is below limit, for two columns of equal count.</summary>
    public readonly bool ValidateSumBelow(in ZpmColumn first, ulong limit)
    {
        uint blocks = blockCount;
        for (uint block = 0; block < blocks; block++)
        {
            first.GetBlockRange(block, false, out long firstLow, out long firstHigh);
            GetBlockRange(block, true, out long low, out long high);
            if (firstLow + low >= 0 && (ulong)(firstHigh + high) < limit)
                continue;
            uint start = block << BlockShift;
            uint end = Math.Min(start + BlockSize, _count);
            for (uint i = start; i < end; i++)
            {
                if (first.Get(i) + Get(i) >= limit)
                    return false;
            }
        }
        return true;
    }

    readonly void GetBlockRange(uint block, bool signedBase, out long low, out long high)
    {
        long start = signedBase ? (int)GetBase(block) : GetBase(block);
        long slope = _slopes == null ? 0 : _slopes[block];
        uint values = Math.Min((uint)BlockSize, _count - (block << BlockShift));
        long travel = slope * (values - 1);
        low = start + Math.Min(0, travel);
        high = start + Math.Max(0, travel) + ((1L << (int)GetWidth(block)) - 1);
    }
}
