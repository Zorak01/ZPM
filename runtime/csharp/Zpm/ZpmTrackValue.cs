// SPDX-License-Identifier: GPL-3.0-or-later
namespace Zpm;

/// <summary>What a track's keys hold.</summary>
public enum ZpmTrackValue : uint
{
    Float = 0,
    Int = 1,
    Bool = 2,
    String = 3,
    Reference = 4,
}
