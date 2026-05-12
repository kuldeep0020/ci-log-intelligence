---
name: release-ops-preferences
description: "Kuldeep prefers to push commits himself and doesn't want diagnostic/scratch files committed to the repo"
metadata: 
  node_type: memory
  type: feedback
  originSessionId: fb52fcd1-253a-41d3-9003-f16cd6d448c9
---

Two release-time behavioral preferences observed on 2026-05-12:

**1. User pushes commits themselves; don't attempt the push.**

When `git push origin main` failed because the SSH remote URL isn't authorized on this machine, the user rejected my suggestion to swap the remote to HTTPS and pushed from his own shell instead. Repeated this pattern for both v0.2.0 and v0.2.1.

**Why:** he keeps the SSH remote intentionally and has his own working push path (gh auth credential helper or a separate setup). Mutating his git remote config to work around a local auth gap is not the right fix.

**How to apply:** when a push is needed, commit locally and pause for him to run the push. Don't run `git remote set-url`, `gh auth setup-git`, or any other config-mutating workaround. Direct `git push https://...` one-shot is fine to suggest but don't run it unless he asks.

**2. Don't commit one-off diagnostic / scratch scripts.**

I added `scripts/test_progress.py` (a useful diagnostic that confirmed the [[progress-token-client-gap]]) to a docs commit and he rejected it: "this is just a normal test script that we used for our own diagnostic, this does not need to go." I removed it via `git rm` + `commit --amend`.

**Why:** the repo's public surface should stay tight. Diagnostic scripts that helped during one debugging session don't need to ship — they bloat the repo, become stale, and confuse contributors.

**How to apply:** when writing throwaway scripts for diagnosis, keep them out of the working tree (or untracked under `/tmp/`) rather than under `scripts/`. If a diagnostic genuinely deserves to live in the repo, ask first.
