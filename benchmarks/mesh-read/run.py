# SPDX-License-Identifier: GPL-3.0-or-later
"""Measure reads of ZPM files next to the frozen mesh-core codec, on one host.

Published mesh-core timings came from another machine, so they are never compared here:
the frozen codec is rerun in the same session, with the same runtime.
"""

import argparse
import csv
import datetime as dt
import hashlib
import json
import os
import platform
import shutil
import struct
import subprocess
import sys
import time
from array import array
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent
REPOSITORY = ROOT.parents[1]
MESH_CORE = ROOT.parent / "mesh-core"
FROZEN = MESH_CORE / "reference" / "bin" / "Release" / "net9.0" / "MeshCore.dll"
READER = ROOT / "reference" / "bin" / "Release" / "net10.0" / "MeshRead.dll"
FIXTURE_HEADER = struct.Struct("<4s4I")
FIXTURE_MAGIC = b"ZMS1"
FUZZ_LIMIT_BYTES = 300_000
FUZZ_TRIALS = 1000
FUZZ_SEED = 72641

sys.path.insert(0, str(REPOSITORY / "blender"))

from zpm.format import (  # noqa: E402
    Mesh,
    decode,
    encode,
    map_file,
    same_geometry,
    snap_positions,
)


def run_json(arguments, **options):
    result = subprocess.run(
        arguments, check=True, capture_output=True, text=True, **options
    )
    return json.loads(result.stdout)


def load_fixture(path: Path) -> Mesh:
    data = path.read_bytes()
    _, vertices, faces, corners, _ = FIXTURE_HEADER.unpack_from(data)
    values = array("I")
    values.frombytes(data[FIXTURE_HEADER.size :])
    positions = array("f")
    positions.frombytes(values[: vertices * 3].tobytes())
    sizes_end = vertices * 3 + faces
    corners_end = sizes_end + corners
    return Mesh(
        positions,
        values[vertices * 3 : sizes_end],
        values[sizes_end : corners_end],
        values[corners_end:],
    )


def write_fixture(mesh: Mesh, path: Path) -> None:
    """Write a decoded mesh as a fixture, so the C# reader can be compared with it."""
    sizes = [mesh.arity] * mesh.face_count if mesh.arity else list(mesh.face_sizes)
    header = FIXTURE_HEADER.pack(
        FIXTURE_MAGIC,
        mesh.vertex_count,
        mesh.face_count,
        len(mesh.corners),
        mesh.edge_count,
    )
    path.write_bytes(
        header
        + mesh.positions.tobytes()
        + array("I", sizes).tobytes()
        + array("I", mesh.corners).tobytes()
        + array("I", mesh.edges).tobytes()
    )


def bytes_by_region(data: bytes) -> dict:
    totals = Counter()
    for region in map_file(data):
        totals[region.name.lower() + "_bytes"] += region.length
    return dict(totals)


def collect_fixtures(private_folder: Path | None) -> list:
    fixtures = []
    manifest = json.loads((MESH_CORE / "fixtures/manifest.json").read_text())
    for fixture in manifest["fixtures"]:
        path = MESH_CORE / "fixtures" / fixture["file"]
        if hashlib.sha256(path.read_bytes()).hexdigest() != fixture["sha256"]:
            raise ValueError(f"Fixture hash mismatch: {fixture['file']}")
        fixtures.append((fixture["label"], fixture["kind"], path))
    if private_folder:
        for path in sorted(private_folder.glob("*.zms")):
            fixtures.append((path.stem, "real Blender mesh (private)", path))
    return fixtures


def measure_frozen(dotnet, env, path, packets, common) -> list:
    frozen = run_json([dotnet, str(FROZEN), "encode", str(path), str(packets)], env=env)
    smallest = min(frozen["variants"], key=lambda variant: variant["bytes"])
    rows = []
    for variant in frozen["variants"]:
        name = variant["name"]
        if name not in ("raw32", "compact16", smallest["name"]):
            continue
        timing = run_json([dotnet, str(FROZEN), "measure", variant["packet"]], env=env)
        rows.append(
            {
                **common,
                "method": name,
                "reader": "frozen full decode",
                "stored_bytes": variant["bytes"],
                "is_old_smallest": name == smallest["name"],
                "full_read_us": timing["reuse_decode_ms"] * 1000,
                "first_read_us": timing["reuse_decode_ms"] * 1000,
                "allocated_bytes": timing["reuse_allocated_bytes"],
            }
        )
        if name in ("raw32", "compact16"):
            view = run_json([dotnet, str(READER), "measure-view", variant["packet"]])
            rows.append(
                {
                    **common,
                    "method": name,
                    "reader": "in-place view",
                    "stored_bytes": variant["bytes"],
                    "full_read_us": view["read_all_ns"] / 1000,
                    "first_read_us": (view["open_ns"] + view["random_face_ns"]) / 1000,
                    **view,
                }
            )
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--private-fixtures", type=Path)
    args = parser.parse_args()
    dotnet = shutil.which("dotnet")
    if dotnet is None:
        parser.error("Put the dotnet host on PATH")
    # The frozen build targets net9.0; rolling forward runs it on this same runtime.
    frozen_env = {**os.environ, "DOTNET_ROLL_FORWARD": "Major"}

    started = dt.datetime.now(dt.UTC)
    clock = time.perf_counter()
    run_id = started.strftime("%Y%m%dT%H%M%SZ")
    destination = ROOT / "runs" / run_id
    destination.mkdir(parents=True)
    rows = []
    fuzz = []
    for label, kind, path in collect_fixtures(args.private_fixtures):
        packets = destination / "packets" / label
        packets.mkdir(parents=True)
        mesh = load_fixture(path)
        common = {
            "fixture": label,
            "kind": kind,
            "points": mesh.vertex_count,
            "faces": mesh.face_count,
            "corners": len(mesh.corners),
            "edges": mesh.edge_count,
        }
        rows += measure_frozen(dotnet, frozen_env, path, packets, common)

        encode_clock = time.perf_counter()
        data = encode(mesh)
        encode_ms = (time.perf_counter() - encode_clock) * 1000
        file_path = packets / "mesh.zpm"
        file_path.write_bytes(data)
        decoded = decode(data)
        if not same_geometry(snap_positions(mesh), decoded):
            raise ValueError(f"File does not match its source mesh: {label}")
        expected_path = packets / "expected.zms"
        write_fixture(decoded, expected_path)
        subprocess.run(
            [dotnet, str(READER), "verify", str(expected_path), str(file_path)],
            check=True,
        )
        view = run_json([dotnet, str(READER), "measure", str(file_path)])
        rows.append(
            {
                **common,
                **bytes_by_region(data),
                "method": "zpm",
                "reader": "in-place view",
                "stored_bytes": len(data),
                "encode_ms": encode_ms,
                "full_read_us": view["read_all_ns"] / 1000,
                "first_read_us": (view["open_ns"] + view["random_face_ns"]) / 1000,
                **view,
            }
        )
        if len(data) <= FUZZ_LIMIT_BYTES:
            result = run_json(
                [
                    dotnet,
                    str(READER),
                    "fuzz",
                    str(file_path),
                    str(FUZZ_SEED),
                    str(FUZZ_TRIALS),
                ]
            )
            fuzz.append({"fixture": label, **result})
        print(f"Measured {label}", flush=True)

    runtimes = subprocess.check_output([dotnet, "--list-runtimes"], text=True)
    metadata = {
        "run_id": run_id,
        "started_utc": started.isoformat(),
        "wall_seconds": time.perf_counter() - clock,
        "runtime": [line.split(" [")[0] for line in runtimes.strip().splitlines()],
        "host": platform.platform(),
        "processor": platform.processor(),
        "fuzz": fuzz,
        "results": rows,
    }
    (destination / "results.json").write_text(
        json.dumps(metadata, indent=2) + "\n", encoding="utf-8"
    )
    names = list(dict.fromkeys(key for row in rows for key in row))
    with (destination / "results.csv").open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=names)
        writer.writeheader()
        writer.writerows(rows)
    print(f"Done in {metadata['wall_seconds']:.1f} s. Output: runs/{run_id}")
    print(run_id)


if __name__ == "__main__":
    main()
