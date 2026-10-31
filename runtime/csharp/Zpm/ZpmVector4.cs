// SPDX-License-Identifier: GPL-3.0-or-later
using System.Runtime.InteropServices;

namespace Zpm;

/// <summary>Four floats, as stored: a color, or rotation values.</summary>
[StructLayout(LayoutKind.Sequential, Pack = 4)]
public readonly struct ZpmVector4
{
    /// <summary>First value.</summary>
    public readonly float x;

    /// <summary>Second value.</summary>
    public readonly float y;

    /// <summary>Third value.</summary>
    public readonly float z;

    /// <summary>Fourth value.</summary>
    public readonly float w;
}
