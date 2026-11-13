// SPDX-License-Identifier: GPL-3.0-or-later
using System;

namespace Zpm;

/// <summary>How a spline closes and spaces its knots.</summary>
[Flags]
public enum ZpmSplineFlags : uint
{
    None = 0,
    Cyclic = 1,
    Endpoint = 2,
    BezierKnots = 4,
}
