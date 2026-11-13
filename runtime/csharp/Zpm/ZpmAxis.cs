// SPDX-License-Identifier: GPL-3.0-or-later
using System;
using System.Runtime.CompilerServices;
using System.Runtime.Intrinsics;
using System.Runtime.Intrinsics.X86;

namespace Zpm;

/// <summary>One position axis: stored bits, a palette with indices, or one constant value.</summary>
public unsafe struct ZpmAxis
{
    enum Storage : byte { Missing, Bits, Grid, Palette, SignedPalette, Constant }

    const uint SignBit = 0x80000000u;
    const int SignShift = 31;
    const int Stride = 3;
    const int LowBit = 1;
    const int BatchValues = 64;
    const int BatchSlack = 8;
    const uint LargestStackPalette = 4096;
    const int WideValues = 8;

    Storage _storage;
    uint _constant;
    float _step;
    ZpmColumn _values;
    ZpmColumn _indices;

    /// <summary>Returns false until a section has provided this axis.</summary>
    public readonly bool isBound => _storage != Storage.Missing;

    public void BindBits(byte* section, ZpmSectionFlags flags)
    {
        _storage = Storage.Bits;
        _values = ZpmColumn.Bind(section, flags);
    }

    public void BindGrid(byte* section, ZpmSectionFlags flags, float step)
    {
        _storage = Storage.Grid;
        _step = step;
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

    public void BindIndices(byte* section, ZpmSectionFlags flags, float step)
    {
        _step = step;
        _storage = (flags & ZpmSectionFlags.SignedIndices) != 0 ? Storage.SignedPalette : Storage.Palette;
        _indices = ZpmColumn.Bind(section, flags);
    }

    [MethodImpl(MethodImplOptions.AggressiveInlining)]
    internal static uint OrderedToBits(uint ordered) => ordered ^ ((uint)((int)~ordered >> SignShift) | SignBit);

    [MethodImpl(MethodImplOptions.AggressiveInlining)]
    readonly uint GridToBits(int grid) => BitConverter.SingleToUInt32Bits(grid * _step);

    /// <summary>Returns one value's exact Float32 bits.</summary>
    [MethodImpl(MethodImplOptions.AggressiveInlining)]
    public readonly uint GetBits(uint index)
    {
        switch (_storage)
        {
            case Storage.Bits:
                return OrderedToBits(_values.Get(index));
            case Storage.Palette:
                return GridToBits((int)_values.Get(_indices.Get(index)));
            case Storage.SignedPalette:
                uint signed = _indices.Get(index);
                int size = (int)_values.Get(signed >> LowBit);
                return GridToBits((signed & LowBit) != 0 ? -size : size);
            case Storage.Grid:
                return GridToBits((int)_values.Get(index));
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
            case Storage.Palette when CanLoadPalette(length):
            case Storage.SignedPalette when CanLoadPalette(length):
                CopyThroughLoadedPalette(first, length, destination);
                break;
            case Storage.Palette:
                _indices.CopyTo(first, length, destination, Stride);
                for (uint i = 0; i < length; i++, destination += Stride)
                    *destination = GridToBits((int)_values.Get(*destination));
                break;
            case Storage.Grid:
                CopyGrid(first, length, destination);
                break;
            case Storage.SignedPalette:
                _indices.CopyTo(first, length, destination, Stride);
                for (uint i = 0; i < length; i++, destination += Stride)
                {
                    uint signed = *destination;
                    int size = (int)_values.Get(signed >> LowBit);
                    *destination = GridToBits((signed & LowBit) != 0 ? -size : size);
                }
                break;
            default:
                for (uint i = 0; i < length; i++, destination += Stride)
                    *destination = _constant;
                break;
        }
    }

    readonly bool CanLoadPalette(uint length) => length >= BatchValues && _values.count <= LargestStackPalette;

    /// <summary>Turns grid integers into float bits in place, eight per step where AVX2 exists.</summary>
    readonly void ConvertGridToBits(uint* values, uint length)
    {
        uint i = 0;
        if (Avx2.IsSupported)
        {
            Vector256<float> step = Vector256.Create(_step);
            for (; i + WideValues <= length; i += WideValues)
                (Avx.ConvertToVector256Single(Vector256.Load((int*)values + i)) * step).AsUInt32().Store(values + i);
        }
        for (; i < length; i++)
            values[i] = GridToBits((int)values[i]);
    }

    [SkipLocalsInit]
    readonly void CopyGrid(uint first, uint length, uint* destination)
    {
        uint* batch = stackalloc uint[BatchValues + BatchSlack];
        while (length != 0)
        {
            uint take = Math.Min(BatchValues - (first & (BatchValues - 1)), length);
            _values.CopyTo(first, take, batch, 1);
            ConvertGridToBits(batch, take);
            for (uint i = 0; i < take; i++, destination += Stride)
                *destination = batch[i];
            first += take;
            length -= take;
        }
    }

    /// <summary>Unpacks the palette once onto the stack, so each value is one array read, not a column read.</summary>
    [SkipLocalsInit]
    readonly void CopyThroughLoadedPalette(uint first, uint length, uint* destination)
    {
        uint* palette = stackalloc uint[(int)_values.count + BatchSlack];
        _values.CopyTo(0, _values.count, palette, 1);
        ConvertGridToBits(palette, _values.count);
        uint* batch = stackalloc uint[BatchValues + BatchSlack];
        bool isSigned = _storage == Storage.SignedPalette;
        while (length != 0)
        {
            uint take = Math.Min(BatchValues - (first & (BatchValues - 1)), length);
            _indices.CopyTo(first, take, batch, 1);
            if (isSigned)
            {
                for (uint i = 0; i < take; i++, destination += Stride)
                    *destination = palette[batch[i] >> LowBit] ^ (batch[i] << SignShift);
            }
            else
            {
                for (uint i = 0; i < take; i++, destination += Stride)
                    *destination = palette[batch[i]];
            }
            first += take;
            length -= take;
        }
    }

    /// <summary>Checks this axis fits the file and holds count values with valid indices.</summary>
    public readonly bool Validate(byte* limit, uint count)
    {
        switch (_storage)
        {
            case Storage.Bits:
            case Storage.Grid:
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
