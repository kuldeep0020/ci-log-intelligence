from __future__ import annotations

from typing import Any, Iterable, Optional

from .ingestion import ingest_log
from .ingestion.github.fetcher import GitHubLogFetcher, normalize_job_name
from .ingestion.github.models import (
    AnalysisMetadata,
    CIAnalysisReport,
    FailedLogAnalysis,
    FailureRecord,
    PassedContextView,
    RootCauseSummary,
)
from .ingestion.github.resolver import resolve_github_url
from .models import ScoreComponents, ScoredBlock
from .parsing import parse_log
from .reducer import reduce_parsed_lines
from .reducer.comparison import (
    analyze_cross_run,
    extract_passed_context,
    render_block_excerpt,
    select_root_cause,
    summarize_failed_block,
)
from .reducer.detectors import DetectedFailure, JobContext
from .storage import InMemoryStorage
from .summarizer import summarize_reduction_result
from .utils.logging import get_structured_logger, log_stage_event
from .utils.metrics import MetricsCollector, measure_stage


def analyze_ci_url(
    ci_url: str,
    *,
    include_passed: bool = True,
    max_passed_runs: int = 3,
    max_runs: int = 5,
    fetcher: Optional[GitHubLogFetcher] = None,
    metrics: Optional[MetricsCollector] = None,
) -> CIAnalysisReport:
    logger = get_structured_logger("ci_log_intelligence.ci")
    collector = metrics or MetricsCollector()

    with measure_stage("resolve_ci_url", collector, logger):
        target = resolve_github_url(ci_url)

    github_fetcher = fetcher or GitHubLogFetcher(logger=logger)
    fetch_run_limit = max(max_runs, max_passed_runs + 2 if include_passed else max_runs)
    with measure_stage("fetch_github_logs", collector, logger):
        fetched = github_fetcher.fetch_logs(
            target,
            include_passed=include_passed,
            max_runs=fetch_run_limit,
            max_passed_runs=max_passed_runs,
        )

    failed_logs = [log for log in fetched.logs if log.status == "failed"]
    passed_logs = [log for log in fetched.logs if log.status == "passed"]
    collector.record_metric("failed_jobs", float(len(failed_logs)))
    collector.record_metric("passed_jobs", float(len(passed_logs)))
    log_stage_event(
        logger,
        "analyze_ci_url",
        runs=len(fetched.runs),
        failed_jobs=len(failed_logs),
        passed_jobs=len(passed_logs),
    )

    failed_analyses: list[FailedLogAnalysis] = []
    total_anchors = 0.0
    total_blocks = 0.0
    for failed_log in failed_logs:
        failed_metrics = MetricsCollector()
        job_context = JobContext(
            job_name=failed_log.job_name,
            run_id=failed_log.run_id,
            repo=target.repo,
        )
        with measure_stage("reduce_failed_log", collector, logger):
            reduction_result = _analyze_single_log(
                failed_log.content,
                metrics=failed_metrics,
                job_context=job_context,
            )
        snapshot = failed_metrics.snapshot()
        total_anchors += float(snapshot["metrics"].get("number_of_anchors", 0.0))
        total_blocks += float(snapshot["metrics"].get("number_of_blocks", 0.0))
        failed_analyses.append(
            FailedLogAnalysis(
                log=failed_log,
                logical_job_name=normalize_job_name(failed_log.job_name),
                result=reduction_result,
            )
        )

    collector.record_metric("anchors_detected", total_anchors)
    collector.record_metric("blocks_generated", total_blocks)
    log_stage_event(
        logger,
        "failed_log_analysis",
        anchors_detected=total_anchors,
        blocks_generated=total_blocks,
    )

    with measure_stage("extract_passed_context", collector, logger):
        passed_contexts = extract_passed_context(failed_analyses, passed_logs)

    with measure_stage("cross_run_analysis", collector, logger):
        insights = analyze_cross_run(failed_analyses, passed_contexts)

    return _build_report(
        runs=fetched.runs,
        failed_logs=failed_logs,
        passed_logs=passed_logs,
        failed_analyses=failed_analyses,
        passed_contexts=passed_contexts,
        insights=insights,
    )


def _build_report(
    *,
    runs,
    failed_logs,
    passed_logs,
    failed_analyses,
    passed_contexts,
    insights,
) -> CIAnalysisReport:
    root_cause_candidate = select_root_cause(failed_analyses)
    if root_cause_candidate is None:
        root_cause = RootCauseSummary(
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
        failures: list[FailureRecord] = []
    else:
        analysis, scored_block = root_cause_candidate
        root_cause = _summarize_root_cause(scored_block, analysis.log.job_name, analysis.log.run_id)
        failures = _build_failure_records(failed_analyses)

    passed_context_views = [
        PassedContextView(job_name=context.job_name, excerpt=context.excerpt)
        for context in passed_contexts
    ]
    metadata = AnalysisMetadata(
        total_runs_analyzed=len({run.run_id for run in runs}),
        failed_runs=len({log.run_id for log in failed_logs}),
        passed_runs=len({log.run_id for log in passed_logs}),
    )

    return CIAnalysisReport(
        root_cause=root_cause,
        failures=failures,
        passed_context=passed_context_views,
        cross_run_insights=list(insights),
        metadata=metadata,
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
            failure_type, extracted = _resolve_failure_type(block, detected)
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


def _resolve_failure_type(
    scored_block: ScoredBlock,
    detected_failures: list[DetectedFailure],
) -> tuple[str, dict[str, Any]]:
    """Resolve the FailureRecord ``type`` and ``extracted_fields`` for a scored block.

    Walks the DetectedFailures whose ``anchor_lines`` fall inside the block, picks the
    most-specific type (``"generic"`` loses to anything else; ties between specialized
    types break by highest severity then earliest anchor line), and merges
    ``extracted_fields`` from contributors of the winning type ONLY.

    Primary-type-wins, others discarded: if a block has both a ``hash_mismatch``
    contributor and a hypothetical ``go_test_fail`` contributor at equal severity,
    only the higher-severity type's fields flow through. Mixing unrelated schemas
    under one record's ``extracted_fields`` would force the agent to do union-type
    inference per key; keeping a single coherent schema per ``type`` value is the
    contract we want to preserve.

    For the v1 cut with only ``GenericDetector``, returns
    ``("generic", {"signal_names": [...]})`` with signal names de-duplicated and in
    first-seen order.
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


def _analyze_single_log(
    content: str,
    metrics: Optional[MetricsCollector] = None,
    job_context: Optional[JobContext] = None,
):
    logger = get_structured_logger("ci_log_intelligence")
    collector = metrics or MetricsCollector()
    backend = InMemoryStorage()
    stored_log = ingest_log(content, backend)
    try:
        with measure_stage("parse", collector, logger):
            parsed_lines = parse_log(stored_log, backend)

        result = reduce_parsed_lines(
            parsed_lines,
            metrics=collector,
            logger=logger,
            job_context=job_context,
        )

        with measure_stage("summarize", collector, logger):
            result.summary = summarize_reduction_result(result)

        selected_lines = sum(len(scored.block.lines) for scored in result.blocks)
        collector.record_metric("reduction_ratio", selected_lines / max(len(parsed_lines), 1))
        collector.record_metric("number_of_blocks", float(len(result.blocks)))
        return result
    finally:
        backend.delete(stored_log.reference)
