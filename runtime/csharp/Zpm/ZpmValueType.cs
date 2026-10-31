// SPDX-License-Identifier: GPL-3.0-or-later
namespace Zpm;

/// <summary>What one node of a value tree holds.</summary>
public enum ZpmValueType : uint
{
    Bool = 0,
    Int = 1,
    Float = 2,
    String = 3,
    Reference = 4,
    IntArray = 5,
    FloatArray = 6,
    Dictionary = 7,
    List = 8,
}
