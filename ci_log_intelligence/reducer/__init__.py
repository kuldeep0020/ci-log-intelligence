from __future__ import annotations

import logging
from typing import Iterable, Optional

from ..models import ReductionResult, ScoredBlock
from ..utils.logging import get_structured_logger, log_stage_event
from ..utils.metrics import MetricsCollector, measure_stage
from .anchors import detect_anchors
from .classification import classify_blocks, rank_blocks
from .clustering import build_clusters
from .detectors import JobContext, detected_failures_to_anchors, run_detectors
from .expansion import expand_context
from .merge import merge_blocks
from .scoring import score_blocks
from .suppression import suppress_noise


def reduce_parsed_lines(
    parsed_lines: Iterable,
    metrics: Optional[MetricsCollector] = None,
    logger: Optional[logging.Logger] = None,
    job_context: Optional[JobContext] = None,
) -> ReductionResult:
    parsed_line_list = list(parsed_lines)
    collector = metrics or MetricsCollector()
    structured_logger = logger or get_structured_logger("ci_log_intelligence.reducer")

    effective_job_context = job_context or JobContext(job_name=None, run_id=None, repo=None)
    with measure_stage("detect_anchors", collector, structured_logger):
        # Step 1 scaffolding: detected_failures will be plumbed through ``ReductionResult`` in step 3.
        # Today it is built only to feed ``detected_failures_to_anchors``.
        detected_failures = run_detectors(parsed_line_list, effective_job_context)
        anchors = detected_failures_to_anchors(detected_failures)
    collector.record_metric("number_of_anchors", float(len(anchors)))
    log_stage_event(structured_logger, "detect_anchors", anchors=len(anchors))

    with measure_stage("build_clusters", collector, structured_logger):
        clusters = build_clusters(anchors, parsed_line_list)
    log_stage_event(structured_logger, "build_clusters", clusters=len(clusters))

    with measure_stage("expand_context", collector, structured_logger):
        expanded_blocks = expand_context(parsed_line_list, clusters)
    log_stage_event(structured_logger, "expand_context", blocks=len(expanded_blocks))

    with measure_stage("suppress_noise", collector, structured_logger):
        suppressed_blocks = suppress_noise(expanded_blocks)
    log_stage_event(structured_logger, "suppress_noise", blocks=len(suppressed_blocks))

    with measure_stage("merge_blocks", collector, structured_logger):
        merged_blocks = merge_blocks(suppressed_blocks)
    log_stage_event(structured_logger, "merge_blocks", blocks=len(merged_blocks))

    with measure_stage("score_blocks", collector, structured_logger):
        scored_blocks = score_blocks(merged_blocks, total_lines=len(parsed_line_list))
    log_stage_event(structured_logger, "score_blocks", blocks=len(scored_blocks))

    with measure_stage("classify_blocks", collector, structured_logger):
        classified_blocks = classify_blocks(scored_blocks)
    log_stage_event(structured_logger, "classify_blocks", blocks=len(classified_blocks))

    with measure_stage("rank_blocks", collector, structured_logger):
        ranked_blocks = rank_blocks(classified_blocks)
    collector.record_metric("number_of_blocks", float(len(ranked_blocks)))
    log_stage_event(structured_logger, "rank_blocks", blocks=len(ranked_blocks))

    return ReductionResult(blocks=list(ranked_blocks), summary=None)


__all__ = [
    "classify_blocks",
    "detect_anchors",
    "expand_context",
    "merge_blocks",
    "rank_blocks",
    "reduce_parsed_lines",
    "score_blocks",
    "suppress_noise",
    "build_clusters",
]
