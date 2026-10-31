// SPDX-License-Identifier: GPL-3.0-or-later
using System;

namespace Zpm;

/// <summary>What is declared about a track.</summary>
[Flags]
public enum ZpmTrackFlags : uint
{
    None = 0,
    Baked = 1,
    SphericalRotation = 2,
}
