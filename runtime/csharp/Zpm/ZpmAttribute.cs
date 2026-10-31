// SPDX-License-Identifier: GPL-3.0-or-later
using System;
using System.Runtime.CompilerServices;

namespace Zpm;

/// <summary>
/// One custom attribute, got with ZpmMeshView.TryGetAttribute. Holds its column, so reading a value
/// costs no lookup. Values are exact. Component order is all of component 0, then all of component 1.
/// </summary>
public unsafe struct ZpmAttribute
{
    const int TypeShift = 8;
    const int ComponentsShift = 16;
    const int MeaningShift = 24;
    const uint FieldMask = 255;

    ZpmColumn _values;
    uint _elementCount;
    uint _components;
    ZpmAttributeDomain _domain;
    ZpmAttributeType _type;
    ZpmAttributeMeaning _meaning;

    /// <summary>What this attribute holds one element for.</summary>
    public readonly ZpmAttributeDomain domain => _domain;

    /// <summary>How one component is stored.</summary>
    public readonly ZpmAttributeType type => _type;

    /// <summary>What the components mean together.</summary>
    public readonly ZpmAttributeMeaning meaning => _meaning;

    /// <summary>How many values each element has.</summary>
    public readonly uint componentCount => _components;

    /// <summary>How many elements it holds.</summary>
    public readonly uint elementCount => _elementCount;

    /// <summary>Reads the domain from a format word from the file's format table.</summary>
    public static ZpmAttributeDomain GetDomain(uint format) => (ZpmAttributeDomain)(format & FieldMask);

    /// <summary>Points at an attribute's column. Touches only its count.</summary>
    public static ZpmAttribute Bind(uint format, byte* section, ZpmSectionFlags flags, uint elementCount) => new()
    {
        _values = ZpmColumn.Bind(section, flags),
        _elementCount = elementCount,
        _components = (format >> ComponentsShift) & FieldMask,
        _domain = GetDomain(format),
        _type = (ZpmAttributeType)((format >> TypeShift) & FieldMask),
        _meaning = (ZpmAttributeMeaning)((format >> MeaningShift) & FieldMask),
    };

    /// <summary>Returns one integer component. For signed types, cast to int.</summary>
    [MethodImpl(MethodImplOptions.AggressiveInlining)]
    public readonly uint GetUInt(uint element, uint component) => _values.Get(component * _elementCount + element);

    /// <summary>Returns one signed integer component.</summary>
    [MethodImpl(MethodImplOptions.AggressiveInlining)]
    public readonly int GetInt(uint element, uint component) => (int)GetUInt(element, component);

    /// <summary>Returns one Float32 component with its exact bits. The type must be Float32.</summary>
    [MethodImpl(MethodImplOptions.AggressiveInlining)]
    public readonly float GetFloat(uint element, uint component) =>
        BitConverter.UInt32BitsToSingle(ZpmAxis.OrderedToBits(GetUInt(element, component)));

    /// <summary>Checks the format is known, the column fits the file and values fit their type.</summary>
    public readonly bool Validate(byte* limit)
    {
        if (_components == 0 || _meaning > ZpmAttributeMeaning.Matrix || _type > ZpmAttributeType.Float32
            || _domain > ZpmAttributeDomain.Mesh || _values.count != (ulong)_elementCount * _components
            || !_values.ValidateLayout(limit))
            return false;
        return _type switch
        {
            ZpmAttributeType.Bool => _values.ValidateBelow(2),
            ZpmAttributeType.UInt8 => _values.ValidateBelow(1UL << 8),
            ZpmAttributeType.UInt16 => _values.ValidateBelow(1UL << 16),
            _ => true,
        };
    }
}
