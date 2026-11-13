// SPDX-License-Identifier: GPL-3.0-or-later
using System.Runtime.InteropServices;

namespace Zpm;

/// <summary>One image file: a UDIM tile, or a plain image's only file.</summary>
[StructLayout(LayoutKind.Sequential, Pack = 4)]
public readonly struct ZpmImageTile
{
    /// <summary>UDIM number, or 0.</summary>
    public readonly uint number;

    /// <summary>Its embedded bytes, or ZpmLayout.NoIndex.</summary>
    public readonly uint payload;
}
