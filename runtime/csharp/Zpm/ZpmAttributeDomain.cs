// SPDX-License-Identifier: GPL-3.0-or-later
namespace Zpm;

/// <summary>What a custom attribute holds one element for.</summary>
public enum ZpmAttributeDomain : byte
{
    Point = 0,
    Edge = 1,
    Face = 2,
    Corner = 3,
    Mesh = 4,
}
