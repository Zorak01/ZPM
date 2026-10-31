// SPDX-License-Identifier: GPL-3.0-or-later

namespace Zpm;

/// <summary>One corner's unit tangent, with the bitangent's sign in w.</summary>
public readonly struct ZpmTangent
{
    public readonly float x, y, z, w;

    public ZpmTangent(float x, float y, float z, float w)
    {
        this.x = x;
        this.y = y;
        this.z = z;
        this.w = w;
    }
}
