// SPDX-License-Identifier: GPL-3.0-or-later
using System.Numerics;
using System.Runtime.CompilerServices;
using System.Runtime.Intrinsics;
using System.Runtime.Intrinsics.X86;

namespace Zpm.DirectRead;

/// <summary>
/// A read-only array of UInt32 values stored in file bytes, readable by index without decoding.
/// value[i] = base + slope * j + residual[j], with j the position inside a 64-value block.
/// Costs one block lookup and one unaligned read per value. Allocates nothing.
/// </summary>
public unsafe struct ZpmColumn
{
    public const int BlockSize = 64;
    public const int BlocksPerGroup = 8;
    public const int MaxWidth = 32;
    const int BlockShift = 6;
    const uint BlockMask = 63;
    const int GroupShift = 3;
    const uint GroupMask = 7;
    const int BitsPerByteShift = 3;
    const uint BitMask = 7;
    const ulong SumBytes = 0x0101010101010101UL;
    const uint WideValues = 8;
    const uint MaxWideWidth = 27;

    uint* _bases;
    sbyte* _slopes;
    byte* _widths;
    uint* _groups;
    byte* _payload;
    uint _count;

    public readonly uint count => _count;
    public readonly uint blockCount => BlockCountOf(_count);

    public static uint BlockCountOf(uint count) => (uint)(((ulong)count + BlockMask) >> BlockShift);
    public static uint GroupCountOf(uint blocks) => (uint)(((ulong)blocks + GroupMask) >> GroupShift);
    public static uint Align4(uint offset) => (offset + 3) & ~3u;

    /// <summary>Points at a column. The entry is its file offset, with bit 0 set when it stores slopes.</summary>
    public static ZpmColumn Bind(byte* file, uint entry, uint count)
    {
        uint blocks = BlockCountOf(count);
        uint offset = entry & ~1u;
        var column = new ZpmColumn { _count = count };
        column._bases = (uint*)(file + offset);
        offset += blocks * 4;
        if ((entry & 1) != 0)
        {
            column._slopes = (sbyte*)(file + offset);
            offset += blocks;
        }
        column._widths = file + offset;
        offset = Align4(offset + blocks);
        column._groups = (uint*)(file + offset);
        offset += GroupCountOf(blocks) * 4;
        column._payload = file + offset;
        return column;
    }

    [MethodImpl(MethodImplOptions.AggressiveInlining)]
    readonly uint GetBlockOffset(uint block)
    {
        uint group = block >> GroupShift;
        ulong widths = Unsafe.ReadUnaligned<ulong>(_widths + (group << GroupShift));
        ulong before = widths & ((1UL << (int)((block & GroupMask) << BitsPerByteShift)) - 1);
        // Each full block holds 64 values, so its payload is exactly width * 8 bytes.
        uint skipped = (uint)((before * SumBytes) >> 56) << BitsPerByteShift;
        return _groups[group] + skipped;
    }

    /// <summary>Returns one value. The index must be below count; nothing checks it here.</summary>
    [MethodImpl(MethodImplOptions.AggressiveInlining)]
    public readonly uint Get(uint index)
    {
        uint block = index >> BlockShift;
        uint position = index & BlockMask;
        uint width = _widths[block];
        uint bit = position * width;
        ulong raw = Unsafe.ReadUnaligned<ulong>(_payload + GetBlockOffset(block) + (bit >> BitsPerByteShift));
        uint residual = (uint)((raw >> (int)(bit & BitMask)) & ((1UL << (int)width) - 1));
        uint value = _bases[block] + residual;
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
        uint width = _widths[block];
        uint slope = _slopes == null ? 0 : (uint)(int)_slopes[block];
        uint line = _bases[block] + slope * position;
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
        byte* tables = (byte*)_bases;
        uint blocks = blockCount;
        if (tables >= limit && blocks != 0)
            return false;
        // Check the descriptors fit first: their pointers came from the untrusted count.
        if ((ulong)(limit - tables) < (ulong)blocks * 5 || _payload > limit)
            return false;
        ulong running = 0;
        for (uint block = 0; block < blocks; block++)
        {
            uint width = _widths[block];
            if (width > MaxWidth)
                return false;
            if ((block & GroupMask) == 0)
            {
                if (_groups[block >> GroupShift] != running)
                    return false;
            }
            bool last = block + 1 == blocks;
            uint values = last ? _count - (block << BlockShift) : BlockSize;
            if (!last)
                running += (ulong)width << BitsPerByteShift;
            else
                running += ((ulong)width * values + BitMask) >> BitsPerByteShift;
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

    /// <summary>Returns the lowest and highest value a block could hold, from its descriptor alone.</summary>
    public readonly void GetBlockRange(uint block, bool signedBase, out long low, out long high)
    {
        long start = signedBase ? (int)_bases[block] : _bases[block];
        long slope = _slopes == null ? 0 : _slopes[block];
        uint values = Math.Min((uint)BlockSize, _count - (block << BlockShift));
        long travel = slope * (values - 1);
        low = start + Math.Min(0, travel);
        high = start + Math.Max(0, travel) + ((1L << _widths[block]) - 1);
    }
}

/// <summary>
/// Marks some of N elements, and answers in constant time how many marked or unmarked
/// elements come before any element. Stores 8 + 4 bytes per 64 elements.
/// </summary>
public unsafe struct ZpmRankMask
{
    const int WordShift = 6;
    const uint WordMask = 63;
    const int BitsPerWord = 64;

    ulong* _words;
    uint* _ranks;
    uint _count;

    public readonly uint wordCount => WordCountOf(_count);

    public static uint WordCountOf(uint count) => (uint)(((ulong)count + WordMask) >> WordShift);
    public static uint ByteSizeOf(uint count) => WordCountOf(count) * 12;

    public static ZpmRankMask Bind(byte* file, uint offset, uint count) => new()
    {
        _words = (ulong*)(file + offset),
        _ranks = (uint*)(file + offset + WordCountOf(count) * 8),
        _count = count,
    };

    [MethodImpl(MethodImplOptions.AggressiveInlining)]
    public readonly bool IsSet(uint index) => (_words[index >> WordShift] >> (int)(index & WordMask) & 1) != 0;

    /// <summary>Returns how many unmarked elements come before this index.</summary>
    [MethodImpl(MethodImplOptions.AggressiveInlining)]
    public readonly uint GetClearRank(uint index)
    {
        uint word = index >> WordShift;
        ulong below = (1UL << (int)(index & WordMask)) - 1;
        return _ranks[word] + (uint)BitOperations.PopCount(~_words[word] & below);
    }

    /// <summary>Returns how many marked elements come before this index.</summary>
    [MethodImpl(MethodImplOptions.AggressiveInlining)]
    public readonly uint GetSetRank(uint index) => index - GetClearRank(index);

    /// <summary>Returns how many elements from this index on, inside its word, share its mark.</summary>
    [MethodImpl(MethodImplOptions.AggressiveInlining)]
    public readonly uint GetClearRun(uint index)
    {
        int shift = (int)(index & WordMask);
        ulong marked = _words[index >> WordShift] >> shift;
        return (uint)Math.Min(BitOperations.TrailingZeroCount(marked), BitsPerWord - shift);
    }

    /// <summary>Checks the mask fits the file and its ranks add up to the expected unmarked total.</summary>
    public readonly bool Validate(byte* limit, uint clearCount)
    {
        uint words = wordCount;
        if ((byte*)_words >= limit && words != 0)
            return false;
        if ((ulong)(limit - (byte*)_words) < (ulong)words * 12)
            return false;
        uint running = 0;
        for (uint word = 0; word < words; word++)
        {
            if (_ranks[word] != running)
                return false;
            ulong bits = _words[word];
            uint used = word + 1 == words ? _count - (word << WordShift) : BitsPerWord;
            if (used < BitsPerWord && bits >> (int)used != 0)
                return false;
            running += used - (uint)BitOperations.PopCount(bits);
        }
        return running == clearCount;
    }
}
