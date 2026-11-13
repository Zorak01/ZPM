// SPDX-License-Identifier: GPL-3.0-or-later
using System.Runtime.InteropServices;

namespace Zpm;

/// <summary>One node of a value tree.</summary>
[StructLayout(LayoutKind.Sequential, Pack = 4)]
public readonly struct ZpmValueNode
{
    /// <summary>Its name in the string table, or ZpmLayout.NoIndex.</summary>
    public readonly uint key;

    /// <summary>What it holds.</summary>
    public readonly ZpmValueType type;

    /// <summary>Its value, or where its array or children start.</summary>
    public readonly uint a;

    /// <summary>How many array values or children it has.</summary>
    public readonly uint b;
}
