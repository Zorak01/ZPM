// SPDX-License-Identifier: GPL-3.0-or-later
using System.Runtime.InteropServices;

namespace Zpm;

/// <summary>One metallic-roughness material.</summary>
[StructLayout(LayoutKind.Sequential, Pack = 4)]
public readonly struct ZpmMaterial
{
    /// <summary>Base color and alpha.</summary>
    public readonly ZpmVector4 baseColor;

    /// <summary>From 0 to 1.</summary>
    public readonly float metallic;

    /// <summary>From 0 to 1.</summary>
    public readonly float roughness;

    /// <summary>Emission color.</summary>
    public readonly ZpmVector3 emission;

    /// <summary>Emission intensity.</summary>
    public readonly float emissionStrength;

    /// <summary>Normal map strength.</summary>
    public readonly float normalScale;

    /// <summary>The cutoff when alphaMode is Cutoff.</summary>
    public readonly float alphaCutoff;

    /// <summary>How alpha is used.</summary>
    public readonly ZpmAlphaMode alphaMode;

    /// <summary>Bit 0: double sided.</summary>
    public readonly uint flags;

    /// <summary>Where its texture slots start.</summary>
    public readonly uint firstSlot;

    /// <summary>How many texture slots it has.</summary>
    public readonly uint slotCount;
}
