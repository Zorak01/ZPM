// SPDX-License-Identifier: GPL-3.0-or-later
namespace Zpm;

/// <summary>Curve tables and control point columns. Any point is one call away.</summary>
public unsafe struct ZpmCurves
{
    ZpmSceneTable<ZpmCurveInfo> _curves;
    ZpmSceneTable<ZpmSpline> _splines;
    ZpmFloatColumn _x, _y, _z, _w, _radius, _tilt, _leftX, _leftY, _leftZ, _rightX, _rightY, _rightZ, _knots;
    ZpmColumn _handleTypes;
    bool _hasPoints, _hasHandles, _hasKnots;

    /// <summary>How many curves the file holds.</summary>
    public readonly uint curveCount => _curves.count;

    /// <summary>Whether any spline has Bezier handles.</summary>
    public readonly bool hasHandles => _hasHandles;

    /// <summary>Points at one section, if it is a curve kind. Returns whether it was, and sets isValid.</summary>
    public bool TryBindSection(ZpmSectionKind kind, ZpmSectionFlags flags, byte* section, long length, out bool isValid)
    {
        isValid = true;
        switch (kind)
        {
            case ZpmSectionKind.CurveInfo: isValid = _curves.TryBind(section, length); return true;
            case ZpmSectionKind.SplineInfo: isValid = _splines.TryBind(section, length); return true;
            case ZpmSectionKind.CurvePointX: _x.Bind(section, flags); _hasPoints = true; return true;
            case ZpmSectionKind.CurvePointY: _y.Bind(section, flags); return true;
            case ZpmSectionKind.CurvePointZ: _z.Bind(section, flags); return true;
            case ZpmSectionKind.CurvePointW: _w.Bind(section, flags); return true;
            case ZpmSectionKind.CurvePointRadius: _radius.Bind(section, flags); return true;
            case ZpmSectionKind.CurvePointTilt: _tilt.Bind(section, flags); return true;
            case ZpmSectionKind.CurveHandleLeftX: _leftX.Bind(section, flags); _hasHandles = true; return true;
            case ZpmSectionKind.CurveHandleLeftY: _leftY.Bind(section, flags); return true;
            case ZpmSectionKind.CurveHandleLeftZ: _leftZ.Bind(section, flags); return true;
            case ZpmSectionKind.CurveHandleRightX: _rightX.Bind(section, flags); return true;
            case ZpmSectionKind.CurveHandleRightY: _rightY.Bind(section, flags); return true;
            case ZpmSectionKind.CurveHandleRightZ: _rightZ.Bind(section, flags); return true;
            case ZpmSectionKind.CurveHandleTypes: _handleTypes = ZpmColumn.Bind(section, flags); return true;
            case ZpmSectionKind.CurveKnots: _knots.Bind(section, flags); _hasKnots = true; return true;
            default: return false;
        }
    }

    /// <summary>Returns one curve's spline range. The index must be below curveCount.</summary>
    public readonly ref readonly ZpmCurveInfo GetCurve(uint curve) => ref _curves[curve];

    /// <summary>Returns one spline. Indices come from a curve's firstSpline.</summary>
    public readonly ref readonly ZpmSpline GetSpline(uint spline) => ref _splines[spline];

    /// <summary>Returns one control point. Indices come from a spline's firstPoint.</summary>
    public readonly ZpmCurvePoint GetPoint(uint point) =>
        new(new ZpmColor(_x.Get(point), _y.Get(point), _z.Get(point), _w.Get(point)), _radius.Get(point), _tilt.Get(point));

    /// <summary>Returns a point's left handle. The file must have handles.</summary>
    public readonly ZpmNormal GetLeftHandle(uint point) => new(_leftX.Get(point), _leftY.Get(point), _leftZ.Get(point));

    /// <summary>Returns a point's right handle. The file must have handles.</summary>
    public readonly ZpmNormal GetRightHandle(uint point) => new(_rightX.Get(point), _rightY.Get(point), _rightZ.Get(point));

    /// <summary>Returns a stored knot. Indices come from a spline's firstKnot.</summary>
    public readonly float GetKnot(uint knot) => _knots.Get(knot);

    /// <summary>Walks every spline: only that proves each stays in range.</summary>
    public readonly bool Validate(byte* limit)
    {
        uint points = _hasPoints ? _x.count : 0;
        if (_hasPoints && (!_x.Validate(limit, points) || !_y.Validate(limit, points) || !_z.Validate(limit, points)
            || !_w.Validate(limit, points) || !_radius.Validate(limit, points) || !_tilt.Validate(limit, points)))
            return false;
        if (_hasHandles && (!_leftX.Validate(limit, points) || !_leftY.Validate(limit, points) || !_leftZ.Validate(limit, points)
            || !_rightX.Validate(limit, points) || !_rightY.Validate(limit, points) || !_rightZ.Validate(limit, points)
            || _handleTypes.count != (ulong)points * 2 || !_handleTypes.ValidateLayout(limit)))
            return false;
        uint knots = _hasKnots ? _knots.count : 0;
        if (_hasKnots && !_knots.Validate(limit, knots))
            return false;
        for (uint i = 0; i < _curves.count; i++)
        {
            if ((ulong)_curves[i].firstSpline + _curves[i].splineCount > _splines.count)
                return false;
        }
        for (uint i = 0; i < _splines.count; i++)
        {
            ref readonly ZpmSpline spline = ref _splines[i];
            if (spline.type > ZpmSplineType.Nurbs || (ulong)spline.firstPoint + spline.pointCount > points
                || (spline.type == ZpmSplineType.Bezier && !_hasHandles))
                return false;
            if (spline.firstKnot != ZpmLayout.NoIndex && (ulong)spline.firstKnot + spline.knotCount > knots)
                return false;
        }
        return true;
    }
}
