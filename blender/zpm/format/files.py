# SPDX-License-Identifier: GPL-3.0-or-later

"""Bounded reads and atomic writes of ZPM files."""

import os
import tempfile
from pathlib import Path

from .decoder import decode
from .encoder import encode
from .layout import DEFAULT_MAX_BYTES
from .model import FormatError, Mesh


def load(path: str | Path, *, max_bytes: int = DEFAULT_MAX_BYTES) -> Mesh:
    """Read a bounded file. Reject oversized files before allocating them."""
    with Path(path).open("rb") as stream:
        if os.fstat(stream.fileno()).st_size > max_bytes:
            raise FormatError("File exceeds the configured size limit")
        return decode(stream.read(max_bytes + 1), max_bytes=max_bytes)


def save(path: str | Path, mesh: Mesh) -> None:
    """Replace a file atomically after successful encoding, in the same directory."""
    data = encode(mesh)
    destination = Path(path)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=destination.parent,
            prefix=f".{destination.name}.",
            delete=False,
        ) as stream:
            temporary = Path(stream.name)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
