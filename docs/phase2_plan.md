# PHASE 2 Plan: Repository Structure, Module Plan, Dependencies, and Tests

## 1. Phase Goal

Translate the Phase 1 design into an implementation-ready plan without writing
runtime code yet.

Phase 2 deliverables:

- repository structure
- file and module plan
- class and function plan
- dependency plan
- test plan

Out of scope for Phase 2:

- code implementation
- test implementation
- CI workflow implementation
- packaging and deployment hardening beyond naming planned files

## 2. Repository Structure

```text
ci-log-intelligence/
├── README.md
├── requirements.txt
├── architecture.md
├── codex_build_prompt.md
├── docs/
│   ├── phase1_design.md
│   └── phase2_plan.md
├── src/
│   └── ci_log_intelligence/
│       ├── __init__.py
│       ├── config.py
│       ├── errors.py
│       ├── logging.py
│       ├── contracts/
│       │   ├── __init__.py
│       │   ├── api.py
│       │   ├── core.py
│       │   └── enums.py
│       ├── storage/
│       │   ├── __init__.py
│       │   ├── base.py
│       │   ├── memory.py
│       │   └── spill.py
│       ├── fetchers/
│       │   ├── __init__.py
│       │   ├── base.py
│       │   └── github_actions.py
│       ├── parsers/
│       │   ├── __init__.py
│       │   ├── normalize.py
│       │   ├── sectionizer.py
│       │   ├── signals.py
│       │   └── parser.py
│       ├── reducer/
│       │   ├── __init__.py
│       │   ├── anchors.py
│       │   ├── clustering.py
│       │   ├── expansion.py
│       │   ├── suppression.py
│       │   ├── merge.py
│       │   ├── scoring.py
│       │   ├── classification.py
│       │   ├── redaction.py
│       │   ├── ranking.py
│       │   └── pipeline.py
│       ├── summarizer/
│       │   ├── __init__.py
│       │   └── service.py
│       ├── publisher/
│       │   ├── __init__.py
│       │   └── github_markdown.py
│       ├── services/
│       │   ├── __init__.py
│       │   └── analyze_ci.py
│       └── api/
│           ├── __init__.py
│           ├── dependencies.py
│           ├── routes.py
│           └── app.py
├── tests/
│   ├── unit/
│   │   ├── contracts/
│   │   ├── fetchers/
│   │   ├── parsers/
│   │   ├── reducer/
│   │   ├── summarizer/
│   │   ├── publisher/
│   │   └── services/
│   ├── integration/
│   │   ├── test_analyze_ci_api.py
│   │   ├── test_analyze_ci_pipeline.py
│   │   └── test_github_fetch_to_publish.py
│   ├── fixtures/
│   │   ├── logs/
│   │   │   ├── python_traceback.log
│   │   │   ├── pytest_multi_failure.log
│   │   │   ├── javac_compile_error.log
│   │   │   ├── network_timeout.log
│   │   │   ├── oom_killed.log
│   │   │   ├── noisy_large_log.log
│   │   │   ├── partial_log.log
│   │   │   ├── interleaved_steps.log
│   │   │   ├── duplicated_retry_output.log
│   │   │   └── secret_leak_sample.log
│   │   └── api/
│   │       └── github_workflow_event.json
│   └── golden/
│       ├── reducer/
│       └── publisher/
└── .github/
    └── workflows/
        └── ci-log-intelligence.yml
```

## 3. Structural Decisions

### 3.1 `src/` layout

Use a `src/` layout to avoid import ambiguity in tests and keep packaging clean.

### 3.2 Contracts split

- `contracts/enums.py`: stable enums and constants
- `contracts/core.py`: internal models such as `RawLogDocument`, `ParsedLine`,
  `LogBlock`
- `contracts/api.py`: request and response models for HTTP and MCP boundaries

This keeps internal evolution separate from external API compatibility.

### 3.3 Reducer split

The reducer should be decomposed by concern rather than as one large module.

- `anchors.py`: candidate anchor detection
- `clustering.py`: failure-cluster construction
- `expansion.py`: contextual window logic
- `suppression.py`: noise suppression and candidate deduplication
- `merge.py`: block merge rules
- `scoring.py`: score breakdown and ranking primitives
- `classification.py`: failure classification
- `redaction.py`: excerpt-safe redaction
- `ranking.py`: cluster-aware selection and duplicate removal
- `pipeline.py`: orchestration entry point

This is the main quality control for the system. It prevents the reducer from
collapsing into one hard-to-test file.

## 4. Module and File Plan

### 4.1 Top-Level Support Files

#### `src/ci_log_intelligence/config.py`

Purpose:

- application settings
- reducer thresholds
- HTTP timeouts
- spill-to-disk thresholds
- excerpt and redaction limits

Planned contents:

- `Settings`
- `ReducerSettings`
- `FetcherSettings`

#### `src/ci_log_intelligence/errors.py`

Purpose:

- typed exceptions and error translation boundaries

Planned contents:

- `CIAnalysisError`
- `FetchError`
- `ParseError`
- `ReductionError`
- `ConfigurationError`

#### `src/ci_log_intelligence/logging.py`

Purpose:

- consistent structured logger setup
- request and pipeline correlation helpers

### 4.2 Contracts

#### `src/ci_log_intelligence/contracts/enums.py`

Planned enums:

- `Provider`
- `SignalKind`
- `AnchorTier`
- `FailureClass`
- `StepKind`
- `AnalysisStatus`
- `StorageMode`

#### `src/ci_log_intelligence/contracts/core.py`

Planned models:

- `LineRef`
- `ExcerptLine`
- `Evidence`
- `ScoreComponent`
- `RootCauseHypothesis`
- `FailureClassification`
- `DiagnosticWarning`
- `TruncationMeta`
- `ClusterProvenance`
- `AnalysisMeta`
- `RawLogDocument`
- `ParsedLine`
- `LogSection`
- `ParsedLog`
- `LogBlock`
- `ReducerStats`
- `ReducedLog`
- `SummaryResult`

#### `src/ci_log_intelligence/contracts/api.py`

Planned models:

- `AnalyzeCIRequest`
- `AnalyzeCIResponse`

Validation responsibilities:

- bound `max_blocks`
- bound `max_excerpt_lines`
- validate supported providers
- normalize optional request fields

### 4.3 Storage

#### `src/ci_log_intelligence/storage/base.py`

Planned interfaces:

- `StorageBackend`

Planned methods:

- `write_text(content: str) -> str`
- `read_range(storage_ref: str, start_byte: int, end_byte: int) -> str`
- `read_line(storage_ref: str, line_ref: LineRef) -> str`
- `delete(storage_ref: str) -> None`

#### `src/ci_log_intelligence/storage/memory.py`

Purpose:

- in-memory backend for small logs and tests

Planned class:

- `InMemoryStorageBackend`

#### `src/ci_log_intelligence/storage/spill.py`

Purpose:

- temporary-file backed storage for large logs

Planned class:

- `SpillFileStorageBackend`

### 4.4 Fetchers

#### `src/ci_log_intelligence/fetchers/base.py`

Planned interfaces:

- `LogFetcher`

Planned methods:

- `fetch(request: AnalyzeCIRequest) -> RawLogDocument`

#### `src/ci_log_intelligence/fetchers/github_actions.py`

Purpose:

- fetch GitHub Actions job logs
- normalize archive or plain-text responses
- capture provider metadata

Planned class:

- `GitHubActionsFetcher`

Planned helper functions:

- `resolve_github_log_url`
- `download_github_log`
- `decode_log_payload`
- `build_raw_log_document`

### 4.5 Parsers

#### `src/ci_log_intelligence/parsers/normalize.py`

Purpose:

- line normalization
- ANSI stripping
- line-length bounding
- noise marker detection

Planned functions:

- `strip_ansi`
- `normalize_line`
- `detect_noise_kinds`
- `compute_semantic_fingerprint`

#### `src/ci_log_intelligence/parsers/sectionizer.py`

Purpose:

- detect section, step, and retry attempt boundaries

Planned functions:

- `detect_sections`
- `detect_step_kind`
- `infer_step_index`
- `infer_attempt_number`

#### `src/ci_log_intelligence/parsers/signals.py`

Purpose:

- detect semantic signal kinds for each line

Planned functions:

- `detect_signal_kinds`
- `detect_level`
- `extract_timestamp`
- `estimate_anchor_confidence`

#### `src/ci_log_intelligence/parsers/parser.py`

Purpose:

- convert `RawLogDocument` to `ParsedLog`

Planned class:

- `LogParser`

Planned methods:

- `parse(raw_log: RawLogDocument) -> ParsedLog`

Internal helpers:

- `_iter_lines`
- `_build_parsed_line`
- `_build_sections`
- `_collect_warnings`

### 4.6 Reducer

#### `src/ci_log_intelligence/reducer/anchors.py`

Purpose:

- detect anchors from parsed lines
- assign anchor tier and metadata

Planned models or typed helpers:

- `AnchorCandidate`

Planned functions:

- `detect_anchors(parsed_log: ParsedLog) -> list[AnchorCandidate]`
- `collapse_repeated_anchors(anchors: list[AnchorCandidate]) -> list[AnchorCandidate]`

#### `src/ci_log_intelligence/reducer/clustering.py`

Purpose:

- partition anchors into failure clusters

Planned models:

- `FailureCluster`

Planned functions:

- `cluster_anchors(...) -> list[FailureCluster]`
- `compute_cluster_fingerprint(...) -> str`
- `select_cluster_seeds(cluster: FailureCluster) -> list[AnchorCandidate]`

#### `src/ci_log_intelligence/reducer/expansion.py`

Purpose:

- expand cluster seeds into contextual candidate blocks

Planned models:

- `CandidateBlock`

Planned functions:

- `expand_context(seed, parsed_log) -> CandidateBlock`
- `apply_truncation(candidate_block) -> CandidateBlock`
- `materialize_excerpt(candidate_block, parsed_log, storage_backend) -> list[ExcerptLine]`

#### `src/ci_log_intelligence/reducer/suppression.py`

Purpose:

- suppress repetitive or low-information regions
- remove duplicate candidate windows

Planned functions:

- `suppress_noise(candidate_blocks, parsed_log)`
- `deduplicate_candidate_blocks(candidate_blocks)`
- `deduplicate_ranked_blocks(blocks)`
- `compute_overlap_ratio(block_a, block_b) -> float`

#### `src/ci_log_intelligence/reducer/merge.py`

Purpose:

- merge candidate blocks within the same cluster

Planned functions:

- `merge_blocks(candidate_blocks) -> list[CandidateBlock]`
- `should_merge(block_a, block_b) -> bool`

#### `src/ci_log_intelligence/reducer/scoring.py`

Purpose:

- compute block scores and score breakdowns

Planned functions:

- `score_block(block, parsed_log) -> tuple[float, list[ScoreComponent]]`
- `compute_root_cause_signal_score(...) -> float`
- `compute_context_signal_score(...) -> float`
- `compute_step_priority_bonus(...) -> float`
- `compute_recency_bias(...) -> float`
- `compute_noise_penalty(...) -> float`
- `compute_wrapper_dominance_penalty(...) -> float`
- `clamp_score(value: float) -> float`

#### `src/ci_log_intelligence/reducer/classification.py`

Purpose:

- assign stable failure classes from evidence

Planned functions:

- `classify_failure(block) -> FailureClassification`
- `derive_failure_class(evidence) -> str`

#### `src/ci_log_intelligence/reducer/redaction.py`

Purpose:

- redact secrets after scoring and before excerpt publication

Planned functions:

- `redact_block_excerpts(blocks) -> list[LogBlock]`
- `redact_text(text: str) -> str`
- `detect_secret_patterns(text: str) -> bool`

#### `src/ci_log_intelligence/reducer/ranking.py`

Purpose:

- select cluster representatives and top-k global results

Planned functions:

- `rank_by_cluster_then_global(blocks) -> list[LogBlock]`
- `select_cluster_representatives(blocks) -> list[LogBlock]`
- `apply_top_k(blocks, k: int) -> list[LogBlock]`

#### `src/ci_log_intelligence/reducer/pipeline.py`

Purpose:

- expose reducer entry point

Planned class:

- `LogReducer`

Planned methods:

- `reduce(parsed_log: ParsedLog, max_blocks: int) -> ReducedLog`

Execution responsibilities:

- detect anchors
- cluster anchors
- expand context
- suppress noise
- deduplicate candidates
- merge blocks
- score and classify
- deduplicate ranked blocks
- apply no-anchor fallback
- redact excerpts
- produce reducer stats

### 4.7 Summarizer

#### `src/ci_log_intelligence/summarizer/service.py`

Purpose:

- convert reduced evidence into a concise summary without inventing facts

Planned class:

- `SummarizerService`

Planned methods:

- `summarize(reduced_log: ReducedLog) -> SummaryResult`

Note:

- V1 should be deterministic and template-based, not LLM-dependent
- an LLM-backed summarizer can be added later behind the same interface

### 4.8 Publisher

#### `src/ci_log_intelligence/publisher/github_markdown.py`

Purpose:

- render GitHub-safe markdown from semantic output

Planned class:

- `GitHubMarkdownPublisher`

Planned methods:

- `render_analysis(response: AnalyzeCIResponse) -> str`
- `render_block(block: LogBlock) -> str`
- `render_warnings(warnings: list[DiagnosticWarning]) -> str`

### 4.9 Service Orchestration

#### `src/ci_log_intelligence/services/analyze_ci.py`

Purpose:

- orchestrate the end-to-end analysis flow

Planned class:

- `AnalyzeCIService`

Planned methods:

- `analyze(request: AnalyzeCIRequest) -> AnalyzeCIResponse`

Dependencies:

- fetcher
- parser
- reducer
- summarizer

### 4.10 API

#### `src/ci_log_intelligence/api/dependencies.py`

Purpose:

- wire settings and service instances

Planned functions:

- `get_settings`
- `get_storage_backend`
- `get_fetcher`
- `get_parser`
- `get_reducer`
- `get_summarizer`
- `get_analyze_service`

#### `src/ci_log_intelligence/api/routes.py`

Purpose:

- define HTTP routes

Planned functions:

- `analyze_ci`
- `healthcheck`

#### `src/ci_log_intelligence/api/app.py`

Purpose:

- create FastAPI app

Planned functions:

- `create_app() -> FastAPI`

## 5. Implementation Order

Phase 3 should implement in this order:

1. contracts and enums
2. storage backends
3. parser normalization and signal detection
4. reducer pipeline
5. summarizer
6. orchestration service
7. API app and routes
8. publisher
9. GitHub Action integration

This order puts the highest-risk logic first: parser and reducer quality.

## 6. Dependency Plan

## 6.1 Runtime Dependencies

Keep the runtime dependency set minimal even if the workspace currently contains
many unrelated packages.

Required:

- `fastapi`
  - HTTP API
- `uvicorn`
  - local server runtime
- `pydantic`
  - contract and settings validation
- `pydantic-settings`
  - configuration management
- `httpx`
  - async-capable provider HTTP client

Likely useful:

- `python-json-logger`
  - structured logging output
- `typing_extensions`
  - compatibility for richer type hints if needed

Stdlib should cover:

- compression handling: `gzip`, `zipfile`
- temp storage: `tempfile`, `pathlib`
- hashing: `hashlib`
- regex and parsing: `re`
- time and timestamps: `datetime`
- enums and types: `enum`, `dataclasses` or pydantic models

Do not add in Phase 3 unless a hard need appears:

- heavy NLP or ML libraries
- pandas
- databases
- message queues
- background task frameworks

## 6.2 Test Dependencies

Required:

- `pytest`
- `pytest-asyncio`
- `httpx`

Optional but recommended:

- `pytest-cov`
  - coverage reporting

## 6.3 Dependency Hygiene

Planned rule:

- the project should eventually maintain a minimal project-specific requirements
  file instead of inheriting a large shared environment

Phase 2 decision:

- implementation should only rely on the dependencies listed above plus the
  standard library

## 7. Test Plan

## 7.1 Unit Test Coverage

### Contracts

Test goals:

- request and response validation
- enum stability
- failure classification serialization
- warning and truncation metadata integrity

### Fetchers

Test goals:

- GitHub log download happy path
- auth and rate-limit failures
- compressed payload handling
- partial-log handling

### Parsers

Test goals:

- ANSI stripping
- step and attempt detection
- signal detection for Python, Java, shell, and generic CI failures
- noise marker detection
- semantic fingerprint stability

### Reducer

Test goals:

- anchor tier assignment
- failure clustering
- context expansion around cause lines
- merge rules
- score breakdown correctness
- step-aware scoring
- recency bias behavior
- candidate and ranked deduplication
- noise suppression
- no-anchor fallback
- failure classification
- redaction after scoring

### Summarizer

Test goals:

- deterministic summary generation
- no hallucinated root cause
- correct warning propagation

### Publisher

Test goals:

- markdown formatting
- warning rendering
- redaction preservation in output
- bounded excerpt size

### Services and API

Test goals:

- end-to-end orchestration
- response model integrity
- healthcheck correctness
- error translation into API responses

## 7.2 Integration Tests

Required scenarios:

- Python traceback with clear root cause
- multiple pytest failures in one job
- compile error in build step outranking later wrapper failure
- network timeout classified as infrastructure
- OOM kill classified as resource exhaustion
- very noisy large log with suppression and top-k output
- partial log with downgraded confidence
- interleaved step log with provenance-safe clustering
- secret-bearing log where output is redacted but scoring remains stable

## 7.3 Golden Tests

Use golden fixtures for:

- reducer top-k block output
- score breakdown snapshots
- failure classification output
- GitHub markdown rendering

Golden tests are especially important for the reducer because subtle scoring
changes can cause regressions without breaking unit assertions.

## 7.4 Test Data Strategy

Fixtures should be curated rather than synthetic whenever possible.

Guidelines:

- keep logs small enough for fast tests unless specifically testing large-log
  behavior
- include realistic provider boilerplate
- include duplicated or noisy lines to exercise suppression
- include secret-like strings for redaction tests
- store expected classifications and line ranges alongside fixtures where useful

## 7.5 Coverage Priorities

Highest priority:

- reducer scoring and ranking
- parser signal detection
- clustering and deduplication
- no-anchor fallback
- redaction placement correctness

Medium priority:

- fetcher robustness
- publisher rendering
- API validation and error translation

Lower priority in first implementation pass:

- ancillary logging utilities
- healthcheck endpoint

## 8. Open Implementation Constraints

The following rules should guide Phase 3:

- keep the reducer deterministic
- keep summarization template-based in V1
- keep the dependency set minimal
- prefer small pure functions in parser and reducer modules
- keep GitHub-specific behavior out of parser and reducer internals

## 9. Phase 2 Exit Criteria

Phase 2 is complete when:

- every planned file has a clear purpose
- module boundaries are implementation-ready
- core classes and functions are named
- dependency choices are constrained
- test coverage is defined for the risky paths

No code should be written to satisfy Phase 2.
