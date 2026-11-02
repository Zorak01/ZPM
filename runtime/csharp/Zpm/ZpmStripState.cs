// SPDX-License-Identifier: GPL-3.0-or-later
namespace Zpm;

/// <summary>Where a corner strip decode has reached. Lives on the caller's stack.</summary>
public unsafe struct ZpmStripState
{
    /// <summary>The next bit to read.</summary>
    public ulong position;

    /// <summary>The vertex a "next new vertex" bit stands for.</summary>
    public uint nextNew;

    /// <summary>The edge the last link used, which sets the preferred edge.</summary>
    public uint lastEdge;

    /// <summary>The first corner of the face held, and how many corners it has.</summary>
    public uint faceStart, faceSize;

    /// <summary>The vertices of the face held.</summary>
    public fixed uint vertices[ZpmCornerStrips.MaxFaceCorners];
}
