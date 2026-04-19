## CI Log Intelligence

CI Log Intelligence is a deterministic, rule-based system for debugging CI failures from raw job logs and GitHub Actions URLs. It has three primary surfaces:

- Python API for local analysis
- CLI for humans debugging CI
- MCP server for agents such as Codex, VS Code / GitHub Copilot, and Claude-compatible MCP clients

## What it does

The system accepts raw CI logs or GitHub CI URLs, fetches relevant workflow runs and jobs, reduces failed logs into high-signal failure blocks, extracts comparable context from passed runs, and produces a structured diagnosis that can be consumed by humans or AI agents.

For GitHub-backed analysis it can accept:

- pull request URLs
- workflow run URLs
- job URLs

## Algorithm

The reducer is intentionally heuristic and deterministic. The pipeline is:

1. Parse lines into `ParsedLine` records with line number, timestamp, step id, and detected signals.
2. Detect anchors with regex tiers:
   - severity 3: `Traceback`, `Exception`, `ERROR`
   - severity 2: `FAILED`, `AssertionError`
   - severity 1: `WARNING`, `Retrying`
3. Build clusters from nearby anchors in the same step.
4. Expand context around each cluster with a default window of plus/minus 20 lines.
5. Extend further when a stack trace is present.
6. Suppress blank lines, separators, and duplicate noise.
7. Merge overlapping or near-adjacent blocks within the same step.
8. Score each block using anchor severity, signal density, recency, and duplicate penalty.
9. Classify blocks as `root_cause`, `symptom`, or `flaky`.
10. Rank the final blocks deterministically.

For CI-aware analysis:

1. Failed jobs run through the full reducer.
2. Passed jobs do not run through full reduction.
3. Passed jobs only contribute step-matched, nearby, or test-name-matched excerpts.
4. A cross-run analyzer compares failed blocks with passed excerpts to produce deterministic insights such as environment-specific failures, missing steps, or query/result differences.

## Installation

See [INSTALL.md](/Users/kumar/workspace/ci-log-intelligence/INSTALL.md) for the full setup guide across Codex, VS Code / GitHub Copilot, and Claude Desktop.

Quick start:

```bash
python -m pip install -e .
gh auth login
```

This repo ships shared MCP configuration for:

- Codex: `.codex/config.toml`
- VS Code / GitHub Copilot: `.vscode/mcp.json`
- Claude Desktop manual example: `docs/claude_desktop_config.example.json`

## CLI usage

Analyze CI from GitHub:

```bash
ci-log-intel analyze \
  --url https://github.com/owner/repo/pull/123 \
  --include-passed
```

Structured JSON output:

```bash
ci-log-intel analyze \
  --url https://github.com/owner/repo/actions/runs/123456789 \
  --include-passed \
  --json
```

## MCP usage

Run the MCP server over stdio:

```bash
ci-log-intelligence-mcp
```

Run the MCP server over HTTP:

```bash
ci-log-intelligence-mcp --transport http --host 127.0.0.1 --port 8001
```

The exposed MCP tool is:

- `analyze_ci_failure`

Tool input:

```json
{
  "ci_url": "https://github.com/owner/repo/pull/123"
}
```

Tool output:

```json
{
  "root_cause": {
    "summary": "string",
    "log_excerpt": "string",
    "confidence": 0.87
  },
  "failed_blocks": [
    {
      "start_line": 10,
      "end_line": 25,
      "summary": "string"
    }
  ],
  "passed_context": [
    {
      "job_name": "test-redshift",
      "excerpt": "string"
    }
  ],
  "cross_run_insights": [
    "Failure occurs only in variant snowflake for job group test."
  ],
  "metadata": {
    "total_runs_analyzed": 3,
    "failed_runs": 1,
    "passed_runs": 2
  }
}
```

## Python usage

Analyze a raw log:

```python
from ci_log_intelligence import analyze_log

result = analyze_log("STEP: test\nERROR build failed\nException: boom")
print(result.summary)
for block in result.blocks:
    print(block.block.start_line, block.block.end_line, block.score, block.classification)
```

Analyze a GitHub CI URL:

```python
from ci_log_intelligence import analyze_ci_url

report = analyze_ci_url(
    "https://github.com/owner/repo/pull/123",
    include_passed=True,
    max_passed_runs=3,
)
print(report.root_cause.summary)
```

## HTTP API

Run the local HTTP API:

```bash
uvicorn ci_log_intelligence.api:app --reload
```

Send a raw log for analysis:

```bash
curl -X POST http://127.0.0.1:8000/analyze \
  -H "Content-Type: application/json" \
  -d '{"log":"STEP: test\nERROR build failed\nException: boom"}'
```

## GitHub authentication

GitHub ingestion prefers the local `gh` CLI and falls back to `requests` with `GITHUB_TOKEN`.

Supported sources:

- PR URL
- workflow run URL
- job URL

## Testing

Run the full test suite:

```bash
python -m unittest discover -s tests -v
```
