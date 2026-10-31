// SPDX-License-Identifier: GPL-3.0-or-later
namespace Zpm;

/// <summary>What a texture does past its edge.</summary>
public enum ZpmSamplerWrap : uint
{
    Repeat = 0,
    Extend = 1,
    Clip = 2,
    Mirror = 3,
}
