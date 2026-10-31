// SPDX-License-Identifier: GPL-3.0-or-later
using System.Runtime.InteropServices;

namespace Zpm;

/// <summary>One lattice's size and points.</summary>
[StructLayout(LayoutKind.Sequential, Pack = 4)]
public readonly struct ZpmLatticeInfo
{
    /// <summary>Points along U.</summary>
    public readonly uint pointsU;

    /// <summary>Points along V.</summary>
    public readonly uint pointsV;

    /// <summary>Points along W.</summary>
    public readonly uint pointsW;

    /// <summary>Its first point.</summary>
    public readonly uint firstPoint;

    /// <summary>How many points.</summary>
    public readonly uint pointCount;
}
