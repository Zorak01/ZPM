// SPDX-License-Identifier: GPL-3.0-or-later
using System.Runtime.InteropServices;

namespace Zpm;

/// <summary>Which resource a value tree belongs to.</summary>
[StructLayout(LayoutKind.Sequential, Pack = 4)]
public readonly struct ZpmValueOwner
{
    /// <summary>The kind of resource.</summary>
    public readonly ZpmResourceKind kind;

    /// <summary>Which one.</summary>
    public readonly uint index;

    /// <summary>The tree's root.</summary>
    public readonly uint node;
}
