// SPDX-License-Identifier: GPL-3.0-or-later
using System.Runtime.InteropServices;

namespace Zpm;

/// <summary>One bone in armature space.</summary>
[StructLayout(LayoutKind.Sequential, Pack = 4)]
public readonly struct ZpmBone
{
    /// <summary>Its parent in the armature, or ZpmLayout.NoIndex.</summary>
    public readonly uint parent;

    /// <summary>Behaviour.</summary>
    public readonly ZpmBoneFlags flags;

    /// <summary>Head position.</summary>
    public readonly ZpmVector3 head;

    /// <summary>Tail position.</summary>
    public readonly ZpmVector3 tail;

    /// <summary>Roll in radians.</summary>
    public readonly float roll;

    /// <summary>Envelope size.</summary>
    public readonly float envelopeDistance;

    /// <summary>Radius at the head.</summary>
    public readonly float headRadius;

    /// <summary>Radius at the tail.</summary>
    public readonly float tailRadius;
}
