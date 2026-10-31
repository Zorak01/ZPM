// SPDX-License-Identifier: GPL-3.0-or-later
using System;

namespace Zpm;

/// <summary>
/// Typed, named data on any resource, read in place. A dictionary or list points at children always
/// later in the table, so walking cannot loop. Holds no memory of its own.
/// </summary>
public unsafe struct ZpmValueTree
{
    ZpmSceneTable<ZpmValueNode> _nodes;
    ZpmSceneTable<ZpmValueOwner> _owners;
    ZpmColumn _pool;
    ZpmNames _strings;
    bool _hasPool;

    /// <summary>How many nodes the file holds.</summary>
    public readonly uint nodeCount => _nodes.count;

    /// <summary>The file's shared strings.</summary>
    public readonly ZpmNames strings => _strings;

    /// <summary>Points at one section, if it is a value kind. Returns whether it was, and sets isValid.</summary>
    public bool TryBindSection(ZpmSectionKind kind, ZpmSectionFlags flags, byte* section, long length, out bool isValid)
    {
        isValid = true;
        switch (kind)
        {
            case ZpmSectionKind.ValueStrings: _strings = ZpmNames.Bind(section); return true;
            case ZpmSectionKind.ValueNodes: isValid = _nodes.TryBind(section, length); return true;
            case ZpmSectionKind.ValueOwners: isValid = _owners.TryBind(section, length); return true;
            case ZpmSectionKind.ValuePool: _pool = ZpmColumn.Bind(section, flags); _hasPool = true; return true;
            default: return false;
        }
    }

    /// <summary>Finds the tree a resource carries, by binary search. Returns false when it has none.</summary>
    public readonly bool TryGetRoot(ZpmResourceKind kind, uint index, out ZpmValueNode root)
    {
        root = default;
        uint low = 0, high = _owners.count;
        while (low < high)
        {
            uint middle = low + (high - low) / 2;
            ref readonly ZpmValueOwner owner = ref _owners[middle];
            if (owner.kind == kind && owner.index == index)
            {
                root = _nodes[owner.node];
                return true;
            }
            if (owner.kind < kind || (owner.kind == kind && owner.index < index))
                low = middle + 1;
            else
                high = middle;
        }
        return false;
    }

    /// <summary>Returns one child of a dictionary or list.</summary>
    public readonly ref readonly ZpmValueNode GetChild(in ZpmValueNode parent, uint child) => ref _nodes[parent.a + child];

    /// <summary>Finds a dictionary's child by its UTF-8 name, walking its children. Returns false when absent.</summary>
    public readonly bool TryFindChild(in ZpmValueNode parent, ReadOnlySpan<byte> utf8Name, out ZpmValueNode child)
    {
        child = default;
        for (uint i = 0; i < parent.b; i++)
        {
            ref readonly ZpmValueNode candidate = ref _nodes[parent.a + i];
            if (_strings.TryGet(candidate.key, out byte* text, out uint length)
                && new ReadOnlySpan<byte>(text, (int)length).SequenceEqual(utf8Name))
            {
                child = candidate;
                return true;
            }
        }
        return false;
    }

    /// <summary>Returns a Float node's value, or one value of a FloatArray node.</summary>
    public readonly float GetFloat(in ZpmValueNode node, uint element = 0) => node.type == ZpmValueType.Float
        ? BitConverter.UInt32BitsToSingle(node.a)
        : BitConverter.UInt32BitsToSingle(ZpmAxis.OrderedToBits(_pool.Get(node.a + element)));

    /// <summary>Returns an Int or Bool node's value, or one value of an IntArray node.</summary>
    public readonly int GetInt(in ZpmValueNode node, uint element = 0) =>
        node.type == ZpmValueType.IntArray ? (int)_pool.Get(node.a + element) : (int)node.a;

    /// <summary>Finds a String node's UTF-8 text. Returns false for any other node.</summary>
    public readonly bool TryGetString(in ZpmValueNode node, out byte* utf8, out uint length)
    {
        utf8 = null;
        length = 0;
        return node.type == ZpmValueType.String && _strings.TryGet(node.a, out utf8, out length);
    }

    /// <summary>Walks every node: only that proves each stays in range and points forward.</summary>
    public readonly bool Validate(byte* limit, uint objectCount, in ZpmSceneView scene)
    {
        if (!_strings.Validate(limit) || (_hasPool && !_pool.ValidateLayout(limit)))
            return false;
        uint poolCount = _hasPool ? _pool.count : 0;
        for (uint i = 0; i < _nodes.count; i++)
        {
            ref readonly ZpmValueNode node = ref _nodes[i];
            if (node.key != ZpmLayout.NoIndex && node.key >= _strings.count)
                return false;
            bool isFine = node.type switch
            {
                ZpmValueType.Bool or ZpmValueType.Int or ZpmValueType.Float => true,
                ZpmValueType.String => node.a < _strings.count,
                ZpmValueType.Reference => node.a < objectCount,
                ZpmValueType.IntArray or ZpmValueType.FloatArray => (ulong)node.a + node.b <= poolCount,
                ZpmValueType.Dictionary or ZpmValueType.List => node.b == 0 || (node.a > i && (ulong)node.a + node.b <= _nodes.count),
                _ => false,
            };
            if (!isFine)
                return false;
        }
        for (uint i = 0; i < _owners.count; i++)
        {
            ref readonly ZpmValueOwner owner = ref _owners[i];
            if (owner.node >= _nodes.count || owner.kind > ZpmResourceKind.Camera || owner.index >= scene.GetResourceCount(owner.kind))
                return false;
            if (i != 0 && (_owners[i - 1].kind > owner.kind || (_owners[i - 1].kind == owner.kind && _owners[i - 1].index >= owner.index)))
                return false;
        }
        return true;
    }
}
