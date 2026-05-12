# ci-log-intelligence

**Stop dumping 50,000-line CI logs into your AI coding agent.** This MCP server reads the logs *for* the agent and returns a few hundred tokens of focused, typed failure context — so the agent can debug your CI without flooding its context window.

[![CI](https://github.com/kuldeep0020/ci-log-intelligence/actions/workflows/ci.yml/badge.svg)](https://github.com/kuldeep0020/ci-log-intelligence/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

<!--
After the first PyPI release, add this badge above for a live version indicator:

[![PyPI version](https://img.shields.io/pypi/v/ci-log-intelligence.svg)](https://pypi.org/project/ci-log-intelligence/)
-->


## The problem

You ask Claude / Codex / Copilot to fix a failing CI build. The agent runs `gh run view --log`, gets back 60,000 lines of pytest output, and pastes the whole thing into its context. Now:

- The actual failure is buried somewhere on line 47,892.
- Your context window is ~80% spent on log output before any work begins.
- Every tool call after this costs more because the cached context is enormous.
- The agent's reasoning quality drops because the relevant signal is diluted.

After a few of these, your conversation either OOMs the context or gets too expensive to be useful.

## What this does

`ci-log-intelligence` is an MCP server (also usable as a CLI / Python library) that sits between the agent and the CI logs. You give it a GitHub URL — a PR, a workflow run, or a single job — and it does the heavy reading in its own process:

```text
PR / run / job URL  →  fetch logs  →  parse  →  11 detector plugins  →  typed failure records
                                                                              │
                                                                              ▼
                                                                      a few hundred tokens
                                                                      of focused context
                                                                      back to your agent
```

You get back a structured response: a ranked list of typed `FailureRecord`s (`hash_mismatch`, `build_error_rust`, `pytest_fail`, `go_test_fail`, …), each with the test name / file path / error code / log excerpt that's actually relevant — not 50K lines of `npm install` output.

## Three MCP tools, designed to explore-then-drill

Rather than one omnibus call that returns a fixed payload, the server exposes three tools that map onto how an agent actually wants to work:

| Tool | When to use | Approximate response size |
|---|---|---|
| `list_failed_jobs(ci_url)` | First call. Cheap map of failed jobs with classifications + the failure types present in each. No per-block content. | ~200–500 tokens |
| `analyze_ci_failure(ci_url, top_k=3, failure_types=None, …)` | Get the top-K typed failure records with content. Filterable by detector (`failure_types=["hash_mismatch"]`). | ~1–4K tokens |
| `get_block(ci_url, block_index, surround=5)` | Drill into a specific block. Returns full content with `in_block` / `is_anchor` flags. | per-block |

Results are cached per `(repo, run_id, job_id)`. A second call against the same URL skips the GitHub fetch, the parse, and the reducer entirely.

## Quick start

### Install

```bash
pip install ci-log-intelligence
```

Or from source:

```bash
git clone https://github.com/kuldeep0020/ci-log-intelligence.git
cd ci-log-intelligence
pip install -e .
```

### Authenticate with GitHub

The fetcher prefers the local `gh` CLI; falls back to a `GITHUB_TOKEN` env var.

```bash
gh auth login         # preferred
# or
export GITHUB_TOKEN=ghp_…
```

### Wire up your MCP client

**Claude Code (CLI)** — one command, available in every project:

```bash
claude mcp add ci-log-intelligence --scope user -- ci-log-intelligence-mcp
claude mcp list   # confirm it shows up
```

**Claude Desktop** — add to your `claude_desktop_config.json` (macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`; Windows: `%APPDATA%\Claude\claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "ci-log-intelligence": {
      "command": "ci-log-intelligence-mcp",
      "args": []
    }
  }
}
```

Fully quit and relaunch Claude Desktop after editing the file.

**Codex** — this repo includes `.codex/config.toml`; open the repo in Codex and run `/mcp` to confirm `ci-log-intelligence` is listed.

**VS Code / GitHub Copilot** — this repo includes `.vscode/mcp.json`; open the repo in VS Code with Copilot agent mode enabled.

See [INSTALL.md](INSTALL.md) for full setup instructions including troubleshooting, environment variables, HTTP transport, and other MCP clients.

## A 30-second demo

In your AI agent, after wiring up the MCP server:

> "The build at `https://github.com/me/myrepo/actions/runs/12345` failed. Can you fix it?"

The agent now has three tools available. A reasonable trace:

```text
agent  →  list_failed_jobs("https://github.com/me/myrepo/actions/runs/12345")

server →  {
            "jobs": [
              {
                "job_name": "postgres-test (bundling)",
                "block_count": 3,
                "failure_types_present": ["hash_mismatch", "generic"],
                "classifications": {"root_cause": 1, "symptom": 2},
                "job_url": "…/runs/12345/jobs/678"
              }
            ],
            "metadata": {"failed_jobs": 1, "total_runs_analyzed": 1}
          }

agent  →  analyze_ci_failure(
             ci_url="…/runs/12345",
             failure_types=["hash_mismatch"]
          )

server →  {
            "root_cause": {
              "summary": "Run 12345 job postgres-test (bundling) root_cause at lines 1058-1062: ...",
              "log_excerpt": "common.go:1058: file hashes don't match for ...\n--- FAIL: TestRunSetPartial (45.3s)\n…",
              "has_traceback": false,
              "has_assertion": true,
              "score": 10.0,
              "score_components": {"severity_weight": 10.0, "signal_density": 0.5, "duplicate_penalty": 0.0}
            },
            "failures": [
              {
                "type": "hash_mismatch",
                "classification": "root_cause",
                "severity": 2,
                "score": 10.0,
                "start_line": 1058,
                "end_line": 1062,
                "summary": "…",
                "log_excerpt": "…",
                "extracted_fields": {
                  "test_name": "TestRunSetPartial",
                  "warehouse_target": "postgres",
                  "job_name": "postgres-test (bundling)"
                }
              }
            ],
            "metadata": {"failures_returned": 1, "failures_total": 1, …}
          }
```

The agent now knows: it's a golden-file hash mismatch in `TestRunSetPartial` on the postgres warehouse target. It can run `make update_ref_samples` scoped to that one test. Total context consumed: <2K tokens instead of 50K.

## CLI usage

For humans debugging CI in a terminal:

```bash
ci-log-intel analyze --url https://github.com/owner/repo/pull/123 --include-passed
```

Machine-readable JSON:

```bash
ci-log-intel analyze --url https://github.com/owner/repo/actions/runs/12345 --json
```

## Python usage

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

For raw log strings (no GitHub fetch):

```python
from ci_log_intelligence import analyze_log

result = analyze_log("STEP: test\nERROR build failed\nException: boom")
for failure in result.detected_failures:
    print(failure.type, failure.anchor_lines, failure.extracted_fields)
```

## How it works

The pipeline is deterministic and heuristic — no LLM in the loop. A set of `Detector` plugins scans each parsed line and emits typed `DetectedFailure` records; the framework clusters anchors, expands context (step-bounded), suppresses noise, scores, classifies, and ranks.

### Detectors shipped in v1

| Detector | Severity | What it catches |
|---|---|---|
| `hash_mismatch` | 2 | `file hashes don't match` paired with `--- FAIL:` in the same step (golden-file failures) |
| `go_test_fail` | 2 | Standalone `--- FAIL: TestName` from `go test` (not paired with hash mismatches) |
| `pytest_fail` | 2 | `FAILED tests/x.py::test_y - …` summary lines with traceback pairing |
| `rust_test_fail` | 2 | `test foo::bar ... FAILED` paired with `thread '…' panicked at` |
| `junit_xml` | 2 | `<testcase>...<failure>` / `<error>` fragments embedded in log streams |
| `build_error_rust` | 3 | `error[E####]:` + `-->` location, plus bare cargo summaries |
| `build_error_go` | 3 | `./pkg/file.go:line:col: message` |
| `build_error_npm` | 3 | Multi-line `npm ERR!` / `yarn error` blocks |
| `build_error_make` | 3 | `make: *** [target] Error N` |
| `build_error_gcc` | 3 | `file:line:col: error: …` with note continuation (gcc/clang) |
| `generic` | 1–3 | Hardened keyword fallback (`Traceback`, `Exception`, `ERROR`, `FAILED`, etc.) with word boundaries, case-insensitive matching, and a benign-mention filter (`"0 errors"` won't anchor) |

Build errors at severity 3 outrank test failures at severity 2, so when a build broke *before* any test ran the build error is correctly selected as `root_cause` and the cascading test failures show as `symptom`s.

### Adding a detector

Each detector is a single file under `ci_log_intelligence/reducer/detectors/`. Implement the `Detector` Protocol (one `scan()` method that returns a list of `DetectedFailure` records) and add yourself to the registry. The framework handles clustering, expansion, scoring, classification, and the typed-record output.

See [architecture.md](architecture.md) for the full pipeline description, data contracts, and design rationale.

## CI-aware comparison

When you give it a PR URL, the server fetches **both** failed and passed jobs in the same workflow run. Failed jobs go through the full reducer; passed jobs use targeted extraction (matching step IDs, test names, or assertion text from failed blocks). A cross-run analyzer then surfaces insights like:

- "Failure occurs only in variant `snowflake` for job group `test`."
- "Step `build-stage` is present in passed runs but missing in failing run for job group `test`."
- "Test `foo` behaves differently between passed and failed runs."

These come back in `cross_run_insights` so the agent can quickly see whether a failure is environment-specific, a regression, or flaky.

## HTTP API

If you'd rather not use MCP, there's a small FastAPI endpoint for raw-log analysis:

```bash
uvicorn ci_log_intelligence.api:app --reload
```

```bash
curl -X POST http://127.0.0.1:8000/analyze \
  -H "Content-Type: application/json" \
  -d '{"log":"STEP: test\nERROR build failed\nException: boom"}'
```

## Testing

```bash
python -m unittest discover -s tests -v
```

250+ tests covering each detector, the cache, the MCP tool surface, and end-to-end scenarios across multiple detector types.

## Known limitations

- All specialized detectors are severity 2 or 3 and tiebreak on earliest anchor line. A `specificity` weighting on `DetectedFailure` is on the v1.1 roadmap.
- Windows-style paths (`C:\src\foo.cpp:5:1:`) may not parse correctly in the GCC build-error detector. Linux CI only for now.
- The JUnit XML detector caps at 50 records per scan; consumers should check `extracted_fields.get("truncated", False)`.
- Long-running Go tests with `(1m30s)` duration format report the seconds tail only.

See [architecture.md](architecture.md#known-limitations) for the full list.

## Contributing

Issues and PRs welcome. The codebase is small (~2.5K LOC + tests) and the detector framework is designed to make adding a new language / tool a single-file change. Run the tests, follow the existing patterns in `ci_log_intelligence/reducer/detectors/`, and open a PR.

## License

MIT. See [LICENSE](LICENSE).
