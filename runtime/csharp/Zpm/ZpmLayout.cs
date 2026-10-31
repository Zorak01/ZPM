// SPDX-License-Identifier: GPL-3.0-or-later
using System;

namespace Zpm;

/// <summary>What a file section holds. A directory lists its sections in rising order.</summary>
public enum ZpmSectionKind : ushort
{
    MeshInfo = 1,
    PositionRepeatMask = 2,
    PositionRepeatIds = 3,
    PositionBitsX = 16,
    PositionConstantX = 20,
    PositionPaletteX = 24,
    PositionIndicesX = 28,
    CornerIds = 32,
    CornerFirst = 33,
    CornerOffset1 = 34,
    CornerOffset2 = 35,
    CornerOffset3 = 36,
    CornerRepeatMask = 37,
    CornerBackDistances = 38,
    FaceStarts = 40,
    EdgeFirst = 48,
    EdgeSpan = 49,
    EdgeLooseMask = 50,
    EdgeLooseFirst = 51,
    EdgeLooseSecond = 52,
    EdgeCorners = 53,
    EdgeFlips = 54,
    EdgeFirstUse = 55,
}

/// <summary>How a section is stored.</summary>
[Flags]
public enum ZpmSectionFlags : ushort
{
    None = 0,
    HasSlopes = 1,
    SignedIndices = 2,
}

/// <summary>Fixed byte layout of a file; docs/mesh-core.md is the written contract.</summary>
public static class ZpmLayout
{
    public const uint Magic = 0x004D505A; // "ZPM\0"
    public const ushort Version = 1;
    public const int FileHeaderBytes = 12;
    public const int DirectoryEntryBytes = 8;
    public const int MeshInfoBytes = 24;
    public const int ColumnCountBytes = 4;
    public const int ConstantBytes = 4;
    public const int TailBytes = 32;
    public const int AxisCount = 3;
    public const int MaxRelativeArity = 4;
}
