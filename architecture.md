# CI Log Intelligence -- Architecture

## Goal

Return focused CI failure context from a PR / CI URL so the calling agent doesn't have to ingest 50K+ line raw logs. The MCP server is the boundary: it does the heavy parsing and reduction in a separate process and returns a compact structured failure report.

## Pipeline

```text
raw log
  -> parse (signal patterns + step boundaries + timestamp prefix)
  -> run_detectors (plugin scan, see "Detector framework")
  -> detected_failures_to_anchors
  -> build_clusters       (same step, gap < 10 lines)
  -> expand_context       (step-bounded +/- 20 lines, stack-trace extension)
  -> suppress_noise       (blank, separator, duplicate)
  -> merge_blocks         (overlapping or near-adjacent in same step)
  -> score_blocks         (severity_weight + signal_density - duplicate_penalty)
  -> classify_blocks      (detector classification_claim wins; else signal-based)
  -> rank_blocks          (-score, classification priority, start_line)
  -> _build_report        (one typed FailureRecord per scored block;
                           root_cause selected via select_root_cause)
```

The single entry points are:

- `analyze_log(raw_text)` -- returns `ReductionResult` for one log.
- `analyze_ci_url(url, ...)` -- resolves the URL, fetches every relevant job, reduces each failed log, extracts targeted context from passed logs, and assembles a `CIAnalysisReport`.

## Detector framework

Detectors are pluggable scanners over the parsed-line stream. Each implements the `Detector` protocol:

```python
class Detector(Protocol):
    name: str
    failure_type: str

    def scan(
        self,
        parsed_lines: Sequence[ParsedLine],
        job_context: JobContext,
    ) -> list[DetectedFailure]: ...
```

`JobContext` carries the per-job inputs (`job_name`, `run_id`, `repo`) so detectors that need cross-line correlation -- such as `HashMismatchDetector` inferring `warehouse_target` from the job name -- get them without grovelling through globals.

Each `DetectedFailure` records:

- `type` -- failure category discriminator (`hash_mismatch`, `build_error_rust`, ...). Stable across anchors produced by the same detection.
- `anchor_lines` -- one or more line numbers (cross-line detectors emit multiple).
- `severity` -- 1 (informational/flake), 2 (failure), 3 (root-cause-strength). Drives scoring.
- `classification_claim` -- if the detector knows the classification (`root_cause`, `symptom`, `flaky`), otherwise `None` (classifier decides).
- `extracted_fields` -- type-specific payload surfaced in the report's typed record (e.g. `test_name`, `file_path`, `warehouse_target`). Schema is per-`type`.
- `suggested_block_range` -- optional advisory expansion bounds for the cluster.
- `anchor_type` -- optional override of the `Anchor` type emitted to the downstream cluster pass.

The built-in registry (in `ci_log_intelligence/reducer/detectors/__init__.py`):

| Name | Severity | One-line description |
|------|----------|----------------------|
| `hash_mismatch` | 2 | `file hashes don't match` paired with nearest `--- FAIL:` in same step |
| `go_test_fail` | 2 | Standalone `--- FAIL:` markers not already claimed by hash-mismatch pairing |
| `pytest_fail` | 2 | `FAILED tests/x.py::test_y` summary lines paired with the `______ test_x ______` traceback header |
| `rust_test_fail` | 2 | `test foo ... FAILED` paired with `thread '...' panicked at ...` |
| `junit_xml` | 2 | `<testcase>...<failure>` / `<error>` fragments embedded in log streams |
| `build_error_rust` | 3 | `error[E####]: ...` + `-->` location, and bare cargo summary lines |
| `build_error_go` | 3 | `./pkg/file.go:line:col: message` |
| `build_error_npm` | 3 | `npm ERR!` / `yarn error` blocks |
| `build_error_make` | 3 | `make: *** [target] Error N` recipe failures |
| `build_error_gcc` | 3 | `file:line:col: error: ...` with note/caret continuation |
| `generic` | 1-3 | Hardened keyword fallback (`Traceback`, `Exception`, `ERROR`, `FAILED`, `AssertionError`, `WARNING`, `Retrying`) with benign-mention filtering (`0 errors`, `no failures`) |

### Resolution rule (specialized wins over generic)

When multiple detectors fire on lines that land inside the same scored block, `resolve_failure_type` picks the FailureRecord `type`:

1. If any specialized detector (anything except `generic`) contributed an anchor inside the block, choose the most specific specialized type. Ties between specialized types break by highest severity, then earliest anchor line.
2. Otherwise the block is `type="generic"` and `extracted_fields.signal_names` lists the contributing signal names.

This rule keeps the typed-record discriminator stable even when the generic keyword detector and a specialized detector overlap (e.g. `FAILED tests/x.py::test_y` matches both pytest_fail and generic's `failed` pattern).

### classification_claim contract

A detector that emits `classification_claim` declares the block's classification authoritative. `classify_blocks` accepts the claim verbatim. When a block has no detector claim, classification falls back to the signal-based heuristic (traceback/exception/error -> `root_cause`; isolated warning/retrying -> `flaky`; else `symptom`).

## MCP surface

The MCP server (`ci_log_intelligence/mcp/server.py`) exposes three tools designed for an explore-then-drill flow:

```text
list_failed_jobs(url)          # cheap map: job names + failure types present
   -> analyze_ci_failure(url,  # main typed-record analysis
                         top_k=3,
                         failure_types=[...],
                         include_passed=True)
   -> get_block(job_url,       # drill into one block by index
                block_index=0,
                surround=5)
```

Tools:

- `list_failed_jobs(ci_url)` -- returns `{ jobs: [{ run_id, job_id, job_name, conclusion, job_url, block_count, failure_types_present, classifications }], metadata: {...} }`. No per-block content -- just enough for the agent to decide where to look next.
- `analyze_ci_failure(ci_url, top_k=3, failure_types=None, include_passed=True, max_passed_runs=1)` -- returns the full `CIAnalysisReport` shape with the failures list filtered by `failure_types` and truncated to `top_k`. `metadata.failures_total` carries the PRE-filter, PRE-truncation count so the caller can detect truncation.
- `get_block(ci_url, block_index, surround=5)` -- returns one block's full line stream with `surround` context lines on either side. `ci_url` must be a job-scoped URL.

### Caching

A process-local `JobCache` keyed on `(repo, run_id, job_id)` stores both the raw `ParsedLine[]` and the `ReductionResult` per failed job. CI job logs are immutable once a job finishes, so the cache has no TTL and uses LRU eviction. The cache-aware fetch path (`fetch_with_cache_awareness`) skips the GitHub log-content fetch when the cache already covers a job, and the cache hit also skips parse + reduce -- the dominant cost. As a result, the three-tool sequence above hits the GitHub text endpoint at most once per failed job total, not once per tool call.

## CI-aware comparison

Passed-job context extraction is intentionally NOT a full reduction pass. `extract_passed_context` walks each passed log looking for matches on the corresponding failed job's step IDs, test names, and nearby line content, and emits a `PassedContextExcerpt` of just those windows. This keeps passed logs cheap regardless of size.

`analyze_cross_run` then compares failed and passed groups by their logical job name (warehouse-stripped) and emits deterministic insights:

- variant-only failures (failed in `postgres`, passed in `snowflake`)
- missing steps in failed runs that succeeded in passed runs
- query/result differences when both runs hit the same step

## Schema

The MCP `analyze_ci_failure` tool returns `CIAnalysisReport.to_dict()`:

```json
{
  "root_cause": {
    "summary": "string",
    "log_excerpt": "string",
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
      "summary": "string",
      "log_excerpt": "string",
      "extracted_fields": { "test_name": "...", "warehouse_target": "postgres" }
    }
  ],
  "passed_context": [{ "job_name": "string", "excerpt": "string" }],
  "cross_run_insights": ["string"],
  "metadata": {
    "total_runs_analyzed": 3,
    "failed_runs": 1,
    "passed_runs": 2,
    "failures_returned": 1,
    "failures_total": 1
  }
}
```

`failures` is a discriminated union on `type`. The current set of `type` values and the typed `extracted_fields` per type:

| `type` | Typed `extracted_fields` |
|--------|--------------------------|
| `hash_mismatch` | `test_name?`, `warehouse_target?`, `job_name?` |
| `go_test_fail` | `test_name`, `framework`, `duration_seconds?`, `package?` |
| `pytest_fail` | `test_id`, `framework`, `assertion_message?` |
| `rust_test_fail` | `test_name`, `framework`, `panic_message?`, `panic_location?` |
| `junit_xml` | `test_name`, `classname`, `element_type`, `message`, `framework`, `truncated?` |
| `build_error_rust` | `language`, `message`, `error_code?`, `file_path?`, `line?`, `column?` |
| `build_error_go` | `language`, `file_path`, `line?`, `column?`, `message` |
| `build_error_npm` | `tool`, `error_code?`, `errno?` |
| `build_error_make` | `target`, `exit_code`, `makefile?`, `makefile_line?` |
| `build_error_gcc` | `language`, `file_path`, `severity_text`, `line?`, `column?`, `message?` |
| `generic` | `signal_names: [string]` |

Fields suffixed `?` are present only when the source line carried the data (e.g. `--- FAIL: TestX` without `(45.3s)` omits `duration_seconds`).

## Known limitations

- Specificity weighting: all specialized detectors emit severity 2 or 3. When two specialized detectors fire on the same block, the tiebreak is `min(anchor_lines)`. Future work: per-detector specificity score.
- Windows-style paths (`C:\src\foo.cpp:5:1:`) may not parse correctly in the GCC build-error detector. Linux-only CI in v1.
- JUnit XML detector caps at 50 records per scan; consumers must check `extracted_fields.get("truncated", False)`.
- Long-running Go tests with `(1m30s)` duration format report the seconds tail only.
