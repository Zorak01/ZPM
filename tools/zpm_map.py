# SPDX-License-Identifier: GPL-3.0-or-later
"""Print what every byte of a ZPM file holds: python tools/zpm_map.py FILE"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "blender"))

from zpm.format import decode, map_file


def main():
    data = Path(sys.argv[1]).read_bytes()
    decode(data)
    regions = map_file(data)
    print(f"{'Offset':>10} {'Bytes':>10} {'Share':>7}  Region")
    for region in regions:
        share = region.length / len(data)
        print(f"{region.offset:>10,} {region.length:>10,} {share:>7.1%}  {region.name}")
    named = sum(region.length for region in regions)
    print(f"{'':>10} {named:>10,} of {len(data):,} bytes named")


if __name__ == "__main__":
    main()
