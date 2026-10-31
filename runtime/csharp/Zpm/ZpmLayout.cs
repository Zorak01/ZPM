// SPDX-License-Identifier: GPL-3.0-or-later
using System;

namespace Zpm;

/// <summary>What a file section holds. A directory lists its sections in rising order.</summary>
public enum ZpmSectionKind : ushort
{
    MeshInfo = 1,
    PositionRepeatMask = 2,
    PositionBackDistances = 3,
    Space = 4,
    PositionGridX = 12,
    VertexCreases = 15,
    PositionBitsX = 16,
    PositionConstantX = 20,
    PositionPaletteX = 24,
    PositionIndicesX = 28,
    CornerIds = 32,
    CornerFirst = 33,
    CornerOffset1 = 34,
    CornerOffset2 = 35,
    CornerOffset3 = 36,
    CornerRepeatMask = 37,
    CornerBackDistances = 38,
    CornerSharp = 39,
    FaceStarts = 40,
    FaceFlat = 41,
    FaceMaterials = 42,
    MaterialSlotNames = 43,
    FaceHoleStarts = 44,
    CornerSmoothingGroups = 45,
    EdgeFirst = 48,
    EdgeSpan = 49,
    EdgeLooseMask = 50,
    EdgeLooseFirst = 51,
    EdgeLooseSecond = 52,
    EdgeCorners = 53,
    EdgeFlips = 54,
    EdgeFirstUse = 55,
    EdgeSharp = 56,
    EdgeSeam = 57,
    EdgeCreases = 58,
    CreaseChainOffsets = 59,
    CreaseChainVertices = 60,
    NormalStream = 64,
    NormalStreamLast = 72,
    UvSeams = 78,
    UvNames = 79,
    UvStream = 80,
    UvStreamLast = 88,
    TangentMethod = 95,
    TangentStream = 96,
    TangentStreamLast = 104,
    ColorNames = 110,
    ColorFormats = 111,
    ColorRedGreenStream = 112,
    ColorRedGreenStreamLast = 120,
    ColorBlueAlphaStream = 128,
    ColorBlueAlphaStreamLast = 136,
    GroupNames = 144,
    WeightOffsets = 145,
    WeightGroups = 146,
    WeightValues = 147,
    AttributeNames = 160,
    AttributeFormats = 161,
    AttributeValues = 162,
    ShapeNames = 170,
    ShapeGroupNames = 171,
    ShapeInfo = 172,
    ShapeStarts = 173,
    ShapeVertices = 174,
    ShapePositionX = 175,
    ShapePositionY = 176,
    ShapePositionZ = 177,
    ShapeNormalX = 178,
    ShapeNormalY = 179,
    ShapeNormalZ = 180,
    ShapeTangentX = 181,
    ShapeTangentY = 182,
    ShapeTangentZ = 183,
    SceneMeshStarts = 200,
    SceneMeshes = 201,
    ObjectNames = 202,
    ObjectInfo = 203,
    PoseChannels = 204,
    CollectionNames = 205,
    CollectionParents = 206,
    CollectionMemberStarts = 207,
    CollectionMembers = 208,
    MaterialNames = 209,
    MaterialInfo = 210,
    TextureSlots = 211,
    TextureUvNames = 212,
    Samplers = 213,
    ImageNames = 214,
    ImageColorSpaces = 215,
    ImageInfo = 216,
    ImageTiles = 217,
    ImagePaths = 218,
    ImagePayloadStarts = 219,
    ImagePayloads = 220,
    LightNames = 221,
    LightInfo = 222,
    ArmatureNames = 223,
    ArmatureBoneStarts = 224,
    BoneNames = 225,
    BoneInfo = 226,
    SkinStarts = 227,
    SkinBones = 228,
    TextureRoles = 229,
    ValueStrings = 230,
    ValueNodes = 231,
    ValuePool = 232,
    ValueOwners = 233,
    CameraNames = 234,
    CurveNames = 235,
    CurveInfo = 236,
    SplineInfo = 237,
    CurvePointX = 238,
    CurvePointY = 239,
    CurvePointZ = 240,
    CurvePointW = 241,
    CurvePointRadius = 242,
    CurvePointTilt = 243,
    CurveHandleLeftX = 244,
    CurveHandleLeftY = 245,
    CurveHandleLeftZ = 246,
    CurveHandleRightX = 247,
    CurveHandleRightY = 248,
    CurveHandleRightZ = 249,
    CurveHandleTypes = 250,
    CurveKnots = 251,
    LatticeNames = 252,
    LatticeInfo = 253,
    LatticeRestX = 254,
    LatticeRestY = 255,
    LatticeRestZ = 256,
    LatticeDeformX = 257,
    LatticeDeformY = 258,
    LatticeDeformZ = 259,
    LatticeWeights = 260,
    ClipNames = 261,
    ClipInfo = 262,
    TrackInfo = 263,
    TimeStarts = 264,
    Times = 265,
    KeyValues = 266,
    KeyModes = 267,
    KeyHandles = 268,
    ExtensionNames = 269,
    ExtensionInfo = 270,
    ExtensionPayloads = 271,
}

/// <summary>A section's place in a corner stream, added to the stream's first kind.</summary>
public enum ZpmStreamRole
{
    RepeatMask = 0,
    Distances = 1,
    ValuesA = 2,
    ValuesB = 3,
    VertexA = 4,
    VertexB = 5,
    ExceptionMask = 6,
    ExceptionA = 7,
    ExceptionB = 8,
}

/// <summary>How a section is stored.</summary>
[Flags]
public enum ZpmSectionFlags : byte
{
    None = 0,
    HasSlopes = 1,
    SignedIndices = 2,
}

/// <summary>Fixed byte layout of a file; docs/mesh-core.md is the written contract.</summary>
public static class ZpmLayout
{
    public const uint Magic = 0x004D505A; // "ZPM\0"
    public const ushort Version = 1;
    public const int FileHeaderBytes = 12;
    public const int DirectoryEntryBytes = 8;
    public const int MeshInfoBytes = 32;
    public const int SpaceBytes = 8;
    public const uint NoIndex = uint.MaxValue;
    public const int OptionalKindStart = 0x8000;
    public const int ShapeInfoBytes = 32;
    public const int ColumnCountBytes = 4;
    public const int ConstantBytes = 4;
    public const int TailBytes = 32;
    public const int AxisCount = 3;
    public const int MaxRelativeArity = 4;
    public const uint MinRingCorners = 3;
    public const int NoGrid = int.MinValue;
    public const float NormalMax = 4095f;
    public const float UvGridScale = 1048576f;
    public const float UnitMax = 65535f;
    public const uint UnitLimit = 65536;
    public const float OffsetGridScale = 65536f;
    public const uint TangentSignBit = 4096;
    public const uint CustomNormalsFlag = 1;
    public const uint NormalsFromFacesFlag = 2;
}
