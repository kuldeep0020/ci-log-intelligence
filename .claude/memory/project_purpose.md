---
name: ci-log-intelligence purpose
description: Why this tool exists — focused CI log failure extraction via MCP to prevent context dilution in the calling agent
type: project
originSessionId: fb52fcd1-253a-41d3-9003-f16cd6d448c9
---
`ci-log-intelligence` is an MCP server. It takes a PR or CI job URL, spins up a separate subagent that fetches and reads the CI logs, isolates the actual failure (failing test, build error, lint, whatever), and returns a compact, well-contextualized error report to the calling agent — so the calling agent never has to ingest raw 50K+ line logs.

**Why:** Origin incident: user was debugging a CI failure where tests ran 3–4 hours and the run emitted 50K+ lines of logs. The main agent pulled the full logs to investigate, which exploded token usage and spread context thin across an enormous surface area, degrading Claude Code's performance on the actual fix. The tool exists specifically to be the boundary that keeps that flood out of the caller's context.

**How to apply:** Treat preserving the caller's context budget as a first-class design constraint. The subagent is the boundary — it does the heavy reading and returns a focused summary plus the relevant snippet, not raw log content. When reviewing or designing features here, ask: does this push raw log volume back to the caller, or does it keep extraction work on the subagent side? Favor changes that compress; be suspicious of changes that broaden the payload.
