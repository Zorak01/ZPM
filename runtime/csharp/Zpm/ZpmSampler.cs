// SPDX-License-Identifier: GPL-3.0-or-later
using System.Runtime.InteropServices;

namespace Zpm;

/// <summary>How a texture is filtered and wrapped.</summary>
[StructLayout(LayoutKind.Sequential, Pack = 4)]
public readonly struct ZpmSampler
{
    /// <summary>Filter.</summary>
    public readonly ZpmSamplerFilter filter;

    /// <summary>Wrap along U.</summary>
    public readonly ZpmSamplerWrap wrapU;

    /// <summary>Wrap along V.</summary>
    public readonly ZpmSamplerWrap wrapV;
}
