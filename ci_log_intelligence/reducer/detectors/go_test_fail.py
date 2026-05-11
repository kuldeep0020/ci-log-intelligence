"""Detector for standalone Go ``--- FAIL: TestName`` markers.

Emits one ``DetectedFailure(type="go_test_fail", ...)`` per Go test failure
that is NOT already paired with a ``file hashes don't match`` line by
:class:`HashMismatchDetector`. Coordination is by line-number set: this
detector calls ``hash_mismatch_claimed_fail_lines`` to obtain the exact
FAIL line numbers HashMismatchDetector will claim (NEAREST FAIL per
mismatch, same step, within ``HASH_MISMATCH_PAIRING_WINDOW`` lines) and
skips precisely those. Any co-located FAILs that the hash-mismatch
detector did NOT pair flow through here, so a single failing test never
produces both a ``hash_mismatch`` and a ``go_test_fail`` record on the
same FAIL line, and no FAIL is silently dropped by both detectors.

Extracted fields:

* ``test_name`` -- captured non-whitespace token (subtests included).
* ``framework`` -- constant ``"go"``.
* ``duration_seconds`` -- parsed from a trailing ``(N.Ns)`` if present.
* ``package`` -- parsed from a later ``FAIL\\s+\\S+\\s+\\S+`` line in the
  same step, looking up to 100 lines AFTER the FAIL line. Informational
  only; not included in ``anchor_lines``.
"""

from __future__ import annotations

import re
from typing import Any, Optional, Sequence

from ...models import ParsedLine
from .base import DetectedFailure, JobContext
from .hash_mismatch import hash_mismatch_claimed_fail_lines
from .patterns import GO_TEST_FAIL_PATTERN

# Duration captures the trailing ``(<n.n>s)`` form Go test emits for short
# tests. Long-running tests with multi-unit durations (``(1m30s)``,
# ``(1h2m3s)``) match the ``30s`` / ``3s`` tail only -- the higher units
# are silently truncated. Acceptable v1 limitation; revisit if real logs
# show consumers misreading the duration as the actual test runtime.
_DURATION_PATTERN = re.compile(r"\((?P<duration>\d+(?:\.\d+)?)s\)")
# Go test runner emits ``FAIL\tgithub.com/owner/repo/pkg\t1.234s`` after a
# failing test. The package token is the second whitespace-delimited field.
# The Go runner's package-summary line can be prefixed by a CI timestamp
# (e.g. ``2024-01-15T12:34:58.100Z``), so we anchor on a leading boundary
# (start-of-line OR whitespace) rather than ``^`` directly.
_PACKAGE_PATTERN = re.compile(r"(?:^|\s)FAIL\s+(?P<package>\S+)\s+\S+")

# How far AFTER the FAIL line we look for the package summary line.
_PACKAGE_LOOKAHEAD_WINDOW = 100


class GoTestFailDetector:
    """Emits typed records for ``--- FAIL: TestName`` lines not owned by the hash-mismatch detector."""

    name: str = "go_test_fail"
    failure_type: str = "go_test_fail"

    def scan(
        self,
        parsed_lines: Sequence[ParsedLine],
        _job_context: JobContext,
    ) -> list[DetectedFailure]:
        # Defer to HashMismatchDetector for the exact set of FAIL lines it will
        # claim. Skipping by line-number set (rather than a re-scan against the
        # +/-50 line window) keeps the two detectors in lock-step when multiple
        # FAILs sit near a single mismatch: HashMismatchDetector pairs only the
        # nearest FAIL, and the other co-located FAILs flow through here.
        claimed_fail_lines = hash_mismatch_claimed_fail_lines(parsed_lines)

        failures: list[DetectedFailure] = []
        for line in parsed_lines:
            fail_match = GO_TEST_FAIL_PATTERN.search(line.content)
            if not fail_match:
                continue
            if line.line_number in claimed_fail_lines:
                continue
            failures.append(
                _build_failure(line, fail_match.group("test_name"), parsed_lines)
            )
        return failures


def _build_failure(
    fail_line: ParsedLine,
    test_name: str,
    parsed_lines: Sequence[ParsedLine],
) -> DetectedFailure:
    extracted: dict[str, Any] = {
        "framework": "go",
        "test_name": test_name,
    }
    duration = _parse_duration(fail_line.content)
    if duration is not None:
        extracted["duration_seconds"] = duration
    package = _find_package(fail_line, parsed_lines)
    if package is not None:
        extracted["package"] = package
    return DetectedFailure(
        type="go_test_fail",
        anchor_lines=[fail_line.line_number],
        severity=2,
        classification_claim="root_cause",
        extracted_fields=extracted,
        suggested_block_range=None,
        anchor_type="go_test_fail",
    )


def _parse_duration(content: str) -> Optional[float]:
    match = _DURATION_PATTERN.search(content)
    if not match:
        return None
    try:
        return float(match.group("duration"))
    except ValueError:
        return None


def _find_package(
    fail_line: ParsedLine,
    parsed_lines: Sequence[ParsedLine],
) -> Optional[str]:
    """Look up to 100 lines AFTER ``fail_line`` (same step) for ``FAIL <package> <duration>``."""
    fail_lineno = fail_line.line_number
    upper_bound = fail_lineno + _PACKAGE_LOOKAHEAD_WINDOW
    for line in parsed_lines:
        if line.line_number <= fail_lineno:
            continue
        if line.line_number > upper_bound:
            break
        if line.step_id != fail_line.step_id:
            continue
        match = _PACKAGE_PATTERN.search(line.content)
        if match:
            return match.group("package")
    return None


__all__ = ["GoTestFailDetector"]
