// SPDX-License-Identifier: GPL-3.0-or-later
namespace Zpm;

/// <summary>One curve control point. Handles are zero outside Bezier splines.</summary>
public readonly struct ZpmCurvePoint
{
    /// <summary>Position, and NURBS weight in w.</summary>
    public readonly ZpmColor position;

    /// <summary>Thickness and twist at this point.</summary>
    public readonly float radius, tilt;

    public ZpmCurvePoint(in ZpmColor position, float radius, float tilt)
    {
        this.position = position;
        this.radius = radius;
        this.tilt = tilt;
    }
}
