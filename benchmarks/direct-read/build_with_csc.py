# SPDX-License-Identifier: GPL-3.0-or-later
"""Build the reference with a bare Roslyn compiler, for machines without a .NET SDK.

python build_with_csc.py DOTNET_HOST CSC_DLL RUNTIME_DIR
Machines with the SDK can run `dotnet build -c Release` in reference/ instead.
"""

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
REFERENCE = ROOT / "reference"
OUTPUT = REFERENCE / "bin" / "Release" / "net10.0"
USINGS = "global using System;\nglobal using System.IO;\nglobal using System.Linq;\n"
USINGS += "global using System.Collections.Generic;\n"
RUNTIME_CONFIG = {
    "runtimeOptions": {
        "tfm": "net10.0",
        "framework": {"name": "Microsoft.NETCore.App", "version": "10.0.0"},
        "configProperties": {"System.Runtime.TieredCompilation": False},
    }
}


def main():
    host, csc, runtime = sys.argv[1:4]
    OUTPUT.mkdir(parents=True, exist_ok=True)
    usings = OUTPUT / "GlobalUsings.g.cs"
    usings.write_text(USINGS, encoding="utf-8")
    references = sorted(
        path
        for path in Path(runtime).glob("*.dll")
        if path.name.startswith(("System.", "Microsoft.", "netstandard", "mscorlib"))
        and "Native" not in path.name
    )
    arguments = [
        "-nologo",
        "-optimize+",
        "-unsafe+",
        "-debug-",
        "-nostdlib+",
        "-target:exe",
        "-langversion:latest",
        f"-out:{OUTPUT / 'DirectRead.dll'}",
        *(f"-r:{path}" for path in references),
        str(usings),
        *(str(path) for path in sorted(REFERENCE.glob("*.cs"))),
    ]
    response = OUTPUT / "build.rsp"
    response.write_text(
        "\n".join(f'"{argument}"' for argument in arguments), encoding="utf-8"
    )
    result = subprocess.run([host, csc, f"@{response}"], text=True, capture_output=True)
    errors = [line for line in result.stdout.splitlines() if "error" in line]
    print("\n".join(errors[:40]))
    (OUTPUT / "DirectRead.runtimeconfig.json").write_text(
        json.dumps(RUNTIME_CONFIG, indent=2), encoding="utf-8"
    )
    print("exit", result.returncode)
    return result.returncode


if __name__ == "__main__":
    sys.exit(main())
