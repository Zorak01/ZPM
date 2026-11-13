// SPDX-License-Identifier: GPL-3.0-or-later
using System.Runtime.CompilerServices;

namespace Zpm;

/// <summary>A table of UTF-8 names in file bytes: a count, then a length and bytes per name.</summary>
public unsafe struct ZpmNames
{
    const int FieldBytes = 4;

    byte* _section;
    uint _count;

    /// <summary>How many names the table holds.</summary>
    public readonly uint count => _count;

    /// <summary>Points at a name table. Reads only its count.</summary>
    public static ZpmNames Bind(byte* section) => new()
    {
        _section = section,
        _count = Unsafe.ReadUnaligned<uint>(section),
    };

    /// <summary>Finds one name's UTF-8 bytes by walking the table. Returns false when out of range.</summary>
    public readonly bool TryGet(uint index, out byte* utf8, out uint length)
    {
        utf8 = null;
        length = 0;
        if (index >= _count)
            return false;
        byte* at = _section + FieldBytes;
        for (uint i = 0; i < index; i++)
            at += FieldBytes + Unsafe.ReadUnaligned<uint>(at);
        length = Unsafe.ReadUnaligned<uint>(at);
        utf8 = at + FieldBytes;
        return true;
    }

    /// <summary>Checks every name fits the file.</summary>
    public readonly bool Validate(byte* limit)
    {
        if (_section == null)
            return true;
        byte* at = _section + FieldBytes;
        for (uint i = 0; i < _count; i++)
        {
            if (at > limit || limit - at < FieldBytes)
                return false;
            uint length = Unsafe.ReadUnaligned<uint>(at);
            if (length > (ulong)(limit - at) - FieldBytes)
                return false;
            at += FieldBytes + length;
        }
        return true;
    }
}
