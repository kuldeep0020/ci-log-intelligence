"""FastMCP server exposing the CI-log-intelligence tools.

We intentionally do NOT use ``from __future__ import annotations`` here
because FastMCP's ``without_injected_parameters`` wrapper preserves the
original string annotations and exposes them to pydantic via
``get_type_hints`` resolved against the wrapper's globals (FastMCP's own
module), where ``Optional`` is not in scope. Evaluating annotations eagerly
at definition time sidesteps that gap.
"""

import argparse
import asyncio
from typing import Optional

from fastmcp import Context, FastMCP

from ..progress import ProgressCallback
from . import tools
from .cache import get_default_cache

server = FastMCP("ci-log-intelligence")


def _make_progress_bridge(ctx: Optional[Context]) -> Optional[ProgressCallback]:
    """Adapt FastMCP's async ``Context.report_progress`` to a sync ProgressCallback.

    Sync callback invocations happen on the worker thread that's executing the
    blocking tool body; the bridge schedules the async progress notification on
    the main event loop via ``run_coroutine_threadsafe`` and discards the
    returned future so progress is fire-and-forget. A failed notification must
    not fail the actual tool call -- if the event loop has closed (or the
    schedule itself raises) we drop the notification silently.
    """
    if ctx is None:
        return None
    loop = asyncio.get_running_loop()

    def bridge(current: int, total: int, message: str) -> None:
        try:
            asyncio.run_coroutine_threadsafe(
                ctx.report_progress(progress=current, total=total, message=message),
                loop,
            )
        except RuntimeError:
            # Loop closed mid-call; drop the notification silently rather than
            # raising from the worker thread.
            pass

    return bridge


@server.tool(name="list_failed_jobs")
async def list_failed_jobs_tool(
    ci_url: str,
    ctx: Optional[Context] = None,
) -> dict[str, object]:
    """List failed jobs for a CI URL without per-block content.

    A cheap "map" call. Use this first to decide which jobs to drill into
    with ``analyze_ci_failure`` or ``get_block``.

    Input:
        ci_url: GitHub PR URL, workflow run URL, or job URL.

    Returns:
        {
            "jobs": [
                {
                    "run_id": int,
                    "job_id": int,
                    "job_name": str,
                    "conclusion": "failure",
                    "job_url": str,                       # https://github.com/owner/repo/actions/runs/X/job/Y
                    "block_count": int,                   # number of ranked failure blocks
                    "failure_types_present": [str, ...],  # e.g. ["hash_mismatch", "generic"]
                    "classifications": {"root_cause": N, "symptom": N, "flaky": N}
                },
                ...
            ],
            "metadata": {"total_runs_analyzed": int, "failed_jobs": int}
        }

    Output is intentionally compact (target <500 tokens for a typical
    5-10 failed job set). Each failed job's parsed log and reduction
    result is cached, so a subsequent ``analyze_ci_failure`` or
    ``get_block`` call on the same URL skips re-fetching from GitHub
    and re-running the reducer.
    """
    progress = _make_progress_bridge(ctx)
    return await asyncio.to_thread(
        tools.list_failed_jobs,
        ci_url,
        cache=get_default_cache(),
        progress=progress,
    )


@server.tool(name="analyze_ci_failure")
async def analyze_ci_failure_tool(
    ci_url: str,
    top_k: int = 3,
    failure_types: Optional[list[str]] = None,
    include_passed: bool = True,
    max_passed_runs: int = 1,
    ctx: Optional[Context] = None,
) -> dict[str, object]:
    """Analyze a CI failure URL and return a focused, typed failure report.

    Input:
        ci_url: GitHub PR URL, workflow run URL, or job URL.
        top_k: Maximum number of FailureRecords to include (highest-score
            first). Default 3.
        failure_types: Optional filter by record type
            (e.g. ["hash_mismatch", "build_error_rust"]). None means no
            filter. Known types: "hash_mismatch", "go_test_fail",
            "pytest_fail", "rust_test_fail", "junit_xml",
            "build_error_rust", "build_error_go", "build_error_npm",
            "build_error_make", "build_error_gcc", "generic".
        include_passed: Fetch and extract context from passing job variants
            for cross-run comparison. Default True.
        max_passed_runs: Cap on the number of passing variants per logical
            job. Default 1.

    Returns:
        {
            "root_cause": {summary, log_excerpt, has_traceback,
                           has_stack_trace, has_assertion, score,
                           score_components},
            "failures": [
                {type, classification, severity, score, start_line,
                 end_line, summary, log_excerpt, extracted_fields},
                ...
            ],
            "passed_context": [{job_name, excerpt}, ...],
            "cross_run_insights": [str, ...],
            "metadata": {
                "total_runs_analyzed": int,
                "failed_runs": int,
                "passed_runs": int,
                "failures_returned": int,
                "failures_total": int
            }
        }

    ``failures`` is filtered by ``failure_types`` (when set) and truncated
    to ``top_k``. Compare ``failures_returned`` vs ``failures_total`` to
    detect truncation.

    The reducer is deterministic and results are cached per
    ``(repo, run_id, job_id)``, so repeat calls on the same URL are cheap.
    """
    progress = _make_progress_bridge(ctx)
    return await asyncio.to_thread(
        tools.analyze_ci_failure,
        ci_url,
        top_k=top_k,
        failure_types=failure_types,
        include_passed=include_passed,
        max_passed_runs=max_passed_runs,
        cache=get_default_cache(),
        progress=progress,
    )


@server.tool(name="get_block")
async def get_block_tool(
    ci_url: str,
    block_index: int,
    surround: int = 5,
    ctx: Optional[Context] = None,
) -> dict[str, object]:
    """Drill into a specific block of a specific failed job.

    Input:
        ci_url: A job-scoped URL of the form
            ``https://github.com/<owner>/<repo>/actions/runs/<run>/job/<job>``.
            PR or run URLs are rejected with ``code="invalid_url"``.
        block_index: 0-indexed position in the job's ranked ``failures``
            list (matches the order produced by ``analyze_ci_failure``).
        surround: Number of raw log lines to include before the block's
            start and after its end as context. Default 5.

    Returns (on success):
        {
            "job_url": str,
            "job_name": str,
            "run_id": int,
            "job_id": int,
            "block_index": int,
            "type": str,                  # "hash_mismatch" | "generic" | ...
            "classification": str,         # "root_cause" | "symptom" | "flaky"
            "severity": int,
            "score": float,
            "summary": str,
            "extracted_fields": {...},
            "start_line": int,
            "end_line": int,
            "lines": [
                {"line_number": int, "content": str,
                 "in_block": bool, "is_anchor": bool},
                ...
            ]
        }

    Returns (on error):
        {"error": str, "code": "invalid_url" | "index_out_of_range" |
                                "fetch_failed" | "invalid_argument",
         "block_count": int (only for "index_out_of_range")}
    """
    progress = _make_progress_bridge(ctx)
    return await asyncio.to_thread(
        tools.get_block,
        ci_url,
        block_index,
        surround=surround,
        cache=get_default_cache(),
        progress=progress,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m ci_log_intelligence.mcp.server")
    parser.add_argument("--transport", choices=("stdio", "http"), default="stdio")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8001)
    args = parser.parse_args(argv)

    if args.transport == "stdio":
        server.run(transport="stdio", show_banner=False)
    else:
        server.run(
            transport="http",
            host=args.host,
            port=args.port,
            show_banner=False,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
