// SPDX-License-Identifier: GPL-3.0-or-later
using System.Runtime.InteropServices;

namespace Zpm;

/// <summary>One bone in armature space.</summary>
[StructLayout(LayoutKind.Sequential, Pack = 4)]
public readonly struct ZpmBone
{
    const int InheritScaleShift = 8;
    const uint FlagBits = 255;

    /// <summary>Its parent in the armature, or ZpmLayout.NoIndex.</summary>
    public readonly uint parent;

    readonly uint _flags;

    /// <summary>Head and tail positions.</summary>
    public readonly ZpmVector3 head, tail;

    /// <summary>Roll in radians, around the bone's own axis.</summary>
    public readonly float roll;

    /// <summary>Envelope size, and radius at each end.</summary>
    public readonly float envelopeDistance, headRadius, tailRadius;

    /// <summary>Its flags.</summary>
    public ZpmBoneFlags flags => (ZpmBoneFlags)(_flags & FlagBits);

    /// <summary>How it takes its parent's scale.</summary>
    public ZpmInheritScale inheritScale => (ZpmInheritScale)(_flags >> InheritScaleShift);

    /// <summary>The flags and inheritance as stored.</summary>
    public uint storedFlags => _flags;
}
