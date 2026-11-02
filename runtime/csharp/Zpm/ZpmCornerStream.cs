// SPDX-License-Identifier: GPL-3.0-or-later
using System.Runtime.CompilerServices;
using System.Runtime.Intrinsics;
using System.Runtime.Intrinsics.X86;

namespace Zpm;

/// <summary>
/// One pair of stored values per corner, such as a normal or a UV. The file holds each distinct
/// pair once with corners reaching back to it, or one pair per vertex or per stored position with
/// the corners that disagree stored apart. Any corner reads directly. Allocates nothing.
/// </summary>
public unsafe struct ZpmCornerStream
{
    const int BlockShift = 6;
    const int WideFitValues = 4;
    const int WideFitSlack = 4;

    bool _isShared, _isByPosition, _usesSharedMap, _hasExceptions, _isBound;
    ZpmBackReferences _ids;
    ZpmColumn _a, _b;
    ZpmRankMask _exceptions;
    ZpmColumn _exceptionA, _exceptionB;
    ZpmColumn _fitAX, _fitAY, _fitAZ, _fitBX, _fitBY, _fitBZ;
    bool _hasFit;
    byte _fitShift;

    /// <summary>Returns false until a section has provided this stream.</summary>
    public readonly bool isBound => _isBound;

    /// <summary>Whether values are predicted from position, so reads need the vertex's grid steps.</summary>
    public readonly bool hasFit => _hasFit;

    /// <summary>Whether its map is the one on the first layer, which the caller must bind too.</summary>
    public readonly bool usesSharedMap => _usesSharedMap;

    /// <summary>Points at one section of the stream. Returns false for a role that does not exist.</summary>
    public bool TryBindRole(int role, byte* section, ZpmSectionFlags flags, uint cornerCount)
    {
        _isBound = true;
        switch ((ZpmStreamRole)role)
        {
            case ZpmStreamRole.RepeatMask:
                _isShared = true;
                _ids.BindMask(section, cornerCount);
                return true;
            case ZpmStreamRole.Distances:
                _ids.BindDistances(section, flags);
                return true;
            case ZpmStreamRole.ValuesA:
                _usesSharedMap = (flags & ZpmSectionFlags.SharedMap) != 0;
                _isShared |= _usesSharedMap;
                _a = ZpmColumn.Bind(section, flags);
                return true;
            case ZpmStreamRole.VertexA:
                _isByPosition = (flags & ZpmSectionFlags.ByPosition) != 0;
                _a = ZpmColumn.Bind(section, flags);
                return true;
            case ZpmStreamRole.ValuesB:
            case ZpmStreamRole.VertexB:
                _b = ZpmColumn.Bind(section, flags);
                return true;
            case ZpmStreamRole.ExceptionMask:
                _hasExceptions = true;
                _exceptions = ZpmRankMask.Bind(section, cornerCount);
                return true;
            case ZpmStreamRole.ExceptionA:
                _exceptionA = ZpmColumn.Bind(section, flags);
                return true;
            case ZpmStreamRole.ExceptionB:
                _exceptionB = ZpmColumn.Bind(section, flags);
                return true;
            case ZpmStreamRole.FitAX:
                _hasFit = true;
                _fitShift = (byte)((flags & ZpmSectionFlags.CoarseFit) != 0 ? ZpmLayout.FitCoarseShift : ZpmLayout.FitShift);
                _fitAX = ZpmColumn.Bind(section, flags);
                return true;
            case ZpmStreamRole.FitAY:
                _fitAY = ZpmColumn.Bind(section, flags);
                return true;
            case ZpmStreamRole.FitAZ:
                _fitAZ = ZpmColumn.Bind(section, flags);
                return true;
            case ZpmStreamRole.FitBX:
                _fitBX = ZpmColumn.Bind(section, flags);
                return true;
            case ZpmStreamRole.FitBY:
                _fitBY = ZpmColumn.Bind(section, flags);
                return true;
            case ZpmStreamRole.FitBZ:
                _fitBZ = ZpmColumn.Bind(section, flags);
                return true;
            default:
                return false;
        }
    }

    /// <summary>Gives a stream stored per position the vertices' position IDs, once all its sections are bound.</summary>
    public void UsePositionIds(in ZpmBackReferences positionIds)
    {
        if (_isByPosition)
            _ids = positionIds;
    }

    /// <summary>
    /// Returns one corner's value, adding its prediction when the stream has one. x, y and z are the
    /// vertex's position in grid steps. Costs up to six more column reads than GetPair.
    /// </summary>
    [MethodImpl(MethodImplOptions.AggressiveInlining)]
    public readonly void GetFittedPair(uint corner, uint vertex, long x, long y, long z, out uint a, out uint b)
    {
        if (_hasExceptions && _exceptions.IsSet(corner))
        {
            uint rank = _exceptions.GetSetRank(corner);
            a = _exceptionA.Get(rank);
            b = _exceptionB.Get(rank);
            return;
        }
        uint block = vertex >> BlockShift;
        a = _a.Get(vertex) + (uint)((x * (int)_fitAX.Get(block) + y * (int)_fitAY.Get(block) + z * (int)_fitAZ.Get(block)) >> _fitShift);
        b = _b.Get(vertex) + (uint)((x * (int)_fitBX.Get(block) + y * (int)_fitBY.Get(block) + z * (int)_fitBZ.Get(block)) >> _fitShift);
    }

    /// <summary>Returns one corner's stored pair. Needs the corner's vertex, which the view supplies.</summary>
    [MethodImpl(MethodImplOptions.AggressiveInlining)]
    public readonly void GetPair(uint corner, uint vertex, out uint a, out uint b)
    {
        if (_isShared)
        {
            uint id = _ids.Get(corner);
            a = _a.Get(id);
            b = _b.Get(id);
            return;
        }
        if (_hasExceptions && _exceptions.IsSet(corner))
        {
            uint rank = _exceptions.GetSetRank(corner);
            a = _exceptionA.Get(rank);
            b = _exceptionB.Get(rank);
            return;
        }
        uint slot = _isByPosition ? _ids.Get(vertex) : vertex;
        a = _a.Get(slot);
        b = _b.Get(slot);
    }

    /// <summary>Writes one pair per vertex at the start of the buffer.</summary>
    readonly void CopyVertexPairs(uint vertices, uint* pairs)
    {
        uint stored = _a.count;
        if (!_isByPosition)
            stored = vertices;
        _a.CopyTo(0, stored, pairs, 2);
        _b.CopyTo(0, stored, pairs + 1, 2);
        if (_isByPosition)
            _ids.ExpandAll(pairs, 2);
    }

    /// <summary>
    /// Fills the caller's buffer with every corner's stored pair: the fastest way to read them all.
    /// corners holds each corner's vertex ID, as GetCorners writes them.
    /// </summary>
    public readonly void CopyAllPairs(uint* corners, uint cornerCount, uint* pairs)
    {
        if (_isShared)
        {
            uint ids = _ids.idCount;
            _a.CopyTo(0, ids, pairs, 2);
            _b.CopyTo(0, ids, pairs + 1, 2);
            _ids.ExpandAll(pairs, 2);
            return;
        }
        if (TryExpandVertexPairs(corners, cornerCount, null, 0f, pairs))
            return;
        for (uint corner = 0; corner < cornerCount; corner++)
            GetPair(corner, corners[corner], out pairs[corner * 2], out pairs[corner * 2 + 1]);
    }

    /// <summary>
    /// Fills the caller's buffer with every corner's value, predictions included, the fastest way to
    /// read them all. positions holds XYZ floats and step the grid step, as GetVertices and gridStep give.
    /// </summary>
    public readonly void CopyAllValues(uint* corners, uint cornerCount, uint* positions, float step, uint* pairs)
    {
        if (!_hasFit)
        {
            CopyAllPairs(corners, cornerCount, pairs);
            return;
        }
        if (TryExpandVertexPairs(corners, cornerCount, positions, step, pairs))
            return;
        float inverse = 1f / step;
        for (uint corner = 0; corner < cornerCount; corner++)
        {
            uint vertex = corners[corner];
            float* position = (float*)(positions + vertex * 3);
            GetFittedPair(corner, vertex, (long)System.MathF.Round(position[0] * inverse), (long)System.MathF.Round(position[1] * inverse),
                (long)System.MathF.Round(position[2] * inverse), out pairs[corner * 2], out pairs[corner * 2 + 1]);
        }
    }

    /// <summary>
    /// Writes each vertex's pair at the start of the buffer, then spreads them out from the end. With
    /// vertices numbered by first use, a vertex's slot is never past the corners using it, so nothing
    /// is overwritten before it is read. Returns false, writing nothing, when that does not hold.
    /// </summary>
    readonly bool TryExpandVertexPairs(uint* corners, uint cornerCount, uint* positions, float step, uint* pairs)
    {
        uint vertices = _isByPosition ? _ids.count : _a.count;
        if (vertices > cornerCount)
            return false;
        for (uint corner = 0; corner < cornerCount; corner++)
        {
            if (corners[corner] > corner)
                return false;
        }
        CopyVertexPairs(vertices, pairs);
        if (_hasFit && positions != null)
            AddFit(vertices, positions, step, pairs);
        for (uint corner = cornerCount; corner-- > 0;)
        {
            ((ulong*)pairs)[corner] = ((ulong*)pairs)[corners[corner]];
        }
        if (_hasExceptions)
        {
            uint rank = 0;
            for (uint corner = _exceptions.GetNextSet(0); corner < cornerCount; corner = _exceptions.GetNextSet(corner + 1), rank++)
            {
                pairs[corner * 2] = _exceptionA.Get(rank);
                pairs[corner * 2 + 1] = _exceptionB.Get(rank);
            }
        }
        return true;
    }

    static bool ValidateFit(in ZpmColumn column, uint blocks, byte* limit) => column.count == blocks && column.ValidateLayout(limit);

    /// <summary>
    /// Fills the caller's buffer with one stored pair per vertex: the fast way to fill a vertex buffer.
    /// positions holds XYZ floats and step the grid step, used only when the stream predicts from
    /// position. Returns false when corners of a vertex can differ, which needs CopyAllPairs.
    /// </summary>
    public readonly bool TryCopyVertexPairs(uint vertexCount, uint* positions, float step, uint* pairs)
    {
        if (_isShared || _hasExceptions)
            return false;
        CopyVertexPairs(vertexCount, pairs);
        if (_hasFit)
            AddFit(vertexCount, positions, step, pairs);
        return true;
    }

    [SkipLocalsInit]
    readonly void AddFit(uint vertexCount, uint* positions, float step, uint* pairs)
    {
        float inverse = 1f / step;
        int* xs = stackalloc int[(1 << BlockShift) + WideFitSlack];
        int* ys = stackalloc int[(1 << BlockShift) + WideFitSlack];
        int* zs = stackalloc int[(1 << BlockShift) + WideFitSlack];
        for (uint vertex = 0; vertex < vertexCount;)
        {
            uint block = vertex >> BlockShift;
            uint end = System.Math.Min(vertexCount, (block + 1) << BlockShift);
            int ax = (int)_fitAX.Get(block), ay = (int)_fitAY.Get(block), az = (int)_fitAZ.Get(block);
            int bx = (int)_fitBX.Get(block), by = (int)_fitBY.Get(block), bz = (int)_fitBZ.Get(block);
            if ((ax | ay | az | bx | by | bz) == 0)
            {
                vertex = end;
                continue;
            }
            uint length = end - vertex;
            for (uint i = 0; i < length; i++)
            {
                float* position = (float*)(positions + (vertex + i) * 3);
                xs[i] = RoundToInt(position[0] * inverse);
                ys[i] = RoundToInt(position[1] * inverse);
                zs[i] = RoundToInt(position[2] * inverse);
            }
            AddPrediction(xs, ys, zs, length, ax, ay, az, _fitShift, pairs + vertex * 2);
            AddPrediction(xs, ys, zs, length, bx, by, bz, _fitShift, pairs + vertex * 2 + 1);
            vertex = end;
        }
    }

    /// <summary>Rounds halves to even, as MathF.Round does, in one instruction where SSE exists.</summary>
    [MethodImpl(MethodImplOptions.AggressiveInlining)]
    static int RoundToInt(float value) =>
        Sse.IsSupported ? Sse.ConvertToInt32(Vector128.CreateScalarUnsafe(value)) : (int)System.MathF.Round(value);

    /// <summary>Adds one part's prediction to every second value, four per step where AVX2 exists.</summary>
    static void AddPrediction(int* xs, int* ys, int* zs, uint length, int cx, int cy, int cz, byte shift, uint* values)
    {
        uint i = 0;
        if (Avx2.IsSupported)
        {
            Vector256<int> wideX = Vector256.Create(cx), wideY = Vector256.Create(cy), wideZ = Vector256.Create(cz);
            for (; i + WideFitValues <= length; i += WideFitValues)
            {
                // The shift is logical, which is safe: only the low 32 bits of the result are kept.
                Vector256<ulong> sum = Avx2.ShiftRightLogical(
                    Avx2.Multiply(Avx2.ConvertToVector256Int64(xs + i).AsInt32(), wideX)
                    + Avx2.Multiply(Avx2.ConvertToVector256Int64(ys + i).AsInt32(), wideY)
                    + Avx2.Multiply(Avx2.ConvertToVector256Int64(zs + i).AsInt32(), wideZ), shift).AsUInt64();
                values[i * 2] += (uint)sum.GetElement(0);
                values[i * 2 + 2] += (uint)sum.GetElement(1);
                values[i * 2 + 4] += (uint)sum.GetElement(2);
                values[i * 2 + 6] += (uint)sum.GetElement(3);
            }
        }
        for (; i < length; i++)
            values[i * 2] += (uint)(((long)xs[i] * cx + (long)ys[i] * cy + (long)zs[i] * cz) >> shift);
    }

    /// <summary>Checks the stream fits the file and every lookup stays in range.</summary>
    public readonly bool Validate(byte* limit, uint cornerCount, uint vertexCount)
    {
        if (!_a.ValidateLayout(limit) || !_b.ValidateLayout(limit) || _a.count != _b.count)
            return false;
        if (_isShared)
            return _ids.Validate(limit, cornerCount) && _ids.idCount == _a.count;
        if (_isByPosition ? _hasFit || _ids.count != vertexCount || _ids.idCount != _a.count : _a.count < vertexCount)
            return false;
        if (_hasFit)
        {
            uint blocks = (uint)(((ulong)_a.count + (1u << BlockShift) - 1) >> BlockShift);
            if (!ValidateFit(_fitAX, blocks, limit) || !ValidateFit(_fitAY, blocks, limit) || !ValidateFit(_fitAZ, blocks, limit)
                || !ValidateFit(_fitBX, blocks, limit) || !ValidateFit(_fitBY, blocks, limit) || !ValidateFit(_fitBZ, blocks, limit))
                return false;
        }
        if (!_hasExceptions)
            return true;
        uint exceptionCount = _exceptionA.count;
        return exceptionCount <= cornerCount && _exceptionB.count == exceptionCount
            && _exceptions.Validate(limit, cornerCount - exceptionCount)
            && _exceptionA.ValidateLayout(limit) && _exceptionB.ValidateLayout(limit);
    }
}
