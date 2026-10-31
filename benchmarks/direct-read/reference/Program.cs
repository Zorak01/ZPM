// SPDX-License-Identifier: GPL-3.0-or-later
using System.Diagnostics;
using System.Runtime.InteropServices;
using System.Text.Json;

namespace Zpm.DirectRead;

/// <summary>
/// encode FIXTURE OUTPUT_DIR   writes and verifies direct.zmd
/// measure PACKET              times reads of a .zmd file
/// measure-view PACKET         times the same reads of a RAW32 or COMPACT16 .zmc packet
/// fuzz PACKET SEED COUNT      flips bytes and proves validated files never read out of range
/// </summary>
static unsafe class Program
{
    static int Main(string[] arguments)
    {
        switch (arguments[0])
        {
            case "encode":
                Encode(arguments[1], arguments[2]);
                return 0;
            case "measure":
                return MeasureZmd(arguments[1]);
            case "measure-view":
                return MeasureView(arguments[1]);
            case "fuzz":
                return Fuzz(arguments[1], int.Parse(arguments[2]), int.Parse(arguments[3]));
            default:
                Console.Error.WriteLine("Unknown mode");
                return 2;
        }
    }

    static void Encode(string fixture, string folder)
    {
        ZpmSource source = ZpmSource.Load(fixture);
        Directory.CreateDirectory(folder);
        var variants = new List<object>();
        foreach (string name in new[] { "direct" })
        {
            var clock = Stopwatch.StartNew();
            byte[] file = ZpmEncoder.Encode(source, out ZpmEncodeReport report);
            clock.Stop();
            Verify(source, file);
            string path = Path.Combine(folder, name + ".zmd");
            File.WriteAllBytes(path, file);
            variants.Add(new
            {
                name,
                bytes = file.Length,
                encode_ms = clock.Elapsed.TotalMilliseconds,
                packet = path,
                position_bytes = report.positionBytes,
                corner_bytes = report.cornerBytes,
                face_bytes = report.faceBytes,
                edge_bytes = report.edgeBytes,
                table_bytes = report.tableBytes,
                unique_positions = report.uniquePositions,
                axis_modes = report.axisModes,
                corner_mode = report.cornerMode,
                edge_mode = report.edgeMode,
                exact = true,
            });
        }
        Console.WriteLine(JsonSerializer.Serialize(new
        {
            points = source.vertexCount,
            faces = source.faceCount,
            corners = source.cornerCount,
            edges = source.edgeCount,
            variants,
        }));
    }

    /// <summary>Compares every read path to the source. Any difference ends the run with an error.</summary>
    static void Verify(ZpmSource source, byte[] file)
    {
        fixed (byte* data = file)
        {
            if (!ZpmMeshView.TryOpen(data, file.Length, out ZpmMeshView view) || !view.Validate())
                throw new InvalidDataException("Encoded file failed validation");
            if (view.vertexCount != source.vertexCount || view.faceCount != source.faceCount
                || view.cornerCount != source.cornerCount || view.edgeCount != source.edgeCount)
                throw new InvalidDataException("Counts differ");
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

    static int MeasureZmd(string path)
    {
        byte* data = LoadNative(path, out long length);
        if (!ZpmMeshView.TryOpen(data, length, out ZpmMeshView view) || !view.Validate())
            return 3;
        var bench = new Bench<ZpmMeshView>(view, () =>
        {
            ZpmMeshView.TryOpen(data, length, out ZpmMeshView opened);
            return opened.vertexCount;
        });
        bench.Run();
        return 0;
    }

    static int MeasureView(string path)
    {
        byte* data = LoadNative(path, out long length);
        if (!Compact16View.TryOpen(data, length, out Compact16View view))
            return 3;
        var bench = new Bench<Compact16View>(view, () =>
        {
            Compact16View.TryOpen(data, length, out Compact16View opened);
            return opened.vertexCount;
        });
        bench.Run();
        return 0;
    }

    static int Fuzz(string path, int seed, int count)
    {
        const int MaxFlips = 8;
        const uint MaxCount = 1 << 24;
        byte[] original = File.ReadAllBytes(path);
        var random = new Random(seed);
        int accepted = 0;
        int rejected = 0;
        uint* output = (uint*)NativeMemory.AllocZeroed(MaxCount * 4 * 3);
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
                    && view.vertexCount < MaxCount && view.cornerCount < MaxCount && view.edgeCount < MaxCount)
                {
                    accepted++;
                    view.GetVertices(0, view.vertexCount, output);
                    view.GetCorners(0, view.cornerCount, output);
                    view.GetEdges(0, view.edgeCount, output);
                    view.GetFaceSizes(0, view.faceCount, output);
                    view.GetMesh(output, output + MaxCount, output + MaxCount, output + MaxCount);
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
                else
                    rejected++;
                for (int i = flips - 1; i >= 0; i--)
                    data[saved[i].at] = saved[i].value;
            }
        }
        Console.WriteLine(JsonSerializer.Serialize(new { trials = count, accepted, rejected }));
        return 0;
    }
}

/// <summary>Runs the same timed reads on any reader. Generic over the struct so calls stay direct.</summary>
sealed unsafe class Bench<TReader> where TReader : struct, IZpmMeshReader
{
    const int Rounds = 9;
    const int WarmupRuns = 25;
    const int RunsPerRound = 25;
    const int OpenRunsPerRound = 2000;
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

    public Bench(TReader reader, Func<uint> open)
    {
        _reader = reader;
        _open = open;
        _positions = Alloc(reader.vertexCount * 3);
        _corners = Alloc(reader.cornerCount);
        _edges = Alloc(reader.edgeCount * 2);
        _sizes = Alloc(reader.faceCount);
        _faceBuffer = Alloc(Math.Max(reader.cornerCount, 16));
        _vertexIds = Samples(reader.vertexCount, SampleCount);
        _cornerIds = Samples(reader.cornerCount, SampleCount);
        _faceIds = Samples(reader.faceCount, SampleCount);
        _edgeIds = Samples(reader.edgeCount, SampleCount);
    }

    static uint* Alloc(uint count) => (uint*)NativeMemory.AlignedAlloc((nuint)(Math.Max(count, 1) * 4L + 64), 64);

    /// <summary>A fixed-seed list of indices, so every reader visits the same elements.</summary>
    static uint[] Samples(uint count, int samples)
    {
        if (count == 0)
            return Array.Empty<uint>();
        var ids = new uint[samples];
        ulong state = Seed;
        for (int i = 0; i < samples; i++)
        {
            state += Seed;
            ulong z = state;
            z = (z ^ (z >> 30)) * 0xBF58476D1CE4E5B9UL;
            z = (z ^ (z >> 27)) * 0x94D049BB133111EBUL;
            ids[i] = (uint)((z ^ (z >> 31)) % count);
        }
        return ids;
    }

    readonly record struct Timing(double median_ns, double min_ns, double max_ns, long allocated_bytes);

    Timing Measure(Action work, int runsPerRound, double operationsPerRun)
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
    void ReadSizesOnce() => _reader.GetFaceSizes(0, _reader.faceCount, _sizes);

    void ReadAllOnce() => _reader.GetMesh(_positions, _sizes, _corners, _edges);

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

    public void Run()
    {
        Timing open = Measure(OpenOnce, OpenRunsPerRound, 1);
        Timing validate = Measure(ValidateOnce, RunsPerRound, 1);
        Timing vertices = Measure(ReadVerticesOnce, RunsPerRound, 1);
        Timing corners = Measure(ReadCornersOnce, RunsPerRound, 1);
        Timing edges = Measure(ReadEdgesOnce, RunsPerRound, 1);
        Timing all = Measure(ReadAllOnce, RunsPerRound, 1);
        Timing randomVertex = Measure(RandomVertices, 2, Math.Max(_vertexIds.Length, 1));
        Timing randomCorner = Measure(RandomCorners, 2, Math.Max(_cornerIds.Length, 1));
        Timing randomFace = Measure(RandomFaces, 2, Math.Max(_faceIds.Length, 1));
        Timing randomEdge = Measure(RandomEdges, 2, Math.Max(_edgeIds.Length, 1));
        long allocated = Math.Max(Math.Max(Math.Max(validate.allocated_bytes, all.allocated_bytes), randomVertex.allocated_bytes),
            Math.Max(Math.Max(randomCorner.allocated_bytes, randomFace.allocated_bytes), randomEdge.allocated_bytes));
        Console.WriteLine(JsonSerializer.Serialize(new
        {
            open_ns = open.median_ns,
            validate_ns = validate.median_ns,
            read_vertices_ns = vertices.median_ns,
            read_corners_ns = corners.median_ns,
            read_edges_ns = edges.median_ns,
            read_all_ns = all.median_ns,
            read_all_min_ns = all.min_ns,
            read_all_max_ns = all.max_ns,
            random_vertex_ns = randomVertex.median_ns,
            random_corner_ns = randomCorner.median_ns,
            random_face_ns = randomFace.median_ns,
            random_edge_ns = randomEdge.median_ns,
            allocated_bytes = allocated,
            sink = _sink,
        }));
    }
}
