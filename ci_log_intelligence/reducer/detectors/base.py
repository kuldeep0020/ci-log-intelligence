from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional, Protocol, Sequence

from ...models import ParsedLine


@dataclass(slots=True, frozen=True)
class JobContext:
    job_name: Optional[str]
    run_id: Optional[int]
    repo: Optional[str]


@dataclass(slots=True)
class DetectedFailure:
    """A single failure detected by a Detector.

    Fields:
        type: Failure category discriminator emitted in the report's ``failures[]`` array
            (e.g. ``"generic"``, ``"hash_mismatch"``, ``"build_error_rust"``). Stable across
            anchors produced by the same detection.
        anchor_lines: One or more line numbers (1-indexed) that triggered this detection.
            Cross-line detectors (e.g. hash-mismatch pairing) emit multiple lines.
        severity: 1 (informational/flake), 2 (failure), 3 (root-cause-strength). Drives scoring.
        classification_claim: If the detector knows the classification, one of
            ``"root_cause" | "symptom" | "flaky"``. Otherwise ``None`` -- the classifier decides.
        extracted_fields: Type-specific payload surfaced in the report's typed record
            (e.g. ``test_name``, ``file_path``, ``warehouse_target``). Schema is per-``type``.
        suggested_block_range: Advisory ``(start_line, end_line)`` the detector believes is the
            informative span. When set, ``expand_context`` will honor it as the outer bound
            instead of applying the default +/-20 expansion. When ``None``, default expansion
            applies. Consumed in a later step; detectors may populate it today.
        anchor_type: If set, overrides ``type`` for the ``Anchor`` records produced by this
            detection. Used when one detector emits several distinct anchor types per
            detection (e.g. ``GenericDetector`` maps the 7 signal patterns onto separate
            anchor types while reporting ``type="generic"`` for the failure record).
            When ``None``, the anchor takes ``type`` directly.
    """

    type: str
    anchor_lines: list[int]
    severity: int
    classification_claim: Optional[str] = None
    extracted_fields: dict[str, Any] = field(default_factory=dict)
    suggested_block_range: Optional[tuple[int, int]] = None
    anchor_type: Optional[str] = None


class Detector(Protocol):
    name: str
    failure_type: str

    def scan(
        self,
        parsed_lines: Sequence[ParsedLine],
        job_context: JobContext,
    ) -> list[DetectedFailure]: ...


__all__ = ["DetectedFailure", "Detector", "JobContext"]
