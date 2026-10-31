// SPDX-License-Identifier: GPL-3.0-or-later
using System;
using System.Numerics;
using System.Runtime.CompilerServices;

namespace Zpm;

/// <summary>
/// Marks some of N elements, and answers in constant time how many marked or unmarked
/// elements come before any element. Stores 8 + 4 bytes per 64 elements.
/// </summary>
public unsafe struct ZpmRankMask
{
    const int WordShift = 6;
    const uint WordMask = 63;
    const int BitsPerWord = 64;
    const int WordBytes = 8;
    const int RankBytes = 4;

    ulong* _words;
    uint* _ranks;
    uint _count;

    readonly uint wordCount => (uint)(((ulong)_count + WordMask) >> WordShift);

    /// <summary>Points at a mask section covering count elements. Touches nothing.</summary>
    public static ZpmRankMask Bind(byte* section, uint count)
    {
        var mask = new ZpmRankMask { _count = count, _words = (ulong*)section };
        mask._ranks = (uint*)(section + (ulong)mask.wordCount * WordBytes);
        return mask;
    }

    /// <summary>Returns whether this element is marked.</summary>
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

    /// <summary>Returns how many unmarked elements run from this index, inside its word.</summary>
    [MethodImpl(MethodImplOptions.AggressiveInlining)]
    public readonly uint GetClearRun(uint index)
    {
        int shift = (int)(index & WordMask);
        ulong marked = _words[index >> WordShift] >> shift;
        return (uint)Math.Min(BitOperations.TrailingZeroCount(marked), BitsPerWord - shift);
    }

    /// <summary>Checks the mask fits the file and its ranks add up to the unmarked total.</summary>
    public readonly bool Validate(byte* limit, uint clearCount)
    {
        uint words = wordCount;
        if ((byte*)_words > limit || (ulong)(limit - (byte*)_words) < (ulong)words * (WordBytes + RankBytes))
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
