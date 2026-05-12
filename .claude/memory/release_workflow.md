---
name: release-workflow
description: "How to ship a new version of ci-log-intelligence to PyPI — version bump, GitHub release, OIDC publish"
metadata: 
  node_type: memory
  type: reference
  originSessionId: fb52fcd1-253a-41d3-9003-f16cd6d448c9
---

Release process for `ci-log-intelligence`:

1. Bump `version = "X.Y.Z"` in `pyproject.toml`.
2. Commit using two logically-grouped commits matching the existing pattern: `feat/fix/docs(scope): ...` for the change, then `chore: bump version to X.Y.Z for <description> release`.
3. User pushes to `main` themselves — do not push for them (see [[release-ops-preferences]]).
4. Create the GitHub release: `gh release create vX.Y.Z --target main --title "..." --notes "..."`.
5. The release event triggers `.github/workflows/publish.yml`, which runs tests on Python 3.10–3.13, builds sdist + wheel, and publishes to PyPI via OIDC trusted publishing. No PyPI API token is involved.
6. The full workflow takes ~1m15s. PyPI's index/CDN can lag another 30–60s after the workflow says "success" — installs of the new version may need `pip install --force-reinstall "ci-log-intelligence==X.Y.Z"` for a brief window.

Verify with `gh run list --workflow=publish.yml --limit 1` (looks for the `release` event row).

Releases are tagged `vX.Y.Z`. No tags existed before v0.2.0 (2026-05-12).
