## CI Log Intelligence

CI Log Intelligence is a deterministic, rule-based system for debugging CI failures from raw job logs and GitHub Actions URLs. It has three primary surfaces:

- Python API for local analysis
- CLI for humans debugging CI
- MCP server for agents such as Codex, VS Code / GitHub Copilot, and Claude-compatible MCP clients

## What it does

The system accepts raw CI logs or GitHub CI URLs, fetches relevant workflow runs and jobs, runs a plugin-based detector framework over each failed log to produce typed failure records, extracts comparable context from passed runs, and produces a structured diagnosis that can be consumed by humans or AI agents.

For GitHub-backed analysis it can accept:

- pull request URLs
- workflow run URLs
- job URLs

## Algorithm

The reducer is deterministic and heuristic. A set of Detector plugins scans each parsed line and emits typed `DetectedFailure` records; the framework clusters their anchors, expands surrounding context, suppresses noise, scores by severity, classifies, and ranks. Failures with a `classification_claim` from a detector override the signal-based heuristic.

Detectors (severity in parentheses):

- `hash_mismatch` (2): `file hashes don't match` paired with `--- FAIL:` in the same step
- `go_test_fail` (2): standalone `--- FAIL:` markers (not paired with hash-mismatch)
- `pytest_fail` (2): `FAILED tests/x.py::test_y` with traceback pairing
- `rust_test_fail` (2): `test foo ... FAILED` with thread-panic pairing
- `junit_xml` (2): `<testcase>...<failure>` fragments in log streams
- `build_error_rust` (3): `error[E####]` + `-->` location and bare cargo summaries
- `build_error_go` (3): `./pkg/file.go:line:col:` messages
- `build_error_npm` (3): `npm ERR!` / `yarn error` blocks
- `build_error_make` (3): `make: *** [target] Error N`
- `build_error_gcc` (3): `file:line:col: error: ...` with note continuation
- `generic` (1-3): hardened keyword fallback (`Traceback` / `Exception` / `ERROR` / `FAILED` / etc.)

Build errors at severity 3 rank above test failures at severity 2, so when a build broke before any test ran the build error is correctly selected as `root_cause`. Score formula: `severity*5 + signal_density - duplicate_penalty` (no recency bias).

For CI-aware analysis:

1. Failed jobs run through the full pipeline.
2. Passed jobs use targeted extraction (step / test name / nearby line match), not full reduction.
3. A cross-run analyzer compares failed blocks with passed excerpts to surface variant-only failures, missing steps, and query/result differences.

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

The MCP server exposes three tools so the calling agent can explore-then-drill instead of paying for one fixed payload on every call:

1. `list_failed_jobs(ci_url)` -- cheap map of failed jobs with job names, classifications, and failure types present. No per-block content. Use this first to decide which job to investigate.

2. `analyze_ci_failure(ci_url, top_k=3, failure_types=None, include_passed=True, max_passed_runs=1)` -- main typed-record analysis. `failure_types` filters by detector (e.g. `["hash_mismatch"]`). `top_k` truncates the result; `metadata.failures_total` surfaces how many records were produced before truncation.

3. `get_block(ci_url, block_index, surround=5)` -- drill into a specific block by position. `ci_url` must be a job-scoped URL.

Results are cached per `(repo, run_id, job_id)`, so subsequent calls against the same URL skip the GitHub fetch, parse, and reduction entirely.

Tool output (for `analyze_ci_failure`):

```json
{
  "root_cause": {
    "summary": "...",
    "log_excerpt": "...",
    "has_traceback": true,
    "has_stack_trace": true,
    "has_assertion": false,
    "score": 15.5,
    "score_components": {
      "severity_weight": 15.0,
      "signal_density": 0.5,
      "duplicate_penalty": 0.0
    }
  },
  "failures": [
    {
      "type": "hash_mismatch",
      "classification": "root_cause",
      "severity": 2,
      "score": 10.0,
      "start_line": 100,
      "end_line": 145,
      "summary": "...",
      "log_excerpt": "...",
      "extracted_fields": {
        "test_name": "TestRunSetPartial",
        "warehouse_target": "postgres",
        "job_name": "postgres-test (bundling)"
      }
    }
  ],
  "passed_context": [{"job_name": "...", "excerpt": "..."}],
  "cross_run_insights": ["..."],
  "metadata": {
    "total_runs_analyzed": 3,
    "failed_runs": 1,
    "passed_runs": 2,
    "failures_returned": 1,
    "failures_total": 1
  }
}
```

## Python usage

Analyze a raw log:

```python
from ci_log_intelligence import analyze_log

result = analyze_log("STEP: test\nERROR build failed\nException: boom")
for failure in result.detected_failures:
    print(failure.type, failure.anchor_lines, failure.extracted_fields)
for scored in result.blocks:
    print(scored.block.start_line, scored.block.end_line, scored.score, scored.classification)
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
for record in report.failures:
    print(record.type, record.classification, record.score, record.extracted_fields)
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
