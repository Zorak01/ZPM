// SPDX-License-Identifier: GPL-3.0-or-later
using System.Runtime.InteropServices;

namespace Zpm;

/// <summary>A 4x4 matrix, row by row.</summary>
[StructLayout(LayoutKind.Sequential, Pack = 4)]
public readonly struct ZpmMatrix4
{
    /// <summary>First row.</summary>
    public readonly ZpmVector4 row0;

    /// <summary>Second row.</summary>
    public readonly ZpmVector4 row1;

    /// <summary>Third row.</summary>
    public readonly ZpmVector4 row2;

    /// <summary>Fourth row.</summary>
    public readonly ZpmVector4 row3;
}
