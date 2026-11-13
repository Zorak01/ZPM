// SPDX-License-Identifier: GPL-3.0-or-later
using System.Runtime.InteropServices;

namespace Zpm;

/// <summary>One animated value.</summary>
[StructLayout(LayoutKind.Sequential, Pack = 4)]
public readonly struct ZpmTrack
{
    /// <summary>Kind of resource it animates.</summary>
    public readonly ZpmResourceKind ownerKind;

    /// <summary>Which one.</summary>
    public readonly uint owner;

    /// <summary>Field path, in the string table.</summary>
    public readonly uint path;

    /// <summary>Which element of an array field.</summary>
    public readonly uint arrayIndex;

    /// <summary>What keys hold.</summary>
    public readonly ZpmTrackValue valueType;

    /// <summary>What a reference key indexes.</summary>
    public readonly ZpmResourceKind targetKind;

    /// <summary>Values per key.</summary>
    public readonly uint components;

    /// <summary>Baked, or a spherical rotation.</summary>
    public readonly ZpmTrackFlags flags;

    /// <summary>Its shared list of key times.</summary>
    public readonly uint timeSet;

    /// <summary>How many keys.</summary>
    public readonly uint keyCount;

    /// <summary>Where its values start.</summary>
    public readonly uint firstValue;

    /// <summary>Where its key modes start, or ZpmLayout.NoIndex when all are linear.</summary>
    public readonly uint firstMode;

    /// <summary>Where its handles start, or ZpmLayout.NoIndex.</summary>
    public readonly uint firstHandle;
}
