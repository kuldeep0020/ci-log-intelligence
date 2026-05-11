from __future__ import annotations

from typing import Any, Iterable, Optional, Sequence

from .ingestion.github.fetcher import normalize_job_name
from .ingestion.github.models import (
    AnalysisMetadata,
    CIAnalysisReport,
    FailedLogAnalysis,
    FailureRecord,
    NormalizedLog,
    PassedContextView,
    RootCauseSummary,
    WorkflowRun,
)
from .models import ScoreComponents, ScoredBlock
from .reducer.comparison import (
    render_block_excerpt,
    select_root_cause,
    summarize_failed_block,
)
from .reducer.detectors import DetectedFailure


def build_report(
    *,
    runs: Sequence[WorkflowRun],
    failed_logs: Sequence[NormalizedLog],
    passed_logs: Sequence[NormalizedLog],
    failed_analyses: Sequence[FailedLogAnalysis],
    passed_contexts: Iterable,
    insights: Iterable[str],
    top_k: Optional[int] = None,
    failure_types: Optional[Sequence[str]] = None,
) -> CIAnalysisReport:
    """Assemble the final ``CIAnalysisReport`` from per-job analyses.

    When ``failure_types`` is provided, the ``failures`` array is filtered to records whose
    ``type`` matches one of the listed strings. When ``top_k`` is provided, the (already
    score-sorted) array is truncated to that length. Both filters are reflected in
    ``metadata.failures_returned`` / ``metadata.failures_total`` so callers can detect
    truncation.
    """
    root_cause_candidate = select_root_cause(failed_analyses)
    if root_cause_candidate is None:
        root_cause = _empty_root_cause()
        all_failures: list[FailureRecord] = []
    else:
        _, scored_block = root_cause_candidate
        analysis = root_cause_candidate[0]
        root_cause = _summarize_root_cause(
            scored_block, analysis.log.job_name, analysis.log.run_id
        )
        all_failures = _build_failure_records(failed_analyses)

    failures_total = len(all_failures)

    if failure_types is not None:
        allowed = {failure_type for failure_type in failure_types}
        all_failures = [record for record in all_failures if record.type in allowed]

    if top_k is not None:
        all_failures = all_failures[: max(top_k, 0)]

    failures_returned = len(all_failures)

    passed_context_views = [
        PassedContextView(job_name=context.job_name, excerpt=context.excerpt)
        for context in passed_contexts
    ]
    metadata = AnalysisMetadata(
        total_runs_analyzed=len({run.run_id for run in runs}),
        failed_runs=len({log.run_id for log in failed_logs}),
        passed_runs=len({log.run_id for log in passed_logs}),
        failures_returned=failures_returned,
        failures_total=failures_total,
    )

    return CIAnalysisReport(
        root_cause=root_cause,
        failures=all_failures,
        passed_context=passed_context_views,
        cross_run_insights=list(insights),
        metadata=metadata,
    )


def _empty_root_cause() -> RootCauseSummary:
    return RootCauseSummary(
        summary="No failing jobs found in the analyzed CI runs.",
        log_excerpt="",
        has_traceback=False,
        has_stack_trace=False,
        has_assertion=False,
        score=0.0,
        score_components=ScoreComponents(
            severity_weight=0.0,
            signal_density=0.0,
            duplicate_penalty=0.0,
        ),
    )


def _build_failure_records(
    failed_analyses: Iterable[FailedLogAnalysis],
) -> list[FailureRecord]:
    failures: list[FailureRecord] = []
    for current_analysis in sorted(
        failed_analyses,
        key=lambda item: (-item.log.run_id, item.log.job_name.lower(), item.log.job_id),
    ):
        detected = current_analysis.result.detected_failures
        for block in current_analysis.result.blocks:
            failure_type, extracted = resolve_failure_type(block, detected)
            highest_severity = max(
                (anchor.severity for anchor in block.block.anchors),
                default=0,
            )
            failures.append(
                FailureRecord(
                    type=failure_type,
                    classification=block.classification,
                    severity=highest_severity,
                    score=block.score,
                    start_line=block.block.start_line,
                    end_line=block.block.end_line,
                    summary=summarize_failed_block(
                        block, current_analysis.log.job_name, current_analysis.log.run_id
                    ),
                    log_excerpt=render_block_excerpt(block),
                    extracted_fields=extracted,
                )
            )
    return failures


def resolve_failure_type(
    scored_block: ScoredBlock,
    detected_failures: list[DetectedFailure],
) -> tuple[str, dict[str, Any]]:
    """Resolve the FailureRecord ``type`` and ``extracted_fields`` for a scored block.

    Walks the DetectedFailures whose ``anchor_lines`` fall inside the block, picks the
    most-specific type (``"generic"`` loses to anything else; ties between specialized
    types break by highest severity then earliest anchor line), and merges
    ``extracted_fields`` from contributors of the winning type ONLY.
    """
    block_line_range = range(
        scored_block.block.start_line, scored_block.block.end_line + 1
    )
    contributors = [
        failure
        for failure in detected_failures
        if any(line in block_line_range for line in failure.anchor_lines)
    ]
    if not contributors:
        return "generic", {}

    specialized = [c for c in contributors if c.type != "generic"]
    if specialized:
        primary = min(
            specialized,
            key=lambda failure: (-failure.severity, min(failure.anchor_lines, default=0)),
        )
        merged: dict[str, Any] = {}
        for c in specialized:
            if c.type == primary.type:
                merged.update(c.extracted_fields)
        return primary.type, merged

    signal_names: list[str] = []
    for c in contributors:
        name = c.extracted_fields.get("signal_name")
        if name and name not in signal_names:
            signal_names.append(name)
    return "generic", {"signal_names": signal_names}


def _summarize_root_cause(
    scored_block: ScoredBlock,
    job_name: str,
    run_id: int,
) -> RootCauseSummary:
    block_signals = {signal for line in scored_block.block.lines for signal in line.signals}
    has_traceback = "traceback" in block_signals
    has_stack_trace = any(
        line.content.startswith("  File ") for line in scored_block.block.lines
    )
    has_assertion = "assertion_error" in block_signals or any(
        "AssertionError" in line.content for line in scored_block.block.lines
    )
    return RootCauseSummary(
        summary=summarize_failed_block(scored_block, job_name, run_id),
        log_excerpt=render_block_excerpt(scored_block),
        has_traceback=has_traceback,
        has_stack_trace=has_stack_trace,
        has_assertion=has_assertion,
        score=scored_block.score,
        score_components=scored_block.score_components,
    )


__all__ = ["build_report", "resolve_failure_type"]
