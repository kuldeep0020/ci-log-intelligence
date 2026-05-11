from __future__ import annotations

from typing import Sequence

from ...models import Anchor, ParsedLine
from .base import DetectedFailure, Detector, JobContext
from .build_error_gcc import GccBuildErrorDetector
from .build_error_go import GoBuildErrorDetector
from .build_error_make import MakeBuildErrorDetector
from .build_error_npm import NpmBuildErrorDetector
from .build_error_rust import RustBuildErrorDetector
from .generic import GenericDetector
from .go_test_fail import GoTestFailDetector
from .hash_mismatch import HashMismatchDetector
from .junit_xml import JUnitXmlDetector
from .pytest_fail import PytestFailDetector
from .rust_test_fail import RustTestFailDetector

# Ordered specialized-first: ties in ``_resolve_failure_type`` already break by
# severity then earliest anchor line, but keeping specialized detectors ahead
# of ``GenericDetector`` makes the registry's intent explicit.
#
# Build-error detectors are listed AFTER test-framework detectors so that on
# logs which carry both shapes the ordering matches the upstream-to-downstream
# direction of failure causation, but ``_resolve_failure_type`` breaks ties by
# anchor severity first -- and build errors carry severity 3 versus 2 for
# test failures -- so the actual winning record will be the build error
# regardless of registry order.
_REGISTRY: list[Detector] = [
    HashMismatchDetector(),
    GoTestFailDetector(),
    PytestFailDetector(),
    RustTestFailDetector(),
    JUnitXmlDetector(),
    RustBuildErrorDetector(),
    GoBuildErrorDetector(),
    NpmBuildErrorDetector(),
    MakeBuildErrorDetector(),
    GccBuildErrorDetector(),
    GenericDetector(),
]


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
    "GccBuildErrorDetector",
    "GenericDetector",
    "GoBuildErrorDetector",
    "GoTestFailDetector",
    "HashMismatchDetector",
    "JUnitXmlDetector",
    "JobContext",
    "MakeBuildErrorDetector",
    "NpmBuildErrorDetector",
    "PytestFailDetector",
    "RustBuildErrorDetector",
    "RustTestFailDetector",
    "detected_failures_to_anchors",
    "get_detectors",
    "run_detectors",
]
