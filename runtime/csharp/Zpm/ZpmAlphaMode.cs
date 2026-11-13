// SPDX-License-Identifier: GPL-3.0-or-later
namespace Zpm;

/// <summary>How a material uses alpha.</summary>
public enum ZpmAlphaMode : uint
{
    Opaque = 0,
    Cutoff = 1,
    Blend = 2,
}
