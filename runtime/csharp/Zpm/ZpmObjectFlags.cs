// SPDX-License-Identifier: GPL-3.0-or-later
using System;

namespace Zpm;

/// <summary>How an object is shown.</summary>
[Flags]
public enum ZpmObjectFlags : uint
{
    None = 0,
    HiddenInViewport = 1,
    HiddenInRender = 2,
    NotSelectable = 4,
}
