# SPDX-License-Identifier: GPL-3.0-or-later
"""Audit tracked publication contents; this does not replace a secret scanner."""

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN_SUFFIXES = {
    ".mp3",
    ".mp4",
    ".wav",
    ".mov",
    ".mkv",
    ".flac",
    ".aac",
    ".docx",
    ".blend",
    ".blend1",
    ".key",
    ".pem",
    ".exe",
    ".dll",
}
FORBIDDEN_PARTS = {
    "local-work",
    "private",
    "test-results",
    "node_modules",
    "__pycache__",
    ".agents",
    ".codex",
    ".claude",
    ".skills",
}


def main():
    result = subprocess.check_output(["git", "ls-files", "-z"], cwd=ROOT).decode(
        "utf-8"
    )
    paths = [Path(name) for name in result.split("\0") if name]
    if not paths:
        raise ValueError("No tracked files to audit")
    for path in paths:
        if path.suffix.lower() in FORBIDDEN_SUFFIXES:
            raise ValueError(f"Non-public file tracked: {path}")
        if any(part.lower() in FORBIDDEN_PARTS for part in path.parts):
            raise ValueError(f"Private or generated directory tracked: {path}")
        if path.name in {"AGENTS.md", "AGENT-RULES.md", "SKILL.md", ".env"}:
            raise ValueError(f"Local instructions or settings tracked: {path}")
        absolute = ROOT / path
        if not absolute.is_file():
            continue
        try:
            text = absolute.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        if re.search(r"[A-Za-z]:[\\/]Users[\\/]", text):
            raise ValueError(f"Machine-specific personal path in {path}")
        if "-----BEGIN " + "PRIVATE KEY-----" in text:
            raise ValueError(f"Private key in {path}")
    print(f"Publication audit passed: {len(paths)} tracked files")


if __name__ == "__main__":
    main()
