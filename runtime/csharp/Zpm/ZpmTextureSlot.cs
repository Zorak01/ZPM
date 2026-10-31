// SPDX-License-Identifier: GPL-3.0-or-later
using System.Runtime.InteropServices;

namespace Zpm;

/// <summary>One image a material uses. TryGetTextureRole says what it feeds.</summary>
[StructLayout(LayoutKind.Sequential, Pack = 4)]
public readonly struct ZpmTextureSlot
{
    /// <summary>The image.</summary>
    public readonly uint image;

    /// <summary>How it samples.</summary>
    public readonly uint sampler;

    /// <summary>UV offset.</summary>
    public readonly ZpmVector2 offset;

    /// <summary>UV rotation in radians.</summary>
    public readonly float rotation;

    /// <summary>UV scale.</summary>
    public readonly ZpmVector2 scale;
}
