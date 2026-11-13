// SPDX-License-Identifier: GPL-3.0-or-later
namespace Zpm;

/// <summary>How one component of a custom attribute is stored. All are exact.</summary>
public enum ZpmAttributeType : byte
{
    Bool = 0,
    Int8 = 1,
    UInt8 = 2,
    Int16 = 3,
    UInt16 = 4,
    Int32 = 5,
    UInt32 = 6,
    Float32 = 7,
    String = 8,
}
