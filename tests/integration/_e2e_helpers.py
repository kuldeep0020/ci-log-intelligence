"""Shared fixtures and helpers for end-to-end scenario tests.

Kept in a non-``test_*`` module so ``unittest discover`` does not pick it up
as a test module, and so the individual scenario files stay under the
project's 400-LOC file cap.
"""

from __future__ import annotations

from typing import Optional, Sequence

from ci_log_intelligence.ci_analysis import _build_report
from ci_log_intelligence.ingestion import ingest_log
from ci_log_intelligence.ingestion.github.models import (
    FailedLogAnalysis,
    NormalizedLog,
)
from ci_log_intelligence.parsing import parse_log
from ci_log_intelligence.reducer import reduce_parsed_lines
from ci_log_intelligence.reducer.detectors import JobContext
from ci_log_intelligence.storage import InMemoryStorage


def parse_lines(content: str):
    """Parse ``content`` through the storage backend and return the line list."""
    backend = InMemoryStorage()
    stored = ingest_log(content, backend)
    return parse_log(stored, backend)


def build_single_report(
    content: str,
    *,
    job_name: str = "ci-job",
    run_id: int = 42,
    top_k: Optional[int] = None,
    failure_types: Optional[Sequence[str]] = None,
):
    """Run the full pipeline and assemble a ``CIAnalysisReport`` for one log.

    Returns ``(reduction_result, report, analysis)`` so tests can assert on
    both the detector-layer output (``reduction_result.detected_failures``)
    and the report-layer typed records (``report.failures``).
    """
    parsed = parse_lines(content)
    job_context = JobContext(job_name=job_name, run_id=run_id, repo="r/x")
    result = reduce_parsed_lines(parsed, job_context=job_context)
    analysis = FailedLogAnalysis(
        log=NormalizedLog(
            run_id=run_id,
            job_id=run_id * 100,
            job_name=job_name,
            status="failed",
            content=content,
        ),
        logical_job_name=job_name,
        result=result,
    )
    report = _build_report(
        runs=[],
        failed_logs=[analysis.log],
        passed_logs=[],
        failed_analyses=[analysis],
        passed_contexts=[],
        insights=[],
        top_k=top_k,
        failure_types=failure_types,
    )
    return result, report, analysis


__all__ = ["build_single_report", "parse_lines"]
