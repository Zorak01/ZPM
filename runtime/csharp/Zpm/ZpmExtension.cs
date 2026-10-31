// SPDX-License-Identifier: GPL-3.0-or-later
using System.Runtime.InteropServices;

namespace Zpm;

/// <summary>One named, versioned payload.</summary>
[StructLayout(LayoutKind.Sequential, Pack = 4)]
public readonly struct ZpmExtension
{
    /// <summary>Payload version.</summary>
    public readonly uint version;

    /// <summary>Bit 0: readers lacking it must refuse the file.</summary>
    public readonly uint flags;

    /// <summary>Where its bytes start.</summary>
    public readonly uint start;

    /// <summary>How many bytes.</summary>
    public readonly uint length;
}
