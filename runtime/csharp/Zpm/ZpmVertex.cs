// SPDX-License-Identifier: GPL-3.0-or-later
using System;

namespace Zpm;

/// <summary>One vertex position as exact Float32 bit patterns.</summary>
public readonly struct ZpmVertex
{
    public readonly uint xBits, yBits, zBits;

    public ZpmVertex(uint xBits, uint yBits, uint zBits)
    {
        this.xBits = xBits;
        this.yBits = yBits;
        this.zBits = zBits;
    }

    public float x => BitConverter.UInt32BitsToSingle(xBits);
    public float y => BitConverter.UInt32BitsToSingle(yBits);
    public float z => BitConverter.UInt32BitsToSingle(zBits);
}
