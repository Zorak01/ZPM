# SPDX-License-Identifier: GPL-3.0-or-later
"""Prove the C# reader on features real fixtures lack: slots, holes, chains and more.

Put the dotnet host on PATH and build the reader first. Writes only to a temp folder.
"""

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parents[1] / "tests"))

from run import READER, write_fixture, write_surface  # noqa: E402
from test_format import authored_mesh, holed_mesh  # noqa: E402
from zpm.format import decode, encode  # noqa: E402

_FUZZ_SEED = 1
_FUZZ_TRIALS = 3000


def _verify(name: str, source, dotnet: str, folder: Path) -> None:
    data = encode(source)
    decoded = decode(data)
    file_path = folder / f"{name}.zpm"
    file_path.write_bytes(data)
    write_fixture(decoded, folder / f"{name}.zms")
    write_surface(decoded, folder / f"{name}.surface")
    subprocess.run(
        [
            dotnet,
            str(READER),
            "verify",
            str(folder / f"{name}.zms"),
            str(file_path),
            str(folder / f"{name}.surface"),
        ],
        check=True,
    )
    fuzz = subprocess.check_output(
        [
            dotnet,
            str(READER),
            "fuzz",
            str(file_path),
            str(_FUZZ_SEED),
            str(_FUZZ_TRIALS),
        ]
    )
    print(f"PASS {name}: {len(data)} bytes, fuzz {json.loads(fuzz)}")


def main():
    dotnet = shutil.which("dotnet")
    if dotnet is None:
        raise SystemExit("Put the dotnet host on PATH")
    with tempfile.TemporaryDirectory() as directory:
        _verify("authored", authored_mesh(), dotnet, Path(directory))
        _verify("holed", holed_mesh(), dotnet, Path(directory))


if __name__ == "__main__":
    main()
