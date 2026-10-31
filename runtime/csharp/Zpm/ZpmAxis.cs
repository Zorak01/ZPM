// SPDX-License-Identifier: GPL-3.0-or-later
using System.Runtime.CompilerServices;

namespace Zpm;

/// <summary>One position axis: stored bits, a palette with indices, or one constant value.</summary>
public unsafe struct ZpmAxis
{
    enum Storage : byte { Missing, Bits, Palette, SignedPalette, Constant }

    const uint SignBit = 0x80000000u;
    const int SignShift = 31;
    const int Stride = 3;

    Storage _storage;
    uint _constant;
    ZpmColumn _values;
    ZpmColumn _indices;

    /// <summary>Returns false until a section has provided this axis.</summary>
    public readonly bool isBound => _storage != Storage.Missing;

    public void BindBits(byte* section, ZpmSectionFlags flags)
    {
        _storage = Storage.Bits;
        _values = ZpmColumn.Bind(section, flags);
    }

    public void BindConstant(byte* section)
    {
        _storage = Storage.Constant;
        _constant = Unsafe.ReadUnaligned<uint>(section);
    }

    public void BindPalette(byte* section, ZpmSectionFlags flags)
    {
        _values = ZpmColumn.Bind(section, flags);
    }

    public void BindIndices(byte* section, ZpmSectionFlags flags)
    {
        _storage = (flags & ZpmSectionFlags.SignedIndices) != 0 ? Storage.SignedPalette : Storage.Palette;
        _indices = ZpmColumn.Bind(section, flags);
    }

    [MethodImpl(MethodImplOptions.AggressiveInlining)]
    static uint OrderedToBits(uint ordered) => ordered ^ ((uint)((int)~ordered >> SignShift) | SignBit);

    /// <summary>Returns one value's exact Float32 bits.</summary>
    [MethodImpl(MethodImplOptions.AggressiveInlining)]
    public readonly uint GetBits(uint index)
    {
        switch (_storage)
        {
            case Storage.Bits:
                return OrderedToBits(_values.Get(index));
            case Storage.Palette:
                return _values.Get(_indices.Get(index));
            case Storage.SignedPalette:
                uint signed = _indices.Get(index);
                return _values.Get(signed >> 1) | (signed << SignShift);
            default:
                return _constant;
        }
    }

    /// <summary>Writes a range of this axis to the caller's buffer, every three elements.</summary>
    public readonly void CopyTo(uint first, uint length, uint* destination)
    {
        switch (_storage)
        {
            case Storage.Bits:
                _values.CopyTo(first, length, destination, Stride);
                for (uint i = 0; i < length; i++, destination += Stride)
                    *destination = OrderedToBits(*destination);
                break;
            case Storage.Palette:
                _indices.CopyTo(first, length, destination, Stride);
                for (uint i = 0; i < length; i++, destination += Stride)
                    *destination = _values.Get(*destination);
                break;
            case Storage.SignedPalette:
                _indices.CopyTo(first, length, destination, Stride);
                for (uint i = 0; i < length; i++, destination += Stride)
                {
                    uint signed = *destination;
                    *destination = _values.Get(signed >> 1) | (signed << SignShift);
                }
                break;
            default:
                for (uint i = 0; i < length; i++, destination += Stride)
                    *destination = _constant;
                break;
        }
    }

    /// <summary>Checks this axis fits the file and holds count values with valid indices.</summary>
    public readonly bool Validate(byte* limit, uint count)
    {
        switch (_storage)
        {
            case Storage.Bits:
                return _values.count == count && _values.ValidateLayout(limit);
            case Storage.Palette:
            case Storage.SignedPalette:
                ulong bound = _storage == Storage.Palette ? _values.count : (ulong)_values.count * 2;
                return _indices.count == count && _values.ValidateLayout(limit)
                    && _indices.ValidateLayout(limit) && _indices.ValidateBelow(bound);
            default:
                return _storage == Storage.Constant;
        }
    }
}
