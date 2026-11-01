// SPDX-License-Identifier: GPL-3.0-or-later
using System;
using System.Runtime.InteropServices;

namespace Zpm;

/// <summary>Three floats, as stored.</summary>
[StructLayout(LayoutKind.Sequential, Pack = 4)]
public readonly struct ZpmVector3
{
    public readonly float x, y, z;

    public ZpmVector3(float x, float y, float z)
    {
        this.x = x;
        this.y = y;
        this.z = z;
    }

    /// <summary>Returns this vector minus another.</summary>
    public ZpmVector3 Minus(in ZpmVector3 other) => new(x - other.x, y - other.y, z - other.z);

    /// <summary>Returns the vector's length.</summary>
    public float GetLength() => MathF.Sqrt(x * x + y * y + z * z);
}
