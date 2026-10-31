// SPDX-License-Identifier: GPL-3.0-or-later
using System;

namespace Zpm;

/// <summary>How a bone behaves.</summary>
[Flags]
public enum ZpmBoneFlags : uint
{
    None = 0,
    Connected = 1,
    Deform = 2,
    InheritRotation = 4,
    InheritScale = 8,
}
