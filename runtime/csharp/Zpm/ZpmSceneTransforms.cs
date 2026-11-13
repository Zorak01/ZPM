// SPDX-License-Identifier: GPL-3.0-or-later
using System;

namespace Zpm;

/// <summary>
/// Works out where things are: object world matrices, and each bone's rest, bind, pose and skinning
/// matrices. Allocates nothing. Evaluates parents of every type, transforms with their deltas, pose
/// channels and partial inheritance. It does not run constraints, drivers or modifiers, so a vertex
/// parent uses the mesh as stored. A bone keeping its parent's shear is only exact without shear.
/// </summary>
public unsafe readonly struct ZpmSceneTransforms
{
    const float ThetaSafe = 1e-3f;
    const float ThetaCritical = 1e-9f;
    static ReadOnlySpan<byte> SettingsName => new byte[] { (byte)'s', (byte)'e', (byte)'t', (byte)'t', (byte)'i', (byte)'n', (byte)'g', (byte)'s' };
    static ReadOnlySpan<byte> DeltaLocationName => new byte[] { (byte)'d', (byte)'e', (byte)'l', (byte)'t', (byte)'a', (byte)'_', (byte)'l', (byte)'o', (byte)'c', (byte)'a', (byte)'t', (byte)'i', (byte)'o', (byte)'n' };
    static ReadOnlySpan<byte> DeltaScaleName => new byte[] { (byte)'d', (byte)'e', (byte)'l', (byte)'t', (byte)'a', (byte)'_', (byte)'s', (byte)'c', (byte)'a', (byte)'l', (byte)'e' };
    static ReadOnlySpan<byte> DeltaQuaternionName => new byte[] { (byte)'d', (byte)'e', (byte)'l', (byte)'t', (byte)'a', (byte)'_', (byte)'r', (byte)'o', (byte)'t', (byte)'a', (byte)'t', (byte)'i', (byte)'o', (byte)'n', (byte)'_', (byte)'q', (byte)'u', (byte)'a', (byte)'t', (byte)'e', (byte)'r', (byte)'n', (byte)'i', (byte)'o', (byte)'n' };
    static ReadOnlySpan<byte> DeltaEulerName => new byte[] { (byte)'d', (byte)'e', (byte)'l', (byte)'t', (byte)'a', (byte)'_', (byte)'r', (byte)'o', (byte)'t', (byte)'a', (byte)'t', (byte)'i', (byte)'o', (byte)'n', (byte)'_', (byte)'e', (byte)'u', (byte)'l', (byte)'e', (byte)'r' };

    readonly ZpmSceneView _scene;

    public ZpmSceneTransforms(in ZpmSceneView scene) => _scene = scene;

    /// <summary>
    /// Returns a bone's rest matrix in armature space: its Y axis runs from head to tail, turned by roll.
    /// bone indexes the armature's bones. Costs a few square roots.
    /// </summary>
    public ZpmMatrix4 GetBoneRestMatrix(uint armature, uint bone)
    {
        ref readonly ZpmBone stored = ref _scene.GetBone(_scene.GetFirstBone(armature) + bone);
        ZpmVector3 axis = stored.tail.Minus(stored.head);
        float length = axis.GetLength();
        float x = axis.x / length, y = axis.y / length, z = axis.z / length;
        float theta = 1f + y;
        float thetaAlternate = x * x + z * z;
        float m00, m02, m10, m12, m20, m22, m01, m11, m21;
        if (theta > ThetaSafe || thetaAlternate > ThetaCritical)
        {
            if (theta <= ThetaSafe)
                theta = thetaAlternate * 0.5f + thetaAlternate * thetaAlternate * 0.125f;
            m00 = 1f - x * x / theta;
            m02 = -x * z / theta;
            m20 = -x * z / theta;
            m22 = 1f - z * z / theta;
            m10 = -x;
            m12 = -z;
            m01 = x;
            m11 = y;
            m21 = z;
        }
        else
        {
            m00 = -1f;
            m11 = -1f;
            m22 = 1f;
            m01 = m02 = m10 = m12 = m20 = m21 = 0f;
        }
        float s = MathF.Sin(stored.roll), c = MathF.Cos(stored.roll), t = 1f - c;
        float r00 = x * x * t + c, r01 = x * y * t - z * s, r02 = x * z * t + y * s;
        float r10 = y * x * t + z * s, r11 = y * y * t + c, r12 = y * z * t - x * s;
        float r20 = z * x * t - y * s, r21 = z * y * t + x * s, r22 = z * z * t + c;
        return new(
            new(r00 * m00 + r01 * m10 + r02 * m20, r00 * m01 + r01 * m11 + r02 * m21, r00 * m02 + r01 * m12 + r02 * m22, stored.head.x),
            new(r10 * m00 + r11 * m10 + r12 * m20, r10 * m01 + r11 * m11 + r12 * m21, r10 * m02 + r11 * m12 + r12 * m22, stored.head.y),
            new(r20 * m00 + r21 * m10 + r22 * m20, r20 * m01 + r21 * m11 + r22 * m21, r20 * m02 + r21 * m12 + r22 * m22, stored.head.z),
            new(0f, 0f, 0f, 1f));
    }

    /// <summary>Returns the inverse bind matrix a skinning shader multiplies by: the inverse of rest.</summary>
    public ZpmMatrix4 GetBoneInverseBindMatrix(uint armature, uint bone) => GetBoneRestMatrix(armature, bone).InvertAffine();

    /// <summary>Returns a bone's posed matrix in armature space, walking up its parents.</summary>
    public ZpmMatrix4 GetBonePoseMatrix(uint armatureObject, uint bone)
    {
        uint armature = _scene.GetObject(armatureObject).data;
        ref readonly ZpmBone stored = ref _scene.GetBone(_scene.GetFirstBone(armature) + bone);
        ZpmMatrix4 rest = GetBoneRestMatrix(armature, bone);
        ZpmMatrix4 local = GetChannelMatrix(armatureObject, bone, (stored.flags & ZpmBoneFlags.Connected) != 0);
        if (stored.parent == ZpmLayout.NoIndex)
            return rest.Multiply(local);
        ZpmMatrix4 parentRest = GetBoneRestMatrix(armature, stored.parent);
        ZpmMatrix4 offset = parentRest.InvertAffine().Multiply(rest);
        ZpmMatrix4 parentPose = GetBonePoseMatrix(armatureObject, stored.parent);
        bool usesRotation = (stored.flags & ZpmBoneFlags.InheritRotation) != 0;
        if (usesRotation && stored.inheritScale == ZpmInheritScale.Full)
            return parentPose.Multiply(offset).Multiply(local);
        ZpmMatrix4 inherited = usesRotation
            ? GetInheritedRotation(parentPose, stored.inheritScale)
            : GetInheritedScaleOnly(parentRest, parentPose, stored.inheritScale);
        if (stored.inheritScale == ZpmInheritScale.Average)
        {
            float average = MathF.Cbrt(MathF.Abs(parentPose.GetVolumeScale()));
            inherited = inherited.ScaleColumns(average, average, average);
        }
        ZpmMatrix4 rotationAndScale = inherited.Multiply(offset);
        if (stored.inheritScale == ZpmInheritScale.FixShear)
            rotationAndScale = rotationAndScale.WithoutShear();
        // Position always follows the parent's whole pose, whatever the bone inherits.
        ZpmVector3 position = parentPose.Multiply(offset).TransformPoint(local.GetPosition());
        return rotationAndScale.Multiply(local).WithPosition(position);
    }

    static ZpmMatrix4 GetInheritedRotation(in ZpmMatrix4 parentPose, ZpmInheritScale mode) => mode switch
    {
        ZpmInheritScale.None or ZpmInheritScale.NoneLegacy or ZpmInheritScale.Average => parentPose.WithoutScale(),
        ZpmInheritScale.Aligned => parentPose.WithoutShear(),
        _ => parentPose,
    };

    static ZpmMatrix4 GetInheritedScaleOnly(in ZpmMatrix4 parentRest, in ZpmMatrix4 parentPose, ZpmInheritScale mode)
    {
        if (mode is not (ZpmInheritScale.Full or ZpmInheritScale.FixShear or ZpmInheritScale.Aligned))
            return parentRest;
        ZpmVector3 scale = parentPose.GetColumnLengths();
        return parentRest.ScaleColumns(scale.x, scale.y, scale.z);
    }

    /// <summary>Returns what moves a skinned vertex in armature space: pose * inverse bind.</summary>
    public ZpmMatrix4 GetBoneSkinMatrix(uint armatureObject, uint bone) =>
        GetBonePoseMatrix(armatureObject, bone).Multiply(GetBoneInverseBindMatrix(_scene.GetObject(armatureObject).data, bone));

    /// <summary>A connected bone stays at its parent's tail, so its pose location does nothing.</summary>
    ZpmMatrix4 GetChannelMatrix(uint armatureObject, uint bone, bool isConnected)
    {
        ref readonly ZpmObject item = ref _scene.GetObject(armatureObject);
        for (uint i = 0; i < item.poseChannelCount; i++)
        {
            ref readonly ZpmPoseChannel channel = ref _scene.GetPoseChannel(item.firstPoseChannel + i);
            if (channel.bone == bone)
            {
                ZpmVector3 location = isConnected ? default : channel.location;
                return ZpmMatrix4.CreateTransform(location, channel.rotationMode, channel.rotation, channel.scale);
            }
        }
        return ZpmMatrix4.identity;
    }

    /// <summary>Returns an object's own transform with its deltas, before its parent. Deltas cost a few name lookups.</summary>
    public ZpmMatrix4 GetObjectLocalMatrix(uint obj)
    {
        ref readonly ZpmObject item = ref _scene.GetObject(obj);
        ZpmValueTree values = _scene.values;
        if (!values.TryGetRoot(ZpmResourceKind.Object, obj, out ZpmValueNode root) || !values.TryFindChild(root, SettingsName, out ZpmValueNode settings))
            return ZpmMatrix4.CreateTransform(item.location, item.rotationMode, item.rotation, item.scale);
        ZpmVector3 location = item.location;
        if (values.TryFindChild(settings, DeltaLocationName, out ZpmValueNode found))
            location = new(location.x + values.GetFloat(found, 0), location.y + values.GetFloat(found, 1), location.z + values.GetFloat(found, 2));
        ZpmVector3 scale = item.scale;
        if (values.TryFindChild(settings, DeltaScaleName, out found))
            scale = new(scale.x * values.GetFloat(found, 0), scale.y * values.GetFloat(found, 1), scale.z * values.GetFloat(found, 2));
        ZpmMatrix4 rotated = ZpmMatrix4.CreateTransform(default, item.rotationMode, item.rotation, scale);
        ZpmVector4 delta = default;
        if (TryGetDeltaRotation(values, settings, item.rotationMode, ref delta))
            rotated = ZpmMatrix4.CreateTransform(default, item.rotationMode, delta, new(1f, 1f, 1f)).Multiply(rotated);
        return rotated.WithPosition(location);
    }

    static bool TryGetDeltaRotation(in ZpmValueTree values, in ZpmValueNode settings, ZpmRotationMode mode, ref ZpmVector4 delta)
    {
        if (mode == ZpmRotationMode.AxisAngle)
            return false;
        ReadOnlySpan<byte> name = mode == ZpmRotationMode.Quaternion ? DeltaQuaternionName : DeltaEulerName;
        if (!values.TryFindChild(settings, name, out ZpmValueNode found))
            return false;
        delta = mode == ZpmRotationMode.Quaternion
            ? new(values.GetFloat(found, 0), values.GetFloat(found, 1), values.GetFloat(found, 2), values.GetFloat(found, 3))
            : new(values.GetFloat(found, 0), values.GetFloat(found, 1), values.GetFloat(found, 2), 0f);
        return true;
    }

    static bool TryGetVertex(in ZpmMeshView mesh, uint vertex, out ZpmVector3 position)
    {
        position = default;
        if (vertex >= mesh.vertexCount)
            return false;
        ZpmVertex stored = mesh.GetVertex(vertex);
        position = new(BitConverter.UInt32BitsToSingle(stored.xBits), BitConverter.UInt32BitsToSingle(stored.yBits), BitConverter.UInt32BitsToSingle(stored.zBits));
        return true;
    }

    /// <summary>Works out what a vertex parent contributes. Returns false when the mesh or vertex is missing.</summary>
    bool TryGetVertexParentMatrix(in ZpmObject item, in ZpmMatrix4 parentWorld, out ZpmMatrix4 result)
    {
        result = parentWorld;
        ref readonly ZpmObject parent = ref _scene.GetObject(item.parent);
        if (parent.type != ZpmObjectType.Mesh || !_scene.TryGetMesh(parent.data, out ZpmMeshView mesh)
            || !TryGetVertex(mesh, item.parentVertex0, out ZpmVector3 first))
            return false;
        if (item.parentType == ZpmParentType.Vertex)
        {
            // Blender takes only the vertex's place from a single-vertex parent, not its turn.
            result = ZpmMatrix4.identity.WithPosition(parentWorld.TransformPoint(first));
            return true;
        }
        if (!TryGetVertex(mesh, item.parentVertex1, out ZpmVector3 second) || !TryGetVertex(mesh, item.parentVertex2, out ZpmVector3 third))
            return false;
        result = parentWorld.Multiply(GetTriangleMatrix(first, second, third));
        return true;
    }

    /// <summary>Faces a matrix along a triangle's normal with X along its first edge, as Blender does.</summary>
    static ZpmMatrix4 GetTriangleMatrix(in ZpmVector3 v1, in ZpmVector3 v2, in ZpmVector3 v3)
    {
        ZpmVector3 a = v1.Minus(v2), b = v2.Minus(v3);
        float nx = a.y * b.z - a.z * b.y, ny = a.z * b.x - a.x * b.z, nz = a.x * b.y - a.y * b.x;
        float normalLength = MathF.Sqrt(nx * nx + ny * ny + nz * nz);
        nz = normalLength > 0f ? nz / normalLength : 1f;
        nx = normalLength > 0f ? nx / normalLength : 0f;
        ny = normalLength > 0f ? ny / normalLength : 0f;
        float axisLength = MathF.Sqrt(ny * ny + nx * nx);
        float ax = axisLength > 0f ? ny / axisLength : 1f, ay = axisLength > 0f ? -nx / axisLength : 0f;
        float half = -0.5f * MathF.Acos(Math.Clamp(nz, -1f, 1f));
        float qw = MathF.Cos(half), si = MathF.Sin(half), qx = ax * si, qy = ay * si;
        ZpmMatrix4 tilt = ZpmMatrix4.CreateTransform(default, ZpmRotationMode.Quaternion, new(qw, qx, qy, 0f), new(1f, 1f, 1f));
        ZpmVector3 edge = v2.Minus(v1);
        float ex = tilt.row0.x * edge.x + tilt.row1.x * edge.y + tilt.row2.x * edge.z;
        float ey = tilt.row0.y * edge.x + tilt.row1.y * edge.y + tilt.row2.y * edge.z;
        float spin = 0.5f * MathF.Atan2(ey, ex);
        float cw = MathF.Cos(spin), cz = MathF.Sin(spin);
        ZpmVector4 turn = new(qw * cw, qx * cw + qy * cz, qy * cw - qx * cz, qw * cz);
        var middle = new ZpmVector3((v1.x + v2.x + v3.x) / 3f, (v1.y + v2.y + v3.y) / 3f, (v1.z + v2.z + v3.z) / 3f);
        return ZpmMatrix4.CreateTransform(middle, ZpmRotationMode.Quaternion, turn, new(1f, 1f, 1f));
    }

    /// <summary>
    /// Fills the caller's buffer with every object's world matrix, one per object, parents first and in
    /// one pass. Returns false when a vertex parent's mesh or vertex is missing: that matrix is then
    /// written as if parented to the whole object.
    /// </summary>
    public bool GetWorldMatrices(ZpmMatrix4* destination)
    {
        bool isExact = true;
        for (uint at = 0; at < _scene.objectCount; at++)
        {
            uint index = _scene.GetObjectInOrder(at);
            ref readonly ZpmObject item = ref _scene.GetObject(index);
            ZpmMatrix4 local = GetObjectLocalMatrix(index);
            if (item.parent == ZpmLayout.NoIndex)
            {
                destination[index] = local;
                continue;
            }
            ZpmMatrix4 parent = destination[item.parent];
            if (item.parentType == ZpmParentType.Vertex || item.parentType == ZpmParentType.Vertex3)
            {
                ZpmMatrix4 whole = parent;
                isExact &= TryGetVertexParentMatrix(item, whole, out parent);
            }
            if (item.parentType == ZpmParentType.Bone && item.parentBone != ZpmLayout.NoIndex)
                parent = parent.Multiply(GetBoneTailMatrix(item.parent, item.parentBone));
            destination[index] = parent.Multiply(item.parentInverse).Multiply(local);
        }
        return isExact;
    }

    /// <summary>An object parented to a bone follows the bone's tail.</summary>
    ZpmMatrix4 GetBoneTailMatrix(uint armatureObject, uint bone)
    {
        uint armature = _scene.GetObject(armatureObject).data;
        ref readonly ZpmBone stored = ref _scene.GetBone(_scene.GetFirstBone(armature) + bone);
        return GetBonePoseMatrix(armatureObject, bone).Multiply(ZpmMatrix4.CreateTranslation(0f, stored.tail.Minus(stored.head).GetLength(), 0f));
    }
}
