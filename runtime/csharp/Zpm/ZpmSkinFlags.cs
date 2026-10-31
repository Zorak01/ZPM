// SPDX-License-Identifier: GPL-3.0-or-later
using System;

namespace Zpm;

/// <summary>How a mesh object follows its armature.</summary>
[Flags]
public enum ZpmSkinFlags : uint
{
    None = 0,
    VertexGroups = 1,
    BoneEnvelopes = 2,
    PreserveVolume = 4,
}
