// SPDX-License-Identifier: GPL-3.0-or-later
using System.Runtime.InteropServices;

namespace Zpm;

/// <summary>One bone's posed transform, when it differs from rest.</summary>
[StructLayout(LayoutKind.Sequential, Pack = 4)]
public readonly struct ZpmPoseChannel
{
    /// <summary>The bone, in the object's armature.</summary>
    public readonly uint bone;

    /// <summary>Position relative to rest.</summary>
    public readonly ZpmVector3 location;

    /// <summary>Rotation values.</summary>
    public readonly ZpmVector4 rotation;

    /// <summary>Scale per axis.</summary>
    public readonly ZpmVector3 scale;

    /// <summary>What rotation means.</summary>
    public readonly ZpmRotationMode rotationMode;
}
