# SPDX-License-Identifier: GPL-3.0-or-later
"""Verify package, Blender manifest and optional release tag versions."""

import argparse
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag")
    args = parser.parse_args()
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())
    manifest = tomllib.loads((ROOT / "blender/zpm/blender_manifest.toml").read_text())
    version = project["project"]["version"]
    if manifest["version"] != version:
        raise ValueError("Package and Blender manifest versions differ")
    if args.tag and args.tag != f"v{version}":
        raise ValueError("Release tag does not match package version")
    print(f"Version verified: {version}")


if __name__ == "__main__":
    main()
