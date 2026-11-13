// SPDX-License-Identifier: GPL-3.0-or-later
using System.Numerics;
using System.Runtime.CompilerServices;
using System.Runtime.Intrinsics.X86;

namespace Zpm;

/// <summary>
/// One bit per element, plus where every 64th set bit sits, so the Nth set bit is found by
/// reading at most a few words. Stores 1 bit per element and 4 bytes per 64 set bits.
/// </summary>
public unsafe struct ZpmSelectBits
{
    const int WordShift = 6;
    const uint WordMask = 63;
    const int BitsPerWord = 64;
    const int WordBytes = 8;
    const int SelectBytes = 4;
    const uint SetBitsPerSelect = 64;

    ulong* _words;
    uint* _selects;
    uint _bitCount;

    readonly uint wordCount => (uint)(((ulong)_bitCount + WordMask) >> WordShift);

    /// <summary>Points at a section covering bitCount elements. Touches nothing.</summary>
    public static ZpmSelectBits Bind(byte* section, uint bitCount)
    {
        var bits = new ZpmSelectBits { _bitCount = bitCount, _words = (ulong*)section };
        bits._selects = (uint*)(section + (ulong)bits.wordCount * WordBytes);
        return bits;
    }

    /// <summary>Returns whether this element is marked.</summary>
    [MethodImpl(MethodImplOptions.AggressiveInlining)]
    public readonly bool IsSet(uint index) => (_words[index >> WordShift] >> (int)(index & WordMask) & 1) != 0;

    /// <summary>Returns the position of the Nth set bit. N must be below the set count.</summary>
    public readonly uint Select(uint rank)
    {
        uint position = _selects[rank / SetBitsPerSelect];
        uint remaining = rank % SetBitsPerSelect;
        uint word = position >> WordShift;
        ulong bits = _words[word] & (~0UL << (int)(position & WordMask));
        while (true)
        {
            uint available = (uint)BitOperations.PopCount(bits);
            if (remaining < available)
                return word * BitsPerWord + SelectInWord(bits, remaining);
            remaining -= available;
            bits = _words[++word];
        }
    }

    [MethodImpl(MethodImplOptions.AggressiveInlining)]
    static uint SelectInWord(ulong bits, uint rank)
    {
        if (Bmi2.X64.IsSupported)
            return (uint)BitOperations.TrailingZeroCount(Bmi2.X64.ParallelBitDeposit(1UL << (int)rank, bits));
        for (; rank != 0; rank--)
            bits &= bits - 1;
        return (uint)BitOperations.TrailingZeroCount(bits);
    }

    /// <summary>Returns the first set position after this one, or the bit count when none follows.</summary>
    public readonly uint GetNextSet(uint index)
    {
        uint next = index + 1;
        if (next >= _bitCount)
            return _bitCount;
        uint word = next >> WordShift;
        uint words = wordCount;
        ulong bits = _words[word] & (~0UL << (int)(next & WordMask));
        while (bits == 0)
        {
            if (++word == words)
                return _bitCount;
            bits = _words[word];
        }
        return word * BitsPerWord + (uint)BitOperations.TrailingZeroCount(bits);
    }

    /// <summary>Returns the last set position at or before this one. Bit 0 must be set.</summary>
    public readonly uint GetLastSetAtOrBefore(uint index)
    {
        uint word = index >> WordShift;
        ulong bits = _words[word] & (~0UL >> (int)(WordMask - (index & WordMask)));
        while (bits == 0)
            bits = _words[--word];
        return word * BitsPerWord + WordMask - (uint)BitOperations.LeadingZeroCount(bits);
    }

    /// <summary>Checks the section fits the file, holds setCount bits and its table is right.</summary>
    public readonly bool Validate(byte* limit, uint setCount)
    {
        uint words = wordCount;
        uint selects = (uint)(((ulong)setCount + SetBitsPerSelect - 1) / SetBitsPerSelect);
        ulong needed = (ulong)words * WordBytes + (ulong)selects * SelectBytes;
        if ((byte*)_words > limit || (ulong)(limit - (byte*)_words) < needed)
            return false;
        uint spare = (uint)(words * (ulong)BitsPerWord - _bitCount);
        if (spare != 0 && _words[words - 1] >> (BitsPerWord - (int)spare) != 0)
            return false;
        uint seen = 0;
        for (uint word = 0; word < words; word++)
        {
            ulong bits = _words[word];
            while (bits != 0)
            {
                if (seen >= setCount)
                    return false;
                if (seen % SetBitsPerSelect == 0)
                {
                    uint position = word * BitsPerWord + (uint)BitOperations.TrailingZeroCount(bits);
                    if (_selects[seen / SetBitsPerSelect] != position)
                        return false;
                }
                bits &= bits - 1;
                seen++;
            }
        }
        return seen == setCount;
    }
}
