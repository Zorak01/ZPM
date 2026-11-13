// SPDX-License-Identifier: GPL-3.0-or-later
using System;
using System.Runtime.CompilerServices;

namespace Zpm;

/// <summary>A column of Float32 values kept as ordered bits: exact, and small when values are alike.</summary>
public unsafe struct ZpmFloatColumn
{
    ZpmColumn _values;

    /// <summary>How many values it holds.</summary>
    public readonly uint count => _values.count;

    /// <summary>Points at a column section. Touches only its count.</summary>
    public void Bind(byte* section, ZpmSectionFlags flags) => _values = ZpmColumn.Bind(section, flags);

    /// <summary>Returns one value with its exact bits. The index must be below count.</summary>
    [MethodImpl(MethodImplOptions.AggressiveInlining)]
    public readonly float Get(uint index) => BitConverter.UInt32BitsToSingle(ZpmAxis.OrderedToBits(_values.Get(index)));

    /// <summary>Checks the column fits the file and holds exactly this many values.</summary>
    public readonly bool Validate(byte* limit, uint expected) => _values.count == expected && _values.ValidateLayout(limit);
}
