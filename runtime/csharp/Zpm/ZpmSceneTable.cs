// SPDX-License-Identifier: GPL-3.0-or-later
namespace Zpm;

/// <summary>A run of fixed-size records in file bytes. Reading one is a pointer add.</summary>
public unsafe struct ZpmSceneTable<T> where T : unmanaged
{
    T* _records;
    uint _count;

    /// <summary>How many records it holds.</summary>
    public readonly uint count => _count;

    /// <summary>Points at a section, which must exactly hold whole records. Returns false otherwise.</summary>
    public bool TryBind(byte* section, long length)
    {
        if (length % sizeof(T) != 0 || length / sizeof(T) > uint.MaxValue)
            return false;
        _records = (T*)section;
        _count = (uint)(length / sizeof(T));
        return true;
    }

    /// <summary>Returns one record, in place. The index must be below count; nothing checks it here.</summary>
    public readonly ref readonly T this[uint index] => ref _records[index];
}
