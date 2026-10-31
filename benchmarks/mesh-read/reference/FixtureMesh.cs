// SPDX-License-Identifier: GPL-3.0-or-later

namespace Zpm.Benchmarks;

/// <summary>Mesh arrays in source order, read from a ZMS1 fixture, to compare reads against.</summary>
sealed class FixtureMesh
{
    const uint Magic = 0x31534D5A;

    public uint vertexCount, faceCount, cornerCount, edgeCount;
    public uint[] positions, faceSizes, corners, edges;

    public static FixtureMesh Load(string path)
    {
        using var reader = new BinaryReader(File.OpenRead(path));
        if (reader.ReadUInt32() != Magic)
            throw new InvalidDataException("Not a ZMS1 fixture");
        var mesh = new FixtureMesh
        {
            vertexCount = reader.ReadUInt32(),
            faceCount = reader.ReadUInt32(),
            cornerCount = reader.ReadUInt32(),
            edgeCount = reader.ReadUInt32(),
        };
        mesh.positions = ReadArray(reader, mesh.vertexCount * 3);
        mesh.faceSizes = ReadArray(reader, mesh.faceCount);
        mesh.corners = ReadArray(reader, mesh.cornerCount);
        mesh.edges = ReadArray(reader, mesh.edgeCount * 2);
        return mesh;
    }

    static uint[] ReadArray(BinaryReader reader, uint count)
    {
        var values = new uint[count];
        for (uint i = 0; i < count; i++)
            values[i] = reader.ReadUInt32();
        return values;
    }
}
