// SPDX-License-Identifier: GPL-3.0-or-later
using System.Runtime.InteropServices;

namespace Zpm;

/// <summary>Where one curve's splines are.</summary>
[StructLayout(LayoutKind.Sequential, Pack = 4)]
public readonly struct ZpmCurveInfo
{
    /// <summary>Its first spline.</summary>
    public readonly uint firstSpline;

    /// <summary>How many it has.</summary>
    public readonly uint splineCount;
}
