# Releases

Version 0.1.0 is currently unreleased. Do not describe local builds as published
GitHub releases or passing hosted CI runs.

Before a release, update the package and Blender manifest together, record
changes, and run all checks. The tag must match both versions (for example
`v0.1.0`). Keep packet compatibility changes explicit in the contract.

The release workflow runs CI, builds the Blender extension ZIP and Python
wheel/source archive, generates SHA-256 checksums and creates a **draft** GitHub
release for version tags. Inspect the draft before publishing. Manual workflow
runs produce downloadable artifacts without creating a release.

## GitHub settings

After connecting the remote, enable private vulnerability reporting, secret
scanning/push protection where available, and a rule protecting `main` from
force pushes and deletion. Require the CI checks before merging. Choose review
requirements appropriate to the number of active maintainers; do not introduce
a rule that a solo maintainer cannot satisfy.

These account-side settings are not configured by files in this repository.
