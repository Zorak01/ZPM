// SPDX-License-Identifier: GPL-3.0-or-later
using System.Runtime.CompilerServices;

namespace Zpm;

/// <summary>
/// One named color layer, got with ZpmMeshView.TryGetColorLayer. Holds its streams, so reading
/// a color costs no lookup. RGBA8 packs into one stream; RGBA32F takes two.
/// </summary>
public unsafe struct ZpmColorLayer
{
    const int ByteBits = 8;
    const uint ByteMask = 255;
    const float ByteMax = 255f;
    const float FloatScale = 65536f;
    const uint CornerFlag = 1;
    const uint FloatFlag = 2;

    ZpmCornerStream _redGreen, _blueAlpha;
    bool _isCorner, _isFloat;

    /// <summary>Whether this layer holds a color per corner, not per vertex.</summary>
    public readonly bool isCorner => _isCorner;

    /// <summary>Whether this layer holds float channels, not bytes.</summary>
    public readonly bool isFloat => _isFloat;

    /// <summary>Whether its streams are present for its format.</summary>
    public readonly bool isComplete => _redGreen.isBound && (!_isFloat || _blueAlpha.isBound);

    /// <summary>Sets this layer's format, from the file's format table.</summary>
    public void SetFormat(uint format)
    {
        _isCorner = (format & CornerFlag) != 0;
        _isFloat = (format & FloatFlag) != 0;
    }

    /// <summary>Points at one section of its first or second stream.</summary>
    public bool TryBindRole(bool isSecondStream, int role, byte* section, ZpmSectionFlags flags, uint cornerCount) =>
        isSecondStream
            ? _blueAlpha.TryBindRole(role, section, flags, cornerCount)
            : _redGreen.TryBindRole(role, section, flags, cornerCount);

    /// <summary>Returns a color. Pass the corner and its vertex; a vertex layer uses only the vertex.</summary>
    [MethodImpl(MethodImplOptions.AggressiveInlining)]
    public readonly ZpmColor Get(uint corner, uint vertex)
    {
        _redGreen.GetPair(corner, vertex, out uint first, out uint second);
        if (!_isFloat)
        {
            return new ZpmColor(
                (first & ByteMask) / ByteMax, ((first >> ByteBits) & ByteMask) / ByteMax,
                (second & ByteMask) / ByteMax, ((second >> ByteBits) & ByteMask) / ByteMax);
        }
        _blueAlpha.GetPair(corner, vertex, out uint blue, out uint alpha);
        return new ZpmColor((int)first / FloatScale, (int)second / FloatScale, (int)blue / FloatScale, (int)alpha / FloatScale);
    }

    /// <summary>Checks the layer's streams fit the file and stay in range.</summary>
    public readonly bool Validate(byte* limit, uint cornerCount, uint vertexCount) =>
        isComplete && _redGreen.Validate(limit, cornerCount, vertexCount)
        && (!_isFloat || _blueAlpha.Validate(limit, cornerCount, vertexCount));
}
