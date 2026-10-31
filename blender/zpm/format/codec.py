# SPDX-License-Identifier: GPL-3.0-or-later

"""Exact, aligned mesh codec compatible with the mesh-core benchmark packets.

The ZMC1 test envelope is deliberately retained during format development.
Only RAW32 and COMPACT are supported here; experimental compression modes
are rejected rather than interpreted as a different encoding.
"""

import os
import struct
import sys
import tempfile
from array import array
from enum import StrEnum
from pathlib import Path

from .model import FormatError, Mesh

HEADER = struct.Struct("<4s15I")
DEFAULT_MAX_BYTES = 512 * 1024 * 1024


class Profile(StrEnum):
    RAW32 = "raw32"
    COMPACT = "compact"


def _bytes(buffer: array) -> bytes:
    if sys.byteorder == "little":
        return buffer.tobytes()
    little_endian = array(buffer.typecode, buffer)
    little_endian.byteswap()
    return little_endian.tobytes()


def _array(code: str, data: memoryview) -> array:
    result = array(code)
    result.frombytes(data)
    if sys.byteorder != "little":
        result.byteswap()
    return result


def encode(mesh: Mesh, *, profile: Profile = Profile.COMPACT) -> bytes:
    """Encode validated buffers; positions remain byte-for-byte exact.

    Compact omits uniform face counts and uses 16-bit IDs through 65,536
    vertices. Larger meshes use 32-bit IDs. Neither profile changes vertex IDs.
    """
    mesh.validate()
    try:
        profile = Profile(profile)
    except ValueError as error:
        raise FormatError(f"Unknown profile: {profile}") from error
    raw = profile is Profile.RAW32
    arity = mesh.arity
    if not arity and mesh.face_sizes:
        first = mesh.face_sizes[0]
        if all(size == first for size in mesh.face_sizes):
            arity = first
    if raw:
        sizes = array("I", mesh.face_sizes)
        if mesh.arity:
            sizes = array("I", [mesh.arity]) * mesh.face_count
        arity = 0
    else:
        sizes = array("I", mesh.face_sizes) if not arity else array("I")
    code = "I" if raw or mesh.vertex_count > 65536 else "H"
    streams = (
        _bytes(mesh.positions),
        _bytes(sizes),
        _bytes(array(code, mesh.corners)),
        _bytes(array(code, mesh.edges)),
    )
    values = (
        1,
        mesh.vertex_count,
        mesh.face_count,
        len(mesh.corners),
        mesh.edge_count,
        0,
        1024,
        0 if raw else 4,
        0 if raw else 3,
        arity,
        *(len(stream) for stream in streams),
        int(raw),
    )
    if any(value > 0xFFFFFFFF for value in values):
        raise FormatError("Mesh exceeds the packet's 32-bit size limit")
    return HEADER.pack(b"ZMC1", *values) + b"".join(streams)


def decode(data: bytes, *, max_bytes: int = DEFAULT_MAX_BYTES) -> Mesh:
    """Decode one packet with strict length, mode, range and allocation checks.

    max_bytes limits both packet size and retained decoded buffers. It is not
    a peak process-memory limit: decoding also retains the input packet.
    """
    if len(data) < HEADER.size or len(data) > max_bytes:
        raise FormatError("Packet is truncated or exceeds the configured size limit")
    (
        magic,
        version,
        vertices,
        faces,
        corners,
        edges,
        position_mode,
        block_size,
        face_mode,
        edge_mode,
        arity,
        plen,
        clen,
        flen,
        elen,
        raw,
    ) = HEADER.unpack_from(data)
    if magic != b"ZMC1" or version != 1:
        raise FormatError("Unsupported ZPM mesh packet or version")
    expected_modes = (0, 0) if raw == 1 else (4, 3)
    if raw not in (0, 1) or position_mode != 0:
        raise FormatError("This packet uses an experimental compression mode")
    if (face_mode, edge_mode) != expected_modes:
        raise FormatError("Only aligned RAW32 and COMPACT packets are supported")
    width = 4 if raw or vertices > 65536 else 2
    expected = (
        vertices * 12,
        0 if arity else faces * 4,
        corners * width,
        edges * 2 * width,
    )
    lengths = (plen, clen, flen, elen)
    if lengths != expected or HEADER.size + sum(lengths) != len(data):
        raise FormatError("Packet stream lengths do not match the header")
    if sum(lengths) > max_bytes:
        raise FormatError("Decoded mesh exceeds the configured size limit")
    if arity and (arity < 3 or faces * arity != corners or raw):
        raise FormatError("Invalid uniform face layout")
    if not arity and faces > corners // 3:
        raise FormatError("Invalid face count")
    view = memoryview(data)
    buffers = []
    offset = HEADER.size
    for code, length in zip(
        ("f", "I", "I" if width == 4 else "H", "I" if width == 4 else "H"),
        lengths,
        strict=True,
    ):
        buffers.append(_array(code, view[offset : offset + length]))
        offset += length
    mesh = Mesh(*buffers, arity=arity)
    mesh.validate()
    return mesh


def load(path: str | Path, *, max_bytes: int = DEFAULT_MAX_BYTES) -> Mesh:
    """Read a bounded packet. Reject oversized files before allocating them."""
    with Path(path).open("rb") as stream:
        if os.fstat(stream.fileno()).st_size > max_bytes:
            raise FormatError("File exceeds the configured size limit")
        return decode(stream.read(max_bytes + 1), max_bytes=max_bytes)


def save(path: str | Path, mesh: Mesh, *, profile: Profile = Profile.COMPACT) -> None:
    """Replace a file atomically after successful encoding, in the same directory."""
    packet = encode(mesh, profile=profile)
    destination = Path(path)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=destination.parent,
            prefix=f".{destination.name}.",
            delete=False,
        ) as stream:
            temporary = Path(stream.name)
            stream.write(packet)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
