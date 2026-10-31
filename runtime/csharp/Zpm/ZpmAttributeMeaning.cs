// SPDX-License-Identifier: GPL-3.0-or-later
namespace Zpm;

/// <summary>What a custom attribute's components mean together.</summary>
public enum ZpmAttributeMeaning : byte
{
    None = 0,
    Vector = 1,
    Color = 2,
    Quaternion = 3,
    Matrix = 4,
}
