# SPDX-License-Identifier: GPL-3.0-or-later

"""The native reader of packed geometry, when one is built for this platform.

zpm_native.cpp reads connectivity, splits, positions, normals, UVs and face edges
exactly as codec/packed does, many times faster, and refuses anything it is not sure it
reads alike. tools/build_native.py builds it into bin/<platform>/. With no library, or
when it refuses, Python reads instead, so results never depend on which one ran.
"""

import ctypes
import platform
import sys
from array import array
from pathlib import Path
from typing import NamedTuple

_FOLDER = Path(__file__).with_name("bin")
_SYSTEMS = {"win32": "windows", "linux": "linux", "darwin": "macos"}
_MACHINES = {"amd64": "x64", "x86_64": "x64", "arm64": "arm64", "aarch64": "arm64"}
_FILE_NAMES = {
    "windows": "zpm_native.dll",
    "linux": "libzpm_native.so",
    "macos": "libzpm_native.dylib",
}
_REFUSED = 1
# What reading takes, inside the library and out, so a header claiming more than the
# caller allows is left to Python, which stops at the first missing byte instead.
_BYTES_PER_CORNER = 256
_BYTES_PER_UV_CORNER = 16
_BYTES_PER_VERTEX = 12
_PAIR = 2
_AXES = 3
_FLOAT = "f"
_INDEX = "I"
_SIGNED = "i"


class _Input(ctypes.Structure):
    _fields_ = [
        ("vertices", ctypes.c_uint32),
        ("faces", ctypes.c_uint32),
        ("corners", ctypes.c_uint32),
        ("arity", ctypes.c_uint32),
        ("unit", ctypes.c_double),
        ("connectivity", ctypes.c_void_p),
        ("connectivity_size", ctypes.c_uint64),
        ("splits", ctypes.c_void_p),
        ("splits_size", ctypes.c_uint64),
        ("positions", ctypes.c_void_p),
        ("positions_size", ctypes.c_uint64),
        ("normals", ctypes.c_void_p),
        ("normals_size", ctypes.c_uint64),
        ("uv_layers", ctypes.c_uint32),
        ("uvs", ctypes.c_void_p),
        ("uv_sizes", ctypes.c_void_p),
    ]


class _Output(ctypes.Structure):
    _fields_ = [
        ("welded", ctypes.c_void_p),
        ("face_starts", ctypes.c_void_p),
        ("corners", ctypes.c_void_p),
        ("positions", ctypes.c_void_p),
        ("rows", ctypes.c_void_p),
        ("normal_pairs", ctypes.c_void_p),
        ("normals", ctypes.c_void_p),
        ("uv_pairs", ctypes.c_void_p),
        ("uvs", ctypes.c_void_p),
        ("edges", ctypes.c_void_p),
        ("extras", ctypes.c_void_p),
        ("position_count", ctypes.c_uint32),
        ("edge_count", ctypes.c_uint32),
    ]


class MeshCounts(NamedTuple):
    """What MESH_INFO says a packed mesh holds."""

    vertices: int
    faces: int
    corners: int
    arity: int


class PackedStreams(NamedTuple):
    """A packed mesh's streams, each a whole section; normals may be empty."""

    connectivity: bytes
    splits: bytes
    positions: bytes
    normals: bytes
    uvs: list


class NativeGeometry(NamedTuple):
    """What the native reader returns, in walk order like Python's reader.

    rows holds each welded position's grid row; pairs are the stored integers.
    """

    welded: array
    face_starts: array
    corners: array
    positions: array
    rows: array
    position_count: int
    normal_pairs: array
    normals: array
    uv_pairs: list
    uvs: list
    edges: array


def platform_name() -> str | None:
    """Return this platform's folder name under bin/, or None if none is built for."""
    system = _SYSTEMS.get(sys.platform)
    machine = _MACHINES.get(platform.machine().lower())
    return f"{system}-{machine}" if system and machine else None


def library_path(name: str | None = None) -> Path | None:
    """Return where a platform's library goes, this one's by default."""
    name = name or platform_name()
    if name is None:
        return None
    return _FOLDER / name / _FILE_NAMES[name.split("-")[0]]


class _Library:
    """The loaded library, or None if there is none for this platform."""

    handle = None
    tried = False

    @classmethod
    def get(cls):
        if not cls.tried:
            cls.tried = True
            path = library_path()
            if path is not None and path.is_file():
                try:
                    handle = ctypes.CDLL(str(path))
                    handle.zpm_read_packed_geometry.restype = ctypes.c_int
                    handle.zpm_read_packed_geometry.argtypes = [
                        ctypes.POINTER(_Input),
                        ctypes.POINTER(_Output),
                    ]
                    cls.handle = handle
                except (OSError, AttributeError):
                    cls.handle = None
        return cls.handle


def is_available() -> bool:
    """Return whether the native reader can run here."""
    return _Library.get() is not None


def _zeros(typecode: str, count: int) -> array:
    return array(typecode, bytes(array(typecode).itemsize * count))


def _address(buffer) -> int:
    if isinstance(buffer, array):
        return buffer.buffer_info()[0]
    return ctypes.cast(ctypes.c_char_p(buffer), ctypes.c_void_p).value


def needed_bytes(counts: MeshCounts, uv_layers: int) -> int:
    """Return about how much memory reading a mesh this size natively takes."""
    per_corner = _BYTES_PER_CORNER + uv_layers * _BYTES_PER_UV_CORNER
    return counts.corners * per_corner + counts.vertices * _BYTES_PER_VERTEX


def read_packed_geometry(
    counts: MeshCounts, unit: float, streams: PackedStreams, max_bytes: int
) -> NativeGeometry | None:
    """Return the geometry the streams hold, or None when there is no native reader,
    it would need more than max_bytes, or it refuses: then read with Python, which
    says why or reads it."""
    handle = _Library.get()
    if handle is None or needed_bytes(counts, len(streams.uvs)) > max_bytes:
        return None
    corners = counts.corners
    layers = len(streams.uvs)
    has_normals = bool(streams.normals)
    result = NativeGeometry(
        _zeros(_INDEX, corners),
        _zeros(_INDEX, counts.faces + 1),
        _zeros(_INDEX, corners),
        _zeros(_FLOAT, counts.vertices * _AXES),
        _zeros(_SIGNED, corners * _AXES),
        0,
        _zeros(_SIGNED, corners * _PAIR if has_normals else 0),
        _zeros(_FLOAT, corners * _AXES if has_normals else 0),
        _zeros(_SIGNED, layers * corners * _PAIR),
        _zeros(_FLOAT, layers * corners * _PAIR),
        _zeros(_INDEX, corners * _PAIR),
    )
    uv_addresses = (ctypes.c_void_p * max(layers, 1))(*map(_address, streams.uvs))
    uv_sizes = (ctypes.c_uint64 * max(layers, 1))(*map(len, streams.uvs))
    request = _Input(
        counts.vertices,
        counts.faces,
        corners,
        counts.arity,
        unit,
        _address(streams.connectivity),
        len(streams.connectivity),
        _address(streams.splits),
        len(streams.splits),
        _address(streams.positions),
        len(streams.positions),
        _address(streams.normals) if has_normals else None,
        len(streams.normals),
        layers,
        ctypes.cast(uv_addresses, ctypes.c_void_p),
        ctypes.cast(uv_sizes, ctypes.c_void_p),
    )
    output = _Output(
        *(
            _address(buffer) if len(buffer) else None
            for buffer in (
                result.welded,
                result.face_starts,
                result.corners,
                result.positions,
                result.rows,
                result.normal_pairs,
                result.normals,
                result.uv_pairs,
                result.uvs,
                result.edges,
            )
        ),
        None,
        0,
        0,
    )
    if handle.zpm_read_packed_geometry(request, output) == _REFUSED:
        return None
    pair_count = corners * _PAIR
    return result._replace(
        rows=result.rows[: output.position_count * _AXES],
        position_count=output.position_count,
        uv_pairs=[
            result.uv_pairs[layer * pair_count : (layer + 1) * pair_count]
            for layer in range(layers)
        ],
        uvs=[
            result.uvs[layer * pair_count : (layer + 1) * pair_count]
            for layer in range(layers)
        ],
        edges=result.edges[: output.edge_count * _PAIR],
    )
