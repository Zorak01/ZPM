// SPDX-License-Identifier: GPL-3.0-or-later
using System.Runtime.InteropServices;

namespace Zpm;

/// <summary>Two floats, as stored.</summary>
[StructLayout(LayoutKind.Sequential, Pack = 4)]
public readonly struct ZpmVector2
{
    /// <summary>First value.</summary>
    public readonly float x;

    /// <summary>Second value.</summary>
    public readonly float y;
}
