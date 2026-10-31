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
from test_format import authored_mesh, holed_mesh, sample_scene  # noqa: E402
from zpm.format import decode, encode, encode_scene  # noqa: E402
from zpm.format.codec.scene_objects import _bone_flags  # noqa: E402
from zpm.format.container.layout import TrackValue  # noqa: E402

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


def _verify_scene(dotnet: str, folder: Path) -> None:
    """Compare what the C# scene reader sees with the source, then fuzz it."""
    source = sample_scene()
    data = encode_scene(source)
    path = folder / "scene.zpm"
    path.write_bytes(data)
    seen = json.loads(
        subprocess.check_output([dotnet, str(READER), "dump-scene", str(path)])
    )
    expected = {
        "meters": source.space.meters_per_unit,
        "up": source.space.up,
        "forward": source.space.forward,
        "meshes": [mesh.vertex_count for mesh in source.meshes],
        "objects": [
            {
                "name": item.name,
                "type": item.object_type,
                "data": item.data,
                "parent": item.parent,
                "parentBone": item.parent_bone,
                "location": list(item.location),
                "poseChannels": len(item.pose),
                "skinArmature": item.skin_armature,
                "skinBones": len(item.skin_bones),
            }
            for item in source.objects
        ],
        "materials": [
            {
                "name": m.name,
                "metallic": m.metallic,
                "roughness": m.roughness,
                "slots": len(m.slots),
            }
            for m in source.materials
        ],
        "tiles": [
            {"number": t.number, "path": t.path, "bytes": len(t.payload)}
            for image in source.images
            for t in image.tiles
        ],
        "bones": [
            {
                "name": b.name,
                "parent": b.parent,
                "flags": int(_bone_flags(b)),
                "roll": b.roll,
            }
            for a in source.armatures
            for b in a.bones
        ],
        "lights": [light.power for light in source.lights],
        "members": [m for c in source.collections for m in c.members],
        "splinePoints": [
            s.positions[at * 3] + s.positions[at * 3 + 1] + s.weights[at] + s.radii[at]
            for c in source.curves
            for s in c.splines
            for at in range(s.point_count)
        ],
        "latticePoints": [
            lat.deformed[at * 3 + 1] + lat.softbody_weights[at]
            for lat in source.lattices
            for at in range(len(lat.softbody_weights))
        ],
        "keys": [
            value
            for clip in source.clips
            for t in clip.tracks
            for key, time in enumerate(t.times)
            for value in (
                (time, t.values[key * t.components])
                if t.value_type == TrackValue.FLOAT
                else (time,)
            )
        ],
        "author": source.properties["author"],
        "cameras": len(source.cameras),
        "extensions": len(source.extensions),
    }
    if seen != expected:
        raise SystemExit(f"Scene reader differs:\n{seen}\n{expected}")
    fuzz = subprocess.check_output(
        [
            dotnet,
            str(READER),
            "fuzz-scene",
            str(path),
            str(_FUZZ_SEED),
            str(_FUZZ_TRIALS),
        ]
    )
    print(f"PASS scene: {len(data)} bytes, fuzz {json.loads(fuzz)}")


def main():
    dotnet = shutil.which("dotnet")
    if dotnet is None:
        raise SystemExit("Put the dotnet host on PATH")
    with tempfile.TemporaryDirectory() as directory:
        _verify("authored", authored_mesh(), dotnet, Path(directory))
        _verify("holed", holed_mesh(), dotnet, Path(directory))
        _verify_scene(dotnet, Path(directory))


if __name__ == "__main__":
    main()
