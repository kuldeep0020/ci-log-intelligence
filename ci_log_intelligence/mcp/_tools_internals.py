"""Private helpers backing the three public MCP tool functions.

Kept separate from ``tools.py`` so the public-tool surface stays under
the 400-LOC project limit. Nothing in this module is re-exported and
the symbols here are not part of any public contract.
"""

from __future__ import annotations

from collections import Counter
from typing import Optional

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
from ..models import ParsedLine, ScoredBlock
from ..parsing import parse_log
from ..reducer import reduce_parsed_lines
from ..reducer.comparison import summarize_failed_block
from ..reducer.detectors import JobContext
from ..storage import InMemoryStorage
from ..summarizer import summarize_reduction_result
from .cache import CachedJob, CacheKey, JobCache


def summarize_failed_logs(
    failed_logs: list[NormalizedLog],
    repo: str,
    job_cache: JobCache,
) -> list[dict[str, object]]:
    """Build per-job summaries, populating the cache on miss.

    ``fetch_with_cache_awareness`` only emits empty-``content`` placeholders
    for cache HITS, so a cache miss here means ``content`` is the real log
    text and we can parse + reduce it directly.
    """
    summaries: list[dict[str, object]] = []
    for failed_log in failed_logs:
        cache_key = CacheKey(
            repo=repo, run_id=failed_log.run_id, job_id=failed_log.job_id
        )
        cached = job_cache.get(cache_key)
        if cached is None:
            job_context = JobContext(
                job_name=failed_log.job_name,
                run_id=failed_log.run_id,
                repo=repo,
            )
            parsed_lines, reduction_result = parse_and_reduce(
                failed_log.content, job_context
            )
            cached = CachedJob(
                job_name=failed_log.job_name,
                parsed_lines=parsed_lines,
                reduction_result=reduction_result,
            )
            job_cache.put(cache_key, cached)
        summaries.append(job_summary_from_cache(cache_key, cached))
    return summaries


def build_get_block_response(
    target: GitHubTarget,
    cached: CachedJob,
    scored_block: ScoredBlock,
    block_index: int,
    surround: int,
) -> dict[str, object]:
    """Render the success-shape ``get_block`` response dict."""
    detected = cached.reduction_result.detected_failures
    failure_type, extracted = resolve_failure_type(scored_block, detected)
    highest_severity = max(
        (anchor.severity for anchor in scored_block.block.anchors),
        default=0,
    )
    return {
        "job_url": build_job_url(target),
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
        "lines": slice_lines_with_context(
            cached.parsed_lines, scored_block, surround
        ),
    }


def slice_lines_with_context(
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


def job_summary_from_cache(key: CacheKey, cached: CachedJob) -> dict[str, object]:
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
        "job_url": build_job_url(
            GitHubTarget(repo=key.repo, run_id=key.run_id, job_id=key.job_id)
        ),
        "block_count": len(result.blocks),
        "failure_types_present": failure_types,
        "classifications": dict(classification_counter),
    }


def fetch_and_cache_single_job(
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

    parsed_lines, reduction_result = parse_and_reduce(normalized.content, job_context)
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


def parse_and_reduce(content: str, job_context: JobContext):
    backend = InMemoryStorage()
    stored = ingest_log(content, backend)
    try:
        parsed_lines = parse_log(stored, backend)
        result = reduce_parsed_lines(parsed_lines, job_context=job_context)
        result.summary = summarize_reduction_result(result)
        return list(parsed_lines), result
    finally:
        backend.delete(stored.reference)


def build_job_url(target: GitHubTarget) -> str:
    if target.run_id is None or target.job_id is None:
        return ""
    return (
        f"https://github.com/{target.repo}/actions/runs/{target.run_id}"
        f"/job/{target.job_id}"
    )


__all__ = [
    "build_get_block_response",
    "build_job_url",
    "fetch_and_cache_single_job",
    "job_summary_from_cache",
    "parse_and_reduce",
    "slice_lines_with_context",
    "summarize_failed_logs",
]
