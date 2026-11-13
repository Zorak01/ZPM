# SPDX-License-Identifier: GPL-3.0-or-later

"""Name tables: a count, then a length and UTF-8 bytes per name."""

import struct

from ..container.layout import align
from ..errors import FormatError


def encode_names(names: list) -> bytes:
    data = bytearray(struct.pack("<I", len(names)))
    for name in names:
        encoded = name.encode("utf-8")
        data += struct.pack("<I", len(encoded)) + encoded
    return bytes(data)


def names_length(section: bytes) -> int:
    """Return the bytes a name table uses, before padding."""
    count = struct.unpack_from("<I", section)[0]
    at = 4
    for _ in range(count):
        at += 4 + struct.unpack_from("<I", section, at)[0]
    return at


def read_names(section: bytes) -> list:
    """Return the names. Raises FormatError when the table is malformed."""
    try:
        count = struct.unpack_from("<I", section)[0]
        names = []
        at = 4
        for _ in range(count):
            length = struct.unpack_from("<I", section, at)[0]
            at += 4
            if at + length > len(section):
                raise FormatError("A name runs past its table")
            names.append(section[at : at + length].decode("utf-8"))
            at += length
    except (struct.error, UnicodeDecodeError) as error:
        raise FormatError("A name table is invalid") from error
    if any(section[at:]) or align(at) != len(section):
        raise FormatError("A name table has invalid padding")
    return names
