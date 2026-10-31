// SPDX-License-Identifier: GPL-3.0-or-later
namespace Zpm;

/// <summary>How an absolute shape target blends with its neighbours in time.</summary>
public enum ZpmShapeInterpolation : byte
{
    Linear = 0,
    Cardinal = 1,
    CatmullRom = 2,
    BSpline = 3,
}
