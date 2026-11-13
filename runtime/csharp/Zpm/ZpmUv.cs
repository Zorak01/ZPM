// SPDX-License-Identifier: GPL-3.0-or-later

namespace Zpm;

/// <summary>One corner's texture coordinate.</summary>
public readonly struct ZpmUv
{
    public readonly float u, v;

    public ZpmUv(float u, float v)
    {
        this.u = u;
        this.v = v;
    }
}
