// SPDX-License-Identifier: GPL-3.0-or-later

namespace Zpm;

/// <summary>One corner's unit normal.</summary>
public readonly struct ZpmNormal
{
    public readonly float x, y, z;

    public ZpmNormal(float x, float y, float z)
    {
        this.x = x;
        this.y = y;
        this.z = z;
    }
}
