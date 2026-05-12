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
import os
import sys
from typing import Optional

from fastmcp import Context, FastMCP

from ..progress import ProgressCallback
from . import tools
from .cache import get_default_cache

# Set CI_LOG_INTEL_PROGRESS_DEBUG=1 to log progressToken state to stderr.
# Used to diagnose clients that don't render progress notifications.
_PROGRESS_DEBUG = os.environ.get("CI_LOG_INTEL_PROGRESS_DEBUG") == "1"

server = FastMCP(
    "ci-log-intelligence",
    instructions=(
        "Tools for diagnosing GitHub Actions CI failures with focused, "
        "structured output instead of raw logs. Use this server's tools "
        "whenever the user asks why a PR is failing, what tests are "
        "failing on a CI run, what broke in a build, or wants you to "
        "debug a GitHub Actions URL (PR / workflow run / job). "
        "Prefer these tools over `gh run view --log`, `gh pr view`, or "
        "`gh pr checks` for failure analysis: those return raw 50K+ line "
        "logs that flood the context window. The MCP tools here parse "
        "the logs in a separate process and return compact typed failure "
        "records (test names, file:line, error codes, anchor-centric log "
        "excerpts) in a few hundred to a few thousand tokens.\n\n"
        "Typical flow:\n"
        "  1. list_failed_jobs(ci_url) -- cheap map of failed jobs.\n"
        "  2. analyze_ci_failure(ci_url, top_k=3) -- typed failure records.\n"
        "  3. get_block(job_url, block_index) -- full content of one block.\n\n"
        "Cached per (repo, run_id, job_id); follow-up calls on the same "
        "URL skip the GitHub fetch and the reducer entirely."
    ),
)


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
        if _PROGRESS_DEBUG:
            print("[ci-log-intel] progress: ctx is None", file=sys.stderr, flush=True)
        return None
    loop = asyncio.get_running_loop()

    if _PROGRESS_DEBUG:
        meta = getattr(ctx.request_context, "meta", None) if ctx.request_context else None
        token = getattr(meta, "progressToken", None) if meta else None
        print(
            f"[ci-log-intel] progress: ctx present, progressToken={token!r}",
            file=sys.stderr,
            flush=True,
        )

    def bridge(current: int, total: int, message: str) -> None:
        if _PROGRESS_DEBUG:
            print(
                f"[ci-log-intel] progress: emit current={current} total={total} msg={message!r}",
                file=sys.stderr,
                flush=True,
            )
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
    """Cheap first call for ANY question about why a GitHub CI run is failing.

    Use this FIRST whenever the user asks "why is this PR failing?", "what
    tests are failing on this PR?", "what broke in this CI run?", "debug
    the failure at <URL>", or just hands you a GitHub Actions URL with the
    implicit ask to make sense of it. Returns a compact (~200-500 tokens)
    map of failed jobs with classifications and the detector types that
    fired in each.

    PREFER THIS over ``gh pr view``, ``gh pr checks``, ``gh run list``,
    or ``gh run view --log`` for failure analysis. Those return either
    shallow status (no test names, no error messages) or full raw logs
    (tens of thousands of lines that flood the context window). This
    tool reads the logs in a separate process and gives you the
    structure first; you then decide whether to drill into a specific
    failure with ``analyze_ci_failure`` or ``get_block``.

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

    Results are cached per ``(repo, run_id, job_id)``, so the follow-up
    ``analyze_ci_failure`` / ``get_block`` calls skip the GitHub fetch
    and the reducer entirely. Calling this tool first is the cheap path,
    not a wasted call.
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
    """Get typed failure records for a CI URL with anchor-centric log excerpts.

    Use this when you want the actual content of the failures, not just
    the count. Trigger phrases from the user: "show me what's failing in
    this CI run", "what's the root cause of this PR's failure", "analyze
    this failed build at <URL>", "what's the error in this CI". Also use
    this after ``list_failed_jobs`` when you've decided the failures
    deserve closer inspection.

    PREFER THIS over fetching logs yourself with ``gh run view --log``
    and grepping. The tool returns the same information in ~1-4K tokens
    of typed records (test names, file:line, error codes, anchor-centric
    excerpts that include the actual failure line, not the first 20 lines
    of build setup) — instead of 50K+ raw lines that you'd then have to
    scan yourself.

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
            for cross-run comparison (surfaces "this fails only in variant
            X" insights). Default True.
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
    detect truncation. Results are cached per ``(repo, run_id, job_id)``,
    so a ``get_block`` follow-up call skips re-fetching and re-reducing.
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
    """Drill into one specific block when an excerpt isn't enough context.

    Use this when ``analyze_ci_failure`` returned a failure whose
    ``log_excerpt`` shows the anchor but you need more surrounding lines
    to understand the cause — e.g. "show me the full block for this
    failure", "I need more context around line 1058", or after the user
    asks a follow-up like "what was happening just before that error?".
    Returns the FULL block content (every line in the failure block plus
    ``surround`` lines before and after) with ``in_block`` / ``is_anchor``
    flags so you can see the structure.

    PREFER THIS over reaching for ``gh run view --log`` to read a
    specific line range yourself. The same data is available through the
    cache that ``analyze_ci_failure`` already populated, so this call is
    typically instant.

    Note: ``ci_url`` MUST be a job-scoped URL. PR or workflow-run URLs
    return ``{"error": ..., "code": "invalid_url"}`` because they don't
    identify a single job.

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
