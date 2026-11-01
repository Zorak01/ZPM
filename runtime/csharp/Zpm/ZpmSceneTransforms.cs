// SPDX-License-Identifier: GPL-3.0-or-later
using System;

namespace Zpm;

/// <summary>
/// Works out where things are: object world matrices, and each bone's rest, bind, pose and skinning
/// matrices. Allocates nothing. Evaluates parents, transforms and pose channels. It does not run
/// constraints, drivers or modifiers, and bones with partial inheritance are treated as full.
/// </summary>
public unsafe readonly struct ZpmSceneTransforms
{
    const float ThetaSafe = 1e-3f;
    const float ThetaCritical = 1e-9f;

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
        ZpmMatrix4 offset = GetBoneRestMatrix(armature, stored.parent).InvertAffine().Multiply(rest);
        return GetBonePoseMatrix(armatureObject, stored.parent).Multiply(offset).Multiply(local);
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

    /// <summary>Returns an object's own transform, before its parent.</summary>
    public ZpmMatrix4 GetObjectLocalMatrix(uint obj)
    {
        ref readonly ZpmObject item = ref _scene.GetObject(obj);
        return ZpmMatrix4.CreateTransform(item.location, item.rotationMode, item.rotation, item.scale);
    }

    /// <summary>
    /// Fills the caller's buffer with every object's world matrix, one per object, parents first and in
    /// one pass. Returns false when any object has a vertex parent, which needs mesh data to place: its
    /// matrix is then written as if parented to the whole object. Delta transforms are not included.
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
            isExact &= item.parentType != ZpmParentType.Vertex && item.parentType != ZpmParentType.Vertex3;
            ZpmMatrix4 parent = destination[item.parent];
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
