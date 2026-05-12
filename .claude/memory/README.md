# Claude memory bank

Curated context for Claude Code (and compatible agents) working on this repo. Each file under this directory is a single memory record with YAML frontmatter (`name`, `description`, `type`) and a body that captures something non-obvious about the project, its history, or contributor preferences.

## When to read these

- Starting a fresh session on this codebase — read `MEMORY.md` first; it's the index. Then read whichever entries the task touches.
- Hitting something unexpected (a release procedure, a non-obvious limitation, a contributor preference) — check here before re-deriving.

## When to add or update

Save knowledge here when you discover something a future session would benefit from but couldn't derive from the code or git history alone. Examples:

- A non-obvious limitation that traces to upstream behavior (see `progress_token_client_gap.md`).
- A repeatable process that lives outside the codebase (see `release_workflow.md`).
- A contributor preference that surfaced through a correction or confirmed approach (see `release_ops_preferences.md`).

Do **not** save things that are derivable from the code itself, git history, or other documentation under `architecture.md` / `CONTRIBUTING.md` / `INSTALL.md`. Those are the canonical sources; memory is for context that lives outside them.

## File format

Each memory file has this frontmatter:

```yaml
---
name: short-kebab-case-slug
description: one-line summary used to decide relevance
metadata:
  type: user | feedback | project | reference
---
```

For `feedback` and `project` entries, lead the body with the rule or fact, then a `**Why:**` line (motivation / context) and a `**How to apply:**` line (when this guidance kicks in). Knowing *why* lets future readers judge edge cases instead of blindly following the rule.

Link related memories inline with `[[their-slug]]`.

## Index

See [MEMORY.md](MEMORY.md) for the current list of entries.
