# SPDX-License-Identifier: GPL-3.0-or-later

"""File layout constants. docs/mesh-core.md is the written contract for these."""

import struct
from enum import IntEnum, IntFlag

MAGIC = b"ZPM\0"
VERSION = 1
FILE_HEADER = struct.Struct("<4sHHI")
DIRECTORY_ENTRY = struct.Struct("<HBBI")
MESH_INFO = struct.Struct("<6IiI")
SPACE = struct.Struct("<fBBH")
OBJECT_INFO = struct.Struct("<9I26f2I")
POSE_CHANNEL = struct.Struct("<I10fI")
MATERIAL_INFO = struct.Struct("<12f4I")
TEXTURE_SLOT = struct.Struct("<2I5f")
VALUE_NODE = struct.Struct("<4I")
VALUE_OWNER = struct.Struct("<3I")
CURVE_INFO = struct.Struct("<2I")
SPLINE_INFO = struct.Struct("<8I")
LATTICE_INFO = struct.Struct("<5I")
CLIP_INFO = struct.Struct("<2I3f")
TRACK_INFO = struct.Struct("<13I")
EXTENSION_INFO = struct.Struct("<4I")
SAMPLER = struct.Struct("<3I")
IMAGE_INFO = struct.Struct("<6I")
IMAGE_TILE = struct.Struct("<2I")
LIGHT_INFO = struct.Struct("<2I4f2I6f")
BONE_INFO = struct.Struct("<2I10f")
INDEX = struct.Struct("<I")
SHAPE_INFO = struct.Struct("<2I5fI")
COLUMN_COUNT = struct.Struct("<I")
TAIL_BYTES = 32
ALIGNMENT = 4

BLOCK_SIZE = 64
BLOCKS_PER_GROUP = 8
MAX_WIDTH = 32
MAX_SLOPE = 127
MAX_UNIFORM_ARITY = 255
MAX_RELATIVE_ARITY = 4
MAX_LAYERS = 255
UINT32_MAX = 0xFFFFFFFF
SIGN_BIT = 0x80000000
AXIS_COUNT = 3
DEFAULT_MAX_BYTES = 512 * 1024 * 1024
NO_GRID = -(2**31)
MAX_GRID_VALUE = 1 << 24
NORMAL_BITS = 12
UV_GRID_BITS = 20
COLOR_GRID_BITS = 16
OFFSET_GRID_BITS = 16
UNIT_MAX = 65535
MIN_RING_CORNERS = 3
NO_SHAPE_TARGET = UINT32_MAX
NO_INDEX = UINT32_MAX
OPTIONAL_KIND_START = 0x8000
MAX_VALUE_DEPTH = 64


class MeshFlags(IntFlag):
    NONE = 0
    CUSTOM_NORMALS = 1
    NORMALS_FROM_FACES = 2


class SectionKind(IntEnum):
    """What a section holds. A directory lists sections by rising kind, then layer."""

    MESH_INFO = 1
    POSITION_REPEAT_MASK = 2
    POSITION_BACK_DISTANCES = 3
    SPACE = 4
    POSITION_GRID_X = 12
    POSITION_GRID_Y = 13
    POSITION_GRID_Z = 14
    VERTEX_CREASES = 15
    POSITION_BITS_X = 16
    POSITION_BITS_Y = 17
    POSITION_BITS_Z = 18
    POSITION_CONSTANT_X = 20
    POSITION_CONSTANT_Y = 21
    POSITION_CONSTANT_Z = 22
    POSITION_PALETTE_X = 24
    POSITION_PALETTE_Y = 25
    POSITION_PALETTE_Z = 26
    POSITION_INDICES_X = 28
    POSITION_INDICES_Y = 29
    POSITION_INDICES_Z = 30
    CORNER_IDS = 32
    CORNER_FIRST = 33
    CORNER_OFFSET_1 = 34
    CORNER_OFFSET_2 = 35
    CORNER_OFFSET_3 = 36
    CORNER_REPEAT_MASK = 37
    CORNER_BACK_DISTANCES = 38
    CORNER_SHARP = 39
    FACE_STARTS = 40
    FACE_FLAT = 41
    FACE_MATERIALS = 42
    MATERIAL_SLOT_NAMES = 43
    FACE_HOLE_STARTS = 44
    CORNER_SMOOTHING_GROUPS = 45
    EDGE_FIRST = 48
    EDGE_SPAN = 49
    EDGE_LOOSE_MASK = 50
    EDGE_LOOSE_FIRST = 51
    EDGE_LOOSE_SECOND = 52
    EDGE_CORNERS = 53
    EDGE_FLIPS = 54
    EDGE_FIRST_USE = 55
    EDGE_SHARP = 56
    EDGE_SEAM = 57
    EDGE_CREASES = 58
    CREASE_CHAIN_OFFSETS = 59
    CREASE_CHAIN_VERTICES = 60
    NORMAL_REPEAT_MASK = 64
    NORMAL_DISTANCES = 65
    NORMAL_VALUES_A = 66
    NORMAL_VALUES_B = 67
    NORMAL_VERTEX_A = 68
    NORMAL_VERTEX_B = 69
    NORMAL_EXCEPTION_MASK = 70
    NORMAL_EXCEPTION_A = 71
    NORMAL_EXCEPTION_B = 72
    UV_SEAMS = 78
    UV_NAMES = 79
    UV_REPEAT_MASK = 80
    UV_DISTANCES = 81
    UV_VALUES_A = 82
    UV_VALUES_B = 83
    UV_VERTEX_A = 84
    UV_VERTEX_B = 85
    UV_EXCEPTION_MASK = 86
    UV_EXCEPTION_A = 87
    UV_EXCEPTION_B = 88
    TANGENT_METHOD = 95
    TANGENT_REPEAT_MASK = 96
    TANGENT_DISTANCES = 97
    TANGENT_VALUES_A = 98
    TANGENT_VALUES_B = 99
    TANGENT_VERTEX_A = 100
    TANGENT_VERTEX_B = 101
    TANGENT_EXCEPTION_MASK = 102
    TANGENT_EXCEPTION_A = 103
    TANGENT_EXCEPTION_B = 104
    COLOR_NAMES = 110
    COLOR_FORMATS = 111
    COLOR_RG_REPEAT_MASK = 112
    COLOR_RG_DISTANCES = 113
    COLOR_RG_VALUES_A = 114
    COLOR_RG_VALUES_B = 115
    COLOR_RG_VERTEX_A = 116
    COLOR_RG_VERTEX_B = 117
    COLOR_RG_EXCEPTION_MASK = 118
    COLOR_RG_EXCEPTION_A = 119
    COLOR_RG_EXCEPTION_B = 120
    COLOR_BA_REPEAT_MASK = 128
    COLOR_BA_DISTANCES = 129
    COLOR_BA_VALUES_A = 130
    COLOR_BA_VALUES_B = 131
    COLOR_BA_VERTEX_A = 132
    COLOR_BA_VERTEX_B = 133
    COLOR_BA_EXCEPTION_MASK = 134
    COLOR_BA_EXCEPTION_A = 135
    COLOR_BA_EXCEPTION_B = 136
    GROUP_NAMES = 144
    WEIGHT_OFFSETS = 145
    WEIGHT_GROUPS = 146
    WEIGHT_VALUES = 147
    ATTRIBUTE_NAMES = 160
    ATTRIBUTE_FORMATS = 161
    ATTRIBUTE_VALUES = 162
    SHAPE_NAMES = 170
    SHAPE_GROUP_NAMES = 171
    SHAPE_INFO = 172
    SHAPE_STARTS = 173
    SHAPE_VERTICES = 174
    SHAPE_POSITION_X = 175
    SHAPE_POSITION_Y = 176
    SHAPE_POSITION_Z = 177
    SHAPE_NORMAL_X = 178
    SHAPE_NORMAL_Y = 179
    SHAPE_NORMAL_Z = 180
    SHAPE_TANGENT_X = 181
    SHAPE_TANGENT_Y = 182
    SHAPE_TANGENT_Z = 183
    SCENE_MESH_STARTS = 200
    SCENE_MESHES = 201
    OBJECT_NAMES = 202
    OBJECT_INFO = 203
    POSE_CHANNELS = 204
    COLLECTION_NAMES = 205
    COLLECTION_PARENTS = 206
    COLLECTION_MEMBER_STARTS = 207
    COLLECTION_MEMBERS = 208
    MATERIAL_NAMES = 209
    MATERIAL_INFO = 210
    TEXTURE_SLOTS = 211
    TEXTURE_UV_NAMES = 212
    SAMPLERS = 213
    IMAGE_NAMES = 214
    IMAGE_COLOR_SPACES = 215
    IMAGE_INFO = 216
    IMAGE_TILES = 217
    IMAGE_PATHS = 218
    IMAGE_PAYLOAD_STARTS = 219
    IMAGE_PAYLOADS = 220
    LIGHT_NAMES = 221
    LIGHT_INFO = 222
    ARMATURE_NAMES = 223
    ARMATURE_BONE_STARTS = 224
    BONE_NAMES = 225
    BONE_INFO = 226
    SKIN_STARTS = 227
    SKIN_BONES = 228
    TEXTURE_ROLES = 229
    VALUE_STRINGS = 230
    VALUE_NODES = 231
    VALUE_POOL = 232
    VALUE_OWNERS = 233
    CAMERA_NAMES = 234
    CURVE_NAMES = 235
    CURVE_INFO = 236
    SPLINE_INFO = 237
    CURVE_POINT_X = 238
    CURVE_POINT_Y = 239
    CURVE_POINT_Z = 240
    CURVE_POINT_W = 241
    CURVE_POINT_RADIUS = 242
    CURVE_POINT_TILT = 243
    CURVE_HANDLE_LEFT_X = 244
    CURVE_HANDLE_LEFT_Y = 245
    CURVE_HANDLE_LEFT_Z = 246
    CURVE_HANDLE_RIGHT_X = 247
    CURVE_HANDLE_RIGHT_Y = 248
    CURVE_HANDLE_RIGHT_Z = 249
    CURVE_HANDLE_TYPES = 250
    CURVE_KNOTS = 251
    LATTICE_NAMES = 252
    LATTICE_INFO = 253
    LATTICE_REST_X = 254
    LATTICE_REST_Y = 255
    LATTICE_REST_Z = 256
    LATTICE_DEFORM_X = 257
    LATTICE_DEFORM_Y = 258
    LATTICE_DEFORM_Z = 259
    LATTICE_WEIGHTS = 260
    CLIP_NAMES = 261
    CLIP_INFO = 262
    TRACK_INFO = 263
    TIME_STARTS = 264
    TIMES = 265
    KEY_VALUES = 266
    KEY_MODES = 267
    KEY_HANDLES = 268
    EXTENSION_NAMES = 269
    EXTENSION_INFO = 270
    EXTENSION_PAYLOADS = 271


class StreamRole(IntEnum):
    """A section's place in a corner stream, added to the stream's first kind."""

    REPEAT_MASK = 0
    DISTANCES = 1
    VALUES_A = 2
    VALUES_B = 3
    VERTEX_A = 4
    VERTEX_B = 5
    EXCEPTION_MASK = 6
    EXCEPTION_A = 7
    EXCEPTION_B = 8


class ColorFormat(IntFlag):
    """How one color layer is stored, one UInt32 per layer in COLOR_FORMATS."""

    NONE = 0
    CORNER = 1
    FLOAT = 2
    SRGB = 4


class SpaceAxis(IntEnum):
    """A direction along one axis of the file's coordinate system."""

    X = 0
    Y = 1
    Z = 2
    NEG_X = 3
    NEG_Y = 4
    NEG_Z = 5


class AttributeDomain(IntEnum):
    """What a custom attribute holds one element for."""

    POINT = 0
    EDGE = 1
    FACE = 2
    CORNER = 3
    MESH = 4


class AttributeType(IntEnum):
    """How one component of a custom attribute is stored. All are exact."""

    BOOL = 0
    INT8 = 1
    UINT8 = 2
    INT16 = 3
    UINT16 = 4
    INT32 = 5
    UINT32 = 6
    FLOAT32 = 7


class AttributeMeaning(IntEnum):
    """What a custom attribute's components mean together."""

    NONE = 0
    VECTOR = 1
    COLOR = 2
    QUATERNION = 3
    MATRIX = 4


class ShapeInterpolation(IntEnum):
    """How an absolute shape target blends with its neighbours in time."""

    LINEAR = 0
    CARDINAL = 1
    CATMULL_ROM = 2
    BSPLINE = 3


class ShapeFlags(IntFlag):
    """The low bits of a SHAPE_INFO entry; interpolation sits above them."""

    NONE = 0
    RELATIVE = 1
    MUTED = 2


SHAPE_INTERPOLATION_SHIFT = 8


class ObjectType(IntEnum):
    MESH = 0
    EMPTY = 1
    ARMATURE = 2
    LIGHT = 3
    CURVE = 4
    LATTICE = 5
    CAMERA = 6


class RotationMode(IntEnum):
    """What an object's four rotation values mean. Eulers leave the last one zero."""

    QUATERNION = 0
    AXIS_ANGLE = 1
    XYZ = 2
    XZY = 3
    YXZ = 4
    YZX = 5
    ZXY = 6
    ZYX = 7


class ObjectFlags(IntFlag):
    NONE = 0
    HIDDEN_IN_VIEWPORT = 1
    HIDDEN_IN_RENDER = 2
    NOT_SELECTABLE = 4


class SkinFlags(IntFlag):
    NONE = 0
    VERTEX_GROUPS = 1
    BONE_ENVELOPES = 2
    PRESERVE_VOLUME = 4


class AlphaMode(IntEnum):
    OPAQUE = 0
    CUTOFF = 1
    BLEND = 2


class ResourceKind(IntEnum):
    """What a value tree or animation track belongs to."""

    SCENE = 0
    OBJECT = 1
    MESH = 2
    MATERIAL = 3
    IMAGE = 4
    LIGHT = 5
    ARMATURE = 6
    BONE = 7
    COLLECTION = 8
    CURVE = 9
    LATTICE = 10
    CAMERA = 11


class ValueType(IntEnum):
    """What one node of a value tree holds."""

    BOOL = 0
    INT = 1
    FLOAT = 2
    STRING = 3
    REFERENCE = 4
    INT_ARRAY = 5
    FLOAT_ARRAY = 6
    DICT = 7
    LIST = 8


class SplineType(IntEnum):
    POLY = 0
    BEZIER = 1
    NURBS = 2


class SplineFlags(IntFlag):
    NONE = 0
    CYCLIC = 1
    ENDPOINT = 2
    BEZIER_KNOTS = 4


class HandleType(IntEnum):
    FREE = 0
    ALIGNED = 1
    VECTOR = 2
    AUTO = 3
    AUTO_CLAMPED = 4


class TrackValue(IntEnum):
    """What a track's keys hold. A reference key is checked at the time it applies."""

    FLOAT = 0
    INT = 1
    BOOL = 2
    STRING = 3
    REFERENCE = 4


class TrackFlags(IntFlag):
    NONE = 0
    BAKED = 1
    SPHERICAL_ROTATION = 2


class KeyMode(IntEnum):
    """How a key reaches the next one."""

    CONSTANT = 0
    LINEAR = 1
    BEZIER = 2
    SINE = 3
    QUAD = 4
    CUBIC = 5
    QUART = 6
    QUINT = 7
    EXPO = 8
    CIRC = 9
    BACK = 10
    BOUNCE = 11
    ELASTIC = 12


class ExtensionFlags(IntFlag):
    NONE = 0
    REQUIRED = 1


KEY_LEFT_HANDLE_SHIFT = 8
KEY_RIGHT_HANDLE_SHIFT = 16


class SamplerFilter(IntEnum):
    LINEAR = 0
    NEAREST = 1
    CUBIC = 2


class SamplerWrap(IntEnum):
    REPEAT = 0
    EXTEND = 1
    CLIP = 2
    MIRROR = 3


class LightType(IntEnum):
    POINT = 0
    SPOT = 1
    SUN = 2
    AREA = 3


class LightUnit(IntEnum):
    WATTS = 0
    WATTS_PER_SQUARE_METER = 1


class AreaShape(IntEnum):
    SQUARE = 0
    RECTANGLE = 1
    DISK = 2
    ELLIPSE = 3


class BoneFlags(IntFlag):
    """The low bits of a BONE_INFO entry."""

    NONE = 0
    CONNECTED = 1
    DEFORM = 2
    INHERIT_ROTATION = 4
    INHERIT_SCALE = 8


class SectionFlags(IntFlag):
    NONE = 0
    HAS_SLOPES = 1
    SIGNED_INDICES = 2


def align(length: int) -> int:
    return (length + ALIGNMENT - 1) & ~(ALIGNMENT - 1)


def bits_to_ordered(bits: int) -> int:
    """Map Float32 bits to an order that follows numeric value; exact for all bits."""
    return bits ^ UINT32_MAX if bits & SIGN_BIT else bits ^ SIGN_BIT


def ordered_to_bits(ordered: int) -> int:
    """Undo bits_to_ordered."""
    return ordered ^ SIGN_BIT if ordered & SIGN_BIT else ordered ^ UINT32_MAX


def float_bits(value: float) -> int:
    """Return a Float32's bits."""
    return struct.unpack("<I", struct.pack("<f", value))[0]


def float_from_bits(bits: int) -> float:
    """Return the Float32 these bits hold."""
    return struct.unpack("<f", struct.pack("<I", bits))[0]


def to_signed(value: int) -> int:
    """Read a stored UInt32 as the Int32 it holds."""
    return value - (1 << 32) if value & SIGN_BIT else value
