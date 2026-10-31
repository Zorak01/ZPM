// SPDX-License-Identifier: GPL-3.0-or-later
using System.Runtime.InteropServices;
using System.Text.Json;

namespace Zpm.Benchmarks;

/// <summary>
/// verify FIXTURE FILE         compares every read path of a .zpm file with its source mesh
/// measure FILE                times reads of a .zpm file
/// measure-view PACKET         times the same reads of a RAW32 or COMPACT16 .zmc packet
/// fuzz FILE SEED COUNT        flips bytes and proves validated files never read out of range
/// </summary>
static unsafe class Program
{
    const int MaxFlips = 8;
    const uint MaxFuzzCount = 1 << 24;

    static int Main(string[] arguments)
    {
        switch (arguments[0])
        {
            case "verify":
                Verify(FixtureMesh.Load(arguments[1]), File.ReadAllBytes(arguments[2]));
                return 0;
            case "measure":
                return Measure(arguments[1]);
            case "measure-view":
                return MeasureView(arguments[1]);
            case "fuzz":
                return Fuzz(arguments[1], int.Parse(arguments[2]), int.Parse(arguments[3]));
            default:
                Console.Error.WriteLine("Unknown mode");
                return 2;
        }
    }

    /// <summary>Compares every read path to the source. Any difference ends the run with an error.</summary>
    static void Verify(FixtureMesh source, byte[] file)
    {
        fixed (byte* data = file)
        {
            if (!ZpmMeshView.TryOpen(data, file.Length, out ZpmMeshView view) || !view.Validate())
                throw new InvalidDataException("File failed validation");
            Require(view.vertexCount == source.vertexCount && view.faceCount == source.faceCount
                && view.cornerCount == source.cornerCount && view.edgeCount == source.edgeCount, "counts");
            var positions = new uint[source.vertexCount * 3 + 1];
            var corners = new uint[source.cornerCount + 1];
            var edges = new uint[source.edgeCount * 2 + 1];
            var sizes = new uint[source.faceCount + 1];
            fixed (uint* p = positions, c = corners, e = edges, s = sizes)
            {
                view.GetMesh(p, s, c, e);
                Require(positions.AsSpan(0, source.positions.Length).SequenceEqual(source.positions), "mesh positions");
                Require(edges.AsSpan(0, source.edges.Length).SequenceEqual(source.edges), "mesh edges");
                view.GetVertices(0, view.vertexCount, p);
                view.GetCorners(0, view.cornerCount, c);
                view.GetEdges(0, view.edgeCount, e);
                view.GetFaceSizes(0, view.faceCount, s);
                Require(positions.AsSpan(0, source.positions.Length).SequenceEqual(source.positions), "bulk positions");
                Require(corners.AsSpan(0, source.corners.Length).SequenceEqual(source.corners), "bulk corners");
                Require(edges.AsSpan(0, source.edges.Length).SequenceEqual(source.edges), "bulk edges");
                Require(sizes.AsSpan(0, source.faceSizes.Length).SequenceEqual(source.faceSizes), "bulk face sizes");

                for (uint v = 0; v < source.vertexCount; v++)
                {
                    ZpmVertex vertex = view.GetVertex(v);
                    Require(vertex.xBits == source.positions[v * 3] && vertex.yBits == source.positions[v * 3 + 1] && vertex.zBits == source.positions[v * 3 + 2], "vertex");
                }
                for (uint i = 0; i < source.cornerCount; i++)
                    Require(view.GetCorner(i) == source.corners[i], "corner");
                for (uint i = 0; i < source.edgeCount; i++)
                {
                    ZpmEdge edge = view.GetEdge(i);
                    Require(edge.a == source.edges[i * 2] && edge.b == source.edges[i * 2 + 1], "edge");
                }
                uint at = 0;
                for (uint f = 0; f < source.faceCount; f++)
                {
                    uint size = view.GetFace(f, c);
                    Require(size == source.faceSizes[f] && view.GetFaceStart(f) == at && view.GetFaceSize(f) == size, "face size");
                    for (uint j = 0; j < size; j++)
                        Require(c[j] == source.corners[at + j], "face corner");
                    at += size;
                }
                // A range that starts and ends mid-block, the case a whole-file read never hits.
                if (source.vertexCount > 100)
                {
                    view.GetVertices(37, 61, p);
                    Require(positions.AsSpan(0, 61 * 3).SequenceEqual(source.positions.AsSpan(37 * 3, 61 * 3)), "range positions");
                }
                if (source.cornerCount > 100)
                {
                    view.GetCorners(37, 61, c);
                    Require(corners.AsSpan(0, 61).SequenceEqual(source.corners.AsSpan(37, 61)), "range corners");
                }
            }
        }
    }

    static void Require(bool condition, string what)
    {
        if (!condition)
            throw new InvalidDataException("Round trip differs: " + what);
    }

    static byte* LoadNative(string path, out long length)
    {
        byte[] bytes = File.ReadAllBytes(path);
        length = bytes.Length;
        byte* data = (byte*)NativeMemory.AlignedAlloc((nuint)Math.Max(length, 1), 64);
        Marshal.Copy(bytes, 0, (IntPtr)data, bytes.Length);
        return data;
    }

    static int Measure(string path)
    {
        byte* data = LoadNative(path, out long length);
        if (!ZpmMeshView.TryOpen(data, length, out ZpmMeshView view) || !view.Validate())
            return 3;
        new ReadBench<ZpmReader>(new ZpmReader(view), () =>
        {
            ZpmMeshView.TryOpen(data, length, out ZpmMeshView opened);
            return opened.vertexCount;
        }).Run();
        return 0;
    }

    static int MeasureView(string path)
    {
        byte* data = LoadNative(path, out long length);
        if (!Compact16View.TryOpen(data, length, out Compact16View view))
            return 3;
        new ReadBench<Compact16View>(view, () =>
        {
            Compact16View.TryOpen(data, length, out Compact16View opened);
            return opened.vertexCount;
        }).Run();
        return 0;
    }

    static int Fuzz(string path, int seed, int count)
    {
        byte[] original = File.ReadAllBytes(path);
        var random = new Random(seed);
        int accepted = 0;
        int rejected = 0;
        uint* output = (uint*)NativeMemory.AllocZeroed(MaxFuzzCount * 4 * 3);
        fixed (byte* data = original)
        {
            for (int trial = 0; trial < count; trial++)
            {
                int flips = random.Next(1, MaxFlips + 1);
                var saved = new (int at, byte value)[flips];
                for (int i = 0; i < flips; i++)
                {
                    int at = random.Next(original.Length);
                    saved[i] = (at, data[at]);
                    data[at] = (byte)random.Next(256);
                }
                if (ZpmMeshView.TryOpen(data, original.Length, out ZpmMeshView view) && view.Validate()
                    && view.vertexCount < MaxFuzzCount && view.cornerCount < MaxFuzzCount && view.edgeCount < MaxFuzzCount)
                {
                    accepted++;
                    ReadEverything(view, output);
                }
                else
                    rejected++;
                for (int i = flips - 1; i >= 0; i--)
                    data[saved[i].at] = saved[i].value;
            }
        }
        Console.WriteLine(JsonSerializer.Serialize(new { trials = count, accepted, rejected }));
        return 0;
    }

    static void ReadEverything(in ZpmMeshView view, uint* output)
    {
        view.GetVertices(0, view.vertexCount, output);
        view.GetCorners(0, view.cornerCount, output);
        view.GetEdges(0, view.edgeCount, output);
        view.GetFaceSizes(0, view.faceCount, output);
        view.GetMesh(output, output + MaxFuzzCount, output + MaxFuzzCount, output + MaxFuzzCount);
        for (uint i = 0; i < view.cornerCount; i++)
        {
            if (view.GetCorner(i) >= view.vertexCount)
                throw new InvalidDataException("Validated file read a bad vertex ID");
        }
        for (uint i = 0; i < view.edgeCount; i++)
        {
            ZpmEdge edge = view.GetEdge(i);
            if (edge.a >= view.vertexCount || edge.b >= view.vertexCount)
                throw new InvalidDataException("Validated file read a bad edge ID");
        }
    }
}
