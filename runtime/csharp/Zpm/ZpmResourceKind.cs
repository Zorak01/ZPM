// SPDX-License-Identifier: GPL-3.0-or-later
namespace Zpm;

/// <summary>What a value tree or animation track belongs to.</summary>
public enum ZpmResourceKind : uint
{
    Scene = 0,
    Object = 1,
    Mesh = 2,
    Material = 3,
    Image = 4,
    Light = 5,
    Armature = 6,
    Bone = 7,
    Collection = 8,
    Curve = 9,
    Lattice = 10,
    Camera = 11,
}
