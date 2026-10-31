# SPDX-License-Identifier: GPL-3.0-or-later
"""Reproduce the frozen reference codec without report libraries or local paths."""

import argparse
import csv
import datetime as dt
import hashlib
import json
import platform
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent
REFERENCE = ROOT / "reference"


def run_json(arguments, *, cwd=None):
    result = subprocess.run(
        arguments, cwd=cwd, check=True, capture_output=True, text=True
    )
    return json.loads(result.stdout)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    dotnet = shutil.which("dotnet")
    if dotnet is None:
        parser.error("Install .NET SDK 9.0.306 and put dotnet on PATH")
    manifest = json.loads((ROOT / "fixtures/manifest.json").read_text())
    for fixture in manifest["fixtures"]:
        data = (ROOT / "fixtures" / fixture["file"]).read_bytes()
        if hashlib.sha256(data).hexdigest() != fixture["sha256"]:
            raise ValueError(f"Fixture hash mismatch: {fixture['file']}")
    subprocess.run(
        [
            dotnet,
            "build",
            "--configuration",
            "Release",
            "--configfile",
            "NuGet.Config",
            "--nologo",
        ],
        cwd=REFERENCE,
        check=True,
    )
    dll = REFERENCE / "bin/Release/net9.0/MeshCore.dll"
    started = dt.datetime.now(dt.UTC)
    run_id = started.strftime("%Y%m%dT%H%M%SZ")
    destination = ROOT / "runs" / run_id
    destination.mkdir(parents=True)
    rows = []
    verified = 0
    for fixture in manifest["fixtures"]:
        source = ROOT / "fixtures" / fixture["file"]
        encoded = run_json(
            [
                dotnet,
                str(dll),
                "encode",
                str(source),
                str(destination / "packets" / fixture["label"]),
            ]
        )
        # The reference encoder validates both fresh and reused decodes before
        # returning each variant. A mismatch exits unsuccessfully.
        for variant in encoded["variants"]:
            verified += 1
            if args.verify_only:
                continue
            measured = run_json([dotnet, str(dll), "measure", variant["packet"]])
            rows.append(
                {
                    "fixture": fixture["label"],
                    "kind": fixture["kind"],
                    "method": variant["name"],
                    "stored_bytes": variant["bytes"],
                    "encode_ms": variant["encode_ms"],
                    "points": encoded["points"],
                    "faces": encoded["faces"],
                    "corners": encoded["corners"],
                    "edges": encoded["edges"],
                    **measured,
                    "exact": True,
                }
            )
        print(f"Verified {fixture['label']}", flush=True)
    if args.verify_only:
        print(f"PASS: {verified} profiles, fresh and reused buffers")
        return
    metadata = {
        "run_id": run_id,
        "started_utc": started.isoformat(),
        "finished_utc": dt.datetime.now(dt.UTC).isoformat(),
        "runtime": subprocess.check_output(
            [dotnet, "--list-runtimes"], text=True
        ).splitlines(),
        "host": platform.platform(),
        "processor": platform.processor(),
        "source_sha256": hashlib.sha256(
            (REFERENCE / "Program.cs").read_bytes()
        ).hexdigest(),
        "binary_sha256": hashlib.sha256(dll.read_bytes()).hexdigest(),
        "fixture_manifest_sha256": hashlib.sha256(
            (ROOT / "fixtures/manifest.json").read_bytes()
        ).hexdigest(),
        "measurement_contract": "../README.md",
        "results": rows,
    }
    # Runtime installation paths are local details, not benchmark data.
    metadata["runtime"] = [line.split(" [")[0] for line in metadata["runtime"]]
    (destination / "results.json").write_text(
        json.dumps(metadata, indent=2) + "\n", encoding="utf-8"
    )
    with (destination / "results.csv").open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    print(f"Measured {len(rows)} profiles. Output: runs/{run_id}")
    print("Review measurements and environment before adding a public results file.")


if __name__ == "__main__":
    main()
