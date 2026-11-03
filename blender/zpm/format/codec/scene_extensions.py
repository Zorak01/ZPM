# SPDX-License-Identifier: GPL-3.0-or-later

"""Named, versioned payloads, so a file can carry what the format does not define."""

from ..container.layout import EXTENSION_INFO, ExtensionFlags, SectionKind
from ..container.sections import Section, SectionReader
from ..errors import FormatError
from ..scene import Extension
from .scene_tables import names_section, read_name_list, read_records

_KNOWN_EXTENSIONS = frozenset()


def encode_extensions(extensions: list) -> list:
    if not extensions:
        return []
    records = bytearray()
    at = 0
    for extension in extensions:
        flags = (
            ExtensionFlags.REQUIRED if extension.is_required else ExtensionFlags.NONE
        )
        records += EXTENSION_INFO.pack(
            extension.version, flags, at, len(extension.payload)
        )
        at += len(extension.payload)
    return [
        *names_section(SectionKind.EXTENSION_NAMES, [e.name for e in extensions]),
        Section(SectionKind.EXTENSION_INFO, bytes(records)),
        Section(
            SectionKind.EXTENSION_PAYLOADS, b"".join(e.payload for e in extensions)
        ),
    ]


def read_extensions(sections: SectionReader) -> list:
    """Return every extension. Raises FormatError for a required one this code lacks."""
    names = read_name_list(sections, SectionKind.EXTENSION_NAMES)
    if not names:
        return []
    records = read_records(
        sections, SectionKind.EXTENSION_INFO, EXTENSION_INFO, len(names)
    )
    blob = sections.raw(SectionKind.EXTENSION_PAYLOADS)
    extensions = []
    for name, (version, flags, start, length) in zip(names, records, strict=True):
        is_required = bool(flags & ExtensionFlags.REQUIRED)
        if is_required and name not in _KNOWN_EXTENSIONS:
            raise FormatError(f"This file needs an extension this reader lacks: {name}")
        if start + length > len(blob):
            raise FormatError(f"Extension {name} runs past its section")
        extensions.append(
            Extension(name, version, is_required, blob[start : start + length])
        )
    return extensions
