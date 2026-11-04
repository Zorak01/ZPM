# SPDX-License-Identifier: GPL-3.0-or-later

"""Embedded images stored in their smallest exact encoding.

A baseline JPEG is re-coded with Huffman tables made for its own data: see
format/processing/jpeg_tables.py. A PNG is stored as lossless WebP when that is smaller
and every pixel matches.

Blender writes WebP losslessly at quality 100. The WebP is kept only when it decodes
to exactly the pixels the PNG decodes to, so a texture looks the same to every reader
that decodes WebP; only the encoding changes. WebP pixels are sRGB, so a PNG stays as
it is when it carries an ICC profile, or gamma or chromaticities with no sRGB chunk to
override them, or more than 8 bits a channel.
"""

import struct
import tempfile
from pathlib import Path

import bpy

from ..format.processing.jpeg_tables import optimize_tables

_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
_CHUNK_HEAD = struct.Struct(">I4s")
_CHUNK_CRC_BYTES = 4
_HEADER_CHUNK = b"IHDR"
_END_CHUNK = b"IEND"
# Bit depth sits after width and height in the header chunk.
_DEPTH_OFFSET = 8
_PLAIN_DEPTH = 8
# Chunks that change how a reader shows the pixels. An sRGB chunk overrides gamma and
# chromaticities, which Blender writes beside it; nothing overrides an ICC profile.
_PROFILE_CHUNK = b"iCCP"
_SRGB_CHUNK = b"sRGB"
_COLOUR_CHUNKS = frozenset({b"gAMA", b"cHRM"})
_LOSSLESS_QUALITY = 100
# Blender holds 8-bit images as four channels; the copy is made the same way.
_RGBA_CHANNELS = 4
_WEBP_SUFFIX = ".webp"
_PNG_SUFFIX = ".png"


def smallest_encoding(path: str, payload: bytes) -> tuple:
    """Return (path, payload) for an embedded image: a JPEG with its own Huffman
    tables, or a lossless WebP and its path, when smaller with the same pixels; else
    both unchanged."""
    if not _is_plain_png(payload):
        return path, optimize_tables(payload)
    with tempfile.TemporaryDirectory() as folder:
        source = Path(folder) / f"source{_PNG_SUFFIX}"
        target = Path(folder) / f"target{_WEBP_SUFFIX}"
        source.write_bytes(payload)
        original = bpy.data.images.load(str(source))
        encoded = None
        try:
            if original.channels != _RGBA_CHANNELS:
                return path, payload
            pixels = _pixels(original)
            _save_webp(original, pixels, target)
            encoded = bpy.data.images.load(str(target))
            same = _pixels(encoded) == pixels
            webp = target.read_bytes()
        finally:
            bpy.data.images.remove(original)
            if encoded is not None:
                bpy.data.images.remove(encoded)
    if not same or len(webp) >= len(payload):
        return path, payload
    return _webp_path(path), webp


def _is_plain_png(payload: bytes) -> bool:
    """Return whether a payload is an 8-bit PNG whose pixels read as sRGB."""
    if not payload.startswith(_PNG_MAGIC):
        return False
    kinds = set()
    at = len(_PNG_MAGIC)
    while at + _CHUNK_HEAD.size <= len(payload):
        length, kind = _CHUNK_HEAD.unpack_from(payload, at)
        body = at + _CHUNK_HEAD.size
        kinds.add(kind)
        if kind == _HEADER_CHUNK and payload[body + _DEPTH_OFFSET] != _PLAIN_DEPTH:
            return False
        if kind == _END_CHUNK:
            overridden = _SRGB_CHUNK in kinds or not kinds & _COLOUR_CHUNKS
            return _PROFILE_CHUNK not in kinds and overridden
        at = body + length + _CHUNK_CRC_BYTES
    return False


def _pixels(image: bpy.types.Image) -> list:
    values = [0.0] * (image.size[0] * image.size[1] * image.channels)
    image.pixels.foreach_get(values)
    return values


def _save_webp(original: bpy.types.Image, pixels: list, target: Path) -> None:
    """Write the pixels as lossless WebP, with no colour management on the way."""
    width, height = original.size
    copy = bpy.data.images.new("ZPM WebP", width, height, alpha=True)
    try:
        copy.colorspace_settings.name = original.colorspace_settings.name
        copy.pixels.foreach_set(pixels)
        copy.filepath_raw = str(target)
        copy.file_format = "WEBP"
        copy.save(quality=_LOSSLESS_QUALITY)
    finally:
        bpy.data.images.remove(copy)


def _webp_path(path: str) -> str:
    if path.lower().endswith(_PNG_SUFFIX):
        return path[: -len(_PNG_SUFFIX)] + _WEBP_SUFFIX
    return path
