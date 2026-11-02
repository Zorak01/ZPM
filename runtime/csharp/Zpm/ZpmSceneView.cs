// SPDX-License-Identifier: GPL-3.0-or-later
using System;
using System.Runtime.CompilerServices;

namespace Zpm;

/// <summary>
/// Reads a whole ZPM file straight from its bytes. Holds pointers and never allocates or copies.
/// Each mesh is a complete mesh file inside this one, opened with TryGetMesh. The bytes must stay
/// alive and pinned while the view is used.
/// </summary>
public unsafe struct ZpmSceneView
{
    byte* _data;
    byte* _limit;
    float _metersPerUnit;
    ZpmSpaceAxis _upAxis, _forwardAxis;
    ZpmSceneTable<ulong> _meshStarts, _payloadStarts;
    ZpmSceneTable<uint> _collectionParents, _collectionMemberStarts, _collectionMembers;
    ZpmSceneTable<uint> _armatureBoneStarts, _skinStarts, _skinBones;
    ZpmSceneTable<uint> _objectOrder, _objectChildStarts, _objectChildren, _boneOrder, _boneChildStarts, _boneChildren;
    ZpmSceneTable<ZpmObject> _objects;
    ZpmSceneTable<ZpmPoseChannel> _poseChannels;
    ZpmSceneTable<ZpmMaterial> _materials;
    ZpmSceneTable<ZpmTextureSlot> _textureSlots;
    ZpmSceneTable<ZpmSampler> _samplers;
    ZpmSceneTable<ZpmImage> _images;
    ZpmSceneTable<ZpmImageTile> _imageTiles;
    ZpmSceneTable<ZpmLight> _lights;
    ZpmSceneTable<ZpmBone> _bones;
    ZpmNames _objectNames, _collectionNames, _materialNames, _textureUvNames, _imageNames, _imageColorSpaces;
    ZpmNames _imagePaths, _lightNames, _armatureNames, _boneNames, _textureRoles;
    ZpmNames _cameraNames, _curveNames, _latticeNames, _clipNames, _extensionNames;
    ZpmValueTree _values;
    ZpmCurves _curves;
    ZpmLattices _lattices;
    ZpmAnimation _animation;
    ZpmSceneTable<ZpmExtension> _extensions;
    byte* _extensionPayloads;
    long _extensionPayloadsLength;
    byte* _meshes;
    byte* _payloads;
    long _meshesLength, _payloadsLength;

    /// <summary>How long one unit of the file's coordinates is, in meters.</summary>
    public readonly float metersPerUnit => _metersPerUnit;

    /// <summary>Which way is up in the file's coordinates.</summary>
    public readonly ZpmSpaceAxis upAxis => _upAxis;

    /// <summary>Which way is forward in the file's coordinates.</summary>
    public readonly ZpmSpaceAxis forwardAxis => _forwardAxis;

    /// <summary>How many meshes the file holds. Objects may share one.</summary>
    public readonly uint meshCount => _meshStarts.count - 1;

    public readonly uint objectCount => _objects.count;
    public readonly uint collectionCount => _collectionNames.count;
    public readonly uint materialCount => _materials.count;
    public readonly uint samplerCount => _samplers.count;
    public readonly uint imageCount => _images.count;
    public readonly uint lightCount => _lights.count;
    public readonly uint armatureCount => _armatureNames.count;
    public readonly uint cameraCount => _cameraNames.count;
    public readonly uint extensionCount => _extensions.count;

    /// <summary>Custom properties, modifiers, constraints, drivers and other named data, by owner.</summary>
    public readonly ZpmValueTree values => _values;

    /// <summary>Curve tables and control points.</summary>
    public readonly ZpmCurves curves => _curves;

    /// <summary>Lattice tables and points.</summary>
    public readonly ZpmLattices lattices => _lattices;

    /// <summary>Clips, tracks and keys.</summary>
    public readonly ZpmAnimation animation => _animation;

    /// <summary>Returns how many resources of one kind the file holds.</summary>
    public readonly uint GetResourceCount(ZpmResourceKind kind) => kind switch
    {
        ZpmResourceKind.Scene => 1,
        ZpmResourceKind.Object => objectCount,
        ZpmResourceKind.Mesh => meshCount,
        ZpmResourceKind.Material => materialCount,
        ZpmResourceKind.Image => imageCount,
        ZpmResourceKind.Light => lightCount,
        ZpmResourceKind.Armature => armatureCount,
        ZpmResourceKind.Bone => _bones.count,
        ZpmResourceKind.Collection => collectionCount,
        ZpmResourceKind.Curve => _curves.curveCount,
        ZpmResourceKind.Lattice => _lattices.latticeCount,
        ZpmResourceKind.Camera => cameraCount,
        _ => 0,
    };

    /// <summary>
    /// Reads the header and directory and points the view at each table. Returns false when the file
    /// is not a scene or is malformed. Call Validate before reading untrusted bytes.
    /// </summary>
    public static bool TryOpen(byte* data, long length, out ZpmSceneView view)
    {
        view = default;
        view._metersPerUnit = 1f;
        view._upAxis = ZpmSpaceAxis.Z;
        view._forwardAxis = ZpmSpaceAxis.NegativeY;
        if (length < ZpmLayout.FileHeaderBytes + ZpmLayout.TailBytes)
            return false;
        if (Unsafe.ReadUnaligned<uint>(data) != ZpmLayout.Magic
            || Unsafe.ReadUnaligned<ushort>(data + 4) != ZpmLayout.Version
            || Unsafe.ReadUnaligned<ulong>(data + 8) != (ulong)length)
            return false;
        uint sections = Unsafe.ReadUnaligned<ushort>(data + 6);
        bool isWide = length > uint.MaxValue;
        int entryBytes = isWide ? ZpmLayout.WideDirectoryEntryBytes : ZpmLayout.DirectoryEntryBytes;
        long directoryEnd = ZpmLayout.FileHeaderBytes + (long)sections * entryBytes;
        long limit = length - ZpmLayout.TailBytes;
        if (directoryEnd > limit)
            return false;
        view._data = data;
        view._limit = data + limit;
        long previous = directoryEnd;
        int previousKind = 0;
        for (uint i = 0; i < sections; i++)
        {
            byte* entry = data + ZpmLayout.FileHeaderBytes + i * entryBytes;
            int kind = Unsafe.ReadUnaligned<ushort>(entry);
            long offset = ReadSectionOffset(entry, isWide);
            long end = i + 1 < sections ? ReadSectionOffset(entry + entryBytes, isWide) : limit;
            if (kind <= previousKind || offset != previous || (offset & 3) != 0 || end < offset || end > limit)
                return false;
            if (!view.TryBindSection((ZpmSectionKind)kind, (ZpmSectionFlags)entry[2], data + offset, end - offset))
                return false;
            previous = end;
            previousKind = kind;
        }
        return previous == limit && view._meshStarts.count != 0;
    }

    static long ReadSectionOffset(byte* entry, bool isWide) =>
        isWide ? (long)Unsafe.ReadUnaligned<ulong>(entry + 4) : Unsafe.ReadUnaligned<uint>(entry + 4);

    bool TryBindSection(ZpmSectionKind kind, ZpmSectionFlags flags, byte* section, long length)
    {
        if ((int)kind >= ZpmLayout.OptionalKindStart)
            return true;
        if (_values.TryBindSection(kind, flags, section, length, out bool isValid)
            || _curves.TryBindSection(kind, flags, section, length, out isValid)
            || _lattices.TryBindSection(kind, flags, section, length, out isValid)
            || _animation.TryBindSection(kind, flags, section, length, out isValid))
            return isValid;
        switch (kind)
        {
            case ZpmSectionKind.Space: return length >= ZpmLayout.SpaceBytes && TryReadSpace(section);
            case ZpmSectionKind.SceneMeshStarts: return _meshStarts.TryBind(section, length);
            case ZpmSectionKind.SceneMeshes: _meshes = section; _meshesLength = length; return true;
            case ZpmSectionKind.ObjectNames: _objectNames = ZpmNames.Bind(section); return true;
            case ZpmSectionKind.ObjectInfo: return _objects.TryBind(section, length);
            case ZpmSectionKind.PoseChannels: return _poseChannels.TryBind(section, length);
            case ZpmSectionKind.CollectionNames: _collectionNames = ZpmNames.Bind(section); return true;
            case ZpmSectionKind.CollectionParents: return _collectionParents.TryBind(section, length);
            case ZpmSectionKind.CollectionMemberStarts: return _collectionMemberStarts.TryBind(section, length);
            case ZpmSectionKind.CollectionMembers: return _collectionMembers.TryBind(section, length);
            case ZpmSectionKind.MaterialNames: _materialNames = ZpmNames.Bind(section); return true;
            case ZpmSectionKind.MaterialInfo: return _materials.TryBind(section, length);
            case ZpmSectionKind.TextureSlots: return _textureSlots.TryBind(section, length);
            case ZpmSectionKind.TextureUvNames: _textureUvNames = ZpmNames.Bind(section); return true;
            case ZpmSectionKind.ObjectOrder: return _objectOrder.TryBind(section, length);
            case ZpmSectionKind.ObjectChildStarts: return _objectChildStarts.TryBind(section, length);
            case ZpmSectionKind.ObjectChildren: return _objectChildren.TryBind(section, length);
            case ZpmSectionKind.BoneOrder: return _boneOrder.TryBind(section, length);
            case ZpmSectionKind.BoneChildStarts: return _boneChildStarts.TryBind(section, length);
            case ZpmSectionKind.BoneChildren: return _boneChildren.TryBind(section, length);
            case ZpmSectionKind.TextureRoles: _textureRoles = ZpmNames.Bind(section); return true;
            case ZpmSectionKind.CameraNames: _cameraNames = ZpmNames.Bind(section); return true;
            case ZpmSectionKind.CurveNames: _curveNames = ZpmNames.Bind(section); return true;
            case ZpmSectionKind.LatticeNames: _latticeNames = ZpmNames.Bind(section); return true;
            case ZpmSectionKind.ClipNames: _clipNames = ZpmNames.Bind(section); return true;
            case ZpmSectionKind.ExtensionNames: _extensionNames = ZpmNames.Bind(section); return true;
            case ZpmSectionKind.ExtensionInfo: return _extensions.TryBind(section, length);
            case ZpmSectionKind.ExtensionPayloads: _extensionPayloads = section; _extensionPayloadsLength = length; return true;
            case ZpmSectionKind.Samplers: return _samplers.TryBind(section, length);
            case ZpmSectionKind.ImageNames: _imageNames = ZpmNames.Bind(section); return true;
            case ZpmSectionKind.ImageColorSpaces: _imageColorSpaces = ZpmNames.Bind(section); return true;
            case ZpmSectionKind.ImageInfo: return _images.TryBind(section, length);
            case ZpmSectionKind.ImageTiles: return _imageTiles.TryBind(section, length);
            case ZpmSectionKind.ImagePaths: _imagePaths = ZpmNames.Bind(section); return true;
            case ZpmSectionKind.ImagePayloadStarts: return _payloadStarts.TryBind(section, length);
            case ZpmSectionKind.ImagePayloads: _payloads = section; _payloadsLength = length; return true;
            case ZpmSectionKind.LightNames: _lightNames = ZpmNames.Bind(section); return true;
            case ZpmSectionKind.LightInfo: return _lights.TryBind(section, length);
            case ZpmSectionKind.ArmatureNames: _armatureNames = ZpmNames.Bind(section); return true;
            case ZpmSectionKind.ArmatureBoneStarts: return _armatureBoneStarts.TryBind(section, length);
            case ZpmSectionKind.BoneNames: _boneNames = ZpmNames.Bind(section); return true;
            case ZpmSectionKind.BoneInfo: return _bones.TryBind(section, length);
            case ZpmSectionKind.SkinStarts: return _skinStarts.TryBind(section, length);
            case ZpmSectionKind.SkinBones: return _skinBones.TryBind(section, length);
            default: return false;
        }
    }

    bool TryReadSpace(byte* space)
    {
        _metersPerUnit = Unsafe.ReadUnaligned<float>(space);
        _upAxis = (ZpmSpaceAxis)space[4];
        _forwardAxis = (ZpmSpaceAxis)space[5];
        return float.IsFinite(_metersPerUnit) && _metersPerUnit > 0
            && _upAxis <= ZpmSpaceAxis.NegativeZ && _forwardAxis <= ZpmSpaceAxis.NegativeZ
            && (int)_upAxis % ZpmLayout.AxisCount != (int)_forwardAxis % ZpmLayout.AxisCount
            && Unsafe.ReadUnaligned<ushort>(space + 6) == 0;
    }

    /// <summary>Opens one mesh, reading only its directory. Returns false when out of range or malformed.</summary>
    public readonly bool TryGetMesh(uint mesh, out ZpmMeshView view)
    {
        view = default;
        if (mesh >= meshCount)
            return false;
        ulong start = _meshStarts[mesh], end = _meshStarts[mesh + 1];
        return _meshes != null && end >= start && end <= (ulong)_meshesLength
            && ZpmMeshView.TryOpen(_meshes + start, (long)(end - start), out view);
    }

    /// <summary>
    /// Returns the object at one place in evaluation order, where each comes after its parent, so one
    /// pass places everything with no recursion. A file with no parents stores no table.
    /// </summary>
    public readonly uint GetObjectInOrder(uint position) => _objectOrder.count != 0 ? _objectOrder[position] : position;

    /// <summary>Returns how many children an object has.</summary>
    public readonly uint GetObjectChildCount(uint obj) =>
        _objectChildStarts.count != 0 ? _objectChildStarts[obj + 1] - _objectChildStarts[obj] : 0;

    /// <summary>Returns one child of an object.</summary>
    public readonly uint GetObjectChild(uint obj, uint child) => _objectChildren[_objectChildStarts[obj] + child];

    /// <summary>Returns the bone at one place in evaluation order, by its index across all armatures.</summary>
    public readonly uint GetBoneInOrder(uint position) => _boneOrder.count != 0 ? _boneOrder[position] : position;

    /// <summary>Returns how many children a bone has, by its index across all armatures.</summary>
    public readonly uint GetBoneChildCount(uint bone) =>
        _boneChildStarts.count != 0 ? _boneChildStarts[bone + 1] - _boneChildStarts[bone] : 0;

    /// <summary>Returns one child of a bone, by indices across all armatures.</summary>
    public readonly uint GetBoneChild(uint bone, uint child) => _boneChildren[_boneChildStarts[bone] + child];

    /// <summary>Returns one object in place. The index must be below objectCount.</summary>
    public readonly ref readonly ZpmObject GetObject(uint index) => ref _objects[index];

    /// <summary>Returns one pose channel in place. Indices come from an object's firstPoseChannel.</summary>
    public readonly ref readonly ZpmPoseChannel GetPoseChannel(uint index) => ref _poseChannels[index];

    /// <summary>Returns one material in place. The index must be below materialCount.</summary>
    public readonly ref readonly ZpmMaterial GetMaterial(uint index) => ref _materials[index];

    /// <summary>Returns one texture slot in place. Indices come from a material's firstSlot.</summary>
    public readonly ref readonly ZpmTextureSlot GetTextureSlot(uint index) => ref _textureSlots[index];

    /// <summary>Returns one sampler in place. The index must be below samplerCount.</summary>
    public readonly ref readonly ZpmSampler GetSampler(uint index) => ref _samplers[index];

    /// <summary>Returns one image in place. The index must be below imageCount.</summary>
    public readonly ref readonly ZpmImage GetImage(uint index) => ref _images[index];

    /// <summary>Returns one image tile in place. Indices come from an image's firstTile.</summary>
    public readonly ref readonly ZpmImageTile GetImageTile(uint index) => ref _imageTiles[index];

    /// <summary>Returns one light in place. The index must be below lightCount.</summary>
    public readonly ref readonly ZpmLight GetLight(uint index) => ref _lights[index];

    /// <summary>Returns one bone in place, by its index across all armatures. See GetFirstBone.</summary>
    public readonly ref readonly ZpmBone GetBone(uint index) => ref _bones[index];

    /// <summary>Returns where an armature's bones start.</summary>
    public readonly uint GetFirstBone(uint armature) => _armatureBoneStarts[armature];

    /// <summary>Returns how many bones an armature has.</summary>
    public readonly uint GetBoneCount(uint armature) => _armatureBoneStarts[armature + 1] - _armatureBoneStarts[armature];

    /// <summary>Returns how many objects a collection holds.</summary>
    public readonly uint GetCollectionMemberCount(uint collection) =>
        _collectionMemberStarts[collection + 1] - _collectionMemberStarts[collection];

    /// <summary>Returns one object in a collection.</summary>
    public readonly uint GetCollectionMember(uint collection, uint member) =>
        _collectionMembers[_collectionMemberStarts[collection] + member];

    /// <summary>Returns a collection's parent, or ZpmLayout.NoIndex.</summary>
    public readonly uint GetCollectionParent(uint collection) => _collectionParents[collection];

    /// <summary>Returns how many vertex groups an object maps to bones.</summary>
    public readonly uint GetSkinBoneCount(uint obj) => _skinStarts.count == 0 ? 0 : _skinStarts[obj + 1] - _skinStarts[obj];

    /// <summary>Returns the bone for one of an object's vertex groups, or ZpmLayout.NoIndex.</summary>
    public readonly uint GetSkinBone(uint obj, uint group) => _skinBones[_skinStarts[obj] + group];

    /// <summary>Finds a tile's embedded encoded bytes, in place. Returns false when it has none.</summary>
    public readonly bool TryGetTilePayload(uint tile, out byte* bytes, out ulong length)
    {
        bytes = null;
        length = 0;
        uint payload = _imageTiles[tile].payload;
        if (payload == ZpmLayout.NoIndex || payload + 1 >= _payloadStarts.count)
            return false;
        ulong start = _payloadStarts[payload];
        bytes = _payloads + start;
        length = _payloadStarts[payload + 1] - start;
        return true;
    }

    /// <summary>Finds what a texture slot feeds, as a UTF-8 name. Returns false when out of range.</summary>
    public readonly bool TryGetTextureRole(uint slot, out byte* utf8, out uint length) => _textureRoles.TryGet(slot, out utf8, out length);

    /// <summary>Returns one extension's facts. The index must be below extensionCount.</summary>
    public readonly ref readonly ZpmExtension GetExtension(uint index) => ref _extensions[index];

    /// <summary>Finds an extension's bytes, in place.</summary>
    public readonly void GetExtensionPayload(uint index, out byte* bytes, out uint length)
    {
        bytes = _extensionPayloads + _extensions[index].start;
        length = _extensions[index].length;
    }

    public readonly bool TryGetExtensionName(uint index, out byte* utf8, out uint length) => _extensionNames.TryGet(index, out utf8, out length);
    public readonly bool TryGetClipName(uint index, out byte* utf8, out uint length) => _clipNames.TryGet(index, out utf8, out length);
    public readonly bool TryGetCurveName(uint index, out byte* utf8, out uint length) => _curveNames.TryGet(index, out utf8, out length);
    public readonly bool TryGetLatticeName(uint index, out byte* utf8, out uint length) => _latticeNames.TryGet(index, out utf8, out length);
    public readonly bool TryGetCameraName(uint index, out byte* utf8, out uint length) => _cameraNames.TryGet(index, out utf8, out length);
    public readonly bool TryGetObjectName(uint index, out byte* utf8, out uint length) => _objectNames.TryGet(index, out utf8, out length);
    public readonly bool TryGetCollectionName(uint index, out byte* utf8, out uint length) => _collectionNames.TryGet(index, out utf8, out length);
    public readonly bool TryGetMaterialName(uint index, out byte* utf8, out uint length) => _materialNames.TryGet(index, out utf8, out length);
    public readonly bool TryGetTextureUvName(uint slot, out byte* utf8, out uint length) => _textureUvNames.TryGet(slot, out utf8, out length);
    public readonly bool TryGetImageName(uint index, out byte* utf8, out uint length) => _imageNames.TryGet(index, out utf8, out length);
    public readonly bool TryGetImageColorSpace(uint index, out byte* utf8, out uint length) => _imageColorSpaces.TryGet(index, out utf8, out length);
    public readonly bool TryGetTilePath(uint tile, out byte* utf8, out uint length) => _imagePaths.TryGet(tile, out utf8, out length);
    public readonly bool TryGetLightName(uint index, out byte* utf8, out uint length) => _lightNames.TryGet(index, out utf8, out length);
    public readonly bool TryGetArmatureName(uint index, out byte* utf8, out uint length) => _armatureNames.TryGet(index, out utf8, out length);
    public readonly bool TryGetBoneName(uint index, out byte* utf8, out uint length) => _boneNames.TryGet(index, out utf8, out length);

    /// <summary>
    /// Proves every link stays in range and every read stays in the file, opening and validating each
    /// mesh. Returns false on a malformed file. Walks every table.
    /// </summary>
    public readonly bool Validate() =>
        ValidateNames() && ValidateMeshes() && ValidateObjects() && ValidateCollections()
        && ValidateMaterials() && ValidateImages() && ValidateLights() && ValidateArmatures()
        && ValidateHierarchy(_objectOrder, _objectChildStarts, _objectChildren, _objects.count)
        && ValidateHierarchy(_boneOrder, _boneChildStarts, _boneChildren, _bones.count)
        && ValidateExtensions() && _curves.Validate(_limit) && _lattices.Validate(_limit)
        && _curveNames.count == _curves.curveCount && _latticeNames.count == _lattices.latticeCount
        && _clipNames.count == _animation.clipCount
        && _animation.Validate(_limit, this, _values.strings.count) && _values.Validate(_limit, objectCount, this);

    /// <summary>Proves hierarchy tables stay in range. A wrong order gives wrong numbers, never a bad read.</summary>
    static bool ValidateHierarchy(in ZpmSceneTable<uint> order, in ZpmSceneTable<uint> starts, in ZpmSceneTable<uint> children, uint count)
    {
        if (order.count == 0)
            return starts.count == 0 && children.count == 0;
        if (order.count != count || !ValidateStarts(starts, count, children.count))
            return false;
        for (uint i = 0; i < count; i++)
        {
            if (order[i] >= count)
                return false;
        }
        for (uint i = 0; i < children.count; i++)
        {
            if (children[i] >= count)
                return false;
        }
        return true;
    }

    /// <summary>A required extension is one this reader cannot know, so the file is refused.</summary>
    readonly bool ValidateExtensions()
    {
        if (_extensionNames.count != _extensions.count)
            return false;
        for (uint i = 0; i < _extensions.count; i++)
        {
            ref readonly ZpmExtension extension = ref _extensions[i];
            if ((extension.flags & 1) != 0 || extension.start + (long)extension.length > _extensionPayloadsLength)
                return false;
        }
        return true;
    }

    readonly bool ValidateNames() =>
        _objectNames.Validate(_limit) && _collectionNames.Validate(_limit) && _materialNames.Validate(_limit)
        && _textureUvNames.Validate(_limit) && _imageNames.Validate(_limit) && _imageColorSpaces.Validate(_limit)
        && _imagePaths.Validate(_limit) && _lightNames.Validate(_limit) && _armatureNames.Validate(_limit) && _boneNames.Validate(_limit)
        && _textureRoles.Validate(_limit) && _cameraNames.Validate(_limit) && _curveNames.Validate(_limit)
        && _latticeNames.Validate(_limit) && _clipNames.Validate(_limit) && _extensionNames.Validate(_limit);

    static bool ValidateStarts(in ZpmSceneTable<uint> starts, uint expectedGroups, long total)
    {
        if (starts.count == 0)
            return expectedGroups == 0 && total == 0;
        if (starts.count != (ulong)expectedGroups + 1 || starts[0] != 0)
            return false;
        for (uint i = 1; i < starts.count; i++)
        {
            if (starts[i] < starts[i - 1])
                return false;
        }
        return starts[starts.count - 1] <= total;
    }

    static bool ValidateByteStarts(in ZpmSceneTable<ulong> starts, long total)
    {
        if (starts.count == 0)
            return total == 0;
        if (starts[0] != 0)
            return false;
        for (uint i = 1; i < starts.count; i++)
        {
            if (starts[i] < starts[i - 1])
                return false;
        }
        return starts[starts.count - 1] <= (ulong)total;
    }

    readonly bool ValidateMeshes()
    {
        if (!ValidateByteStarts(_meshStarts, _meshesLength))
            return false;
        for (uint mesh = 0; mesh < meshCount; mesh++)
        {
            if (!TryGetMesh(mesh, out ZpmMeshView view) || !view.Validate())
                return false;
        }
        return true;
    }

    readonly uint GetDataCount(ZpmObjectType type) => type switch
    {
        ZpmObjectType.Mesh => meshCount,
        ZpmObjectType.Armature => armatureCount,
        ZpmObjectType.Light => lightCount,
        ZpmObjectType.Curve => _curves.curveCount,
        ZpmObjectType.Lattice => _lattices.latticeCount,
        ZpmObjectType.Camera => cameraCount,
        _ => 0,
    };

    static bool IsLink(uint index, uint count) => index == ZpmLayout.NoIndex || index < count;

    readonly bool ValidateObjects()
    {
        uint count = _objects.count;
        if (_objectNames.count != count || (_skinStarts.count != 0 && !ValidateStarts(_skinStarts, count, _skinBones.count)))
            return false;
        for (uint i = 0; i < count; i++)
        {
            ref readonly ZpmObject item = ref _objects[i];
            if (item.type > ZpmObjectType.Camera || item.parentType > ZpmParentType.Bone || item.rotationMode > ZpmRotationMode.Zyx
                || !IsLink(item.data, GetDataCount(item.type)) || !IsLink(item.parent, count)
                || !IsLink(item.skinArmature, count) || !IsLink(item.instanceCollection, collectionCount)
                || (ulong)item.firstPoseChannel + item.poseChannelCount > _poseChannels.count)
                return false;
            if (!ValidatePose(item) || !ValidateParentChain(i))
                return false;
        }
        return true;
    }

    readonly bool ValidatePose(in ZpmObject item)
    {
        uint bones = item.type == ZpmObjectType.Armature && item.data != ZpmLayout.NoIndex && _armatureBoneStarts.count != 0
            ? GetBoneCount(item.data)
            : 0;
        for (uint at = 0; at < item.poseChannelCount; at++)
        {
            ref readonly ZpmPoseChannel channel = ref _poseChannels[item.firstPoseChannel + at];
            if (channel.bone >= bones || channel.rotationMode > ZpmRotationMode.Zyx)
                return false;
        }
        return true;
    }

    readonly bool ValidateParentChain(uint start)
    {
        uint at = _objects[start].parent;
        for (uint steps = 0; at != ZpmLayout.NoIndex; steps++)
        {
            if (steps >= _objects.count)
                return false;
            at = _objects[at].parent;
        }
        return true;
    }

    readonly bool ValidateCollections()
    {
        uint count = collectionCount;
        if (_collectionParents.count != count || !ValidateStarts(_collectionMemberStarts, count, _collectionMembers.count))
            return false;
        for (uint i = 0; i < count; i++)
        {
            uint at = _collectionParents[i];
            for (uint steps = 0; at != ZpmLayout.NoIndex; steps++)
            {
                if (at >= count || steps >= count)
                    return false;
                at = _collectionParents[at];
            }
        }
        for (uint i = 0; i < _collectionMembers.count; i++)
        {
            if (_collectionMembers[i] >= _objects.count)
                return false;
        }
        return true;
    }

    readonly bool ValidateMaterials()
    {
        if (_materialNames.count != _materials.count || _textureUvNames.count != _textureSlots.count
            || _textureRoles.count != _textureSlots.count)
            return false;
        for (uint i = 0; i < _materials.count; i++)
        {
            ref readonly ZpmMaterial material = ref _materials[i];
            if (material.alphaMode > ZpmAlphaMode.Blend || (ulong)material.firstSlot + material.slotCount > _textureSlots.count)
                return false;
        }
        for (uint i = 0; i < _textureSlots.count; i++)
        {
            ref readonly ZpmTextureSlot slot = ref _textureSlots[i];
            if (slot.image >= _images.count || slot.sampler >= _samplers.count)
                return false;
        }
        for (uint i = 0; i < _samplers.count; i++)
        {
            ref readonly ZpmSampler sampler = ref _samplers[i];
            if (sampler.filter > ZpmSamplerFilter.Cubic || sampler.wrapU > ZpmSamplerWrap.Mirror || sampler.wrapV > ZpmSamplerWrap.Mirror)
                return false;
        }
        return true;
    }

    readonly bool ValidateImages()
    {
        if (_imageNames.count != _images.count || _imageColorSpaces.count != _images.count || _imagePaths.count != _imageTiles.count)
            return false;
        uint payloads = _payloadStarts.count == 0 ? 0 : _payloadStarts.count - 1;
        if (!ValidateByteStarts(_payloadStarts, _payloadsLength))
            return false;
        for (uint i = 0; i < _images.count; i++)
        {
            if ((ulong)_images[i].firstTile + _images[i].tileCount > _imageTiles.count)
                return false;
        }
        for (uint i = 0; i < _imageTiles.count; i++)
        {
            if (!IsLink(_imageTiles[i].payload, payloads))
                return false;
        }
        return true;
    }

    readonly bool ValidateLights()
    {
        if (_lightNames.count != _lights.count)
            return false;
        for (uint i = 0; i < _lights.count; i++)
        {
            ref readonly ZpmLight light = ref _lights[i];
            if (light.type > ZpmLightType.Area || light.unit > ZpmLightUnit.WattsPerSquareMeter || light.areaShape > ZpmAreaShape.Ellipse)
                return false;
        }
        return true;
    }

    readonly bool ValidateArmatures()
    {
        uint armatures = armatureCount;
        if (_boneNames.count != _bones.count || !ValidateStarts(_armatureBoneStarts, armatures, _bones.count))
            return false;
        for (uint armature = 0; armature < armatures; armature++)
        {
            uint first = GetFirstBone(armature), count = GetBoneCount(armature);
            for (uint bone = 0; bone < count; bone++)
            {
                uint at = _bones[first + bone].parent;
                for (uint steps = 0; at != ZpmLayout.NoIndex; steps++)
                {
                    if (at >= count || steps >= count)
                        return false;
                    at = _bones[first + at].parent;
                }
            }
        }
        for (uint obj = 0; obj < _objects.count; obj++)
        {
            ref readonly ZpmObject item = ref _objects[obj];
            if (item.parentBone == ZpmLayout.NoIndex)
                continue;
            if (item.parent == ZpmLayout.NoIndex)
                return false;
            ref readonly ZpmObject parent = ref _objects[item.parent];
            if (parent.type != ZpmObjectType.Armature || parent.data == ZpmLayout.NoIndex || item.parentBone >= GetBoneCount(parent.data))
                return false;
        }
        return true;
    }
}
