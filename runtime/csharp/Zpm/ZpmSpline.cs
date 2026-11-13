// SPDX-License-Identifier: GPL-3.0-or-later
using System.Runtime.InteropServices;

namespace Zpm;

/// <summary>One curve within a curve resource.</summary>
[StructLayout(LayoutKind.Sequential, Pack = 4)]
public readonly struct ZpmSpline
{
    /// <summary>Kind.</summary>
    public readonly ZpmSplineType type;

    /// <summary>Closing and knots.</summary>
    public readonly ZpmSplineFlags flags;

    /// <summary>NURBS order.</summary>
    public readonly uint order;

    /// <summary>Tessellation hint.</summary>
    public readonly uint resolution;

    /// <summary>Its first point.</summary>
    public readonly uint firstPoint;

    /// <summary>How many points.</summary>
    public readonly uint pointCount;

    /// <summary>Its first knot, or ZpmLayout.NoIndex when calculated.</summary>
    public readonly uint firstKnot;

    /// <summary>How many knots.</summary>
    public readonly uint knotCount;
}
