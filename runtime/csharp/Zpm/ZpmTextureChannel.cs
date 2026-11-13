// SPDX-License-Identifier: GPL-3.0-or-later
namespace Zpm;

/// <summary>Which part of an image a texture slot reads.</summary>
public enum ZpmTextureChannel : uint
{
    Color = 0,
    Red = 1,
    Green = 2,
    Blue = 3,
    Alpha = 4,
}
