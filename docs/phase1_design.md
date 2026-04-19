# PHASE 1 Design: CI Log Intelligence System

## 1. Objective

Build a system that ingests CI logs, isolates the highest-signal failure
regions, and returns debugging context compact enough for an LLM or human
reviewer to act on quickly.

Primary outcome:

- convert raw CI output into ranked failure-focused log blocks
- surface likely root causes instead of entire logs
- expose the analysis through an MCP-facing HTTP API

## 2. Scope and Non-Goals

### In scope

- module boundaries and responsibilities
- reducer algorithm design
- external and internal data contracts
- operational and parsing edge cases

### Out of scope in Phase 1

- repository structure
- concrete class layout
- dependency selection
- code, tests, CI workflow implementation

## 3. End-to-End Architecture

```text
CI Failure
  -> GitHub Action
  -> Fetcher
  -> Parser
  -> Reducer
  -> Summarizer
  -> MCP Server
  -> Publisher
  -> PR Comment / Agent Consumer
```

The key design decision is that the reducer is the intelligence layer. It must
compress noisy logs into a few ranked, evidence-backed blocks before any
summarization occurs.

## 4. Module Definitions

### 4.1 GitHub Action Adapter

Responsibility:

- trigger on failed workflow runs or failed jobs
- collect workflow context: repo, commit SHA, PR number, workflow name, job
  name, attempt number
- resolve log URLs or artifact references
- call the analysis endpoint
- publish results back to GitHub as a PR comment, workflow summary, or check
  output

Inputs:

- GitHub workflow event payload
- repository metadata
- authentication token

Outputs:

- `AnalyzeCIRequest`
- rendered markdown comment or status payload

Failure handling:

- retries on transient GitHub API failures
- graceful degradation if logs cannot be fetched
- bounded comment size

### 4.2 Fetcher

Responsibility:

- download raw CI logs from a provider-specific source
- normalize compression and transfer encoding
- preserve provenance metadata and source ordering
- persist raw content behind a stable storage reference

Inputs:

- log URL or provider-native identifiers
- provider type, initially `github`
- auth context if required

Outputs:

- `RawLogDocument`

Failure handling:

- auth failures
- rate limiting
- partial downloads
- unsupported content types

### 4.3 Parser

Responsibility:

- turn raw provider content into normalized line-oriented records
- strip transport noise such as ANSI escape codes when configured
- detect sections, steps, attempts, and stream provenance
- emit typed line-level signals for reducer use

Inputs:

- `RawLogDocument`

Outputs:

- `ParsedLog`

Key parsing responsibilities:

- split into ordered lines
- preserve stable line numbers
- detect timestamps, step boundaries, retry attempts, log levels, stack frames,
  test failures, panic markers, exit codes, and CI prefixes
- annotate likely cause lines, wrapper lines, and noisy repetition

### 4.4 Reducer

Responsibility:

- detect candidate failure anchors
- cluster related anchors into distinct failure groups
- expand high-value clusters into contextual windows
- merge, score, rank, and return top-k high-signal slices

Inputs:

- `ParsedLog`

Outputs:

- `ReducedLog`

Design constraints:

- deterministic and explainable scoring
- root cause should outrank terminal wrappers
- safe behavior for large, partial, or interleaved logs

### 4.5 Summarizer

Responsibility:

- convert ranked blocks into concise root-cause hypotheses
- preserve evidence references back to block ids and line ranges

Inputs:

- `ReducedLog`

Outputs:

- `SummaryResult`

Constraint:

- summarizer must not invent failure evidence not present in reducer output

### 4.6 MCP / HTTP Server

Responsibility:

- expose a stable endpoint for analysis
- validate requests and return machine-consumable responses
- orchestrate fetch -> parse -> reduce -> summarize

Initial API:

- `POST /ci/analyze`

Outputs:

- `AnalyzeCIResponse`

### 4.7 Publisher

Responsibility:

- transform semantic analysis output into GitHub-friendly markdown
- render concise summary plus expandable log excerpts

Inputs:

- `AnalyzeCIResponse`
- rendering options such as excerpt limits and redaction policy

Outputs:

- markdown body

### 4.8 Coupling Rules

The pipeline should remain loosely coupled across stages.

Coupling constraints:

- Fetcher knows provider transport and auth only. It must not contain parser or
  reducer heuristics.
- Parser knows log structure and feature extraction only. It emits typed signals
  and provenance, not markdown or GitHub presentation.
- Reducer consumes semantic line records and provenance only. It must not depend
  on HTTP or publisher behavior.
- Summarizer consumes reducer evidence only and produces semantic hypotheses.
- Publisher is the only module that knows GitHub comment formatting.
- MCP / HTTP server orchestrates modules but does not duplicate reducer or
  publisher logic.

## 5. Reducer Design

### 5.1 Reducer Goals

- maximize signal density
- preserve root-cause evidence
- support multiple independent failures in one log
- remain predictable under noisy or very large logs
- rank actionable causes above terminal symptoms
- degrade safely when the log is partial, interleaved, or lacks a clean stack
  trace
- suppress repetitive or low-information noise aggressively before ranking
- classify failures into stable machine-consumable categories

### 5.2 Reducer Memory Model

The reducer contract must support large logs without duplicating the entire log
in memory across stages.

Production design:

- raw content is stored once behind a `storage_ref`
- parser emits line-level metadata and byte offsets into stored content
- reducer operates primarily on typed line records and offsets
- only selected final blocks materialize excerpt text for the response

Design consequences:

- a full-log `raw_text` field is not required in long-lived contracts
- small logs may remain fully in memory, but large logs may spill to disk
  without changing interfaces
- output blocks may carry excerpt text, but internal stages should prefer
  references and typed evidence over duplicated strings

### 5.3 Reducer Inputs

The reducer operates on `ParsedLog`, which is an ordered sequence of line
records with extracted signals and provenance metadata.

Each parsed line should expose:

- line number
- line reference into source storage
- bounded preview text for diagnostics
- job or section provenance
- step or section id
- step kind or step role if available, such as `setup`, `build`, `test`,
  `deploy`, `postprocess`
- step index within the job if available
- attempt id if available
- stream source such as `stdout` or `stderr`
- timestamp if parseable
- typed signal kinds such as `error`, `exception`, `traceback`,
  `stack_frame`, `test_failure`, `timeout`, `oom`, `panic`, `exit_code`,
  `command_failure`, `network_failure`
- semantic fingerprint for deduplication and clustering
- noise markers such as `progress_line`, `download_tick`, `heartbeat`, or
  `repeated_frame`

### 5.4 Candidate Anchor Detection

Anchor detection is the first intelligence pass. A line becomes an anchor when
it likely indicates failure cause or immediate failure evidence.

Anchor classes:

- root-cause signals
  - exception class and message line
  - innermost `Caused by:` line
  - assertion line with expected vs actual values
  - compile or lint error with file and line
  - failing command line or shell error
  - timeout, OOM, or signal-termination line with culprit process
- supporting context signals
  - stack frames
  - test framework failure headers
  - failing test name
  - dependency install and image pull failures
- infrastructure failures
  - network timeout
  - DNS resolution errors
  - rate limiting
  - permission denied
  - auth failures
  - remote service unavailability
- terminal wrapper signals
  - explicit non-zero exit status
  - `Process completed with exit code X`
  - generic `ERROR` or `FAIL` lines without causal detail

Anchor metadata:

- anchor kind
- signal family
- anchor tier
- severity class
- confidence
- line number
- cluster hints such as test identifier, command fingerprint, exception
  fingerprint, and file path

Anchor rules:

- anchors should be weighted hierarchically
  - tier 1: direct root-cause lines
  - tier 2: supporting context such as stack frames or failing test headers
  - tier 3: wrapper and terminal status lines
- root-cause signals are preferred over wrapper signals
- wrapper signals must not dominate scoring when a more specific nearby anchor
  exists in the same cluster
- repeated identical anchors should be collapsed before scoring to avoid spam

### 5.5 Failure Clustering

Before block expansion, anchors should be partitioned into failure clusters.
This is the main safeguard against merging unrelated failures.

Cluster identity should combine:

- workflow job id
- step id or section id
- attempt number
- test identifier if available
- command fingerprint if available
- exception fingerprint if available

Clustering rules:

- anchors with the same test identifier belong to the same cluster unless they
  occur in different attempts
- anchors with the same command fingerprint and close line-distance may share a
  cluster
- teardown or wrapper failures attach to the nearest causal cluster rather than
  becoming dominant standalone clusters unless they are the only evidence
- when provenance fields are present, attempt or job boundaries must never be
  ignored during clustering

Cluster outputs:

- `cluster_id`
- cluster fingerprint
- anchor list
- provenance tuple: job, step, attempt
- candidate root-cause anchors

### 5.6 Context Expansion

Each cluster expands into one or more candidate blocks centered around its best
anchors.

Default expansion:

- 40 lines before
- 50 lines after

Expansion overrides:

- extend upward to include the start of a stack trace
- extend downward while stack frames continue
- extend to capture multiline exception causes such as `Caused by:`
- extend to include the enclosing test case or step heading when nearby
- extend to include the failing command or setup line immediately preceding the
  root cause
- remain within the same job, step, and attempt unless explicit evidence shows
  cross-boundary causality
- cap total block length to a hard maximum to avoid runaway blocks

Recommended hard limits:

- soft target: 90 to 140 lines
- hard max: 220 lines per block before truncation markers are inserted

Truncation behavior:

- if a block exceeds max size, preserve the anchor-centered region and any
  contiguous stack trace
- preserve the highest-weight causal line even when context must be dropped
- add metadata noting truncation

### 5.7 Block Merge Rules

Overlapping or near-adjacent candidate blocks should be merged when they likely
describe the same failure.

Merge conditions:

- overlapping line ranges
- gap smaller than a merge threshold, e.g. 15 lines
- blocks belong to the same failure cluster
- anchor kinds suggest causal continuity, such as exception line followed by
  stack frames and then an exit-code line

Do not merge when:

- blocks belong to different tests, commands, attempts, or jobs
- the only connection is repeated boilerplate
- there is a long unrelated region between anchors

Merged block behavior:

- union line ranges
- unify evidence and remove duplicates by fingerprint
- aggregate anchor counts by kind
- recompute score on the merged block, not by naive score addition

### 5.8 Scoring Model

The scoring model must remain simple, deterministic, and inspectable.

Scoring principle:

- actionable causal evidence should score higher than terminal symptoms
- every returned score should have a machine-readable breakdown
- block ranking should account for step criticality and causal recency

Signal families:

- root-cause signals
  - exception message with class
  - assertion diff
  - compile error with file:line
  - failed command line
  - timeout, OOM, or signal termination with culprit process
- context signals
  - stack trace with root frame
  - failing test identifier
  - file path, module name, SQL object, dependency name, or endpoint
  - `stderr` channel
- wrapper signals
  - non-zero exit code
  - generic failure footer
  - repeated error lines without new specificity

Recommended weights:

- root exception line: `+14`
- assertion failure with test name: `+12`
- compile error with file and line: `+12`
- failed command line: `+10`
- timeout, OOM, or signal termination with culprit process: `+10`
- stack trace with identifiable root frame: `+8`
- failing test identifier: `+6`
- infrastructure marker with concrete endpoint or permission subject: `+6`
- exit code line: `+2`
- generic wrapper line: `+1`

Position and causality adjustments:

- earliest specific cause inside a cluster gets `causal_position_bonus`
- lines on `stderr` receive a mild bonus over equivalent `stdout` lines
- innermost exception lines outrank outer wrappers
- more recent failure clusters receive a bounded `recency_bias` so the reducer
  prefers the latest actionable failure when two blocks are otherwise similar

Step-aware adjustments:

- failures in critical execution steps receive `step_priority_bonus`
- recommended order is:
  - `build`, `compile`, `install`, `migrate`, `setup`: highest
  - `test`: medium
  - `deploy`, `postprocess`, `cleanup`: lower unless they are the only failing
    step
- step-aware scoring should never override a clear causal-evidence advantage; it
  is a tie-breaker class, not a primary signal

Hierarchical anchor weighting:

- tier-1 anchors contribute full weight
- tier-2 anchors contribute reduced weight
- tier-3 anchors contribute minimal weight and are penalized if they dominate
  the block

Recency bias rules:

- recency is measured within the same job attempt unless provider timestamps
  prove otherwise
- recency bias must be bounded so a late generic footer cannot outrank an
  earlier specific cause
- when two distinct clusters have similar causal evidence, prefer the later
  cluster because it usually reflects the terminal job outcome

Penalties:

- length penalty for oversized blocks
- repetition penalty for duplicate fingerprints
- wrapper-dominance penalty when the block is mostly terminal footer content
- ambiguity penalty when evidence is generic and lacks a stable primary cause
- noise penalty when the excerpt is dominated by progress output, retries, or
  repeated frames

Suggested formula:

```text
score =
  root_cause_signal_score
  + context_signal_score
  + causal_position_bonus
  + step_priority_bonus
  + recency_bias
  + specificity_bonus
  + stream_bonus
  - length_penalty
  - repetition_penalty
  - noise_penalty
  - wrapper_dominance_penalty
  - ambiguity_penalty
```

Scoring notes:

- `specificity_bonus` rewards blocks containing concrete symbols such as file
  paths, test names, exception classes, SQL errors, command names, or module
  names
- `length_penalty` should be mild for useful context and aggressive only once a
  block becomes too broad
- a block without a primary cause line should almost never outrank a block with
  one unless the log truly lacks causal evidence
- final scores should be clamped to a bounded range for easier interpretation
- each returned block should include `score_breakdown`
- score breakdown should call out tier contributions, step bonus, and recency
  bias separately

### 5.9 Ranking and Selection

After merge and score:

- select the top-scoring block from each failure cluster first
- then fill any remaining slots by global score
- break ties by earlier causal evidence, then smaller block size
- return top `K` blocks, default `K = 3`

Diversity rule:

- avoid returning near-duplicate blocks from the same cluster
- treat two blocks as duplicates when overlap is high and their dominant
  evidence fingerprint is the same
- keep the higher-score block, or the smaller more precise block if scores are
  similar
- if two blocks overlap heavily, keep the stronger one unless both represent
  distinct failure reasons

Explicit deduplication rules:

- two candidate blocks are duplicates when all of the following hold:
  - overlap ratio is at least `0.60`
  - same `cluster_id`
  - same primary evidence kind
  - same dominant semantic fingerprint, or same primary cause line after
    normalization
- if duplicate blocks differ only by extra wrapper lines, keep the smaller block
- if duplicate blocks differ by one containing the command line and the other
  not, keep the one with the command line
- deduplication must run twice:
  - once before merge to collapse anchor spam
  - once after scoring to remove near-identical returned blocks

No-anchor fallback:

- if the reducer finds no causal anchors but the job is known to have failed, it
  must emit a fallback block around the strongest terminal evidence
- fallback results must carry a warning such as `no_primary_anchor`
- fallback blocks should receive lower confidence than normal causal blocks

### 5.10 Root Cause Extraction

The reducer should attach non-LLM hints that help the summarizer.

Examples:

- likely root cause line
- exception class
- failing test identifier
- failing command
- failing module or file path
- probable infrastructure category: network, auth, timeout, resource exhaustion
- affected service, host, or artifact when present

These hints should be extracted conservatively and stored as evidence, not final
claims.

Root-cause extraction rules:

- prefer the earliest specific line inside a cluster
- prefer exception or assertion lines over outer wrapper messages
- preserve multiple hypotheses only when the evidence is genuinely ambiguous

Failure classification:

- every returned block should be assigned a stable failure class
- initial classes should include:
  - `application_error`
  - `test_failure`
  - `build_failure`
  - `dependency_failure`
  - `infrastructure_network`
  - `infrastructure_auth`
  - `resource_exhaustion`
  - `timeout`
  - `unknown`
- classification should be evidence-backed and may be low-confidence, but it
  must be present for downstream automation

### 5.11 Reducer Output Requirements

Each returned block must include:

- stable id
- cluster id
- line range
- excerpt lines
- score
- score breakdown
- typed evidence
- primary cause line
- failure classification
- cluster provenance
- truncation metadata
- diagnosis confidence

### 5.12 Complexity and Performance

Target behavior:

- single-pass feature extraction in parser
- linear or near-linear reducer complexity in number of lines
- bounded memory amplification

Expected complexity:

- anchor detection: `O(n)`
- clustering: `O(a log a)` or better, where `a` is anchor count
- context expansion: `O(a)` to `O(n)` depending on implementation
- merge after sorting ranges: `O(a log a)`
- scoring: `O(b)` over merged blocks

Large-log strategy:

- stream or chunk parsing if logs exceed memory thresholds
- spill raw content and line offsets to temporary storage for large inputs
- maintain line offsets for stable ranges
- optionally suppress extremely repetitive regions before scoring
- materialize text only for the selected final excerpts

Noise suppression stage:

- run a dedicated suppression pass before clustering and again before final
  excerpt materialization
- suppression targets include:
  - progress bars
  - dependency download ticks
  - repeated stack frames
  - heartbeat lines
  - duplicated retry boilerplate
- suppression must preserve line numbering and provenance even when lines are
  omitted from the rendered excerpt

Redaction placement:

- redact secrets after parsing and line-offset capture but before excerpt
  materialization and publishing
- reducer scoring should operate on normalized semantic signals, not redacted
  placeholder text
- redaction must not change block line numbers or break evidence references

Operational limits:

- if log size exceeds the supported maximum even for spill-to-disk mode, fail
  explicitly with a typed warning instead of silently processing a truncated
  subset
- protect parsing heuristics from pathological single-line inputs by bounding
  line length and per-line processing time

### 5.13 Reducer Pseudocode

```text
parse raw log into ParsedLog
anchors = detect_anchors(parsed_log.lines)
clusters = cluster_anchors(anchors, parsed_log.lines, parsed_log.sections)
candidate_blocks = []

for cluster in clusters:
    seeds = select_cluster_seeds(cluster)
    for seed in seeds:
        block = expand_context(seed, parsed_log)
        candidate_blocks.append(block)

candidate_blocks = suppress_noise(candidate_blocks, parsed_log)
candidate_blocks = deduplicate_candidate_blocks(candidate_blocks)
merged_blocks = merge_blocks(candidate_blocks)

for block in merged_blocks:
    block.score = score_block(block)
    block.evidence = extract_evidence(block)
    block.primary_cause_line = pick_primary_cause_line(block)
    block.failure_classification = classify_failure(block)

ranked_blocks = rank_by_cluster_then_global(merged_blocks)
ranked_blocks = deduplicate_ranked_blocks(ranked_blocks)

if ranked_blocks is empty and log_indicates_failure(parsed_log):
    ranked_blocks = [build_terminal_fallback_block(parsed_log)]

ranked_blocks = redact_block_excerpts(ranked_blocks)
return top_k(ranked_blocks)
```

## 6. Data Contracts

These are design contracts, not implementation classes.

### 6.1 External API Request

#### `AnalyzeCIRequest`

```json
{
  "url": "https://github.com/org/repo/actions/runs/123456789/job/987654321",
  "provider": "github",
  "repo": "org/repo",
  "commit_sha": "abc123",
  "pull_request": 42,
  "job_conclusion": "failure",
  "max_blocks": 3,
  "max_excerpt_lines": 120
}
```

Fields:

- `url: str`
- `provider: Literal["github"]`
- `repo: str | null`
- `commit_sha: str | null`
- `pull_request: int | null`
- `job_conclusion: str | null`
- `max_blocks: int | null`
- `max_excerpt_lines: int | null`

Validation:

- `url` required
- `provider` required
- `max_blocks` bounded, e.g. `1 <= max_blocks <= 10`
- `max_excerpt_lines` bounded to a safe response size

### 6.2 External API Response

#### `AnalyzeCIResponse`

```json
{
  "status": "partial",
  "summary": "Pytest failed because test_user_login raised KeyError in auth/service.py.",
  "blocks": [
    {
      "id": "block-1",
      "cluster_id": "cluster-1",
      "start": 812,
      "end": 858,
      "score": 24.0,
      "primary_cause_line": 829,
      "failure_classification": {
        "kind": "test_failure",
        "confidence": 0.97,
        "source_evidence_line": 829
      },
      "excerpt": [
        {
          "number": 829,
          "text": "E   KeyError: 'user_id'",
          "stream": "stderr",
          "redacted": false
        }
      ],
      "evidence": [
        {
          "kind": "exception",
          "value": "KeyError",
          "line": 829,
          "confidence": 0.98,
          "attributes": {
            "file": "auth/service.py",
            "test": "test_user_login"
          }
        }
      ]
    }
  ],
  "root_causes": [
    {
      "summary": "KeyError in auth/service.py while running test_user_login",
      "block_id": "block-1",
      "confidence": 0.96
    }
  ],
  "failure_classifications": [
    {
      "kind": "test_failure",
      "confidence": 0.97,
      "source_evidence_line": 829
    }
  ],
  "warnings": [
    {
      "code": "partial_log",
      "message": "The provider returned a truncated job log.",
      "retryable": false,
      "line": null
    }
  ],
  "meta": {
    "provider": "github",
    "block_count": 1,
    "line_count": 2014,
    "partial": true
  }
}
```

Fields:

- `status: Literal["ok", "partial", "no_diagnosis", "error"]`
- `summary: str`
- `blocks: list[LogBlock]`
- `root_causes: list[RootCauseHypothesis]`
- `failure_classifications: list[FailureClassification]`
- `warnings: list[DiagnosticWarning]`
- `meta: AnalysisMeta`

### 6.3 Internal Contract: Raw Log

#### `RawLogDocument`

- `source_url: str`
- `provider: str`
- `workflow_job_id: str | null`
- `job_name: str | null`
- `workflow_name: str | null`
- `job_conclusion: str | null`
- `attempt: int | null`
- `content_type: str | null`
- `encoding: str | null`
- `storage_ref: str`
- `byte_length: int`
- `content_digest: str | null`
- `fetched_at: datetime`
- `partial: bool`
- `warnings: list[DiagnosticWarning]`

### 6.4 Internal Contract: Parsed Log

#### `ParsedLog`

- `source: RawLogDocument`
- `total_lines: int`
- `storage_mode: Literal["memory", "spill_to_disk"]`
- `lines: Sequence[ParsedLine]`
- `sections: list[LogSection]`
- `warnings: list[DiagnosticWarning]`

#### `ParsedLine`

- `number: int`
- `line_ref: LineRef`
- `preview: str | null`
- `timestamp: str | null`
- `stream: Literal["stdout", "stderr"] | null`
- `workflow_job_id: str | null`
- `step_id: str | null`
- `step_kind: str | null`
- `step_index: int | null`
- `attempt: int | null`
- `section_id: str | null`
- `level: str | null`
- `signal_kinds: list[str]`
- `semantic_fingerprint: str | null`
- `is_anchor_candidate: bool`
- `anchor_confidence: float | null`
- `noise_score: float`
- `noise_kinds: list[str]`

#### `LogSection`

- `id: str`
- `name: str`
- `start: int`
- `end: int`
- `kind: str`
- `workflow_job_id: str | null`
- `step_id: str | null`
- `step_kind: str | null`
- `step_index: int | null`
- `attempt: int | null`

### 6.5 Internal Contract: Reduced Log

#### `ReducedLog`

- `source: RawLogDocument`
- `total_lines: int`
- `selected_blocks: list[LogBlock]`
- `warnings: list[DiagnosticWarning]`
- `reducer_stats: ReducerStats`

#### `LogBlock`

- `id: str`
- `cluster_id: str`
- `start: int`
- `end: int`
- `excerpt: list[ExcerptLine]`
- `score: float`
- `score_breakdown: list[ScoreComponent]`
- `evidence: list[Evidence]`
- `cluster_provenance: ClusterProvenance`
- `anchor_count: int`
- `primary_cause_line: int | null`
- `failure_classification: FailureClassification`
- `truncation: TruncationMeta | null`
- `diagnosis_confidence: float | null`
- `merge_count: int`

#### `ReducerStats`

- `anchors_detected: int`
- `clusters_detected: int`
- `candidate_blocks: int`
- `merged_blocks: int`
- `returned_blocks: int`
- `no_anchor_fallback_used: bool`
- `noise_lines_suppressed: int`
- `candidate_duplicates_removed: int`
- `ranked_duplicates_removed: int`
- `redactions_applied: int`
- `suppressed_duplicate_blocks: int`

### 6.6 Internal Contract: Summary

#### `SummaryResult`

- `status: Literal["ok", "partial", "no_diagnosis", "error"]`
- `summary: str`
- `root_causes: list[RootCauseHypothesis]`
- `failure_classifications: list[FailureClassification]`
- `confidence: float | null`

### 6.7 Shared Value Objects

#### `LineRef`

- `storage_ref: str`
- `start_byte: int`
- `end_byte: int`

#### `ExcerptLine`

- `number: int`
- `text: str`
- `stream: str | null`
- `redacted: bool`
- `suppressed_context_gap: bool | null`

#### `Evidence`

- `kind: str`
- `value: str`
- `line: int | null`
- `confidence: float | null`
- `attributes: dict[str, str] | null`
- `redacted: bool | null`

#### `ScoreComponent`

- `kind: str`
- `value: float`
- `rationale: str`
- `tier: str | null`

#### `RootCauseHypothesis`

- `summary: str`
- `block_id: str | null`
- `confidence: float | null`

#### `FailureClassification`

- `kind: str`
- `confidence: float | null`
- `source_evidence_line: int | null`

#### `DiagnosticWarning`

- `code: str`
- `message: str`
- `retryable: bool`
- `line: int | null`

#### `TruncationMeta`

- `truncated: bool`
- `reason: str`
- `dropped_before: int`
- `dropped_after: int`

#### `ClusterProvenance`

- `workflow_job_id: str | null`
- `step_id: str | null`
- `step_kind: str | null`
- `step_index: int | null`
- `attempt: int | null`
- `section_id: str | null`

#### `AnalysisMeta`

- `provider: str`
- `block_count: int`
- `line_count: int`
- `partial: bool`
- `noise_suppression_applied: bool | null`
- `redaction_applied: bool | null`

## 7. Edge Cases

### 7.1 Large Logs

Risk:

- memory pressure
- low-signal boilerplate dominates
- full-log duplication exhausts worker memory under concurrency

Handling:

- spill raw content and offsets to temporary storage
- repetition suppression before scoring
- hard caps on returned block count and excerpt size
- typed warning and explicit failure when supported processing limits are
  exceeded

### 7.2 Multiple Independent Failures

Risk:

- merging unrelated failures into one oversized block
- suppressing a legitimate second failure as a duplicate

Handling:

- failure clustering before merge
- cluster-aware ranking that promotes at least one representative per cluster
- separate root-cause hypotheses per block

### 7.3 Missing Stack Traces

Risk:

- only terminal failure lines appear

Handling:

- infer likely cause from nearby stderr, command lines, or compiler output
- lower confidence
- retain terminal exit evidence when no deeper cause exists

### 7.4 Repeated Error Spam

Risk:

- the same exception repeated hundreds of times overwhelms ranking

Handling:

- collapse identical anchor fingerprints
- repetition penalty
- deduplicate near-identical candidate windows

### 7.5 Truncated or Partial Logs

Risk:

- root cause omitted before fetch cutoff
- downstream consumers over-trust incomplete evidence

Handling:

- return explicit warnings in response metadata
- lower diagnosis confidence
- propagate `partial=true` across contracts

### 7.6 ANSI / Structured CI Noise

Risk:

- unreadable content or false anchor detection

Handling:

- strip ANSI escapes
- preserve line references so rendered excerpts can still map back to originals

### 7.7 Non-UTF8 or Compressed Content

Risk:

- fetcher cannot decode content cleanly

Handling:

- decode with fallback strategy
- capture warnings
- fail explicitly if content is unusable

### 7.8 Provider Rate Limits and Auth Failures

Risk:

- analysis cannot access logs

Handling:

- typed fetch errors
- retry on transient failures only
- distinguish `retryable` from `permanent` errors

### 7.9 Very Short Logs

Risk:

- context window logic returns almost the whole log

Handling:

- allow small blocks without penalty
- skip complex clustering when the whole log is already tiny

### 7.10 Infrastructure vs Application Failures

Risk:

- summarizer mislabels infra issues as code bugs

Handling:

- explicit anchor taxonomy
- category evidence such as `network`, `auth`, `dependency`,
  `resource_exhaustion`

### 7.11 Parallel or Interleaved Job Logs

Risk:

- interleaved steps appear causally related when they are not

Handling:

- preserve job, step, attempt, and stream provenance on each parsed line
- constrain clustering and context expansion to provenance boundaries by default

### 7.12 Secret Leakage

Risk:

- returned blocks include tokens or credentials printed by CI

Handling:

- redact obvious secret patterns before publishing
- mark redacted excerpt lines
- preserve original line numbers after redaction

### 7.13 No-Anchor Failures

Risk:

- a job fails but no parser rule identifies a primary anchor

Handling:

- emit a lower-confidence fallback block around the strongest terminal evidence
- surface a `no_primary_anchor` warning
- keep the response machine-readable rather than returning an empty success

### 7.14 Retries and Flaky Reruns

Risk:

- repeated attempts are merged into one failure narrative

Handling:

- track attempt number in provenance
- prohibit cross-attempt clustering unless explicitly requested in future phases

### 7.15 Out-of-Order or Duplicated Lines

Risk:

- provider stitching or retries produce duplicated or out-of-order lines

Handling:

- preserve original source order
- use semantic fingerprints for deduplication
- emit warnings when timestamps and line order conflict materially

### 7.16 Pathological Single-Line Output

Risk:

- huge minified payloads or binary garbage trigger slow regex processing

Handling:

- bound maximum per-line processing length
- classify oversize or binary-like lines as noisy unless they contain explicit
  failure markers

## 8. Operational Design Constraints

- deterministic output for the same input
- stable line references for downstream comments
- bounded output size for MCP and GitHub comments
- observability via reducer stats and typed warnings
- conservative summarization grounded in reducer evidence
- no silent degradation on partial logs or exceeded processing limits

## 9. Phase 1 Exit Criteria

Phase 1 is complete when:

- module boundaries are defined
- reducer behavior is specified deeply enough to implement deterministically
- request, response, and internal contracts are stable
- major failure, scale, and noise edge cases are documented

No code or repository layout decisions are required to satisfy Phase 1.
