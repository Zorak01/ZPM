// SPDX-License-Identifier: GPL-3.0-or-later
using System.Runtime.CompilerServices;

namespace Zpm.DirectRead;

/// <summary>
/// Baseline: reads the existing RAW32 and COMPACT16 ZMC1 packets in place, without decoding.
/// The packets store face sizes but no face offsets, so finding a mixed face walks from the start.
/// </summary>
public unsafe struct Compact16View : IZpmMeshReader
{
    const uint Magic = 0x31434D5A;
    const int HeaderBytes = 64;
    const int FieldBytes = 4;
    const int VertexField = 2, FaceField = 3, CornerField = 4, EdgeField = 5, ArityField = 10;
    const int PositionLengthField = 11, SizeLengthField = 12, CornerLengthField = 13, EdgeLengthField = 14, RawField = 15;
    const uint MaxShortVertices = 65536;

    uint _vertexCount, _faceCount, _cornerCount, _edgeCount, _arity;
    bool _wide;
    uint* _positions;
    uint* _sizes;
    byte* _corners;
    byte* _edges;

    public readonly uint vertexCount => _vertexCount;
    public readonly uint faceCount => _faceCount;
    public readonly uint cornerCount => _cornerCount;
    public readonly uint edgeCount => _edgeCount;

    static uint Field(byte* data, int index) => Unsafe.ReadUnaligned<uint>(data + index * FieldBytes);

    /// <summary>Points at the four streams. Returns false for packets that use an experimental encoding.</summary>
    public static bool TryOpen(byte* data, long length, out Compact16View view)
    {
        view = default;
        if (length < HeaderBytes || Field(data, 0) != Magic || Field(data, 6) != 0)
            return false;
        view._vertexCount = Field(data, VertexField);
        view._faceCount = Field(data, FaceField);
        view._cornerCount = Field(data, CornerField);
        view._edgeCount = Field(data, EdgeField);
        view._arity = Field(data, ArityField);
        view._wide = Field(data, RawField) == 1 || view._vertexCount > MaxShortVertices;
        byte* at = data + HeaderBytes;
        view._positions = (uint*)at;
        at += Field(data, PositionLengthField);
        view._sizes = (uint*)at;
        at += Field(data, SizeLengthField);
        view._corners = at;
        at += Field(data, CornerLengthField);
        view._edges = at;
        at += Field(data, EdgeLengthField);
        return at == data + length;
    }

    /// <summary>Reads every ID, because nothing in the packet bounds them without looking.</summary>
    public readonly bool Validate()
    {
        for (uint i = 0; i < _cornerCount; i++)
        {
            if (GetCorner(i) >= _vertexCount)
                return false;
        }
        for (uint i = 0; i < _edgeCount * 2; i++)
        {
            if (ReadId(_edges, i) >= _vertexCount)
                return false;
        }
        return true;
    }

    [MethodImpl(MethodImplOptions.AggressiveInlining)]
    readonly uint ReadId(byte* stream, uint index) => _wide
        ? Unsafe.ReadUnaligned<uint>(stream + index * 4)
        : Unsafe.ReadUnaligned<ushort>(stream + index * 2);

    public readonly ZpmVertex GetVertex(uint index) =>
        new(_positions[index * 3], _positions[index * 3 + 1], _positions[index * 3 + 2]);

    public readonly uint GetCorner(uint index) => ReadId(_corners, index);

    public readonly uint GetFace(uint face, uint* corners)
    {
        uint start;
        uint size;
        if (_arity != 0)
        {
            start = face * _arity;
            size = _arity;
        }
        else
        {
            start = 0;
            for (uint f = 0; f < face; f++)
                start += _sizes[f];
            size = _sizes[face];
        }
        for (uint i = 0; i < size; i++)
            corners[i] = ReadId(_corners, start + i);
        return size;
    }

    public readonly ZpmEdge GetEdge(uint index) => new(ReadId(_edges, index * 2), ReadId(_edges, index * 2 + 1));

    public readonly void GetVertices(uint first, uint length, uint* destination) =>
        Buffer.MemoryCopy(_positions + first * 3, destination, length * 12L, length * 12L);

    public readonly void GetCorners(uint first, uint length, uint* destination)
    {
        for (uint i = 0; i < length; i++)
            destination[i] = ReadId(_corners, first + i);
    }

    public readonly void GetEdges(uint first, uint length, uint* destination)
    {
        for (uint i = 0; i < length * 2; i++)
            destination[i] = ReadId(_edges, first * 2 + i);
    }

    public readonly void GetMesh(uint* positions, uint* faceSizes, uint* corners, uint* edges)
    {
        GetVertices(0, _vertexCount, positions);
        GetCorners(0, _cornerCount, corners);
        GetFaceSizes(0, _faceCount, faceSizes);
        GetEdges(0, _edgeCount, edges);
    }

    public readonly void GetFaceSizes(uint first, uint length, uint* destination)
    {
        for (uint i = 0; i < length; i++)
            destination[i] = _arity != 0 ? _arity : _sizes[first + i];
    }
}
