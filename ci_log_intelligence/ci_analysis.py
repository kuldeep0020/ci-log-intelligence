from __future__ import annotations

from typing import Optional

from .ingestion import ingest_log
from .ingestion.github.fetcher import GitHubLogFetcher, normalize_job_name
from .ingestion.github.models import (
    AnalysisMetadata,
    CIAnalysisReport,
    FailedBlockView,
    FailedLogAnalysis,
    PassedContextView,
    RootCauseSummary,
)
from .ingestion.github.resolver import resolve_github_url
from .parsing import parse_log
from .reducer import reduce_parsed_lines
from .reducer.comparison import (
    analyze_cross_run,
    extract_passed_context,
    render_block_excerpt,
    select_root_cause,
    summarize_failed_block,
)
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
        with measure_stage("reduce_failed_log", collector, logger):
            reduction_result = _analyze_single_log(failed_log.content, metrics=failed_metrics)
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
            confidence=0.0,
        )
        failed_block_views: list[FailedBlockView] = []
    else:
        analysis, scored_block = root_cause_candidate
        root_cause = RootCauseSummary(
            summary=summarize_failed_block(scored_block, analysis.log.job_name, analysis.log.run_id),
            log_excerpt=render_block_excerpt(scored_block),
            confidence=round(min(0.99, 0.4 + (scored_block.score / 20.0)), 2),
        )
        failed_block_views = []
        for analysis in sorted(
            failed_analyses,
            key=lambda item: (-item.log.run_id, item.log.job_name.lower(), item.log.job_id),
        ):
            for block in analysis.result.blocks:
                failed_block_views.append(
                    FailedBlockView(
                        start_line=block.block.start_line,
                        end_line=block.block.end_line,
                        summary=summarize_failed_block(block, analysis.log.job_name, analysis.log.run_id),
                    )
                )

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
        failed_blocks=failed_block_views,
        passed_context=passed_context_views,
        cross_run_insights=list(insights),
        metadata=metadata,
    )


def _analyze_single_log(content: str, metrics: Optional[MetricsCollector] = None):
    logger = get_structured_logger("ci_log_intelligence")
    collector = metrics or MetricsCollector()
    backend = InMemoryStorage()
    stored_log = ingest_log(content, backend)
    try:
        with measure_stage("parse", collector, logger):
            parsed_lines = parse_log(stored_log, backend)

        result = reduce_parsed_lines(parsed_lines, metrics=collector, logger=logger)

        with measure_stage("summarize", collector, logger):
            result.summary = summarize_reduction_result(result)

        selected_lines = sum(len(scored.block.lines) for scored in result.blocks)
        collector.record_metric("reduction_ratio", selected_lines / max(len(parsed_lines), 1))
        collector.record_metric("number_of_blocks", float(len(result.blocks)))
        return result
    finally:
        backend.delete(stored_log.reference)
