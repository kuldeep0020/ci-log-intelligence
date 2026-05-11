from __future__ import annotations

from collections import Counter
from typing import Optional

import requests

from ..ci_analysis import analyze_ci_url, fetch_with_cache_awareness
from ..ci_report_builder import resolve_failure_type
from ..ingestion import ingest_log
from ..ingestion.github.fetcher import (
    GitHubLogFetcher,
    classify_job_status,
)
from ..ingestion.github.models import (
    GitHubTarget,
    NormalizedLog,
    WorkflowJob,
)
from ..ingestion.github.resolver import resolve_github_url
from ..models import ParsedLine, ScoredBlock
from ..parsing import parse_log
from ..reducer import reduce_parsed_lines
from ..reducer.comparison import summarize_failed_block
from ..reducer.detectors import JobContext
from ..storage import InMemoryStorage
from ..summarizer import summarize_reduction_result
from .cache import CachedJob, CacheKey, JobCache, get_default_cache


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
) -> dict[str, object]:
    """Return a cheap map of failed jobs for a CI URL.

    No per-block content -- just job names, counts, and the failure types
    present in each job. Designed for an explore-then-drill pattern: call
    this first to decide which jobs to inspect with ``analyze_ci_failure``
    or ``get_block``. Each failed job's parse + reduce result is cached so
    follow-up tool calls on the same URL are essentially free.
    """
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
    )
    failed_logs = [log for log in fetched.logs if log.status == "failed"]

    summaries: list[dict[str, object]] = []
    for failed_log in failed_logs:
        cache_key = CacheKey(
            repo=target.repo, run_id=failed_log.run_id, job_id=failed_log.job_id
        )
        cached = job_cache.get(cache_key)
        if cached is None:
            # Cache miss: parse + reduce the freshly-fetched content and store.
            # ``fetch_with_cache_awareness`` only emits placeholder logs for
            # cache HITS, so a cache miss here means ``content`` is real.
            job_context = JobContext(
                job_name=failed_log.job_name,
                run_id=failed_log.run_id,
                repo=target.repo,
            )
            parsed_lines, reduction_result = _parse_and_reduce(
                failed_log.content, job_context
            )
            cached = CachedJob(
                job_name=failed_log.job_name,
                parsed_lines=parsed_lines,
                reduction_result=reduction_result,
            )
            job_cache.put(cache_key, cached)

        summaries.append(_job_summary_from_cache(cache_key, cached))

    summaries.sort(key=lambda item: (-int(item["run_id"]), str(item["job_name"]).lower()))

    return {
        "jobs": summaries,
        "metadata": {
            "total_runs_analyzed": len({run.run_id for run in fetched.runs}),
            "failed_jobs": len(summaries),
        },
    }


def analyze_ci_failure(
    ci_url: str,
    *,
    top_k: int = _DEFAULT_TOP_K,
    failure_types: Optional[list[str]] = None,
    include_passed: bool = True,
    max_passed_runs: int = _DEFAULT_MAX_PASSED_RUNS,
    cache: Optional[JobCache] = None,
    fetcher: Optional[GitHubLogFetcher] = None,
) -> dict[str, object]:
    """Run the full typed-record analysis for a CI URL.

    Returns the report shape from ``CIAnalysisReport.to_dict()`` with
    ``failures`` filtered by ``failure_types`` and truncated to ``top_k``.
    ``metadata.failures_total`` reports the unfiltered count so the agent
    can detect truncation; ``metadata.failures_returned`` is the length of
    the ``failures`` array actually returned.
    """
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
    )
    return report.to_dict()


def get_block(
    ci_url: str,
    block_index: int,
    surround: int = _DEFAULT_SURROUND,
    *,
    cache: Optional[JobCache] = None,
    fetcher: Optional[GitHubLogFetcher] = None,
) -> dict[str, object]:
    """Return the full content of a specific block in a specific job.

    ``ci_url`` MUST be a job-scoped URL (``.../actions/runs/<run>/job/<job>``).
    ``block_index`` is the 0-indexed position in the job's ranked
    ``failures`` list (matching ``analyze_ci_failure``'s ordering).
    ``surround`` is the number of raw log lines included before the block's
    start and after its end as outer context.
    """
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

    cached = job_cache.get(cache_key)
    if cached is None:
        active_fetcher = fetcher or GitHubLogFetcher()
        try:
            cached = _fetch_and_cache_single_job(active_fetcher, target, job_cache)
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

    scored_block = blocks[block_index]
    detected = cached.reduction_result.detected_failures
    failure_type, extracted = resolve_failure_type(scored_block, detected)
    highest_severity = max(
        (anchor.severity for anchor in scored_block.block.anchors),
        default=0,
    )

    return {
        "job_url": _build_job_url(target),
        "job_name": cached.job_name,
        "run_id": target.run_id,
        "job_id": target.job_id,
        "block_index": block_index,
        "type": failure_type,
        "classification": scored_block.classification,
        "severity": highest_severity,
        "score": scored_block.score,
        "summary": summarize_failed_block(scored_block, cached.job_name, target.run_id),
        "extracted_fields": extracted,
        "start_line": scored_block.block.start_line,
        "end_line": scored_block.block.end_line,
        "lines": _slice_lines_with_context(
            cached.parsed_lines, scored_block, surround
        ),
    }


def _slice_lines_with_context(
    parsed_lines: list[ParsedLine],
    scored_block: ScoredBlock,
    surround: int,
) -> list[dict[str, object]]:
    """Return the block's lines plus ``surround`` context lines either side.

    ``parsed_lines`` are 1-indexed by ``ParsedLine.line_number``; we index by
    that field rather than by list position so we are resilient to any future
    parser changes that drop lines.
    """
    block = scored_block.block
    anchor_line_numbers = {anchor.line_number for anchor in block.anchors}
    start = max(1, block.start_line - surround)
    end = block.end_line + surround

    line_by_number = {line.line_number: line for line in parsed_lines}
    result: list[dict[str, object]] = []
    for line_number in range(start, end + 1):
        parsed = line_by_number.get(line_number)
        if parsed is None:
            continue
        result.append(
            {
                "line_number": line_number,
                "content": parsed.content,
                "in_block": block.start_line <= line_number <= block.end_line,
                "is_anchor": line_number in anchor_line_numbers,
            }
        )
    return result


def _job_summary_from_cache(key: CacheKey, cached: CachedJob) -> dict[str, object]:
    result = cached.reduction_result
    failure_types: list[str] = []
    seen_types: set[str] = set()
    classification_counter: Counter[str] = Counter()
    for block in result.blocks:
        failure_type, _ = resolve_failure_type(block, result.detected_failures)
        if failure_type not in seen_types:
            failure_types.append(failure_type)
            seen_types.add(failure_type)
        classification_counter[block.classification] += 1

    return {
        "run_id": key.run_id,
        "job_id": key.job_id,
        "job_name": cached.job_name,
        "conclusion": "failure",
        "job_url": _build_job_url(
            GitHubTarget(repo=key.repo, run_id=key.run_id, job_id=key.job_id)
        ),
        "block_count": len(result.blocks),
        "failure_types_present": failure_types,
        "classifications": dict(classification_counter),
    }


def _fetch_and_cache_single_job(
    active_fetcher: GitHubLogFetcher,
    target: GitHubTarget,
    job_cache: JobCache,
) -> CachedJob:
    if target.run_id is None or target.job_id is None:
        raise ValueError("Job-scoped URL required.")

    jobs = active_fetcher.fetch_jobs_for_run(target.repo, target.run_id)
    selected_job: Optional[WorkflowJob] = None
    for job in jobs:
        if job.job_id == target.job_id:
            selected_job = job
            break
    if selected_job is None:
        raise ValueError(
            f"Job {target.job_id} not found in run {target.run_id} for {target.repo}."
        )

    status = classify_job_status(selected_job.conclusion)
    if status is None:
        raise ValueError(
            f"Job {target.job_id} has conclusion '{selected_job.conclusion}'; no logs available."
        )

    content = active_fetcher.fetch_job_log(target.repo, target.job_id)
    normalized = NormalizedLog(
        run_id=target.run_id,
        job_id=target.job_id,
        job_name=selected_job.job_name,
        status=status,
        content=content,
    )
    job_context = JobContext(
        job_name=normalized.job_name,
        run_id=normalized.run_id,
        repo=target.repo,
    )

    parsed_lines, reduction_result = _parse_and_reduce(normalized.content, job_context)
    cached = CachedJob(
        job_name=normalized.job_name,
        parsed_lines=parsed_lines,
        reduction_result=reduction_result,
    )
    job_cache.put(
        CacheKey(repo=target.repo, run_id=target.run_id, job_id=target.job_id),
        cached,
    )
    return cached


def _parse_and_reduce(content: str, job_context: JobContext):
    backend = InMemoryStorage()
    stored = ingest_log(content, backend)
    try:
        parsed_lines = parse_log(stored, backend)
        result = reduce_parsed_lines(parsed_lines, job_context=job_context)
        result.summary = summarize_reduction_result(result)
        return list(parsed_lines), result
    finally:
        backend.delete(stored.reference)


def _build_job_url(target: GitHubTarget) -> str:
    if target.run_id is None or target.job_id is None:
        return ""
    return (
        f"https://github.com/{target.repo}/actions/runs/{target.run_id}"
        f"/job/{target.job_id}"
    )


__all__ = [
    "list_failed_jobs",
    "analyze_ci_failure",
    "get_block",
]
