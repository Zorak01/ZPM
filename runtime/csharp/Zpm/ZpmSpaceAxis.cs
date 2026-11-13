// SPDX-License-Identifier: GPL-3.0-or-later
namespace Zpm;

/// <summary>A direction along one axis of the file's coordinate system.</summary>
public enum ZpmSpaceAxis : byte
{
    X = 0,
    Y = 1,
    Z = 2,
    NegativeX = 3,
    NegativeY = 4,
    NegativeZ = 5,
}
