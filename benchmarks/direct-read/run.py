# SPDX-License-Identifier: GPL-3.0-or-later
"""Measure the direct-read reference next to the frozen mesh-core reference, on one host.

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
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
MESH_CORE = ROOT.parent / "mesh-core"
FROZEN = MESH_CORE / "reference" / "bin" / "Release" / "net9.0" / "MeshCore.dll"
DIRECT = ROOT / "reference" / "bin" / "Release" / "net10.0" / "DirectRead.dll"
FUZZ_LIMIT_BYTES = 300_000
FUZZ_TRIALS = 2000
FUZZ_SEED = 72641


def run_json(arguments, **options):
    result = subprocess.run(
        arguments, check=True, capture_output=True, text=True, **options
    )
    return json.loads(result.stdout)


def zstd_sizes(packets, extra_path):
    """Whole-file zstd sizes, for scale only: such a file cannot be read directly."""
    if extra_path:
        sys.path.insert(0, extra_path)
    try:
        import zstandard
    except ImportError:
        return {name: None for name in packets}
    compressor = zstandard.ZstdCompressor(level=19)
    return {
        name: len(compressor.compress(Path(path).read_bytes()))
        for name, path in packets.items()
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--private-fixtures", type=Path)
    parser.add_argument("--zstd-path")
    args = parser.parse_args()
    dotnet = shutil.which("dotnet")
    if dotnet is None:
        parser.error("Put the dotnet host on PATH")
    # The frozen build targets net9.0; rolling forward runs it on this same runtime.
    frozen_env = {**os.environ, "DOTNET_ROLL_FORWARD": "Major"}

    fixtures = []
    manifest = json.loads((MESH_CORE / "fixtures/manifest.json").read_text())
    for fixture in manifest["fixtures"]:
        path = MESH_CORE / "fixtures" / fixture["file"]
        if hashlib.sha256(path.read_bytes()).hexdigest() != fixture["sha256"]:
            raise ValueError(f"Fixture hash mismatch: {fixture['file']}")
        fixtures.append((fixture["label"], fixture["kind"], path))
    if args.private_fixtures:
        for path in sorted(args.private_fixtures.glob("*.zms")):
            fixtures.append((path.stem, "real Blender mesh (private)", path))

    started = dt.datetime.now(dt.UTC)
    clock = time.perf_counter()
    run_id = started.strftime("%Y%m%dT%H%M%SZ")
    destination = ROOT / "runs" / run_id
    destination.mkdir(parents=True)
    rows = []
    fuzz = []
    for label, kind, path in fixtures:
        packets = destination / "packets" / label
        frozen = run_json(
            [dotnet, str(FROZEN), "encode", str(path), str(packets)], env=frozen_env
        )
        direct = run_json([dotnet, str(DIRECT), "encode", str(path), str(packets)])
        smallest = min(frozen["variants"], key=lambda variant: variant["bytes"])
        counts = {key: frozen[key] for key in ("points", "faces", "corners", "edges")}
        common = {"fixture": label, "kind": kind, **counts}

        measured_names = {"raw32", "compact16", smallest["name"]}
        for variant in frozen["variants"]:
            if variant["name"] not in measured_names:
                continue
            timing = run_json(
                [dotnet, str(FROZEN), "measure", variant["packet"]], env=frozen_env
            )
            rows.append(
                {
                    **common,
                    "method": variant["name"],
                    "reader": "frozen full decode",
                    "stored_bytes": variant["bytes"],
                    "is_old_smallest": variant["name"] == smallest["name"],
                    "full_read_us": timing["reuse_decode_ms"] * 1000,
                    "first_read_us": timing["reuse_decode_ms"] * 1000,
                    "allocated_bytes": timing["reuse_allocated_bytes"],
                }
            )
            if variant["name"] in ("raw32", "compact16"):
                view = run_json(
                    [dotnet, str(DIRECT), "measure-view", variant["packet"]]
                )
                rows.append(
                    {
                        **common,
                        "method": variant["name"],
                        "reader": "in-place view",
                        "stored_bytes": variant["bytes"],
                        "full_read_us": view["read_all_ns"] / 1000,
                        "first_read_us": (view["open_ns"] + view["random_face_ns"])
                        / 1000,
                        **view,
                    }
                )
        for variant in direct["variants"]:
            view = run_json([dotnet, str(DIRECT), "measure", variant["packet"]])
            rows.append(
                {
                    **common,
                    **variant,
                    "method": variant["name"],
                    "reader": "in-place view",
                    "stored_bytes": variant["bytes"],
                    "full_read_us": view["read_all_ns"] / 1000,
                    "first_read_us": (view["open_ns"] + view["random_face_ns"]) / 1000,
                    **view,
                }
            )
        sizes = zstd_sizes(
            {
                "compact16": packets / "compact16.zmc",
                "direct": packets / "direct.zmd",
            },
            args.zstd_path,
        )
        for row in rows:
            if row["fixture"] == label and row["method"] in sizes:
                row["whole_file_zstd19_bytes"] = sizes[row["method"]]

        direct_path = packets / "direct.zmd"
        if direct_path.stat().st_size <= FUZZ_LIMIT_BYTES:
            result = run_json(
                [
                    dotnet,
                    str(DIRECT),
                    "fuzz",
                    str(direct_path),
                    str(FUZZ_SEED),
                    str(FUZZ_TRIALS),
                ]
            )
            fuzz.append({"fixture": label, **result})
        print(f"Measured {label}", flush=True)

    metadata = {
        "run_id": run_id,
        "started_utc": started.isoformat(),
        "wall_seconds": time.perf_counter() - clock,
        "runtime": subprocess.check_output([dotnet, "--list-runtimes"], text=True)
        .strip()
        .splitlines(),
        "host": platform.platform(),
        "processor": platform.processor(),
        "fuzz": fuzz,
        "results": rows,
    }
    metadata["runtime"] = [line.split(" [")[0] for line in metadata["runtime"]]
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
