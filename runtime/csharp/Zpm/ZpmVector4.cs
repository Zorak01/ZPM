// SPDX-License-Identifier: GPL-3.0-or-later
using System.Runtime.InteropServices;

namespace Zpm;

/// <summary>Four floats, as stored: a color, rotation values, or a matrix row.</summary>
[StructLayout(LayoutKind.Sequential, Pack = 4)]
public readonly struct ZpmVector4
{
    public readonly float x, y, z, w;

    public ZpmVector4(float x, float y, float z, float w)
    {
        this.x = x;
        this.y = y;
        this.z = z;
        this.w = w;
    }
}
