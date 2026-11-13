// SPDX-License-Identifier: GPL-3.0-or-later
using System;
using System.Runtime.InteropServices;

namespace Zpm;

/// <summary>A 4x4 matrix, row by row. A point p transforms as M * p, with its position in the last column.</summary>
[StructLayout(LayoutKind.Sequential, Pack = 4)]
public readonly struct ZpmMatrix4
{
    public readonly ZpmVector4 row0, row1, row2, row3;

    public ZpmMatrix4(in ZpmVector4 row0, in ZpmVector4 row1, in ZpmVector4 row2, in ZpmVector4 row3)
    {
        this.row0 = row0;
        this.row1 = row1;
        this.row2 = row2;
        this.row3 = row3;
    }

    /// <summary>The matrix that changes nothing.</summary>
    public static ZpmMatrix4 identity => new(new(1f, 0f, 0f, 0f), new(0f, 1f, 0f, 0f), new(0f, 0f, 1f, 0f), new(0f, 0f, 0f, 1f));

    static ZpmVector4 Combine(in ZpmVector4 row, in ZpmMatrix4 right) => new(
        row.x * right.row0.x + row.y * right.row1.x + row.z * right.row2.x + row.w * right.row3.x,
        row.x * right.row0.y + row.y * right.row1.y + row.z * right.row2.y + row.w * right.row3.y,
        row.x * right.row0.z + row.y * right.row1.z + row.z * right.row2.z + row.w * right.row3.z,
        row.x * right.row0.w + row.y * right.row1.w + row.z * right.row2.w + row.w * right.row3.w);

    /// <summary>Returns this matrix applied after the other: this * right.</summary>
    public ZpmMatrix4 Multiply(in ZpmMatrix4 right) =>
        new(Combine(row0, right), Combine(row1, right), Combine(row2, right), Combine(row3, right));

    /// <summary>Returns the inverse of a matrix whose last row is 0, 0, 0, 1. Costs a 3x3 inverse.</summary>
    public ZpmMatrix4 InvertAffine()
    {
        float a = row1.y * row2.z - row1.z * row2.y;
        float b = row1.z * row2.x - row1.x * row2.z;
        float c = row1.x * row2.y - row1.y * row2.x;
        float scale = 1f / (row0.x * a + row0.y * b + row0.z * c);
        float m00 = a * scale, m10 = b * scale, m20 = c * scale;
        float m01 = (row0.z * row2.y - row0.y * row2.z) * scale;
        float m11 = (row0.x * row2.z - row0.z * row2.x) * scale;
        float m21 = (row0.y * row2.x - row0.x * row2.y) * scale;
        float m02 = (row0.y * row1.z - row0.z * row1.y) * scale;
        float m12 = (row0.z * row1.x - row0.x * row1.z) * scale;
        float m22 = (row0.x * row1.y - row0.y * row1.x) * scale;
        return new(
            new(m00, m01, m02, -(m00 * row0.w + m01 * row1.w + m02 * row2.w)),
            new(m10, m11, m12, -(m10 * row0.w + m11 * row1.w + m12 * row2.w)),
            new(m20, m21, m22, -(m20 * row0.w + m21 * row1.w + m22 * row2.w)),
            new(0f, 0f, 0f, 1f));
    }

    /// <summary>Returns the matrix with its first three columns scaled by these amounts.</summary>
    public ZpmMatrix4 ScaleColumns(float x, float y, float z) => new(
        new(row0.x * x, row0.y * y, row0.z * z, row0.w),
        new(row1.x * x, row1.y * y, row1.z * z, row1.w),
        new(row2.x * x, row2.y * y, row2.z * z, row2.w),
        row3);

    /// <summary>Returns the length of each of the first three columns: the scale along each axis.</summary>
    public ZpmVector3 GetColumnLengths() => new(
        MathF.Sqrt(row0.x * row0.x + row1.x * row1.x + row2.x * row2.x),
        MathF.Sqrt(row0.y * row0.y + row1.y * row1.y + row2.y * row2.y),
        MathF.Sqrt(row0.z * row0.z + row1.z * row1.z + row2.z * row2.z));

    /// <summary>Returns the determinant of the upper 3x3 part: how much it changes volume.</summary>
    public float GetVolumeScale() =>
        row0.x * (row1.y * row2.z - row1.z * row2.y)
        - row0.y * (row1.x * row2.z - row1.z * row2.x)
        + row0.z * (row1.x * row2.y - row1.y * row2.x);

    /// <summary>Returns the matrix with each of its first three columns at length 1.</summary>
    public ZpmMatrix4 WithoutScale()
    {
        ZpmVector3 lengths = GetColumnLengths();
        return ScaleColumns(1f / lengths.x, 1f / lengths.y, 1f / lengths.z);
    }

    /// <summary>
    /// Returns the matrix with shear removed: its Y axis stays, and the others become square to it,
    /// keeping their lengths. Costs a few square roots.
    /// </summary>
    public ZpmMatrix4 WithoutShear()
    {
        ZpmVector3 lengths = GetColumnLengths();
        float yx = row0.y / lengths.y, yy = row1.y / lengths.y, yz = row2.y / lengths.y;
        float dotX = row0.x * yx + row1.x * yy + row2.x * yz;
        float xx = row0.x - dotX * yx, xy = row1.x - dotX * yy, xz = row2.x - dotX * yz;
        float inverse = 1f / MathF.Sqrt(xx * xx + xy * xy + xz * xz);
        xx *= inverse;
        xy *= inverse;
        xz *= inverse;
        float zx = xy * yz - xz * yy, zy = xz * yx - xx * yz, zz = xx * yy - xy * yx;
        return new(
            new(xx * lengths.x, yx * lengths.y, zx * lengths.z, row0.w),
            new(xy * lengths.x, yy * lengths.y, zy * lengths.z, row1.w),
            new(xz * lengths.x, yz * lengths.y, zz * lengths.z, row2.w),
            row3);
    }

    /// <summary>Returns the point p moved by this matrix.</summary>
    public ZpmVector3 TransformPoint(in ZpmVector3 p) => new(
        row0.x * p.x + row0.y * p.y + row0.z * p.z + row0.w,
        row1.x * p.x + row1.y * p.y + row1.z * p.z + row1.w,
        row2.x * p.x + row2.y * p.y + row2.z * p.z + row2.w);

    /// <summary>Returns the matrix with its position replaced.</summary>
    public ZpmMatrix4 WithPosition(in ZpmVector3 p) =>
        new(new(row0.x, row0.y, row0.z, p.x), new(row1.x, row1.y, row1.z, p.y), new(row2.x, row2.y, row2.z, p.z), row3);

    /// <summary>Returns the matrix's position.</summary>
    public ZpmVector3 GetPosition() => new(row0.w, row1.w, row2.w);

    /// <summary>Returns a translation matrix.</summary>
    public static ZpmMatrix4 CreateTranslation(float x, float y, float z) =>
        new(new(1f, 0f, 0f, x), new(0f, 1f, 0f, y), new(0f, 0f, 1f, z), new(0f, 0f, 0f, 1f));

    /// <summary>Returns a transform: scale, then rotation, then position.</summary>
    public static ZpmMatrix4 CreateTransform(in ZpmVector3 location, ZpmRotationMode mode, in ZpmVector4 rotation, in ZpmVector3 scale)
    {
        GetRotationColumns(mode, rotation, out ZpmVector3 x, out ZpmVector3 y, out ZpmVector3 z);
        return new(
            new(x.x * scale.x, y.x * scale.y, z.x * scale.z, location.x),
            new(x.y * scale.x, y.y * scale.y, z.y * scale.z, location.y),
            new(x.z * scale.x, y.z * scale.y, z.z * scale.z, location.z),
            new(0f, 0f, 0f, 1f));
    }

    static void GetRotationColumns(ZpmRotationMode mode, in ZpmVector4 rotation, out ZpmVector3 x, out ZpmVector3 y, out ZpmVector3 z)
    {
        if (mode == ZpmRotationMode.Quaternion)
            FromQuaternion(rotation.x, rotation.y, rotation.z, rotation.w, out x, out y, out z);
        else if (mode == ZpmRotationMode.AxisAngle)
            FromAxisAngle(rotation, out x, out y, out z);
        else
            FromEuler(mode, rotation, out x, out y, out z);
    }

    /// <summary>Quaternions are stored W, X, Y, Z, and are normalized first.</summary>
    static void FromQuaternion(float w, float x, float y, float z, out ZpmVector3 col0, out ZpmVector3 col1, out ZpmVector3 col2)
    {
        float length = MathF.Sqrt(w * w + x * x + y * y + z * z);
        if (length == 0f)
        {
            col0 = new(1f, 0f, 0f);
            col1 = new(0f, 1f, 0f);
            col2 = new(0f, 0f, 1f);
            return;
        }
        w /= length;
        x /= length;
        y /= length;
        z /= length;
        col0 = new(1f - 2f * (y * y + z * z), 2f * (x * y + w * z), 2f * (x * z - w * y));
        col1 = new(2f * (x * y - w * z), 1f - 2f * (x * x + z * z), 2f * (y * z + w * x));
        col2 = new(2f * (x * z + w * y), 2f * (y * z - w * x), 1f - 2f * (x * x + y * y));
    }

    /// <summary>Axis-angle is stored as angle, then axis.</summary>
    static void FromAxisAngle(in ZpmVector4 rotation, out ZpmVector3 x, out ZpmVector3 y, out ZpmVector3 z)
    {
        float half = rotation.x * 0.5f;
        float sine = MathF.Sin(half);
        float length = MathF.Sqrt(rotation.y * rotation.y + rotation.z * rotation.z + rotation.w * rotation.w);
        float scale = length > 0f ? sine / length : 0f;
        FromQuaternion(MathF.Cos(half), rotation.y * scale, rotation.z * scale, rotation.w * scale, out x, out y, out z);
    }

    /// <summary>The order names the axis applied first, so Xyz is Rz * Ry * Rx.</summary>
    static void FromEuler(ZpmRotationMode mode, in ZpmVector4 rotation, out ZpmVector3 x, out ZpmVector3 y, out ZpmVector3 z)
    {
        ZpmMatrix4 rx = RotationX(rotation.x), ry = RotationY(rotation.y), rz = RotationZ(rotation.z);
        ZpmMatrix4 result = mode switch
        {
            ZpmRotationMode.Xyz => rz.Multiply(ry).Multiply(rx),
            ZpmRotationMode.Xzy => ry.Multiply(rz).Multiply(rx),
            ZpmRotationMode.Yxz => rz.Multiply(rx).Multiply(ry),
            ZpmRotationMode.Yzx => rx.Multiply(rz).Multiply(ry),
            ZpmRotationMode.Zxy => ry.Multiply(rx).Multiply(rz),
            _ => rx.Multiply(ry).Multiply(rz),
        };
        x = new(result.row0.x, result.row1.x, result.row2.x);
        y = new(result.row0.y, result.row1.y, result.row2.y);
        z = new(result.row0.z, result.row1.z, result.row2.z);
    }

    static ZpmMatrix4 RotationX(float angle)
    {
        float c = MathF.Cos(angle), s = MathF.Sin(angle);
        return new(new(1f, 0f, 0f, 0f), new(0f, c, -s, 0f), new(0f, s, c, 0f), new(0f, 0f, 0f, 1f));
    }

    static ZpmMatrix4 RotationY(float angle)
    {
        float c = MathF.Cos(angle), s = MathF.Sin(angle);
        return new(new(c, 0f, s, 0f), new(0f, 1f, 0f, 0f), new(-s, 0f, c, 0f), new(0f, 0f, 0f, 1f));
    }

    static ZpmMatrix4 RotationZ(float angle)
    {
        float c = MathF.Cos(angle), s = MathF.Sin(angle);
        return new(new(c, -s, 0f, 0f), new(s, c, 0f, 0f), new(0f, 0f, 1f, 0f), new(0f, 0f, 0f, 1f));
    }
}
