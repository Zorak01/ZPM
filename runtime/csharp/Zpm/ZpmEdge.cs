// SPDX-License-Identifier: GPL-3.0-or-later

namespace Zpm;

/// <summary>One edge as its two vertex IDs, in source order.</summary>
public readonly struct ZpmEdge
{
    public readonly uint a, b;

    public ZpmEdge(uint a, uint b)
    {
        this.a = a;
        this.b = b;
    }
}
