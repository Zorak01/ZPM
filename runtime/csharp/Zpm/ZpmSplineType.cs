// SPDX-License-Identifier: GPL-3.0-or-later
namespace Zpm;

/// <summary>What kind of curve a spline is.</summary>
public enum ZpmSplineType : uint
{
    Poly = 0,
    Bezier = 1,
    Nurbs = 2,
}
