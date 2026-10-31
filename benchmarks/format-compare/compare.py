# SPDX-License-Identifier: GPL-3.0-or-later
"""Compare ZPM with Draco and meshoptimizer on the same data, at the same precision.

python compare.py --fixtures DIR --meshopt NODE_MODULES/meshoptimizer

Needs DracoPy and zstandard installed, and a built mesh-read benchmark. Each mesh
comes from a mesh-read run's surface.zpm, so every format holds what ZPM kept.
Only triangle meshes are compared, because Draco and meshoptimizer store only triangles.
"""

import argparse
import base64
import datetime as dt
import importlib.util
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
ROUNDS = 5
RUNS_PER_ROUND = 3
POSITION_BITS = 20
NORMAL_BITS = 12
UV_BITS = 20
ZSTD_LEVEL = 19
UV_SCALE = 1 << UV_BITS
EDGE_REGIONS = ("EDGE_",)

sys.path.insert(0, str(REPOSITORY / "blender"))

from zpm.format import decode, encode, map_file  # noqa: E402
from zpm.format.codec.quantize import decode_normal, encode_normal  # noqa: E402
from zpm.format.container.layout import MESH_INFO  # noqa: E402
from zpm.format.processing.snapping import grid_exponent  # noqa: E402


def fan_triangles(mesh) -> list:
    """Return the corner indices of each face split into triangles."""
    sizes = [mesh.arity] * mesh.face_count if mesh.arity else mesh.face_sizes
    triangles = []
    at = 0
    for size in sizes:
        for offset in range(1, size - 1):
            triangles += (at, at + offset, at + offset + 1)
        at += size
    return triangles


def to_vertices(mesh, keep_vertices: bool) -> dict:
    """Build the vertices Draco and meshoptimizer expect.

    Corners merge when they hold the same values. With keep_vertices they must also be
    the same Blender vertex, so these formats hold every vertex ZPM holds.
    """
    exponent = grid_exponent(mesh.positions)
    step = math.ldexp(1.0, exponent)
    uv_name = sorted(mesh.uv_layers)[0] if mesh.uv_layers else None
    uvs = mesh.uv_layers[uv_name] if uv_name else None
    normals = mesh.corner_normals
    known = {}
    rows = []
    corner_indices = {}
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
        key = (point, normal, uv, vertex if keep_vertices else 0)
        index = known.get(key)
        if index is None:
            index = known[key] = len(rows)
            rows.append(key)
        corner_indices[corner] = index
    indices = [corner_indices[corner] for corner in fan_triangles(mesh)]
    return {
        "positions": np.array([row[0] for row in rows], dtype=np.int32),
        "normals": np.array([row[1] for row in rows], dtype=np.uint16),
        "uvs": np.array([row[2] for row in rows], dtype=np.int32) if uvs else None,
        "indices": np.array(indices, dtype=np.uint32),
        "step": step,
    }


def load_mesh_read_module():
    """Reuse the fixture loaders of the mesh-read benchmark."""
    spec = importlib.util.spec_from_file_location(
        "mesh_read_run", REPOSITORY / "benchmarks/mesh-read/run.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_mesh_read = load_mesh_read_module()
load_fixture = _mesh_read.load_fixture
load_surface = _mesh_read.load_surface


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


def sections_mesh_info(data: bytes) -> bytes:
    region = next(r for r in map_file(data) if r.name == "MESH_INFO")
    return data[region.offset : region.offset + region.length]


def measure_draco_geometry(mesh) -> int:
    """Draco holding only positions and triangles, to match computed normals."""
    positions = np.array(mesh.positions, dtype=np.float32).reshape(-1, 3)
    corners = np.array(mesh.corners, dtype=np.uint32)
    triangles = corners[fan_triangles(mesh)].reshape(-1, 3)
    return len(
        DracoPy.encode(
            positions,
            triangles,
            quantization_bits=POSITION_BITS,
            compression_level=10,
        )
    )


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
    parser.add_argument("--fixtures", type=Path, required=True, nargs="+")
    parser.add_argument("--meshopt", type=Path, required=True)
    args = parser.parse_args()
    node = shutil.which("node")
    dotnet = shutil.which("dotnet")
    started = dt.datetime.now(dt.UTC)
    run_id = started.strftime("%Y%m%dT%H%M%SZ")
    work = ROOT / "runs" / run_id
    work.mkdir(parents=True)
    rows = []
    fixtures = [
        path for folder in args.fixtures for path in sorted(folder.glob("*.zms"))
    ]
    for fixture in fixtures:
        source = load_surface(fixture.with_suffix(".surface"), load_fixture(fixture))
        if source is None or not source.corners:
            continue
        clock = time.perf_counter()
        data = encode(source)
        encode_seconds = time.perf_counter() - clock
        path = work / f"{fixture.stem}.zpm"
        path.write_bytes(data)
        mesh = decode(data)
        vertices = to_vertices(mesh, False)
        same_vertices = to_vertices(mesh, True)
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
                "mesh": fixture.stem,
                "encode_seconds": encode_seconds,
                "faces": mesh.face_count,
                "kept_vertices": mesh.vertex_count,
                "corners": len(mesh.corners),
                "merged_vertices": len(vertices["positions"]),
                "has_uvs": vertices["uvs"] is not None,
                "zpm_bytes": len(data),
                "zpm_bytes_without_edges": len(data) - edge_bytes,
                "zpm_zstd_bytes": len(zstd.compress(data)),
                **reads,
                "normals_from_faces": bool(
                    MESH_INFO.unpack(sections_mesh_info(data))[7] & 2
                ),
                "draco": measure_draco(vertices),
                "draco_geometry_only": measure_draco_geometry(mesh),
                "meshopt": measure_meshopt(vertices, args.meshopt, work, node),
                "draco_same_vertices": measure_draco(same_vertices),
                "meshopt_same_vertices": measure_meshopt(
                    same_vertices, args.meshopt, work, node
                ),
            }
        )
        print(f"Measured {fixture.stem}", flush=True)
    metadata = {
        "run_id": run_id,
        "started_utc": started.isoformat(),
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
