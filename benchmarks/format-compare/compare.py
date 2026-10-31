# SPDX-License-Identifier: GPL-3.0-or-later
"""Compare ZPM with Draco and meshoptimizer on the same data, at the same precision.

python compare.py --zpm-run RUN_DIR --meshopt NODE_MODULES/meshoptimizer

Needs DracoPy and zstandard installed, and a built mesh-read benchmark. Each mesh
comes from a mesh-read run's surface.zpm, so every format holds what ZPM kept.
Only triangle meshes are compared, because Draco and meshoptimizer store only triangles.
"""

import argparse
import base64
import datetime as dt
import json
import math
import shutil
import struct
import subprocess
import sys
import time
from pathlib import Path

import DracoPy
import numpy as np
import zstandard

ROOT = Path(__file__).resolve().parent
REPOSITORY = ROOT.parents[1]
READER = REPOSITORY / "benchmarks/mesh-read/reference/bin/Release/net10.0/MeshRead.dll"
ROUNDS = 9
RUNS_PER_ROUND = 25
POSITION_BITS = 20
NORMAL_BITS = 12
UV_BITS = 20
ZSTD_LEVEL = 19
UV_SCALE = 1 << UV_BITS
EDGE_REGIONS = ("EDGE_",)

sys.path.insert(0, str(REPOSITORY / "blender"))

from zpm.format import decode, map_file  # noqa: E402
from zpm.format.optimize import grid_exponent  # noqa: E402
from zpm.format.surface_values import decode_normal, encode_normal  # noqa: E402


def to_vertices(mesh) -> dict:
    """Merge corners into the unique vertices Draco and meshoptimizer expect."""
    exponent = grid_exponent(mesh.positions)
    step = math.ldexp(1.0, exponent)
    uv_name = sorted(mesh.uv_layers)[0] if mesh.uv_layers else None
    uvs = mesh.uv_layers[uv_name] if uv_name else None
    normals = mesh.corner_normals
    known = {}
    rows = []
    indices = []
    for corner, vertex in enumerate(mesh.corners):
        point = tuple(
            round(mesh.positions[vertex * 3 + axis] / step) for axis in range(3)
        )
        normal = encode_normal(*normals[corner * 3 : corner * 3 + 3])
        uv = (
            (round(uvs[corner * 2] * UV_SCALE), round(uvs[corner * 2 + 1] * UV_SCALE))
            if uvs
            else ()
        )
        key = (point, normal, uv)
        index = known.get(key)
        if index is None:
            index = known[key] = len(rows)
            rows.append(key)
        indices.append(index)
    return {
        "positions": np.array([row[0] for row in rows], dtype=np.int32),
        "normals": np.array([row[1] for row in rows], dtype=np.uint16),
        "uvs": np.array([row[2] for row in rows], dtype=np.int32) if uvs else None,
        "indices": np.array(indices, dtype=np.uint32),
        "step": step,
    }


def median_time_us(work) -> float:
    for _ in range(RUNS_PER_ROUND):
        work()
    times = []
    for _ in range(ROUNDS):
        start = time.perf_counter()
        for _ in range(RUNS_PER_ROUND):
            work()
        times.append((time.perf_counter() - start) * 1e6 / RUNS_PER_ROUND)
    return sorted(times)[ROUNDS // 2]


def measure_draco(vertices: dict) -> dict:
    positions = (vertices["positions"] * vertices["step"]).astype(np.float32)
    normals = np.array(
        [decode_normal(int(a), int(b)) for a, b in vertices["normals"]],
        dtype=np.float64,
    )
    extra = {}
    if vertices["uvs"] is not None:
        extra["tex_coord"] = vertices["uvs"] / UV_SCALE
        extra["tex_coord_quantization_bits"] = UV_BITS
    data = DracoPy.encode(
        positions,
        vertices["indices"].reshape(-1, 3),
        quantization_bits=POSITION_BITS,
        compression_level=10,
        normals=normals,
        normal_quantization_bits=NORMAL_BITS,
        **extra,
    )
    return {
        "bytes": len(data),
        "decode_us": median_time_us(lambda: DracoPy.decode(data)),
    }


def measure_meshopt(vertices: dict, package: Path, work: Path, node: str) -> dict:
    path = work / "mesh.bin"
    header = struct.pack(
        "<4I",
        len(vertices["positions"]),
        len(vertices["indices"]),
        1,
        1 if vertices["uvs"] is not None else 0,
    )
    parts = [header, vertices["positions"].tobytes(), vertices["normals"].tobytes()]
    if vertices["uvs"] is not None:
        parts.append(vertices["uvs"].tobytes())
    parts.append(vertices["indices"].tobytes())
    path.write_bytes(b"".join(parts))
    result = subprocess.run(
        [node, str(ROOT / "meshopt_worker.mjs"), str(package), str(path)],
        check=True,
        capture_output=True,
        text=True,
    )
    measured = json.loads(result.stdout)
    payload = base64.b64decode(measured.pop("payload_base64"))
    compressor = zstandard.ZstdCompressor(level=ZSTD_LEVEL)
    measured["bytes"] = sum(measured["stored"].values())
    measured["zstd_bytes"] = len(compressor.compress(payload))
    return measured


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--zpm-run", type=Path, required=True)
    parser.add_argument("--meshopt", type=Path, required=True)
    args = parser.parse_args()
    node = shutil.which("node")
    dotnet = shutil.which("dotnet")
    started = dt.datetime.now(dt.UTC)
    run_id = started.strftime("%Y%m%dT%H%M%SZ")
    work = ROOT / "runs" / run_id
    work.mkdir(parents=True)
    rows = []
    for path in sorted(args.zpm_run.glob("packets/*/surface.zpm")):
        data = path.read_bytes()
        mesh = decode(data)
        if mesh.arity != 3 and any(size != 3 for size in mesh.face_sizes):
            continue
        vertices = to_vertices(mesh)
        edge_bytes = sum(
            region.length
            for region in map_file(data)
            if region.name.startswith(EDGE_REGIONS)
        )
        zstd = zstandard.ZstdCompressor(level=ZSTD_LEVEL)
        reads = json.loads(
            subprocess.run(
                [dotnet, str(READER), "measure-surface", str(path)],
                check=True,
                capture_output=True,
                text=True,
            ).stdout
        )
        rows.append(
            {
                "mesh": path.parent.name,
                "corners": len(mesh.corners),
                "merged_vertices": len(vertices["positions"]),
                "has_uvs": vertices["uvs"] is not None,
                "zpm_bytes": len(data),
                "zpm_bytes_without_edges": len(data) - edge_bytes,
                "zpm_zstd_bytes": len(zstd.compress(data)),
                **reads,
                "draco": measure_draco(vertices),
                "meshopt": measure_meshopt(vertices, args.meshopt, work, node),
            }
        )
        print(f"Measured {path.parent.name}", flush=True)
    metadata = {
        "run_id": run_id,
        "started_utc": started.isoformat(),
        "source_run": args.zpm_run.name,
        "draco": DracoPy.__name__,
        "position_bits": POSITION_BITS,
        "normal_bits": NORMAL_BITS,
        "uv_bits": UV_BITS,
        "results": rows,
    }
    output = work / "results.json"
    output.write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    print(output)


if __name__ == "__main__":
    main()
