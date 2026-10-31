// SPDX-License-Identifier: GPL-3.0-or-later
using System.Runtime.InteropServices;

namespace Zpm;

/// <summary>One piece of animation.</summary>
[StructLayout(LayoutKind.Sequential, Pack = 4)]
public readonly struct ZpmClip
{
    /// <summary>Its first track.</summary>
    public readonly uint firstTrack;

    /// <summary>How many tracks.</summary>
    public readonly uint trackCount;

    /// <summary>How many frames make a second.</summary>
    public readonly float framesPerSecond;

    /// <summary>First frame.</summary>
    public readonly float start;

    /// <summary>Last frame.</summary>
    public readonly float end;
}
