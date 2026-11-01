// SPDX-License-Identifier: GPL-3.0-or-later
namespace Zpm;

/// <summary>How a bone takes its parent's scale.</summary>
public enum ZpmInheritScale : uint
{
    Full = 0,
    FixShear = 1,
    Aligned = 2,
    Average = 3,
    None = 4,
    NoneLegacy = 5,
}
