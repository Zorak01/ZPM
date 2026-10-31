# Contributing

Describe the concrete problem in an issue before proposing large format changes.
For fixes, include a small reproduction and the expected result.

## Setup and validation

Use Python 3.11+ and `python -m pip install -e ".[dev]"`. Run the checks in the
README. Blender changes must also pass `tests/blender_roundtrip.py` in Blender.

Keep byte encoding, validation and file I/O in `zpm.format`, which must not
import Blender. Keep conversion in `mesh_io.py` and UI in `operators.py`.
Use typed buffers and Blender's bulk APIs for mesh data.

Add regression tests for behavior changes. Preserve Float32 bits and ordered
topology unless a versioned contract explicitly says otherwise. Update the
public contract when supported behavior changes.

## Benchmarks

Do not edit published measurements to improve a comparison. Keep measured
source, fixture hashes, runtime, method and run date together. Publish a new
run for changed code or methodology. Report regressions and limitations with
improvements. CI checks correctness, not timing thresholds.

## Pull requests and commits

Describe the triggering problem, resulting behavior and validation. Keep changes
focused. Separate unrelated refactoring from behavior changes. Use plain commit
messages describing the actual change.

Be respectful. Review code and evidence, not people. Harassment and disclosure
of another person's private information are not acceptable.
