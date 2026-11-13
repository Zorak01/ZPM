// SPDX-License-Identifier: GPL-3.0-or-later
using System.Runtime.InteropServices;

namespace Zpm;

/// <summary>One light's settings. Placement comes from the object using it.</summary>
[StructLayout(LayoutKind.Sequential, Pack = 4)]
public readonly struct ZpmLight
{
    /// <summary>Kind.</summary>
    public readonly ZpmLightType type;

    /// <summary>What power is in.</summary>
    public readonly ZpmLightUnit unit;

    /// <summary>Color.</summary>
    public readonly ZpmVector3 color;

    /// <summary>Power.</summary>
    public readonly float power;

    /// <summary>Bit 0: casts shadow.</summary>
    public readonly uint flags;

    /// <summary>Shape of an area light.</summary>
    public readonly ZpmAreaShape areaShape;

    /// <summary>Size of a point or spot source.</summary>
    public readonly float radius;

    /// <summary>Spot cone in radians.</summary>
    public readonly float spotAngle;

    /// <summary>Spot softness.</summary>
    public readonly float spotBlend;

    /// <summary>Sun size in radians.</summary>
    public readonly float sunAngle;

    /// <summary>Area width.</summary>
    public readonly float areaWidth;

    /// <summary>Area height.</summary>
    public readonly float areaHeight;
}
