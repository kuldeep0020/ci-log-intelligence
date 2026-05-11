from __future__ import annotations

from typing import Sequence

from ...models import ParsedLine
from ...signals import SIGNAL_PATTERNS, is_benign_mention, signal_is_filterable
from .base import DetectedFailure, JobContext


class GenericDetector:
    """Tier-based keyword detector used as the catch-all anchor source.

    Each parsed line is checked against the canonical signal patterns in
    :mod:`ci_log_intelligence.signals` -- the same source the parsing layer
    uses to populate ``ParsedLine.signals``. A match emits one
    :class:`DetectedFailure` per (line, signal) pair with the failure
    ``type="generic"`` and ``anchor_type`` set to the signal name -- so
    downstream code can still distinguish ``error`` from ``failed`` from
    ``traceback`` while having a single typed-record carrier per detection.

    Lines that read as a benign zero-count report (e.g. ``[INFO] No errors
    found``, ``0 failures, 0 errors``, ``errors: 0``, ``failures=0``,
    ``No failures``) have their ``error``/``failed``/``warning`` anchors
    suppressed. The other signals (``traceback``, ``exception``,
    ``assertion_error``, ``retrying``) are kept because their tokens do not
    realistically appear inside zero-count benign reports.

    Sharing the patterns with the parsing layer guarantees that
    ``ParsedLine.signals`` and the anchors emitted here always agree on
    which signal names fire for any given line. That parity is load-bearing:
    the classifier reads ``signals`` while the scorer reads anchor severity,
    and a mismatch produces inconsistent block classifications.
    """

    name: str = "generic"
    failure_type: str = "generic"

    def scan(
        self,
        parsed_lines: Sequence[ParsedLine],
        _job_context: JobContext,
    ) -> list[DetectedFailure]:
        failures: list[DetectedFailure] = []
        for line in parsed_lines:
            benign = is_benign_mention(line.content)
            for signal_name, severity, pattern in SIGNAL_PATTERNS:
                if not pattern.search(line.content):
                    continue
                if benign and signal_is_filterable(signal_name):
                    continue
                failures.append(
                    DetectedFailure(
                        type="generic",
                        anchor_lines=[line.line_number],
                        severity=severity,
                        extracted_fields={"signal_name": signal_name},
                        anchor_type=signal_name,
                    )
                )
        return failures


__all__ = ["GenericDetector"]
