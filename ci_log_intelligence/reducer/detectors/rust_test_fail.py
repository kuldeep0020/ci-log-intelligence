"""Detector for ``cargo test`` failures.

Cargo emits two co-occurring patterns for each failing test:

1. A per-test summary line at the end::

       test tests::it_handles_empty_input ... FAILED

2. A panic detail emitted during the test run::

       thread 'tests::it_handles_empty_input' panicked at 'assertion failed: ...', src/lib.rs:42:5

The thread name in Rust unit tests is the test path verbatim, so pairing is
exact: for each ``... FAILED`` line, look BACKWARD up to 500 lines (same
step) for a matching ``thread '<test_name>' panicked at`` line.

Extracted fields:

* ``test_name`` -- from the FAILED line (e.g. ``tests::it_handles_empty_input``).
* ``framework`` -- constant ``"rust"``.
* ``panic_message`` -- the panic string, if paired.
* ``panic_location`` -- ``file:line:col`` suffix on the panic line, if present.
"""

from __future__ import annotations

import re
from typing import Any, Optional, Sequence

from ...models import ParsedLine
from .base import DetectedFailure, JobContext

_FAILED_PATTERN = re.compile(r"(?:^|\s)test\s+(?P<test_name>\S+)\s+\.\.\.\s+FAILED\b")
# ``thread '<name>' panicked at '<message>'`` optionally followed by ``, <location>``.
# Rust panic messages can contain escaped quotes; ``[^']+`` is an acceptable
# approximation for the common case (panic messages in CI logs rarely embed
# raw single quotes).
_PANIC_PATTERN = re.compile(
    r"thread\s+'(?P<thread_name>[^']+)'\s+panicked\s+at\s+"
    r"'(?P<panic_message>[^']+)'(?:,\s+(?P<panic_location>\S+))?"
)

_PANIC_LOOKBACK_WINDOW = 500


class RustTestFailDetector:
    """Detects ``cargo test`` failures and pairs each FAILED line with its panic."""

    name: str = "rust_test_fail"
    failure_type: str = "rust_test_fail"

    def scan(
        self,
        parsed_lines: Sequence[ParsedLine],
        _job_context: JobContext,
    ) -> list[DetectedFailure]:
        failed_lines: list[tuple[ParsedLine, str]] = []
        panic_lines: list[tuple[ParsedLine, str, str, Optional[str]]] = []
        for line in parsed_lines:
            failed_match = _FAILED_PATTERN.search(line.content)
            if failed_match:
                failed_lines.append((line, failed_match.group("test_name")))
                continue
            panic_match = _PANIC_PATTERN.search(line.content)
            if panic_match:
                panic_lines.append(
                    (
                        line,
                        panic_match.group("thread_name"),
                        panic_match.group("panic_message"),
                        panic_match.group("panic_location"),
                    )
                )

        failures: list[DetectedFailure] = []
        for failed_line, test_name in failed_lines:
            failures.append(
                _build_failure(failed_line, test_name, panic_lines)
            )
        return failures


def _build_failure(
    failed_line: ParsedLine,
    test_name: str,
    panic_lines: Sequence[tuple[ParsedLine, str, str, Optional[str]]],
) -> DetectedFailure:
    extracted: dict[str, Any] = {
        "framework": "rust",
        "test_name": test_name,
    }
    panic = _find_panic(failed_line, test_name, panic_lines)
    if panic is not None:
        panic_line, _, panic_message, panic_location = panic
        extracted["panic_message"] = panic_message
        if panic_location:
            extracted["panic_location"] = panic_location
        anchor_lines = sorted({panic_line.line_number, failed_line.line_number})
        suggested = (min(anchor_lines), max(anchor_lines))
    else:
        anchor_lines = [failed_line.line_number]
        suggested = None

    return DetectedFailure(
        type="rust_test_fail",
        anchor_lines=anchor_lines,
        severity=2,
        classification_claim="root_cause",
        extracted_fields=extracted,
        suggested_block_range=suggested,
        anchor_type="rust_test_fail",
    )


def _find_panic(
    failed_line: ParsedLine,
    test_name: str,
    panic_lines: Sequence[tuple[ParsedLine, str, str, Optional[str]]],
) -> Optional[tuple[ParsedLine, str, str, Optional[str]]]:
    """Look BACKWARD up to 500 lines (same step) for a matching panic line."""
    lower_bound = failed_line.line_number - _PANIC_LOOKBACK_WINDOW
    best: Optional[tuple[ParsedLine, str, str, Optional[str]]] = None
    for entry in panic_lines:
        panic_line, thread_name, _, _ = entry
        if panic_line.step_id != failed_line.step_id:
            continue
        if panic_line.line_number >= failed_line.line_number:
            continue
        if panic_line.line_number < lower_bound:
            continue
        if thread_name != test_name:
            continue
        if best is None or panic_line.line_number > best[0].line_number:
            best = entry
    return best


__all__ = ["RustTestFailDetector"]
