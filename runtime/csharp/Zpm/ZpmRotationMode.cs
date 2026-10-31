// SPDX-License-Identifier: GPL-3.0-or-later
namespace Zpm;

/// <summary>What an object's four rotation values mean. Eulers leave the last one zero.</summary>
public enum ZpmRotationMode : uint
{
    Quaternion = 0,
    AxisAngle = 1,
    Xyz = 2,
    Xzy = 3,
    Yxz = 4,
    Yzx = 5,
    Zxy = 6,
    Zyx = 7,
}
