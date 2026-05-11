from __future__ import annotations

from typing import Sequence

from ...models import Anchor, ParsedLine
from .base import DetectedFailure, Detector, JobContext
from .generic import GenericDetector

_REGISTRY: list[Detector] = [GenericDetector()]


def get_detectors() -> list[Detector]:
    return list(_REGISTRY)


def run_detectors(
    parsed_lines: Sequence[ParsedLine],
    job_context: JobContext,
    detectors: Sequence[Detector] | None = None,
) -> list[DetectedFailure]:
    active = detectors if detectors is not None else _REGISTRY
    failures: list[DetectedFailure] = []
    # Detector-major iteration: order is not load-bearing -- ``build_clusters`` re-sorts anchors.
    for detector in active:
        failures.extend(detector.scan(parsed_lines, job_context))
    return failures


def detected_failures_to_anchors(failures: Sequence[DetectedFailure]) -> list[Anchor]:
    """Flatten DetectedFailure records into the Anchor stream the rest of the pipeline expects.

    Anchor type resolution: ``failure.anchor_type`` if set, else ``failure.type``. This lets a
    detector that emits multiple distinct anchor types per detection (e.g. ``GenericDetector``)
    override the per-detection ``type`` discriminator.
    """
    anchors: list[Anchor] = []
    for failure in failures:
        anchor_type = failure.anchor_type or failure.type
        for line_number in failure.anchor_lines:
            anchors.append(
                Anchor(
                    line_number=line_number,
                    type=anchor_type,
                    severity=failure.severity,
                )
            )
    return anchors


__all__ = [
    "DetectedFailure",
    "Detector",
    "GenericDetector",
    "JobContext",
    "detected_failures_to_anchors",
    "get_detectors",
    "run_detectors",
]
