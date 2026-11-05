# SPDX-License-Identifier: GPL-3.0-or-later

"""Build the native packed-geometry reader for every platform the add-on ships to.

Uses zig's C++ compiler (pip install ziglang), which cross-compiles from any host.
Libraries land in blender/zpm/format/native/bin/<platform>/, where the add-on looks.
FMA contraction is off so floating point rounds exactly as Python's does.

    python tools/build_native.py [PLATFORM ...]
"""

import subprocess
import sys
from pathlib import Path

REPOSITORY = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY / "blender"))

from zpm.format.native import library_path  # noqa: E402

SOURCE = REPOSITORY / "blender/zpm/format/native/zpm_native.cpp"
TARGETS = {
    "windows-x64": "x86_64-windows-gnu",
    "linux-x64": "x86_64-linux-gnu.2.17",
    "linux-arm64": "aarch64-linux-gnu.2.17",
    "macos-x64": "x86_64-macos",
    "macos-arm64": "aarch64-macos",
}
FLAGS = ["-O2", "-std=c++17", "-shared", "-fPIC", "-ffp-contract=off", "-s"]


def build(name: str) -> Path:
    """Compile the reader for one platform and return the library's path."""
    output = library_path(name)
    output.parent.mkdir(parents=True, exist_ok=True)
    command = [sys.executable, "-m", "ziglang", "c++", *FLAGS, "-target", TARGETS[name]]
    built = subprocess.run(
        [*command, str(SOURCE), "-o", str(output)], capture_output=True, text=True
    )
    if built.returncode:
        raise SystemExit(f"{name} did not build:\n{built.stderr}")
    for leftover in output.parent.glob("*.pdb"):
        leftover.unlink()
    for leftover in output.parent.glob("*.lib"):
        leftover.unlink()
    return output


def main() -> None:
    for name in sys.argv[1:] or TARGETS:
        path = build(name)
        print(f"{name}: {path.relative_to(REPOSITORY)} {path.stat().st_size:,} bytes")


if __name__ == "__main__":
    main()
