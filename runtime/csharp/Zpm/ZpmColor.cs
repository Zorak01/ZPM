// SPDX-License-Identifier: GPL-3.0-or-later

namespace Zpm;

/// <summary>One color with channels as floats. RGBA8 layers read as 0 to 1.</summary>
public readonly struct ZpmColor
{
    public readonly float r, g, b, a;

    public ZpmColor(float r, float g, float b, float a)
    {
        this.r = r;
        this.g = g;
        this.b = b;
        this.a = a;
    }
}
