// SPDX-License-Identifier: GPL-3.0-or-later
using System.Diagnostics;
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
    const uint MaxFuzzWeights = 1 << 22;

    static int Main(string[] arguments)
    {
        switch (arguments[0])
        {
            case "verify":
                byte[] file = File.ReadAllBytes(arguments[2]);
                Verify(FixtureMesh.Load(arguments[1]), file);
                if (arguments.Length > 3)
                    VerifySurface(arguments[3], file);
                return 0;
            case "measure":
                return Measure(arguments[1]);
            case "measure-view":
                return MeasureView(arguments[1]);
            case "measure-surface":
                return MeasureSurface(arguments[1]);
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

    /// <summary>Compares normals, UVs, flags and creases with what the Python decoder read.</summary>
    static void VerifySurface(string surfacePath, byte[] file)
    {
        const float Tolerance = 1e-5f;
        using var reader = new BinaryReader(File.OpenRead(surfacePath));
        fixed (byte* data = file)
        {
            if (!ZpmMeshView.TryOpen(data, file.Length, out ZpmMeshView view) || !view.Validate())
                throw new InvalidDataException("File failed validation");
            Require((reader.ReadUInt32() != 0) == view.hasNormals, "has normals");
            for (uint c = 0; view.hasNormals && c < view.cornerCount; c++)
            {
                ZpmNormal normal = view.GetCornerNormal(c);
                Require(MathF.Abs(normal.x - reader.ReadSingle()) < Tolerance && MathF.Abs(normal.y - reader.ReadSingle()) < Tolerance
                    && MathF.Abs(normal.z - reader.ReadSingle()) < Tolerance, "normal");
            }
            uint layers = reader.ReadUInt32();
            Require(layers == view.uvLayerCount, "UV layer count");
            for (uint layer = 0; layer < layers; layer++)
            {
                Require(view.TryGetUvLayer(layer, out ZpmCornerStream stream), "UV layer");
                for (uint c = 0; c < view.cornerCount; c++)
                {
                    ZpmUv uv = view.GetCornerUv(stream, c);
                    Require(MathF.Abs(uv.u - reader.ReadSingle()) < Tolerance && MathF.Abs(uv.v - reader.ReadSingle()) < Tolerance, "UV");
                }
            }
            for (uint e = 0; e < view.edgeCount; e++)
            {
                Require(view.IsEdgeSharp(e) == (reader.ReadByte() != 0), "sharp edge");
                Require(view.IsEdgeSeam(e) == (reader.ReadByte() != 0), "seam edge");
                Require(MathF.Abs(view.GetEdgeCrease(e) - reader.ReadSingle()) < Tolerance, "edge crease");
            }
            VerifyBulkStreams(view);
            for (uint f = 0; f < view.faceCount; f++)
                Require(view.IsFaceFlat(f) == (reader.ReadByte() != 0), "flat face");
            for (uint v = 0; v < view.vertexCount; v++)
                Require(MathF.Abs(view.GetVertexCrease(v) - reader.ReadSingle()) < Tolerance, "vertex crease");
            VerifyTangentsColorsWeights(view, reader);
        }
    }

    static void VerifyTangentsColorsWeights(in ZpmMeshView view, BinaryReader reader)
    {
        const float Tolerance = 1e-5f;
        Require((reader.ReadUInt32() != 0) == view.hasTangents, "has tangents");
        for (uint c = 0; view.hasTangents && c < view.cornerCount; c++)
        {
            ZpmTangent tangent = view.GetCornerTangent(c);
            Require(MathF.Abs(tangent.x - reader.ReadSingle()) < Tolerance && MathF.Abs(tangent.y - reader.ReadSingle()) < Tolerance
                && MathF.Abs(tangent.z - reader.ReadSingle()) < Tolerance && tangent.w == reader.ReadSingle(), "tangent");
        }
        uint layers = reader.ReadUInt32();
        Require(layers == view.colorLayerCount, "color layer count");
        for (uint layer = 0; layer < layers; layer++)
        {
            Require(view.TryGetColorLayer(layer, out ZpmColorLayer colors), "color layer");
            uint owners = colors.isCorner ? view.cornerCount : view.vertexCount;
            for (uint i = 0; i < owners; i++)
            {
                ZpmColor color = colors.isCorner ? view.GetCornerColor(colors, i) : view.GetVertexColor(colors, i);
                Require(MathF.Abs(color.r - reader.ReadSingle()) < Tolerance && MathF.Abs(color.g - reader.ReadSingle()) < Tolerance
                    && MathF.Abs(color.b - reader.ReadSingle()) < Tolerance && MathF.Abs(color.a - reader.ReadSingle()) < Tolerance, "color");
            }
        }
        Require(reader.ReadUInt32() == view.vertexGroupCount, "vertex group count");
        var groups = new uint[view.vertexGroupCount * 4 + 64];
        var weights = new float[view.vertexGroupCount * 4 + 64];
        fixed (uint* g = groups)
        fixed (float* w = weights)
        {
            for (uint v = 0; v < view.vertexCount; v++)
            {
                uint count = view.GetVertexWeights(v, g, w);
                Require(count == reader.ReadUInt32() && count == view.GetVertexWeightCount(v), "weight count");
                for (uint i = 0; i < count; i++)
                    Require(g[i] == reader.ReadUInt32() && MathF.Abs(w[i] - reader.ReadSingle()) < Tolerance, "weight");
            }
        }
    }

    /// <summary>The whole-mesh pair copy must match reading each corner on its own.</summary>
    static void VerifyBulkStreams(in ZpmMeshView view)
    {
        var corners = new uint[view.cornerCount + 1];
        var pairs = new uint[view.cornerCount * 2 + 2];
        fixed (uint* c = corners, p = pairs)
        {
            view.GetCorners(0, view.cornerCount, c);
            if (view.TryGetNormalStream(out ZpmCornerStream normals))
                CheckPairs(normals, c, p, view.cornerCount);
            for (uint layer = 0; layer < view.uvLayerCount; layer++)
            {
                if (view.TryGetUvLayer(layer, out ZpmCornerStream uvs))
                    CheckPairs(uvs, c, p, view.cornerCount);
            }
        }
    }

    static void CheckPairs(in ZpmCornerStream stream, uint* corners, uint* pairs, uint cornerCount)
    {
        stream.CopyAllPairs(corners, cornerCount, pairs);
        for (uint corner = 0; corner < cornerCount; corner++)
        {
            stream.GetPair(corner, corners[corner], out uint a, out uint b);
            Require(pairs[corner * 2] == a && pairs[corner * 2 + 1] == b, "bulk corner pairs");
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

    /// <summary>Times reading a whole mesh with its normals and first UV layer, as other formats must.</summary>
    static int MeasureSurface(string path)
    {
        const int Rounds = 9;
        const int RunsPerRound = 25;
        byte* data = LoadNative(path, out long length);
        if (!ZpmMeshView.TryOpen(data, length, out ZpmMeshView view) || !view.Validate())
            return 3;
        bool hasUvs = view.TryGetUvLayer(0, out ZpmCornerStream uvs);
        uint corners = view.cornerCount;
        uint* positions = (uint*)NativeMemory.AlignedAlloc(view.vertexCount * 12 + 64, 64);
        uint* sizes = (uint*)NativeMemory.AlignedAlloc(view.faceCount * 4 + 64, 64);
        uint* cornerIds = (uint*)NativeMemory.AlignedAlloc(corners * 4 + 64, 64);
        uint* edges = (uint*)NativeMemory.AlignedAlloc(view.edgeCount * 8 + 64, 64);
        float* normals = (float*)NativeMemory.AlignedAlloc(corners * 12 + 64, 64);
        float* uvValues = (float*)NativeMemory.AlignedAlloc(corners * 8 + 64, 64);
        uint* normalValues = (uint*)NativeMemory.AlignedAlloc(corners * 8 + 64, 64);
        uint* uvStored = (uint*)NativeMemory.AlignedAlloc(corners * 8 + 64, 64);
        bool hasNormals = view.TryGetNormalStream(out ZpmCornerStream normalStream);
        var storedTimes = new double[Rounds];
        for (int round = 0; round < Rounds; round++)
        {
            long start = Stopwatch.GetTimestamp();
            for (int run = 0; run < RunsPerRound; run++)
            {
                view.GetMesh(positions, sizes, cornerIds, edges);
                if (hasNormals)
                    normalStream.CopyAllPairs(cornerIds, corners, normalValues);
                if (hasUvs)
                    uvs.CopyAllPairs(cornerIds, corners, uvStored);
            }
            storedTimes[round] = (Stopwatch.GetTimestamp() - start) * 1e6 / Stopwatch.Frequency / RunsPerRound;
        }
        Array.Sort(storedTimes);
        var times = new double[Rounds];
        long allocated = 0;
        for (int round = -1; round < Rounds; round++)
        {
            long before = GC.GetAllocatedBytesForCurrentThread();
            long start = Stopwatch.GetTimestamp();
            for (int run = 0; run < RunsPerRound; run++)
            {
                view.GetMesh(positions, sizes, cornerIds, edges);
                for (uint c = 0; c < corners; c++)
                {
                    ZpmNormal normal = view.GetCornerNormal(c);
                    normals[c * 3] = normal.x;
                    normals[c * 3 + 1] = normal.y;
                    normals[c * 3 + 2] = normal.z;
                }
                for (uint c = 0; hasUvs && c < corners; c++)
                {
                    ZpmUv uv = view.GetCornerUv(uvs, c);
                    uvValues[c * 2] = uv.u;
                    uvValues[c * 2 + 1] = uv.v;
                }
            }
            long stop = Stopwatch.GetTimestamp();
            if (round >= 0)
            {
                times[round] = (stop - start) * 1e6 / Stopwatch.Frequency / RunsPerRound;
                allocated = Math.Max(allocated, GC.GetAllocatedBytesForCurrentThread() - before);
            }
        }
        Array.Sort(times);
        Console.WriteLine(JsonSerializer.Serialize(new
        {
            zpm_full_read_us = times[Rounds / 2],
            zpm_full_read_stored_us = storedTimes[Rounds / 2],
            zpm_allocated_bytes = allocated,
        }));
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
            view.IsEdgeSharp(i);
            view.GetEdgeCrease(i);
        }
        for (uint c = 0; view.hasTangents && c < view.cornerCount; c++)
            view.GetCornerTangent(c);
        for (uint layer = 0; layer < view.colorLayerCount; layer++)
        {
            if (view.TryGetColorLayer(layer, out ZpmColorLayer colors))
            {
                for (uint c = 0; colors.isCorner && c < view.cornerCount; c++)
                    view.GetCornerColor(colors, c);
                for (uint v = 0; !colors.isCorner && v < view.vertexCount; v++)
                    view.GetVertexColor(colors, v);
            }
        }
        for (uint v = 0; v < view.vertexCount; v++)
        {
            uint weightCount = view.GetVertexWeightCount(v);
            if (weightCount > MaxFuzzWeights)
                throw new InvalidDataException("Fuzzed vertex has too many weights to test");
            view.GetVertexWeights(v, output, (float*)(output + MaxFuzzWeights));
        }
        for (uint c = 0; view.hasNormals && c < view.cornerCount; c++)
            view.GetCornerNormal(c);
        for (uint layer = 0; layer < view.uvLayerCount; layer++)
        {
            if (!view.TryGetUvLayer(layer, out ZpmCornerStream stream))
                continue;
            for (uint c = 0; c < view.cornerCount; c++)
                view.GetCornerUv(stream, c);
        }
    }
}
