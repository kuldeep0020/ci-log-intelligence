from __future__ import annotations

from typing import Optional, Sequence, TYPE_CHECKING

from .ci_report_builder import (
    _summarize_root_cause,
    build_report,
    resolve_failure_type,
)
from .ci_report_builder import build_report as _build_report
from .ci_report_builder import resolve_failure_type as _resolve_failure_type
from .ingestion import ingest_log
from .ingestion.github.fetcher import GitHubLogFetcher, _sort_logs, normalize_job_name
from .ingestion.github.models import (
    CIAnalysisReport,
    FailedLogAnalysis,
    NormalizedLog,
)
from .ingestion.github.resolver import resolve_github_url
from .mcp.cache import CachedJob, CacheKey
from .parsing import parse_log
from .progress import ProgressCallback, report as report_progress
from .reducer import reduce_parsed_lines
from .reducer.comparison import analyze_cross_run, extract_passed_context
from .reducer.detectors import JobContext
from .storage import InMemoryStorage
from .summarizer import summarize_reduction_result
from .utils.logging import get_structured_logger, log_stage_event
from .utils.metrics import MetricsCollector, measure_stage

if TYPE_CHECKING:  # pragma: no cover - typing-only import to avoid circulars
    from .mcp.cache import JobCache


def analyze_ci_url(
    ci_url: str,
    *,
    include_passed: bool = True,
    max_passed_runs: int = 3,
    max_runs: int = 5,
    fetcher: Optional[GitHubLogFetcher] = None,
    metrics: Optional[MetricsCollector] = None,
    cache: Optional["JobCache"] = None,
    top_k: Optional[int] = None,
    failure_types: Optional[Sequence[str]] = None,
    progress: Optional[ProgressCallback] = None,
) -> CIAnalysisReport:
    """Run the end-to-end CI analysis pipeline for a GitHub URL.

    When ``cache`` is provided, per-job (parse + reduce) results are looked up on
    ``(repo, run_id, job_id)`` and stored after computation. Cache hits skip the
    parse + reduce work. The fetch (GitHub API call for the log content) still
    happens unless the caller arranges to suppress it; today the cache short-
    circuits only the CPU-bound stages, which is the dominant cost for cached
    runs because the GitHub API returns 304 (or is otherwise cheap for an
    already-completed immutable job log).
    """
    logger = get_structured_logger("ci_log_intelligence.ci")
    collector = metrics or MetricsCollector()

    with measure_stage("resolve_ci_url", collector, logger):
        target = resolve_github_url(ci_url)

    github_fetcher = fetcher or GitHubLogFetcher(logger=logger)
    fetch_run_limit = max(max_runs, max_passed_runs + 2 if include_passed else max_runs)
    with measure_stage("fetch_github_logs", collector, logger):
        fetched = fetch_with_cache_awareness(
            github_fetcher,
            target,
            include_passed=include_passed,
            max_runs=fetch_run_limit,
            max_passed_runs=max_passed_runs,
            cache=cache,
            progress=progress,
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

        cached = _lookup_cache(cache, target.repo, failed_log)
        if cached is not None:
            reduction_result = cached.reduction_result
            cache_hit_anchors = float(_count_anchors(reduction_result))
            cache_hit_blocks = float(len(reduction_result.blocks))
            failed_metrics.record_metric("number_of_anchors", cache_hit_anchors)
            failed_metrics.record_metric("number_of_blocks", cache_hit_blocks)
            log_stage_event(logger, "job_cache_hit", run_id=failed_log.run_id, job_id=failed_log.job_id)
        else:
            with measure_stage("reduce_failed_log", collector, logger):
                reduction_result, parsed_lines = _analyze_single_log(
                    failed_log.content,
                    metrics=failed_metrics,
                    job_context=job_context,
                )
            _store_cache(cache, target.repo, failed_log, parsed_lines, reduction_result)

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

    return build_report(
        runs=fetched.runs,
        failed_logs=failed_logs,
        passed_logs=passed_logs,
        failed_analyses=failed_analyses,
        passed_contexts=passed_contexts,
        insights=insights,
        top_k=top_k,
        failure_types=failure_types,
    )


def fetch_with_cache_awareness(
    fetcher: GitHubLogFetcher,
    target,
    *,
    include_passed: bool,
    max_runs: int,
    max_passed_runs: int,
    cache: Optional["JobCache"],
    progress: Optional[ProgressCallback] = None,
):
    """Plan the fetch, then fetch only the log content the cache doesn't already cover.

    When ``cache`` is ``None`` behavior is identical to ``fetcher.fetch_logs``.
    When a cache is provided, planned jobs whose ``(repo, run_id, job_id)`` is
    already present in the cache are short-circuited: an empty-``content``
    placeholder ``NormalizedLog`` is emitted so the downstream loop still sees
    the job (and the cache-hit branch picks it up). Passed jobs are always
    fetched because their reduction is consumed by ``extract_passed_context``
    without going through the cache.

    When ``progress`` is supplied, the callback is invoked once with a
    ``Planning log fetches`` message, once per planned job (either
    ``Fetching ...`` or ``Cached: ...`` depending on cache state), and a
    final ``All logs fetched`` event. The ``total`` field on per-job and
    final events is the count of planned jobs so the client sees a stable
    denominator.
    """
    report_progress(progress, 0, 0, "Planning log fetches")
    plan = fetcher.plan_logs(
        target,
        include_passed=include_passed,
        max_runs=max_runs,
        max_passed_runs=max_passed_runs,
    )
    total_jobs = len(plan.planned_jobs)
    if total_jobs == 0:
        report_progress(progress, 0, 0, "No failed jobs found")
        return fetcher.assemble_fetched_data(
            plan, [], include_passed=include_passed, max_passed_runs=max_passed_runs
        )

    cached_logs, jobs_to_fetch = _split_planned_jobs(
        plan.planned_jobs, target.repo, cache, total_jobs, progress
    )
    fetched_logs = (
        fetcher.fetch_planned_log_content(target.repo, jobs_to_fetch) if jobs_to_fetch else []
    )
    all_logs = _sort_logs(cached_logs + fetched_logs)
    report_progress(progress, total_jobs, total_jobs, "All logs fetched")

    # ``assemble_fetched_data`` handles both include_passed=True (group + cap)
    # and include_passed=False (failed-only filter), so a single call covers
    # both paths.
    return fetcher.assemble_fetched_data(
        plan,
        all_logs,
        include_passed=include_passed,
        max_passed_runs=max_passed_runs,
    )


def _split_planned_jobs(
    planned_jobs,
    repo: str,
    cache: Optional["JobCache"],
    total_jobs: int,
    progress: Optional[ProgressCallback],
):
    """Partition planned jobs into cache-hit placeholders vs jobs needing fetch.

    Emits per-job progress in plan order so the client sees a stable
    ``i+1/total`` counter regardless of which jobs end up cache-hits.
    Cache hits get a ``Cached:`` message; misses get a ``Fetching log for``
    message. Returns ``(cached_logs, jobs_to_fetch)``.
    """
    cached_logs: list = []
    jobs_to_fetch: list = []
    for index, planned in enumerate(planned_jobs):
        run, job, _, status = planned
        if _is_cache_hit(cache, repo, run.run_id, job.job_id, status):
            cached_logs.append(_cache_hit_placeholder(run, job))
            report_progress(
                progress,
                index + 1,
                total_jobs,
                f"Cached: {job.job_name} ({index + 1}/{total_jobs})",
            )
        else:
            report_progress(
                progress,
                index + 1,
                total_jobs,
                f"Fetching log for {job.job_name} ({index + 1}/{total_jobs})",
            )
            jobs_to_fetch.append(planned)
    return cached_logs, jobs_to_fetch


def _is_cache_hit(
    cache: Optional["JobCache"],
    repo: str,
    run_id: int,
    job_id: int,
    status: str,
) -> bool:
    if cache is None or status != "failed":
        return False
    return cache.get(CacheKey(repo=repo, run_id=run_id, job_id=job_id)) is not None


def _cache_hit_placeholder(run, job) -> NormalizedLog:
    """Build the empty-``content`` placeholder for a cache-hit job.

    The analyze loop iterates this job and takes the cache-hit branch; the
    empty ``content`` is never read because the cache lookup short-circuits
    it. See ``NormalizedLog`` docstring for the placeholder contract.
    """
    return NormalizedLog(
        run_id=run.run_id,
        job_id=job.job_id,
        job_name=job.job_name,
        status="failed",
        content="",
    )


def _lookup_cache(
    cache: Optional["JobCache"],
    repo: str,
    failed_log: NormalizedLog,
) -> Optional["CachedJob"]:
    if cache is None:
        return None
    key = CacheKey(repo=repo, run_id=failed_log.run_id, job_id=failed_log.job_id)
    return cache.get(key)


def _store_cache(
    cache: Optional["JobCache"],
    repo: str,
    failed_log: NormalizedLog,
    parsed_lines,
    reduction_result,
) -> None:
    if cache is None:
        return
    key = CacheKey(repo=repo, run_id=failed_log.run_id, job_id=failed_log.job_id)
    cache.put(
        key,
        CachedJob(
            job_name=failed_log.job_name,
            parsed_lines=list(parsed_lines),
            reduction_result=reduction_result,
        ),
    )


def _count_anchors(reduction_result) -> int:
    return sum(len(scored.block.anchors) for scored in reduction_result.blocks)


def _analyze_single_log(
    content: str,
    metrics: Optional[MetricsCollector] = None,
    job_context: Optional[JobContext] = None,
):
    """Parse and reduce one log; returns ``(ReductionResult, parsed_lines)``.

    Returning the parsed line list alongside the reduction result lets the cache
    retain the raw line content needed by ``get_block`` without re-parsing.
    """
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
        return result, parsed_lines
    finally:
        backend.delete(stored_log.reference)
