from __future__ import annotations

from typing import Optional

from .ci_analysis import analyze_ci_url
from .ingestion import ingest_log
from .models import ReductionResult
from .parsing import parse_log
from .reducer import reduce_parsed_lines
from .storage import StorageBackend, create_storage_backend
from .summarizer import summarize_reduction_result
from .utils.logging import get_structured_logger
from .utils.metrics import MetricsCollector, measure_stage

__all__ = [
    "analyze_log",
    "analyze_ci_url",
    "ReductionResult",
]


def analyze_log(
    log: str,
    storage_backend: Optional[StorageBackend] = None,
    spill_threshold_bytes: int = 5_000_000,
    metrics: Optional[MetricsCollector] = None,
) -> ReductionResult:
    logger = get_structured_logger("ci_log_intelligence")
    collector = metrics or MetricsCollector()
    backend = storage_backend or create_storage_backend(
        byte_size=len(log.encode("utf-8")),
        spill_threshold_bytes=spill_threshold_bytes,
    )

    stored_log = ingest_log(log, backend)
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
