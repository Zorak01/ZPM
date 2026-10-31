// SPDX-License-Identifier: GPL-3.0-or-later
using System.Runtime.InteropServices;

namespace Zpm;

/// <summary>One placed thing, as stored in the file. Links are indices, or ZpmLayout.NoIndex.</summary>
[StructLayout(LayoutKind.Sequential, Pack = 4)]
public readonly struct ZpmObject
{
    /// <summary>What it is, which says what data indexes.</summary>
    public readonly ZpmObjectType type;

    /// <summary>Its mesh, armature or light.</summary>
    public readonly uint data;

    /// <summary>Its parent object.</summary>
    public readonly uint parent;

    /// <summary>The bone of its parent armature it follows.</summary>
    public readonly uint parentBone;

    /// <summary>What rotation means.</summary>
    public readonly ZpmRotationMode rotationMode;

    /// <summary>How it is shown.</summary>
    public readonly ZpmObjectFlags flags;

    /// <summary>The collection it instances.</summary>
    public readonly uint instanceCollection;

    /// <summary>The armature object deforming it.</summary>
    public readonly uint skinArmature;

    /// <summary>How it follows that armature.</summary>
    public readonly ZpmSkinFlags skinFlags;

    /// <summary>Position relative to its parent.</summary>
    public readonly ZpmVector3 location;

    /// <summary>Rotation values.</summary>
    public readonly ZpmVector4 rotation;

    /// <summary>Scale per axis.</summary>
    public readonly ZpmVector3 scale;

    /// <summary>The matrix between it and its parent.</summary>
    public readonly ZpmMatrix4 parentInverse;

    /// <summary>Where its pose channels start.</summary>
    public readonly uint firstPoseChannel;

    /// <summary>How many bones it poses.</summary>
    public readonly uint poseChannelCount;
}
