// SPDX-License-Identifier: GPL-3.0-or-later
using System.Diagnostics;
using System.Runtime.InteropServices;
using System.Text.Json;

namespace Zpm.Benchmarks;

/// <summary>Runs the same timed reads on any reader. Generic over the struct so calls stay direct.</summary>
sealed unsafe class ReadBench<TReader> where TReader : struct, IMeshReader
{
    const int Rounds = 9;
    const int WarmupRuns = 25;
    const int RunsPerRound = 25;
    const int OpenRunsPerRound = 2000;
    const int RandomRunsPerRound = 2;
    const int SampleCount = 4096;
    const ulong Seed = 0x9E3779B97F4A7C15UL;

    TReader _reader;
    readonly Func<uint> _open;
    readonly uint* _positions;
    readonly uint* _corners;
    readonly uint* _edges;
    readonly uint* _sizes;
    readonly uint* _faceBuffer;
    readonly uint[] _vertexIds, _cornerIds, _faceIds, _edgeIds;
    ulong _sink;

    public ReadBench(TReader reader, Func<uint> open)
    {
        _reader = reader;
        _open = open;
        _positions = Alloc(reader.vertexCount * 3);
        _corners = Alloc(reader.cornerCount);
        _edges = Alloc(reader.edgeCount * 2);
        _sizes = Alloc(reader.faceCount);
        _faceBuffer = Alloc(Math.Max(reader.cornerCount, 16));
        _vertexIds = Samples(reader.vertexCount);
        _cornerIds = Samples(reader.cornerCount);
        _faceIds = Samples(reader.faceCount);
        _edgeIds = Samples(reader.edgeCount);
    }

    static uint* Alloc(uint count) => (uint*)NativeMemory.AlignedAlloc((nuint)(Math.Max(count, 1) * 4L + 64), 64);

    /// <summary>A fixed-seed list of indices, so every reader visits the same elements.</summary>
    static uint[] Samples(uint count)
    {
        if (count == 0)
            return Array.Empty<uint>();
        var ids = new uint[SampleCount];
        ulong state = Seed;
        for (int i = 0; i < SampleCount; i++)
        {
            state += Seed;
            ulong z = state;
            z = (z ^ (z >> 30)) * 0xBF58476D1CE4E5B9UL;
            z = (z ^ (z >> 27)) * 0x94D049BB133111EBUL;
            ids[i] = (uint)((z ^ (z >> 31)) % count);
        }
        return ids;
    }

    readonly record struct Timing(double medianNs, double minNs, double maxNs, long allocatedBytes);

    static Timing Measure(Action work, int runsPerRound, double operationsPerRun)
    {
        for (int i = 0; i < WarmupRuns; i++)
            work();
        var times = new double[Rounds];
        long allocated = 0;
        for (int round = 0; round < Rounds; round++)
        {
            long before = GC.GetAllocatedBytesForCurrentThread();
            long start = Stopwatch.GetTimestamp();
            for (int run = 0; run < runsPerRound; run++)
                work();
            long stop = Stopwatch.GetTimestamp();
            allocated = Math.Max(allocated, GC.GetAllocatedBytesForCurrentThread() - before);
            times[round] = (stop - start) * 1e9 / Stopwatch.Frequency / runsPerRound / operationsPerRun;
        }
        Array.Sort(times);
        return new Timing(times[Rounds / 2], times[0], times[Rounds - 1], allocated);
    }

    void OpenOnce() => _sink += _open();
    void ValidateOnce() => _sink += _reader.Validate() ? 1u : 0u;
    void ReadVerticesOnce() => _reader.GetVertices(0, _reader.vertexCount, _positions);
    void ReadCornersOnce() => _reader.GetCorners(0, _reader.cornerCount, _corners);
    void ReadEdgesOnce() => _reader.GetEdges(0, _reader.edgeCount, _edges);
    void ReadMeshOnce() => _reader.GetMesh(_positions, _sizes, _corners, _edges);

    void RandomVertices()
    {
        ulong sum = 0;
        foreach (uint id in _vertexIds)
            sum += _reader.GetVertex(id).xBits;
        _sink += sum;
    }

    void RandomCorners()
    {
        ulong sum = 0;
        foreach (uint id in _cornerIds)
            sum += _reader.GetCorner(id);
        _sink += sum;
    }

    void RandomNormals()
    {
        float sum = 0;
        foreach (uint id in _cornerIds)
            sum += _reader.GetCornerNormal(id).x;
        _sink += (ulong)sum;
    }

    void RandomFaces()
    {
        ulong sum = 0;
        foreach (uint id in _faceIds)
            sum += _reader.GetFace(id, _faceBuffer);
        _sink += sum;
    }

    void RandomEdges()
    {
        ulong sum = 0;
        foreach (uint id in _edgeIds)
            sum += _reader.GetEdge(id).a;
        _sink += sum;
    }

    /// <summary>Times each read and prints the medians as one JSON line.</summary>
    public void Run()
    {
        Timing open = Measure(OpenOnce, OpenRunsPerRound, 1);
        Timing validate = Measure(ValidateOnce, RunsPerRound, 1);
        Timing vertices = Measure(ReadVerticesOnce, RunsPerRound, 1);
        Timing corners = Measure(ReadCornersOnce, RunsPerRound, 1);
        Timing edges = Measure(ReadEdgesOnce, RunsPerRound, 1);
        Timing mesh = Measure(ReadMeshOnce, RunsPerRound, 1);
        Timing randomVertex = Measure(RandomVertices, RandomRunsPerRound, Math.Max(_vertexIds.Length, 1));
        Timing randomCorner = Measure(RandomCorners, RandomRunsPerRound, Math.Max(_cornerIds.Length, 1));
        Timing randomNormal = Measure(RandomNormals, RandomRunsPerRound, Math.Max(_cornerIds.Length, 1));
        Timing randomFace = Measure(RandomFaces, RandomRunsPerRound, Math.Max(_faceIds.Length, 1));
        Timing randomEdge = Measure(RandomEdges, RandomRunsPerRound, Math.Max(_edgeIds.Length, 1));
        long allocated = new[]
        {
            validate.allocatedBytes, vertices.allocatedBytes, corners.allocatedBytes, edges.allocatedBytes,
            mesh.allocatedBytes, randomVertex.allocatedBytes, randomCorner.allocatedBytes,
            randomFace.allocatedBytes, randomEdge.allocatedBytes, randomNormal.allocatedBytes,
        }.Max();
        Console.WriteLine(JsonSerializer.Serialize(new
        {
            open_ns = open.medianNs,
            validate_ns = validate.medianNs,
            read_vertices_ns = vertices.medianNs,
            read_corners_ns = corners.medianNs,
            read_edges_ns = edges.medianNs,
            read_all_ns = mesh.medianNs,
            read_all_min_ns = mesh.minNs,
            read_all_max_ns = mesh.maxNs,
            random_vertex_ns = randomVertex.medianNs,
            random_corner_ns = randomCorner.medianNs,
            random_face_ns = randomFace.medianNs,
            random_normal_ns = randomNormal.medianNs,
            random_edge_ns = randomEdge.medianNs,
            allocated_bytes = allocated,
            sink = _sink,
        }));
    }
}
