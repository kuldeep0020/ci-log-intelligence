"""Detector for pytest test failures.

Pytest emits failures in two shapes:

1. **Summary lines** (always present at end of run)::

       FAILED tests/test_foo.py::test_bar - AssertionError: expected 1 got 2

   The summary is the canonical anchor: one per failing test, easy to parse,
   and unambiguous on the test identifier.

2. **Pytest section headers** (``===== FAILURES =====`` followed by
   ``______ test_name ______`` blocks containing the traceback).

   We do NOT emit a separate detection for these because every failure in
   the section will also have a summary line. Instead, when we see a
   summary, we look BACKWARD up to 500 lines (same step) for the
   corresponding traceback separator and -- if found -- include both lines
   as anchors and widen ``suggested_block_range`` to cover the traceback.

Extracted fields:

* ``test_id`` -- e.g. ``tests/test_foo.py::test_bar``.
* ``framework`` -- constant ``"pytest"``.
* ``assertion_message`` -- the captured assertion suffix, if any.
"""

from __future__ import annotations

import re
from typing import Any, Optional, Sequence

from ...models import ParsedLine
from ...parsing import strip_timestamp_prefix
from .base import DetectedFailure, JobContext

# Pytest summary line. ``test_id`` is ``path::test_name`` (parametrized tests
# read like ``tests/test_x.py::test_y[param1]``; ``\S+::\S+`` covers both).
# Anchored to start-of-line (after optional leading whitespace) so substring
# matches inside arbitrary log content -- e.g. ``>>>> reported FAILED
# tests/x.py::test_y`` or XML attributes such as
# ``<failure message="FAILED test::abc"/>`` -- are not misread as pytest
# summaries. Callers pass ``strip_timestamp_prefix(content)`` so the
# anchor still matches timestamp-prefixed CI log lines.
_SUMMARY_PATTERN = re.compile(
    r"^\s*FAILED\s+(?P<test_id>\S+::\S+)(?:\s+-\s+(?P<assertion>.*\S))?\s*$"
)

# Traceback header separator: ``___________________ test_bar ___________________``
# (pytest pads on both sides; the test_name token in the middle is the
# bare test function name, not the full ``path::test_name`` id).
_TRACEBACK_SEPARATOR_PATTERN = re.compile(r"^_+\s+(?P<test_name>\S+)\s+_+\s*$")

_TRACEBACK_LOOKBACK_WINDOW = 500


class PytestFailDetector:
    """Detects pytest ``FAILED test_id`` summary lines and pairs them with traceback headers."""

    name: str = "pytest_fail"
    failure_type: str = "pytest_fail"

    def scan(
        self,
        parsed_lines: Sequence[ParsedLine],
        _job_context: JobContext,
    ) -> list[DetectedFailure]:
        summaries: list[tuple[ParsedLine, str, Optional[str]]] = []
        separators: list[tuple[ParsedLine, str]] = []
        for line in parsed_lines:
            stripped = strip_timestamp_prefix(line.content).rstrip()
            sep_match = _TRACEBACK_SEPARATOR_PATTERN.match(stripped)
            if sep_match:
                separators.append((line, sep_match.group("test_name")))
                # A line that matches the separator shape cannot also be a
                # FAILED summary, so skip to next line.
                continue
            summary_match = _SUMMARY_PATTERN.search(stripped)
            if summary_match:
                summaries.append(
                    (
                        line,
                        summary_match.group("test_id"),
                        summary_match.group("assertion"),
                    )
                )

        # When two tests share a bare name (``a.py::test_x`` and
        # ``b.py::test_x``), every matching separator would otherwise be paired
        # with EACH summary -- producing overlapping ``suggested_block_range``
        # values. Track consumed separator line numbers so each separator pairs
        # at most once.
        consumed_separator_line_numbers: set[int] = set()
        failures: list[DetectedFailure] = []
        for summary_line, test_id, assertion in summaries:
            failures.append(
                _build_failure(
                    summary_line,
                    test_id,
                    assertion,
                    separators,
                    consumed_separator_line_numbers,
                )
            )
        return failures


def _build_failure(
    summary_line: ParsedLine,
    test_id: str,
    assertion: Optional[str],
    separators: Sequence[tuple[ParsedLine, str]],
    consumed_separator_line_numbers: set[int],
) -> DetectedFailure:
    extracted: dict[str, Any] = {
        "framework": "pytest",
        "test_id": test_id,
    }
    if assertion:
        extracted["assertion_message"] = assertion

    bare_name = _bare_test_name(test_id)
    separator_line = _find_traceback_separator(
        summary_line, bare_name, separators, consumed_separator_line_numbers
    )
    if separator_line is not None:
        consumed_separator_line_numbers.add(separator_line.line_number)
        anchor_lines = sorted(
            {summary_line.line_number, separator_line.line_number}
        )
        suggested = (min(anchor_lines), max(anchor_lines))
    else:
        anchor_lines = [summary_line.line_number]
        suggested = None

    return DetectedFailure(
        type="pytest_fail",
        anchor_lines=anchor_lines,
        severity=2,
        classification_claim="root_cause",
        extracted_fields=extracted,
        suggested_block_range=suggested,
        anchor_type="pytest_fail",
    )


def _bare_test_name(test_id: str) -> str:
    """``tests/test_foo.py::test_bar[param]`` -> ``test_bar[param]``."""
    if "::" in test_id:
        return test_id.rsplit("::", 1)[-1]
    return test_id


def _find_traceback_separator(
    summary_line: ParsedLine,
    bare_test_name: str,
    separators: Sequence[tuple[ParsedLine, str]],
    consumed_separator_line_numbers: set[int],
) -> Optional[ParsedLine]:
    """Look BACKWARD up to 500 lines (same step) for an UNCONSUMED ``_____ <test_name> _____``.

    Pytest's traceback separator emits the bare test function name (no
    file path), so we compare against the bare-name suffix of the test_id.
    Parametrized tests have a ``[param]`` suffix that pytest preserves
    verbatim in the separator, so equality matches.

    Separators in ``consumed_separator_line_numbers`` were already paired with
    a previous summary and are skipped; this keeps two summaries with
    colliding bare names from sharing one separator.
    """
    lower_bound = summary_line.line_number - _TRACEBACK_LOOKBACK_WINDOW
    best: Optional[ParsedLine] = None
    for sep_line, sep_name in separators:
        if sep_line.step_id != summary_line.step_id:
            continue
        if sep_line.line_number >= summary_line.line_number:
            continue
        if sep_line.line_number < lower_bound:
            continue
        if sep_name != bare_test_name:
            continue
        if sep_line.line_number in consumed_separator_line_numbers:
            continue
        # Take the LATEST (nearest) separator before the summary.
        if best is None or sep_line.line_number > best.line_number:
            best = sep_line
    return best


__all__ = ["PytestFailDetector"]
