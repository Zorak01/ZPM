# SPDX-License-Identifier: GPL-3.0-or-later

"""Bounded, atomic file access for the one ZPM mesh layout; see direct.py."""

import os
import tempfile
from pathlib import Path

from . import direct
from .model import FormatError, Mesh

DEFAULT_MAX_BYTES = 512 * 1024 * 1024


def encode(mesh: Mesh) -> bytes:
    """Encode validated buffers; positions remain byte-for-byte exact."""
    return direct.encode(mesh)


def decode(data: bytes, *, max_bytes: int = DEFAULT_MAX_BYTES) -> Mesh:
    """Expand one file into a Mesh, with strict length, range and size checks.

    max_bytes limits both file size and retained expanded buffers. It is not
    a peak process-memory limit: expanding also retains the input bytes.
    """
    if len(data) > max_bytes:
        raise FormatError("File exceeds the configured size limit")
    return direct.decode(data, max_bytes)


def load(path: str | Path, *, max_bytes: int = DEFAULT_MAX_BYTES) -> Mesh:
    """Read a bounded file. Reject oversized files before allocating them."""
    with Path(path).open("rb") as stream:
        if os.fstat(stream.fileno()).st_size > max_bytes:
            raise FormatError("File exceeds the configured size limit")
        return decode(stream.read(max_bytes + 1), max_bytes=max_bytes)


def save(path: str | Path, mesh: Mesh) -> None:
    """Replace a file atomically after successful encoding, in the same directory."""
    packet = encode(mesh)
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
