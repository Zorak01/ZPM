// SPDX-License-Identifier: GPL-3.0-or-later
using System.Runtime.InteropServices;

namespace Zpm;

/// <summary>One image's description.</summary>
[StructLayout(LayoutKind.Sequential, Pack = 4)]
public readonly struct ZpmImage
{
    /// <summary>Width in pixels.</summary>
    public readonly uint width;

    /// <summary>Height in pixels.</summary>
    public readonly uint height;

    /// <summary>Channel count.</summary>
    public readonly uint channels;

    /// <summary>Bit 0: has alpha.</summary>
    public readonly uint flags;

    /// <summary>Where its tiles start.</summary>
    public readonly uint firstTile;

    /// <summary>How many files it has.</summary>
    public readonly uint tileCount;
}
