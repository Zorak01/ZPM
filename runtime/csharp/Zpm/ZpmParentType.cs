// SPDX-License-Identifier: GPL-3.0-or-later
namespace Zpm;

/// <summary>How an object follows its parent.</summary>
public enum ZpmParentType : uint
{
    Object = 0,
    Armature = 1,
    Lattice = 2,
    Vertex = 3,
    Vertex3 = 4,
    Bone = 5,
}
