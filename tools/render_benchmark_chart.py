# SPDX-License-Identifier: GPL-3.0-or-later
"""Render a deterministic SVG from the published measurements."""

import html
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "benchmarks/mesh-core/results"


def render():
    data = json.loads((ROOT / "20261031T033106Z.json").read_text())
    labels = ["quad_grid", "triangle_grid", "mixed_tri_quad_ngon", "repository_obj418"]
    parts = [
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 960 440" '
        'role="img" aria-labelledby="title desc">',
        '<title id="title">Mesh-core stored bytes: RAW32 versus COMPACT16</title>',
        '<desc id="desc">Exact positions and topology; .NET reference codec; '
        "measured October 30, 2026. Bars show bytes, not loading speed.</desc>",
        '<rect width="960" height="440" fill="#101820"/>',
        '<g font-family="sans-serif" fill="#edf3fa">',
        '<text x="24" y="32" font-size="21">Mesh-core stored bytes</text>',
        '<text x="24" y="56" font-size="13">2026-10-30 | .NET reference | '
        "Exact Float32 positions | Raw32 / Compact16</text>",
    ]
    maximum = max(
        row["stored_bytes"]
        for row in data["results"]
        if row["fixture"] in labels and row["method"] == "raw32"
    )
    for i, label in enumerate(labels):
        top = 96 + i * 80
        parts.append(
            f'<text x="24" y="{top}" font-size="13">{html.escape(label)}</text>'
        )
        for j, method in enumerate(("raw32", "compact16")):
            row = next(
                row
                for row in data["results"]
                if row["fixture"] == label and row["method"] == method
            )
            width = row["stored_bytes"] / maximum * 560
            color = "#80b5ed" if j == 0 else "#62d5a9"
            y = top - 12 + j * 25
            parts.append(
                f'<rect x="225" y="{y}" width="{width:.2f}" '
                f'height="19" rx="2" fill="{color}"/>'
            )
            parts.append(
                f'<text x="{235 + width:.2f}" y="{y + 14}" '
                f'font-size="12">{row["stored_bytes"]:,} B</text>'
            )
    parts.append(
        '<text x="24" y="425" font-size="12">'
        "Storage reduction is not an end-to-end speed claim. "
        "See raw CSV/JSON and methodology.</text></g></svg>"
    )
    (ROOT / "storage.svg").write_text("\n".join(parts), encoding="utf-8")


if __name__ == "__main__":
    render()
