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
            case "dump-scene":
                return DumpScene(arguments[1]);
            case "fuzz-scene":
                return FuzzScene(arguments[1], int.Parse(arguments[2]), int.Parse(arguments[3]));
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
        uint uvLayers = reader.ReadUInt32();
        Require(uvLayers == view.uvLayerCount, "tangent layer count");
        for (uint layer = 0; layer < uvLayers; layer++)
        {
            Require((reader.ReadUInt32() != 0) == view.TryGetTangentLayer(layer, out ZpmCornerStream tangents), "has tangents");
            for (uint c = 0; tangents.isBound && c < view.cornerCount; c++)
            {
                ZpmTangent tangent = view.GetCornerTangent(tangents, c);
                Require(MathF.Abs(tangent.x - reader.ReadSingle()) < Tolerance && MathF.Abs(tangent.y - reader.ReadSingle()) < Tolerance
                    && MathF.Abs(tangent.z - reader.ReadSingle()) < Tolerance && tangent.w == reader.ReadSingle(), "tangent");
            }
            bool hasSeams = view.TryGetUvSeamLayer(layer, out ZpmColumn seams);
            Require((reader.ReadUInt32() != 0) == hasSeams, "has UV seams");
            for (uint e = 0; hasSeams && e < view.edgeCount; e++)
                Require(seams.Get(e) == reader.ReadByte(), "UV seam");
        }
        uint layers = reader.ReadUInt32();
        Require(layers == view.colorLayerCount, "color layer count");
        for (uint layer = 0; layer < layers; layer++)
        {
            Require(view.TryGetColorLayer(layer, out ZpmColorLayer colors), "color layer");
            Require(colors.isSrgb == (reader.ReadUInt32() != 0), "color space");
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
        VerifyMaterialsAndAuthoredMarks(view, reader);
    }

    static void VerifyMaterialsAndAuthoredMarks(in ZpmMeshView view, BinaryReader reader)
    {
        Require(reader.ReadUInt32() == view.materialSlotCount, "material slot count");
        for (uint f = 0; f < view.faceCount; f++)
            Require(view.GetFaceMaterial(f) == reader.ReadUInt32(), "face material");
        for (uint c = 0; c < view.cornerCount; c++)
            Require(view.IsCornerSharp(c) == (reader.ReadByte() != 0), "sharp corner");
        for (uint c = 0; c < view.cornerCount; c++)
            Require(view.GetCornerSmoothingGroup(c) == reader.ReadUInt32(), "smoothing group");
        uint offsets = reader.ReadUInt32();
        Require(offsets == (view.creaseChainCount == 0 ? 0 : view.creaseChainCount + 1), "crease chain count");
        var chainOffsets = new uint[offsets];
        for (uint i = 0; i < offsets; i++)
            chainOffsets[i] = reader.ReadUInt32();
        var chain = new uint[offsets == 0 ? 1 : chainOffsets[offsets - 1] + 1];
        fixed (uint* vertices = chain)
        {
            for (uint i = 0; i < view.creaseChainCount; i++)
            {
                uint length = view.GetCreaseChain(i, vertices);
                Require(length == view.GetCreaseChainLength(i) && length == chainOffsets[i + 1] - chainOffsets[i], "crease chain length");
                for (uint j = 0; j < length; j++)
                    Require(vertices[j] == reader.ReadUInt32(), "crease chain vertex");
            }
        }
        uint holes = reader.ReadUInt32();
        Require((holes != 0) == view.hasHoles, "has holes");
        var rings = new uint[holes + 1];
        uint seen = 0;
        fixed (uint* ringStarts = rings)
        {
            for (uint f = 0; f < view.faceCount; f++)
            {
                uint count = view.GetFaceRings(f, ringStarts);
                Require(count == view.GetFaceRingCount(f) && ringStarts[0] == view.GetFaceStart(f), "face rings");
                for (uint r = 1; r < count; r++, seen++)
                    Require(ringStarts[r] == reader.ReadUInt32(), "hole start");
            }
        }
        Require(seen == holes, "hole count");
        VerifyAttributes(view, reader);
        VerifyShapeTargets(view, reader);
    }

    static void VerifyAttributes(in ZpmMeshView view, BinaryReader reader)
    {
        uint attributes = reader.ReadUInt32();
        Require(attributes == view.attributeCount, "attribute count");
        for (uint a = 0; a < attributes; a++)
        {
            Require(view.TryGetAttribute(a, out ZpmAttribute attribute), "attribute");
            Require(reader.ReadUInt32() == attribute.elementCount * attribute.componentCount, "attribute size");
            for (uint e = 0; e < attribute.elementCount; e++)
            {
                for (uint c = 0; c < attribute.componentCount; c++)
                {
                    bool isSame = attribute.type == ZpmAttributeType.Float32
                        ? BitConverter.SingleToUInt32Bits(attribute.GetFloat(e, c)) == reader.ReadUInt32()
                        : attribute.GetInt(e, c) == reader.ReadInt32();
                    Require(isSame, "attribute value");
                }
            }
        }
    }

    static void VerifyShapeTargets(in ZpmMeshView view, BinaryReader reader)
    {
        ZpmShapeTargets shapes = view.shapeTargets;
        uint targets = reader.ReadUInt32();
        Require(targets == shapes.count, "shape target count");
        var vertices = new uint[view.vertexCount + 1];
        var offsets = new float[view.vertexCount * 3 + 3];
        fixed (uint* v = vertices)
        fixed (float* o = offsets)
        {
            for (uint t = 0; t < targets; t++)
            {
                for (uint i = 0; i < view.vertexCount; i++)
                {
                    ZpmVertex position = view.GetShapeTargetPosition(t, i);
                    Require(position.x == reader.ReadSingle() && position.y == reader.ReadSingle() && position.z == reader.ReadSingle(), "target position");
                }
                uint count = view.GetShapeOffsets(t, v, o);
                Require(count == shapes.GetAffectedCount(t), "target vertices");
                for (uint i = 0; i < count; i++)
                {
                    ZpmVertex start = view.GetVertex(v[i]);
                    ZpmVertex end = view.GetShapeTargetPosition(t, v[i]);
                    Require(start.x + o[i * 3] == end.x && start.y + o[i * 3 + 1] == end.y && start.z + o[i * 3 + 2] == end.z, "target offset");
                }
                VerifyShapeOffsets(shapes, t, reader, false);
                VerifyShapeOffsets(shapes, t, reader, true);
            }
        }
    }

    static void VerifyShapeOffsets(in ZpmShapeTargets shapes, uint target, BinaryReader reader, bool isTangent)
    {
        const float Tolerance = 1e-5f;
        uint length = reader.ReadUInt32();
        Require((length != 0) == (isTangent ? shapes.hasTangentOffsets : shapes.hasNormalOffsets), "has shape offsets");
        for (uint i = 0; i < length / 3; i++)
        {
            float x = reader.ReadSingle(), y = reader.ReadSingle(), z = reader.ReadSingle();
            if (shapes.TryFindVertex(target, i, out uint at))
            {
                ZpmNormal change = isTangent ? shapes.GetTangentOffsetAt(at) : shapes.GetNormalOffsetAt(at);
                Require(MathF.Abs(change.x - x) < Tolerance && MathF.Abs(change.y - y) < Tolerance && MathF.Abs(change.z - z) < Tolerance, "shape offset");
            }
            else
                Require(x == 0f && y == 0f && z == 0f, "unmoved shape offset");
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

    /// <summary>Prints what the scene reader sees as JSON, so Python can compare it with the source.</summary>
    static int DumpScene(string path)
    {
        byte[] bytes = File.ReadAllBytes(path);
        fixed (byte* data = bytes)
        {
            if (!ZpmSceneView.TryOpen(data, bytes.Length, out ZpmSceneView scene) || !scene.Validate())
                throw new InvalidDataException("Scene failed validation");
            var objects = new List<object>();
            for (uint i = 0; i < scene.objectCount; i++)
            {
                ref readonly ZpmObject item = ref scene.GetObject(i);
                scene.TryGetObjectName(i, out byte* name, out uint length);
                objects.Add(new
                {
                    name = Text(name, length),
                    type = (uint)item.type,
                    item.data,
                    item.parent,
                    item.parentBone,
                    location = new[] { item.location.x, item.location.y, item.location.z },
                    poseChannels = item.poseChannelCount,
                    skinArmature = item.skinArmature,
                    skinBones = scene.GetSkinBoneCount(i),
                });
            }
            var meshes = new List<uint>();
            for (uint i = 0; i < scene.meshCount; i++)
            {
                Require(scene.TryGetMesh(i, out ZpmMeshView mesh), "scene mesh");
                meshes.Add(mesh.vertexCount);
            }
            var materials = new List<object>();
            for (uint i = 0; i < scene.materialCount; i++)
            {
                ref readonly ZpmMaterial material = ref scene.GetMaterial(i);
                scene.TryGetMaterialName(i, out byte* name, out uint length);
                materials.Add(new { name = Text(name, length), material.metallic, material.roughness, slots = material.slotCount });
            }
            var tiles = new List<object>();
            for (uint i = 0; i < scene.imageCount; i++)
            {
                ref readonly ZpmImage image = ref scene.GetImage(i);
                for (uint t = 0; t < image.tileCount; t++)
                {
                    uint tile = image.firstTile + t;
                    scene.TryGetTilePath(tile, out byte* name, out uint length);
                    uint bytesLength = scene.TryGetTilePayload(tile, out byte* _, out uint size) ? size : 0;
                    tiles.Add(new { number = scene.GetImageTile(tile).number, path = Text(name, length), bytes = bytesLength });
                }
            }
            var bones = new List<object>();
            for (uint a = 0; a < scene.armatureCount; a++)
            {
                for (uint b = 0; b < scene.GetBoneCount(a); b++)
                {
                    uint bone = scene.GetFirstBone(a) + b;
                    scene.TryGetBoneName(bone, out byte* name, out uint length);
                    bones.Add(new { name = Text(name, length), scene.GetBone(bone).parent, flags = (uint)scene.GetBone(bone).flags, roll = scene.GetBone(bone).roll });
                }
            }
            var lights = new List<float>();
            for (uint i = 0; i < scene.lightCount; i++)
                lights.Add(scene.GetLight(i).power);
            var splinePoints = new List<float>();
            ZpmCurves curves = scene.curves;
            for (uint c = 0; c < curves.curveCount; c++)
            {
                ref readonly ZpmCurveInfo curve = ref curves.GetCurve(c);
                for (uint s = 0; s < curve.splineCount; s++)
                {
                    ref readonly ZpmSpline spline = ref curves.GetSpline(curve.firstSpline + s);
                    for (uint p = 0; p < spline.pointCount; p++)
                    {
                        ZpmCurvePoint point = curves.GetPoint(spline.firstPoint + p);
                        splinePoints.Add(point.position.r + point.position.g + point.position.a + point.radius);
                    }
                }
            }
            var latticePoints = new List<float>();
            ZpmLattices lattices = scene.lattices;
            for (uint l = 0; l < lattices.latticeCount; l++)
            {
                ref readonly ZpmLatticeInfo lattice = ref lattices.GetLattice(l);
                for (uint p = 0; p < lattice.pointCount; p++)
                    latticePoints.Add(lattices.GetDeformedPoint(lattice.firstPoint + p).y + lattices.GetWeight(lattice.firstPoint + p));
            }
            var keys = new List<float>();
            ZpmAnimation animation = scene.animation;
            for (uint c = 0; c < animation.clipCount; c++)
            {
                ref readonly ZpmClip clip = ref animation.GetClip(c);
                for (uint t = 0; t < clip.trackCount; t++)
                {
                    ref readonly ZpmTrack track = ref animation.GetTrack(clip.firstTrack + t);
                    for (uint k = 0; k < track.keyCount; k++)
                    {
                        keys.Add(animation.GetKeyTime(track, k));
                        if (track.valueType == ZpmTrackValue.Float)
                            keys.Add(animation.GetKeyFloat(track, k));
                    }
                }
            }
            ZpmValueTree values = scene.values;
            Require(values.TryGetRoot(ZpmResourceKind.Scene, 0, out ZpmValueNode root), "scene properties");
            Require(values.TryFindChild(root, System.Text.Encoding.UTF8.GetBytes("properties"), out ZpmValueNode properties), "scene property group");
            Require(values.TryFindChild(properties, System.Text.Encoding.UTF8.GetBytes("author"), out ZpmValueNode author), "scene property");
            Require(values.TryGetString(author, out byte* authorText, out uint authorLength), "string property");
            var members = new List<uint>();
            for (uint c = 0; c < scene.collectionCount; c++)
            {
                for (uint m = 0; m < scene.GetCollectionMemberCount(c); m++)
                    members.Add(scene.GetCollectionMember(c, m));
            }
            Console.WriteLine(JsonSerializer.Serialize(new
            {
                meters = scene.metersPerUnit,
                up = (uint)scene.upAxis,
                forward = (uint)scene.forwardAxis,
                meshes,
                objects,
                materials,
                tiles,
                bones,
                lights,
                members,
                splinePoints,
                latticePoints,
                keys,
                author = Text(authorText, authorLength),
                cameras = scene.cameraCount,
                extensions = scene.extensionCount,
            }));
        }
        return 0;
    }

    static string Text(byte* utf8, uint length) => System.Text.Encoding.UTF8.GetString(utf8, (int)length);

    static int FuzzScene(string path, int seed, int count)
    {
        byte[] original = File.ReadAllBytes(path);
        var random = new Random(seed);
        int accepted = 0, rejected = 0;
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
                if (ZpmSceneView.TryOpen(data, original.Length, out ZpmSceneView scene) && scene.Validate())
                {
                    accepted++;
                    ReadScene(scene);
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

    /// <summary>Touches every link a validated scene holds. Going out of range would crash the run.</summary>
    static void ReadScene(in ZpmSceneView scene)
    {
        for (uint i = 0; i < scene.objectCount; i++)
        {
            ref readonly ZpmObject item = ref scene.GetObject(i);
            if (item.parent != ZpmLayout.NoIndex)
                scene.GetObject(item.parent);
            for (uint p = 0; p < item.poseChannelCount; p++)
                scene.GetPoseChannel(item.firstPoseChannel + p);
            for (uint g = 0; g < scene.GetSkinBoneCount(i); g++)
                scene.GetSkinBone(i, g);
        }
        for (uint i = 0; i < scene.materialCount; i++)
        {
            ref readonly ZpmMaterial material = ref scene.GetMaterial(i);
            for (uint s = 0; s < material.slotCount; s++)
            {
                ref readonly ZpmTextureSlot slot = ref scene.GetTextureSlot(material.firstSlot + s);
                scene.GetImage(slot.image);
                scene.GetSampler(slot.sampler);
            }
        }
        for (uint i = 0; i < scene.imageCount; i++)
        {
            ref readonly ZpmImage image = ref scene.GetImage(i);
            for (uint t = 0; t < image.tileCount; t++)
            {
                if (scene.TryGetTilePayload(image.firstTile + t, out byte* bytes, out uint length) && length != 0)
                    Require(bytes[length - 1] == bytes[length - 1], "payload read");
            }
        }
        for (uint a = 0; a < scene.armatureCount; a++)
        {
            for (uint b = 0; b < scene.GetBoneCount(a); b++)
                scene.GetBone(scene.GetFirstBone(a) + b);
        }
        ZpmCurves curves = scene.curves;
        for (uint c = 0; c < curves.curveCount; c++)
        {
            ref readonly ZpmCurveInfo curve = ref curves.GetCurve(c);
            for (uint s = 0; s < curve.splineCount; s++)
            {
                ref readonly ZpmSpline spline = ref curves.GetSpline(curve.firstSpline + s);
                for (uint p = 0; p < spline.pointCount; p++)
                {
                    curves.GetPoint(spline.firstPoint + p);
                    if (curves.hasHandles)
                        curves.GetLeftHandle(spline.firstPoint + p);
                }
                for (uint k = 0; k < spline.knotCount && spline.firstKnot != ZpmLayout.NoIndex; k++)
                    curves.GetKnot(spline.firstKnot + k);
            }
        }
        ZpmLattices lattices = scene.lattices;
        for (uint l = 0; l < lattices.latticeCount; l++)
        {
            ref readonly ZpmLatticeInfo lattice = ref lattices.GetLattice(l);
            for (uint p = 0; p < lattice.pointCount; p++)
                lattices.GetRestPoint(lattice.firstPoint + p);
        }
        ZpmAnimation animation = scene.animation;
        for (uint c = 0; c < animation.clipCount; c++)
        {
            ref readonly ZpmClip clip = ref animation.GetClip(c);
            for (uint t = 0; t < clip.trackCount; t++)
            {
                ref readonly ZpmTrack track = ref animation.GetTrack(clip.firstTrack + t);
                for (uint k = 0; k < track.keyCount; k++)
                {
                    animation.GetKeyTime(track, k);
                    animation.GetKeyStored(track, k);
                    animation.GetKeyMode(track, k);
                    if (track.firstHandle != ZpmLayout.NoIndex)
                        animation.GetKeyHandle(track, k, 3);
                }
            }
        }
        ZpmValueTree values = scene.values;
        for (uint e = 0; e < scene.extensionCount; e++)
        {
            scene.GetExtensionPayload(e, out byte* payload, out uint size);
            if (size != 0)
                Require(payload[size - 1] == payload[size - 1], "extension read");
        }
        if (values.TryGetRoot(ZpmResourceKind.Scene, 0, out ZpmValueNode root))
            WalkValues(values, root, 0);
        for (uint c = 0; c < scene.collectionCount; c++)
        {
            for (uint m = 0; m < scene.GetCollectionMemberCount(c); m++)
                scene.GetObject(scene.GetCollectionMember(c, m));
            scene.GetCollectionParent(c);
        }
    }

    static void WalkValues(in ZpmValueTree values, in ZpmValueNode node, int depth)
    {
        if (depth > 64 || (node.type != ZpmValueType.Dictionary && node.type != ZpmValueType.List))
            return;
        for (uint i = 0; i < node.b; i++)
            WalkValues(values, values.GetChild(node, i), depth + 1);
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
        for (uint layer = 0; layer < view.uvLayerCount; layer++)
        {
            if (view.TryGetTangentLayer(layer, out ZpmCornerStream tangents))
            {
                for (uint c = 0; c < view.cornerCount; c++)
                    view.GetCornerTangent(tangents, c);
            }
        }
        for (uint f = 0; f < view.faceCount; f++)
        {
            view.GetFaceMaterial(f);
            if (view.GetFaceRingCount(f) > MaxFuzzWeights)
                throw new InvalidDataException("Fuzzed face has too many rings to test");
            view.GetFaceRings(f, output);
        }
        for (uint c = 0; c < view.cornerCount; c++)
        {
            view.IsCornerSharp(c);
            view.GetCornerSmoothingGroup(c);
        }
        for (uint a = 0; a < view.attributeCount; a++)
        {
            if (view.TryGetAttribute(a, out ZpmAttribute attribute))
            {
                for (uint e = 0; e < attribute.elementCount; e++)
                {
                    for (uint c = 0; c < attribute.componentCount; c++)
                        attribute.GetUInt(e, c);
                }
            }
        }
        ZpmShapeTargets shapes = view.shapeTargets;
        for (uint t = 0; t < shapes.count; t++)
        {
            shapes.GetTarget(t);
            for (uint v = 0; v < view.vertexCount; v++)
                view.GetShapeTargetPosition(t, v);
        }
        for (uint chain = 0; chain < view.creaseChainCount; chain++)
        {
            if (view.GetCreaseChainLength(chain) <= MaxFuzzWeights)
                view.GetCreaseChain(chain, output);
        }
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
