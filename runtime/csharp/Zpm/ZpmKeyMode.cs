// SPDX-License-Identifier: GPL-3.0-or-later
namespace Zpm;

/// <summary>How a key reaches the next one.</summary>
public enum ZpmKeyMode : uint
{
    Constant = 0,
    Linear = 1,
    Bezier = 2,
    Sine = 3,
    Quad = 4,
    Cubic = 5,
    Quart = 6,
    Quint = 7,
    Expo = 8,
    Circ = 9,
    Back = 10,
    Bounce = 11,
    Elastic = 12,
}
