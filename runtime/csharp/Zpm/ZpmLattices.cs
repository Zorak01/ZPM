// SPDX-License-Identifier: GPL-3.0-or-later
namespace Zpm;

/// <summary>Lattice tables and point columns. Groups and weights are in each lattice's value tree.</summary>
public unsafe struct ZpmLattices
{
    ZpmSceneTable<ZpmLatticeInfo> _lattices;
    ZpmFloatColumn _restX, _restY, _restZ, _deformX, _deformY, _deformZ, _weights;
    bool _hasPoints;

    /// <summary>How many lattices the file holds.</summary>
    public readonly uint latticeCount => _lattices.count;

    /// <summary>Points at one section, if it is a lattice kind. Returns whether it was, and sets isValid.</summary>
    public bool TryBindSection(ZpmSectionKind kind, ZpmSectionFlags flags, byte* section, long length, out bool isValid)
    {
        isValid = true;
        switch (kind)
        {
            case ZpmSectionKind.LatticeInfo: isValid = _lattices.TryBind(section, length); return true;
            case ZpmSectionKind.LatticeRestX: _restX.Bind(section, flags); _hasPoints = true; return true;
            case ZpmSectionKind.LatticeRestY: _restY.Bind(section, flags); return true;
            case ZpmSectionKind.LatticeRestZ: _restZ.Bind(section, flags); return true;
            case ZpmSectionKind.LatticeDeformX: _deformX.Bind(section, flags); return true;
            case ZpmSectionKind.LatticeDeformY: _deformY.Bind(section, flags); return true;
            case ZpmSectionKind.LatticeDeformZ: _deformZ.Bind(section, flags); return true;
            case ZpmSectionKind.LatticeWeights: _weights.Bind(section, flags); return true;
            default: return false;
        }
    }

    /// <summary>Returns one lattice's size and points. The index must be below latticeCount.</summary>
    public readonly ref readonly ZpmLatticeInfo GetLattice(uint lattice) => ref _lattices[lattice];

    /// <summary>Returns a point's rest position. Indices come from a lattice's firstPoint.</summary>
    public readonly ZpmNormal GetRestPoint(uint point) => new(_restX.Get(point), _restY.Get(point), _restZ.Get(point));

    /// <summary>Returns a point's deformed position.</summary>
    public readonly ZpmNormal GetDeformedPoint(uint point) => new(_deformX.Get(point), _deformY.Get(point), _deformZ.Get(point));

    /// <summary>Returns a point's soft-body weight.</summary>
    public readonly float GetWeight(uint point) => _weights.Get(point);

    /// <summary>Walks every lattice: only that proves each stays in range.</summary>
    public readonly bool Validate(byte* limit)
    {
        uint points = _hasPoints ? _restX.count : 0;
        if (_hasPoints && (!_restX.Validate(limit, points) || !_restY.Validate(limit, points) || !_restZ.Validate(limit, points)
            || !_deformX.Validate(limit, points) || !_deformY.Validate(limit, points) || !_deformZ.Validate(limit, points)
            || !_weights.Validate(limit, points)))
            return false;
        for (uint i = 0; i < _lattices.count; i++)
        {
            ref readonly ZpmLatticeInfo lattice = ref _lattices[i];
            if ((ulong)lattice.firstPoint + lattice.pointCount > points
                || (ulong)lattice.pointsU * lattice.pointsV * lattice.pointsW != lattice.pointCount)
                return false;
        }
        return true;
    }
}
