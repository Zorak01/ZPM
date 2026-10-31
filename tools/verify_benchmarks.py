# SPDX-License-Identifier: GPL-3.0-or-later
"""Check published fixture, source and measurement consistency without timing."""

import csv
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "benchmarks/mesh-core"
METHODS = {
    "raw32",
    "compact16",
    "packed",
    "lanes256",
    "lanes1024",
    "lanes4096",
    "lanes16384",
    "face_delta",
    "face_templates",
    "edge_delta",
}


def verify():
    manifest = json.loads((ROOT / "fixtures/manifest.json").read_text())
    fixtures = {fixture["label"] for fixture in manifest["fixtures"]}
    for fixture in manifest["fixtures"]:
        actual = hashlib.sha256(
            (ROOT / "fixtures" / fixture["file"]).read_bytes()
        ).hexdigest()
        if actual != fixture["sha256"]:
            raise ValueError(f"Changed fixture: {fixture['file']}")
    paths = sorted((ROOT / "results").glob("*.json"))
    if not paths:
        raise ValueError("No published benchmark runs")
    for path in paths:
        data = json.loads(path.read_text())
        snapshot = path.with_suffix(".cs")
        if hashlib.sha256(snapshot.read_bytes()).hexdigest() != data["source_sha256"]:
            raise ValueError(f"Measured source does not match {path.name}")
        rows = data["results"]
        pairs = [(row["fixture"], row["method"]) for row in rows]
        expected = {(fixture, method) for fixture in fixtures for method in METHODS}
        if set(pairs) != expected or len(pairs) != len(expected):
            raise ValueError("Incomplete or duplicated fixture/profile coverage")
        for row in rows:
            if row["exact"] is not True:
                raise ValueError("Non-exact profile in exact mesh benchmark")
            for field in (
                "stored_bytes",
                "decode_ms",
                "reuse_decode_ms",
                "retained_payload_bytes",
                "allocated_bytes",
            ):
                if row[field] < 0:
                    raise ValueError(f"Negative measurement: {field}")
        with path.with_suffix(".csv").open(newline="") as stream:
            csv_rows = list(csv.DictReader(stream))
        if len(csv_rows) != len(rows):
            raise ValueError("CSV and JSON row counts differ")
        for row, csv_row in zip(rows, csv_rows, strict=True):
            if any(str(value) != csv_row[key] for key, value in row.items()):
                raise ValueError("CSV and JSON measurements differ")
        for summary in data.get("summary", []):
            for profile in ("baseline", "fast_small", "smallest"):
                if summary[profile] not in rows:
                    raise ValueError("Summary does not match raw measurements")
        print(f"Verified {path.stem}: {len(rows)} cases and measured source hash")
    print(f"Verified {len(fixtures)} fixture hashes")


if __name__ == "__main__":
    verify()
