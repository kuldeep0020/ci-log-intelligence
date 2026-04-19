# CI Log Intelligence System (MCP + GitHub Integration)

## Goal

Automatically process CI failures, reduce logs to high-signal regions,
and generate Codex-ready debugging context.

## Architecture

CI Failure → GitHub Action → Fetcher → Parser → Reducer → Summarizer →
MCP Server → PR Comment

## Core: Reducer Algorithm

-   Detect error lines (ERROR, Exception, FAIL, Traceback)
-   Extract ±40--50 lines context
-   Create LogBlocks
-   Merge overlapping blocks
-   Score:
    -   Error hits (+2)
    -   Stack traces (+10)
    -   Length penalty
-   Rank top K blocks

## LogBlock Schema

-   start: int
-   end: int
-   content: list\[str\]
-   score: float
-   reasons: list\[str\]

## MCP API

POST /ci/analyze

Input: { "url": "...", "provider": "github" }

Output: { "summary": "...", "blocks": \[...\], "formatted_logs": "...",
"root_causes": \[...\] }

## Edge Cases

-   Large logs
-   Multiple failures
-   Missing traces
-   Rate limits

## Key Insight

Reducer = intelligence layer


# Phase 3.5: MCP Integration + CI-Aware Intelligence Layer

## Objective

Extend the CI Log Intelligence system into a locally usable MCP (Model Context Protocol) tool that:

1. Accepts CI URLs or PR links
2. Fetches relevant CI runs and logs via GitHub
3. Correlates failed and passed runs
4. Reduces logs using the existing reducer
5. Returns structured, high-signal output for AI agents (Codex, Claude, Copilot)

This transforms the system from a library/API into an AI-native debugging tool.

---

## High-Level Flow

```
User Input (PR URL / CI URL)
        ↓
CI Resolver (identify runs/jobs)
        ↓
GitHub Fetcher (logs for failed + passed runs)
        ↓
Log Normalizer
        ↓
Reducer (existing system)
        ↓
Cross-Run Analyzer (failed vs passed comparison)
        ↓
Structured Output (MCP response)
        ↓
AI Agent
```

---

## Supported Inputs

### 1. Pull Request URL
Example:
```
https://github.com/org/repo/pull/123
```

Behavior:
- Fetch latest workflow runs associated with the PR
- Identify failed and passed runs
- Group jobs by logical test dimension (e.g., warehouse)

---

### 2. Workflow Run URL
Example:
```
https://github.com/org/repo/actions/runs/123456
```

Behavior:
- Fetch all jobs within the run
- Identify failed and passed jobs

---

### 3. Job URL
Example:
```
https://github.com/org/repo/actions/runs/123456/jobs/789
```

Behavior:
- Fetch only that job’s logs

---

## GitHub Integration

Use local authentication via:
- gh CLI (preferred)
- or GITHUB_TOKEN environment variable

Capabilities:
1. Resolve PR → workflow runs
2. Fetch workflow metadata
3. Fetch jobs per run
4. Download logs
5. Normalize logs into plain text

---

## Log Normalization

All logs must be converted into:

```
NormalizedLog:
  source: (run_id, job_id, job_name)
  status: passed | failed
  content: raw log string
```

---

## Multi-Run Correlation (CRITICAL)

### Problem
Same test runs across multiple environments (e.g., warehouses):
- Some fail
- Some pass

### Requirements

1. Group jobs by logical equivalence (e.g., same test suite)
2. Identify failing vs passing variants
3. Enable comparison:
   - failed logs → full reduction
   - passed logs → targeted extraction

---

## Passed Log Extraction Strategy

Do NOT fully process passed logs.

Instead:
- Match step_id or test name
- Extract nearby context of failure
- Keep output minimal but comparable

---

## Reducer Usage

- Full reducer → failed logs
- Targeted reducer → passed logs

---

## Cross-Run Analysis Layer

New module:
```
reducer/comparison/
```

Responsibilities:
- Compare failed vs passed blocks
- Detect divergence
- Generate insights

Example insights:
- "Failure occurs only in warehouse X"
- "Step Y behaves differently in passing run"

---

## CLI Interface (FIRST-CLASS ENTRYPOINT)

CLI is a primary way to use the system locally.

### Command

```
ci-log-intel analyze --url <ci_url>
```

### Examples

```
ci-log-intel analyze --url https://github.com/org/repo/pull/123
ci-log-intel analyze --url https://github.com/org/repo/actions/runs/123456
```

### Optional Flags

```
--include-passed
--max-passed-runs 3
--json
```

### Behavior

1. Resolve CI URL
2. Fetch logs via GitHub
3. Run reducer
4. Perform cross-run analysis
5. Output:
   - human-readable summary (default)
   - JSON (if --json)

---

## MCP Server

Expose system via MCP.

### Tool

```
name: analyze_ci_failure
```

### Input

```
{
  "ci_url": "string"
}
```

### Output Schema

```
{
  "root_cause": {
    "summary": "string",
    "log_excerpt": "string",
    "confidence": float
  },
  "failed_blocks": [
    {
      "start_line": int,
      "end_line": int,
      "summary": "string"
    }
  ],
  "passed_context": [
    {
      "job_name": "string",
      "excerpt": "string"
    }
  ],
  "cross_run_insights": [
    "string"
  ],
  "metadata": {
    "total_runs_analyzed": int,
    "failed_runs": int,
    "passed_runs": int
  }
}
```

---

## Design Constraints

- Reducer must remain independent
- Fetcher must be pluggable
- System must be deterministic
- Efficient for large logs
- Avoid redundant processing

---

## Performance Considerations

- Limit runs analyzed (default: 3–5)
- Stream log processing
- Optional caching layer

---

## Observability

Track:
- runs fetched
- logs processed
- reduction ratio
- time per stage

---

## Future Extensions

- Support other CI systems
- Smarter diffing
- LLM summarization layer
- UI integration

---

## Summary

This layer enables:
- CI-aware ingestion
- cross-run intelligence (failed vs passed)
- MCP-based access for AI agents

Result:
AI agents receive high-signal failure context enriched with comparison from passing runs.