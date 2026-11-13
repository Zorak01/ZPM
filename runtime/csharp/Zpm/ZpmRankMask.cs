// SPDX-License-Identifier: GPL-3.0-or-later
using System;
using System.Numerics;
using System.Runtime.CompilerServices;

namespace Zpm;

/// <summary>
/// Marks some of N elements, and answers in constant time how many marked or unmarked
/// elements come before any element. Stores 8 bytes per 64 elements, plus 2 for each count when
/// the mask covers at most 65,535 elements and 4 when it covers more.
/// </summary>
public unsafe struct ZpmRankMask
{
    const int WordShift = 6;
    const uint WordMask = 63;
    const int BitsPerWord = 64;
    const int WordBytes = 8;
    const uint LargestShortCount = ushort.MaxValue;

    ulong* _words;
    byte* _ranks;
    uint _count, _rankBytes, _rankMask;

    readonly uint wordCount => (uint)(((ulong)_count + WordMask) >> WordShift);

    /// <summary>Points at a mask section covering count elements. Touches nothing.</summary>
    public static ZpmRankMask Bind(byte* section, uint count)
    {
        bool isShort = count <= LargestShortCount;
        var mask = new ZpmRankMask
        {
            _count = count,
            _words = (ulong*)section,
            _rankBytes = isShort ? 2u : 4u,
            _rankMask = isShort ? ushort.MaxValue : uint.MaxValue,
        };
        mask._ranks = section + (ulong)mask.wordCount * WordBytes;
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
        return GetWordRank(word) + (uint)BitOperations.PopCount(~_words[word] & below);
    }

    [MethodImpl(MethodImplOptions.AggressiveInlining)]
    readonly uint GetWordRank(uint word) => Unsafe.ReadUnaligned<uint>(_ranks + word * _rankBytes) & _rankMask;

    /// <summary>Returns the first marked element at or after this index, or count when there is none.</summary>
    public readonly uint GetNextSet(uint index)
    {
        uint words = wordCount;
        uint word = index >> WordShift;
        if (word >= words)
            return _count;
        ulong marked = _words[word] >> (int)(index & WordMask) << (int)(index & WordMask);
        while (marked == 0)
        {
            if (++word == words)
                return _count;
            marked = _words[word];
        }
        return (word << WordShift) + (uint)BitOperations.TrailingZeroCount(marked);
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
        if ((byte*)_words > limit || (ulong)(limit - (byte*)_words) < (ulong)words * (WordBytes + _rankBytes))
            return false;
        uint running = 0;
        for (uint word = 0; word < words; word++)
        {
            if (GetWordRank(word) != running)
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
