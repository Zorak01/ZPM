# SPDX-License-Identifier: GPL-3.0-or-later
"""Prove the C# reader's matrices match Blender's: python rig_math.py BLENDER_EXE

Put the dotnet host on PATH and build the reader first. Writes only to a temp folder.
"""

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
READER = ROOT.parent / "mesh-read/reference/bin/Release/net10.0/MeshRead.dll"
_TOLERANCE = 2e-4


def main():
    blender = sys.argv[1]
    dotnet = shutil.which("dotnet")
    with tempfile.TemporaryDirectory() as directory:
        folder = Path(directory)
        subprocess.run(
            [
                blender,
                "--background",
                "--factory-startup",
                "--python-exit-code",
                "1",
                "--python",
                str(ROOT / "rig_fixture.py"),
                "--",
                str(folder),
            ],
            check=True,
            capture_output=True,
        )
        expected = json.loads((folder / "expected.json").read_text())
        seen = json.loads(
            subprocess.check_output(
                [dotnet, str(READER), "dump-rig", str(folder / "rig.zpm")]
            )
        )
    worst = 0.0
    failed = []
    for name, matrix in expected.items():
        error = max(abs(a - b) for a, b in zip(matrix, seen[name], strict=True))
        worst = max(worst, error)
        if error > _TOLERANCE:
            failed.append(f"{name}: {error:.6f}")
    print(f"{len(expected)} matrices, largest difference {worst:.7f}")
    if failed:
        raise SystemExit("Differs from Blender:\n" + "\n".join(failed))
    print("PASS")


if __name__ == "__main__":
    main()
