from __future__ import annotations

from typing import Optional

import requests

from ..ci_analysis import analyze_ci_url, fetch_with_cache_awareness
from ..ingestion.github.fetcher import GitHubLogFetcher
from ..ingestion.github.resolver import resolve_github_url
from ..progress import ProgressCallback, report as report_progress
from ._tools_internals import (
    build_get_block_response,
    fetch_and_cache_single_job,
    summarize_failed_logs,
)
from .cache import CacheKey, JobCache, get_default_cache


_DEFAULT_TOP_K = 3
# ``list_failed_jobs`` and ``analyze_ci_failure`` (via ``analyze_ci_url``) MUST
# scan the same number of runs so cache keys align between tools. Bump both at
# once if this needs to grow.
_DEFAULT_MAX_RUNS = 5
_DEFAULT_MAX_PASSED_RUNS = 1
_DEFAULT_SURROUND = 5


def list_failed_jobs(
    ci_url: str,
    *,
    cache: Optional[JobCache] = None,
    fetcher: Optional[GitHubLogFetcher] = None,
    progress: Optional[ProgressCallback] = None,
) -> dict[str, object]:
    """Return a cheap map of failed jobs for a CI URL.

    No per-block content -- just job names, counts, and the failure types
    present in each job. Designed for an explore-then-drill pattern: call
    this first to decide which jobs to inspect with ``analyze_ci_failure``
    or ``get_block``. Each failed job's parse + reduce result is cached so
    follow-up tool calls on the same URL are essentially free.

    ``progress`` (optional) receives a coarse 0-100 boundary marker
    (``Resolving CI URL`` / ``Done``); finer per-job progress comes from
    ``fetch_with_cache_awareness`` inside this call.
    """
    report_progress(progress, 0, 100, "Resolving CI URL")
    job_cache = cache if cache is not None else get_default_cache()
    active_fetcher = fetcher or GitHubLogFetcher()

    target = resolve_github_url(ci_url)

    # Drive fetch via the same cache-aware path as ``analyze_ci_failure``.
    # ``fetch_with_cache_awareness`` consults the cache before fetching log
    # content, so cache-hit jobs short-circuit to empty-content placeholders
    # and a repeat call against the same URL incurs ZERO new text fetches.
    fetched = fetch_with_cache_awareness(
        active_fetcher,
        target,
        include_passed=False,
        max_runs=_DEFAULT_MAX_RUNS,
        max_passed_runs=0,
        cache=job_cache,
        progress=progress,
    )
    failed_logs = [log for log in fetched.logs if log.status == "failed"]
    summaries = summarize_failed_logs(failed_logs, target.repo, job_cache)
    summaries.sort(key=lambda item: (-int(item["run_id"]), str(item["job_name"]).lower()))

    response = {
        "jobs": summaries,
        "metadata": {
            "total_runs_analyzed": len({run.run_id for run in fetched.runs}),
            "failed_jobs": len(summaries),
        },
    }
    report_progress(progress, 100, 100, "Done")
    return response


def analyze_ci_failure(
    ci_url: str,
    *,
    top_k: int = _DEFAULT_TOP_K,
    failure_types: Optional[list[str]] = None,
    include_passed: bool = True,
    max_passed_runs: int = _DEFAULT_MAX_PASSED_RUNS,
    cache: Optional[JobCache] = None,
    fetcher: Optional[GitHubLogFetcher] = None,
    progress: Optional[ProgressCallback] = None,
) -> dict[str, object]:
    """Run the full typed-record analysis for a CI URL.

    Returns the report shape from ``CIAnalysisReport.to_dict()`` with
    ``failures`` filtered by ``failure_types`` and truncated to ``top_k``.
    ``metadata.failures_total`` reports the unfiltered count so the agent
    can detect truncation; ``metadata.failures_returned`` is the length of
    the ``failures`` array actually returned.

    ``progress`` (optional) receives a coarse 0-100 boundary marker
    (``Resolving CI URL`` / ``Done``); finer per-job progress comes from
    ``analyze_ci_url`` inside this call.
    """
    report_progress(progress, 0, 100, "Resolving CI URL")
    job_cache = cache if cache is not None else get_default_cache()
    # ``analyze_ci_url``'s ``max_runs`` defaults to ``_DEFAULT_MAX_RUNS`` so this
    # call path scans the same window as ``list_failed_jobs``. Keep the two
    # in lock-step via ``_DEFAULT_MAX_RUNS`` if either ever needs to change.
    report = analyze_ci_url(
        ci_url,
        include_passed=include_passed,
        max_passed_runs=max_passed_runs,
        max_runs=_DEFAULT_MAX_RUNS,
        fetcher=fetcher,
        cache=job_cache,
        top_k=top_k,
        failure_types=failure_types,
        progress=progress,
    )
    response = report.to_dict()
    report_progress(progress, 100, 100, "Done")
    return response


def get_block(
    ci_url: str,
    block_index: int,
    surround: int = _DEFAULT_SURROUND,
    *,
    cache: Optional[JobCache] = None,
    fetcher: Optional[GitHubLogFetcher] = None,
    progress: Optional[ProgressCallback] = None,
) -> dict[str, object]:
    """Return the full content of a specific block in a specific job.

    ``ci_url`` MUST be a job-scoped URL (``.../actions/runs/<run>/job/<job>``).
    ``block_index`` is the 0-indexed position in the job's ranked
    ``failures`` list (matching ``analyze_ci_failure``'s ordering).
    ``surround`` is the number of raw log lines included before the block's
    start and after its end as outer context.

    ``progress`` (optional) fires three success-path milestones:
    ``Resolving job URL``, ``Fetching/loading job ...``, ``Done``. Error
    returns do NOT emit further progress.
    """
    report_progress(progress, 0, 3, "Resolving job URL")
    try:
        target = resolve_github_url(ci_url)
    except ValueError as exc:
        return {"error": str(exc), "code": "invalid_url"}

    if target.run_id is None or target.job_id is None:
        return {
            "error": (
                "get_block requires a job-scoped URL "
                "(https://github.com/<owner>/<repo>/actions/runs/<run>/job/<job>); "
                "got a PR or run URL instead."
            ),
            "code": "invalid_url",
        }
    if surround < 0:
        return {"error": "surround must be >= 0", "code": "invalid_argument"}

    job_cache = cache if cache is not None else get_default_cache()
    cache_key = CacheKey(repo=target.repo, run_id=target.run_id, job_id=target.job_id)

    report_progress(progress, 1, 3, f"Fetching/loading job {target.job_id}")
    cached = job_cache.get(cache_key)
    if cached is None:
        active_fetcher = fetcher or GitHubLogFetcher()
        try:
            cached = fetch_and_cache_single_job(active_fetcher, target, job_cache)
        except (ValueError, RuntimeError, requests.HTTPError) as exc:
            # ``requests.HTTPError`` is wrapped to ``RuntimeError`` by
            # ``fetch_job_log`` today; included here as defense in depth so a
            # future refactor that loses that wrapping does not crash the tool.
            return {"error": str(exc), "code": "fetch_failed"}

    blocks = cached.reduction_result.blocks
    if block_index < 0 or block_index >= len(blocks):
        return {
            "error": (
                f"block_index {block_index} is out of range for this job "
                f"(block_count={len(blocks)})."
            ),
            "code": "index_out_of_range",
            "block_count": len(blocks),
        }

    response = build_get_block_response(
        target, cached, blocks[block_index], block_index, surround
    )
    report_progress(progress, 3, 3, "Done")
    return response


__all__ = [
    "list_failed_jobs",
    "analyze_ci_failure",
    "get_block",
]
