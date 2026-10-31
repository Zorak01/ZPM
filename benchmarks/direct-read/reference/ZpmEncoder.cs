// SPDX-License-Identifier: GPL-3.0-or-later
using System.Runtime.CompilerServices;

namespace Zpm.DirectRead;

/// <summary>Mesh arrays in source order, as read from a ZMS1 fixture.</summary>
public sealed class ZpmSource
{
    const uint Magic = 0x31534D5A;

    public uint vertexCount, faceCount, cornerCount, edgeCount;
    public uint[] positions = Array.Empty<uint>();
    public uint[] faceSizes = Array.Empty<uint>();
    public uint[] corners = Array.Empty<uint>();
    public uint[] edges = Array.Empty<uint>();

    public static ZpmSource Load(string path)
    {
        using var reader = new BinaryReader(File.OpenRead(path));
        if (reader.ReadUInt32() != Magic)
            throw new InvalidDataException("Not a ZMS1 fixture");
        var source = new ZpmSource
        {
            vertexCount = reader.ReadUInt32(),
            faceCount = reader.ReadUInt32(),
            cornerCount = reader.ReadUInt32(),
            edgeCount = reader.ReadUInt32(),
        };
        source.positions = ReadArray(reader, source.vertexCount * 3);
        source.faceSizes = ReadArray(reader, source.faceCount);
        source.corners = ReadArray(reader, source.cornerCount);
        source.edges = ReadArray(reader, source.edgeCount * 2);
        return source;
    }

    static uint[] ReadArray(BinaryReader reader, uint count)
    {
        var values = new uint[count];
        for (uint i = 0; i < count; i++)
            values[i] = reader.ReadUInt32();
        return values;
    }
}

/// <summary>Which layers the encoder may use. Smallest allows all; Fast keeps one lookup per value.</summary>
public readonly record struct ZpmEncodeOptions(bool allowRepeatedPositions, bool allowPalettes, bool allowCornerReferences)
{
    public static readonly ZpmEncodeOptions Smallest = new(true, true, true);
    public static readonly ZpmEncodeOptions Fast = new(false, false, false);
}

/// <summary>Bytes per stream of an encoded file, and the choices the encoder made.</summary>
public sealed class ZpmEncodeReport
{
    public int positionBytes, cornerBytes, faceBytes, edgeBytes, tableBytes;
    public uint uniquePositions;
    public string axisModes = "", cornerMode = "", edgeMode = "";
}

/// <summary>Builds a ZMD1 file, trying each legal layout per stream and keeping the smallest.</summary>
public static class ZpmEncoder
{
    /// <summary>A column or table in the file, in the order the view reads the entries.</summary>
    readonly record struct Section(byte[] bytes, bool hasSlopes);

    const long MaxResidualRange = 1L << ZpmColumn.MaxWidth;
    const int MaxSlope = sbyte.MaxValue;

    public static byte[] Encode(ZpmSource source, ZpmEncodeOptions options, out ZpmEncodeReport report)
    {
        report = new ZpmEncodeReport();
        var sections = new List<Section>();
        var header = new byte[ZpmLayout.HeaderBytes];
        WriteU32(header, 0, ZpmLayout.Magic);
        WriteU32(header, ZpmLayout.VertexCountAt, source.vertexCount);
        WriteU32(header, ZpmLayout.FaceCountAt, source.faceCount);
        WriteU32(header, ZpmLayout.CornerCountAt, source.cornerCount);
        WriteU32(header, ZpmLayout.EdgeCountAt, source.edgeCount);

        ZpmFlags flags = ZpmFlags.None;
        report.positionBytes = AppendPositions(source, options, header, sections, ref flags, report);
        report.cornerBytes = AppendCorners(source, header, sections, report);
        report.faceBytes = AppendFaceStarts(source, header, sections);
        report.edgeBytes = AppendEdges(source, options, header, sections, ref flags, report);
        header[ZpmLayout.FlagsAt] = (byte)flags;

        uint tableBytes = (uint)sections.Count * 4;
        report.tableBytes = ZpmLayout.HeaderBytes + (int)tableBytes + ZpmLayout.TailBytes;
        using var output = new MemoryStream();
        output.Write(header);
        output.Write(new byte[tableBytes]);
        byte[] entry = new byte[4];
        for (int i = 0; i < sections.Count; i++)
        {
            while (output.Length % 4 != 0)
                output.WriteByte(0);
            uint offset = (uint)output.Length;
            long resume = output.Position;
            output.Position = ZpmLayout.HeaderBytes + i * 4;
            WriteU32(entry, 0, offset | (sections[i].hasSlopes ? 1u : 0u));
            output.Write(entry);
            output.Position = resume;
            output.Write(sections[i].bytes);
        }
        output.Write(new byte[ZpmLayout.TailBytes]);
        return output.ToArray();
    }

    static int AppendPositions(ZpmSource source, ZpmEncodeOptions options, byte[] header, List<Section> sections, ref ZpmFlags flags, ZpmEncodeReport report)
    {
        uint vertices = source.vertexCount;
        if (vertices == 0)
            return 0;
        var direct = new List<Section>();
        var directModes = new ZpmAxisMode[3];
        var directArguments = new uint[3];
        for (int axis = 0; axis < 3; axis++)
            AppendAxis(source.positions, 3, axis, (int)vertices, options, direct, out directModes[axis], out directArguments[axis]);
        int best = SizeOf(direct);
        List<Section> chosen = direct;
        ZpmAxisMode[] modes = directModes;
        uint[] arguments = directArguments;
        uint unique = vertices;

        if (options.allowRepeatedPositions)
        {
            var seen = new Dictionary<(uint, uint, uint), uint>();
            var rows = new List<uint>();
            var isRepeat = new bool[vertices];
            var repeatIds = new List<long>();
            for (uint v = 0; v < vertices; v++)
            {
                var key = (source.positions[v * 3], source.positions[v * 3 + 1], source.positions[v * 3 + 2]);
                if (seen.TryGetValue(key, out uint id))
                {
                    isRepeat[v] = true;
                    repeatIds.Add(id);
                    continue;
                }
                seen.Add(key, (uint)seen.Count);
                rows.Add(key.Item1);
                rows.Add(key.Item2);
                rows.Add(key.Item3);
            }
            if (repeatIds.Count != 0)
            {
                var layered = new List<Section> { new(BuildRankMask(isRepeat), false), BuildColumn(repeatIds.ToArray()) };
                var layeredModes = new ZpmAxisMode[3];
                var layeredArguments = new uint[3];
                uint[] uniqueRows = rows.ToArray();
                for (int axis = 0; axis < 3; axis++)
                    AppendAxis(uniqueRows, 3, axis, seen.Count, options, layered, out layeredModes[axis], out layeredArguments[axis]);
                if (SizeOf(layered) < best)
                {
                    best = SizeOf(layered);
                    chosen = layered;
                    modes = layeredModes;
                    arguments = layeredArguments;
                    unique = (uint)seen.Count;
                    flags |= ZpmFlags.RepeatedPositions;
                }
            }
        }
        sections.AddRange(chosen);
        WriteU32(header, ZpmLayout.UniqueCountAt, unique);
        for (int axis = 0; axis < 3; axis++)
        {
            header[ZpmLayout.AxisModesAt + axis] = (byte)modes[axis];
            WriteU32(header, ZpmLayout.AxisArgumentsAt + axis * 4, arguments[axis]);
        }
        report.uniquePositions = unique;
        report.axisModes = string.Join('/', modes);
        return best;
    }

    static void AppendAxis(uint[] rows, int stride, int axis, int count, ZpmEncodeOptions options, List<Section> sections, out ZpmAxisMode mode, out uint argument)
    {
        var ordered = new long[count];
        var distinct = new HashSet<uint>();
        var magnitudes = new SortedSet<uint>();
        bool negative = false;
        for (int i = 0; i < count; i++)
        {
            uint bits = rows[i * stride + axis];
            ordered[i] = ZpmAxis.BitsToOrdered(bits);
            distinct.Add(bits);
            magnitudes.Add(bits & 0x7FFFFFFFu);
            negative |= (bits >> 31) != 0;
        }
        if (distinct.Count == 1)
        {
            mode = ZpmAxisMode.Constant;
            argument = rows[axis];
            return;
        }
        Section direct = BuildColumn(ordered);
        mode = ZpmAxisMode.Bits;
        argument = 0;
        if (options.allowPalettes)
        {
            uint[] sorted = magnitudes.ToArray();
            var lookup = new Dictionary<uint, long>(sorted.Length);
            var paletteValues = new long[sorted.Length];
            for (int i = 0; i < sorted.Length; i++)
            {
                lookup.Add(sorted[i], i);
                paletteValues[i] = sorted[i];
            }
            var indices = new long[count];
            for (int i = 0; i < count; i++)
            {
                uint bits = rows[i * stride + axis];
                long index = lookup[bits & 0x7FFFFFFFu];
                indices[i] = negative ? index * 2 + (bits >> 31) : index;
            }
            Section palette = BuildColumn(paletteValues);
            Section mapped = BuildColumn(indices);
            if (Align(palette.bytes.Length) + Align(mapped.bytes.Length) < Align(direct.bytes.Length))
            {
                mode = negative ? ZpmAxisMode.SignedPalette : ZpmAxisMode.Palette;
                argument = (uint)sorted.Length;
                sections.Add(palette);
                sections.Add(mapped);
                return;
            }
        }
        sections.Add(direct);
    }

    static int AppendCorners(ZpmSource source, byte[] header, List<Section> sections, ZpmEncodeReport report)
    {
        uint cornerCount = source.cornerCount;
        if (cornerCount == 0)
            return 0;
        uint arity = UniformArity(source);
        header[ZpmLayout.ArityAt] = (byte)arity;
        Section single = BuildColumn(ToLongs(source.corners));
        var chosen = new List<Section> { single };
        var mode = ZpmCornerMode.Single;
        if (arity is >= 3 and <= ZpmLayout.MaxRelativeArity)
        {
            var relative = new List<Section>();
            var column = new long[source.faceCount];
            for (uint j = 0; j < arity; j++)
            {
                for (uint f = 0; f < source.faceCount; f++)
                {
                    long first = source.corners[f * arity];
                    column[f] = j == 0 ? first : source.corners[f * arity + j] - first;
                }
                relative.Add(BuildColumn(column));
            }
            if (SizeOf(relative) < SizeOf(chosen))
            {
                chosen = relative;
                mode = ZpmCornerMode.RelativeToFirst;
            }
        }
        header[ZpmLayout.CornerModeAt] = (byte)mode;
        sections.AddRange(chosen);
        report.cornerMode = mode.ToString();
        return SizeOf(chosen);
    }

    static uint UniformArity(ZpmSource source)
    {
        if (source.faceCount == 0)
            return 0;
        uint first = source.faceSizes[0];
        if (first > ZpmLayout.MaxUniformArity)
            return 0;
        foreach (uint size in source.faceSizes)
        {
            if (size != first)
                return 0;
        }
        return first;
    }

    static int AppendFaceStarts(ZpmSource source, byte[] header, List<Section> sections)
    {
        if (source.cornerCount == 0 || header[ZpmLayout.ArityAt] != 0)
            return 0;
        uint words = ZpmRankMask.WordCountOf(source.cornerCount);
        var starts = new ulong[words];
        var selects = new List<uint>();
        uint at = 0;
        for (uint face = 0; face < source.faceCount; face++)
        {
            if (face % ZpmLayout.FacesPerSelect == 0)
                selects.Add(at);
            starts[at >> 6] |= 1UL << (int)(at & 63);
            at += source.faceSizes[face];
        }
        var bytes = new byte[words * 8 + (uint)selects.Count * 4];
        Buffer.BlockCopy(starts, 0, bytes, 0, (int)words * 8);
        Buffer.BlockCopy(selects.ToArray(), 0, bytes, (int)words * 8, selects.Count * 4);
        sections.Add(new Section(bytes, false));
        return Align(bytes.Length);
    }

    static int AppendEdges(ZpmSource source, ZpmEncodeOptions options, byte[] header, List<Section> sections, ref ZpmFlags flags, ZpmEncodeReport report)
    {
        uint edges = source.edgeCount;
        if (edges == 0)
            return 0;
        var first = new long[edges];
        var delta = new long[edges];
        for (uint e = 0; e < edges; e++)
        {
            first[e] = source.edges[e * 2];
            delta[e] = (long)source.edges[e * 2 + 1] - source.edges[e * 2];
        }
        var chosen = new List<Section> { BuildColumn(first), BuildColumn(delta) };
        var mode = ZpmEdgeMode.Endpoints;
        ZpmFlags edgeFlags = ZpmFlags.None;
        uint looseTotal = 0;

        if (options.allowCornerReferences && source.cornerCount != 0)
        {
            var next = new uint[source.cornerCount];
            uint at = 0;
            foreach (uint size in source.faceSizes)
            {
                for (uint j = 0; j < size; j++)
                    next[at + j] = at + (j + 1) % size;
                at += size;
            }
            var table = new Dictionary<(uint, uint), List<uint>>();
            for (uint c = 0; c < source.cornerCount; c++)
            {
                uint x = source.corners[c], y = source.corners[next[c]];
                var key = x < y ? (x, y) : (y, x);
                if (!table.TryGetValue(key, out var list))
                    table.Add(key, list = new List<uint>(2));
                list.Add(c);
            }
            var candidates = new List<List<uint>>();
            var isLoose = new bool[edges];
            var looseA = new List<long>();
            var looseB = new List<long>();
            bool canonical = true;
            for (uint e = 0; e < edges; e++)
            {
                uint a = source.edges[e * 2], b = source.edges[e * 2 + 1];
                if (table.TryGetValue(a < b ? (a, b) : (b, a), out var list))
                {
                    candidates.Add(list);
                    canonical &= a <= b;
                }
                else
                {
                    isLoose[e] = true;
                    looseA.Add(a);
                    looseB.Add(b);
                }
            }
            long[] nearestFirst = PickFirstReferences(candidates);
            long[] nearestBlock = PickBlockReferences(candidates);
            Section refs = SmallerColumn(BuildColumn(nearestFirst), BuildColumn(nearestBlock), out long[] picked, nearestFirst, nearestBlock);
            var referenced = new List<Section>();
            if (looseA.Count != 0)
            {
                referenced.Add(new Section(BuildRankMask(isLoose), false));
                referenced.Add(BuildColumn(looseA.ToArray()));
                referenced.Add(BuildColumn(looseB.ToArray()));
            }
            if (candidates.Count != 0)
            {
                referenced.Add(refs);
                if (!canonical)
                {
                    var flips = new long[candidates.Count];
                    int index = 0;
                    for (uint e = 0; e < edges; e++)
                    {
                        if (isLoose[e])
                            continue;
                        flips[index] = source.corners[picked[index]] == source.edges[e * 2] ? 0 : 1;
                        index++;
                    }
                    referenced.Add(BuildColumn(flips));
                }
            }
            if (SizeOf(referenced) < SizeOf(chosen))
            {
                chosen = referenced;
                mode = ZpmEdgeMode.CornerReference;
                looseTotal = (uint)looseA.Count;
                if (looseA.Count != 0)
                    edgeFlags |= ZpmFlags.LooseEdges;
                if (canonical)
                    edgeFlags |= ZpmFlags.CanonicalEdges;
            }
        }
        flags |= edgeFlags;
        header[ZpmLayout.EdgeModeAt] = (byte)mode;
        WriteU32(header, ZpmLayout.LooseCountAt, looseTotal);
        sections.AddRange(chosen);
        report.edgeMode = mode.ToString();
        return SizeOf(chosen);
    }

    static long[] PickFirstReferences(List<List<uint>> candidates)
    {
        var picked = new long[candidates.Count];
        for (int i = 0; i < picked.Length; i++)
            picked[i] = candidates[i][0];
        return picked;
    }

    /// <summary>An edge has a corner on each side, so the encoder picks whichever sits nearest its block.</summary>
    static long[] PickBlockReferences(List<List<uint>> candidates)
    {
        const int Passes = 2;
        long[] picked = PickFirstReferences(candidates);
        var window = new long[ZpmColumn.BlockSize];
        for (int start = 0; start < picked.Length; start += ZpmColumn.BlockSize)
        {
            int count = Math.Min(ZpmColumn.BlockSize, picked.Length - start);
            for (int pass = 0; pass < Passes; pass++)
            {
                Array.Copy(picked, start, window, 0, count);
                Array.Sort(window, 0, count);
                long middle = window[count / 2];
                for (int i = start; i < start + count; i++)
                {
                    long best = picked[i];
                    foreach (uint corner in candidates[i])
                    {
                        if (Math.Abs(corner - middle) < Math.Abs(best - middle))
                            best = corner;
                    }
                    picked[i] = best;
                }
            }
        }
        return picked;
    }

    static Section SmallerColumn(Section a, Section b, out long[] picked, long[] valuesA, long[] valuesB)
    {
        bool useA = a.bytes.Length <= b.bytes.Length;
        picked = useA ? valuesA : valuesB;
        return useA ? a : b;
    }

    /// <summary>Marks elements in 64-bit words, followed by the unmarked count before each word.</summary>
    static byte[] BuildRankMask(bool[] marks)
    {
        uint count = (uint)marks.Length;
        uint words = ZpmRankMask.WordCountOf(count);
        var masks = new ulong[words];
        var ranks = new uint[words];
        uint clear = 0;
        for (uint i = 0; i < count; i++)
        {
            if ((i & 63) == 0)
                ranks[i >> 6] = clear;
            if (marks[i])
                masks[i >> 6] |= 1UL << (int)(i & 63);
            else
                clear++;
        }
        var bytes = new byte[words * 12];
        Buffer.BlockCopy(masks, 0, bytes, 0, (int)words * 8);
        Buffer.BlockCopy(ranks, 0, bytes, (int)words * 8, (int)words * 4);
        return bytes;
    }

    /// <summary>Fits each 64-value block with the slope that needs the fewest residual bits.</summary>
    static Section BuildColumn(long[] values)
    {
        uint count = (uint)values.Length;
        uint blocks = ZpmColumn.BlockCountOf(count);
        var bases = new uint[blocks];
        var slopes = new sbyte[blocks];
        var widths = new byte[blocks];
        var groups = new uint[ZpmColumn.GroupCountOf(blocks)];
        using var payload = new MemoryStream();
        bool anySlope = false;
        for (uint block = 0; block < blocks; block++)
        {
            int start = (int)block * ZpmColumn.BlockSize;
            int length = Math.Min(ZpmColumn.BlockSize, values.Length - start);
            int slope = FitSlope(values, start, length, out long low, out int width);
            bases[block] = unchecked((uint)low);
            slopes[block] = (sbyte)slope;
            widths[block] = (byte)width;
            anySlope |= slope != 0;
            if (block % ZpmColumn.BlocksPerGroup == 0)
                groups[block / ZpmColumn.BlocksPerGroup] = (uint)payload.Length;
            WriteResiduals(payload, values, start, length, slope, low, width);
        }
        uint tables = blocks * 4 + (anySlope ? blocks : 0) + blocks;
        tables = ZpmColumn.Align4(tables);
        var bytes = new byte[tables + (uint)groups.Length * 4 + payload.Length];
        Buffer.BlockCopy(bases, 0, bytes, 0, (int)blocks * 4);
        int at = (int)blocks * 4;
        if (anySlope)
        {
            Buffer.BlockCopy(slopes, 0, bytes, at, (int)blocks);
            at += (int)blocks;
        }
        Buffer.BlockCopy(widths, 0, bytes, at, (int)blocks);
        Buffer.BlockCopy(groups, 0, bytes, (int)tables, groups.Length * 4);
        payload.Position = 0;
        payload.Read(bytes, (int)tables + groups.Length * 4, (int)payload.Length);
        return new Section(bytes, anySlope);
    }

    static int FitSlope(long[] values, int start, int length, out long low, out int width)
    {
        int bestSlope = 0;
        MeasureFit(values, start, length, 0, out low, out width);
        if (length < 3 || width == 0)
            return 0;
        long span = values[start + length - 1] - values[start];
        Span<int> candidates = stackalloc int[3];
        candidates[0] = (int)Math.Clamp(Math.DivRem(span, length - 1, out _), -MaxSlope, MaxSlope);
        candidates[1] = (int)Math.Clamp(values[start + 1] - values[start], -MaxSlope, MaxSlope);
        candidates[2] = (int)Math.Clamp(values[start + length / 2 + 1 < start + length ? start + length / 2 + 1 : start + 1] - values[start + length / 2], -MaxSlope, MaxSlope);
        foreach (int slope in candidates)
        {
            if (slope == 0 || slope == bestSlope)
                continue;
            MeasureFit(values, start, length, slope, out long slopedLow, out int slopedWidth);
            if (slopedWidth < width)
            {
                width = slopedWidth;
                low = slopedLow;
                bestSlope = slope;
            }
        }
        return bestSlope;
    }

    static void MeasureFit(long[] values, int start, int length, int slope, out long low, out int width)
    {
        low = long.MaxValue;
        long high = long.MinValue;
        for (int j = 0; j < length; j++)
        {
            long residual = values[start + j] - slope * (long)j;
            low = Math.Min(low, residual);
            high = Math.Max(high, residual);
        }
        long range = high - low;
        width = range >= MaxResidualRange ? int.MaxValue : BitWidth(range);
    }

    static int BitWidth(long range) => range == 0 ? 0 : 64 - System.Numerics.BitOperations.LeadingZeroCount((ulong)range);

    static void WriteResiduals(MemoryStream payload, long[] values, int start, int length, int slope, long low, int width)
    {
        if (width == int.MaxValue)
            throw new InvalidDataException("Value range exceeds 32 bits");
        ulong pending = 0;
        int bits = 0;
        for (int j = 0; j < length; j++)
        {
            ulong residual = (ulong)(values[start + j] - slope * (long)j - low);
            pending |= residual << bits;
            bits += width;
            while (bits >= 8)
            {
                payload.WriteByte((byte)pending);
                pending >>= 8;
                bits -= 8;
            }
        }
        if (bits > 0)
            payload.WriteByte((byte)pending);
    }

    static long[] ToLongs(uint[] values)
    {
        var result = new long[values.Length];
        for (int i = 0; i < values.Length; i++)
            result[i] = values[i];
        return result;
    }

    static int SizeOf(List<Section> sections)
    {
        int total = 0;
        foreach (Section section in sections)
            total += Align(section.bytes.Length) + 4;
        return total;
    }

    static int Align(int length) => (length + 3) & ~3;

    [MethodImpl(MethodImplOptions.AggressiveInlining)]
    static void WriteU32(byte[] bytes, int at, uint value) => BitConverter.TryWriteBytes(bytes.AsSpan(at), value);
}
