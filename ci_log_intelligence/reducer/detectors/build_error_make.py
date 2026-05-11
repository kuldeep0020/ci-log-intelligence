"""Detector for ``make`` recipe failures.

Make emits a single-line ``*** [...] Error N`` marker when a recipe step
exits with a non-zero status::

    make: *** [Makefile:42: build] Error 1
    make[1]: *** [tests/Makefile:10: test] Error 2

Older make versions omit the makefile and line number, emitting only the
target::

    make: *** [build] Error 1

The detector handles both shapes via an optional ``makefile:line:`` group.

Extracted fields:

* ``language`` -- constant ``"make"``.
* ``target`` -- the rule that failed.
* ``exit_code`` -- int.
* ``makefile`` -- optional file path.
* ``makefile_line`` -- optional int.
"""

from __future__ import annotations

import re
from typing import Any, Sequence

from ...models import ParsedLine
from ...parsing import strip_timestamp_prefix
from .base import DetectedFailure, JobContext

# Anchored to start-of-line (after timestamp strip). The submake prefix
# ``make[1]:`` -> ``make[\d+]:`` is optional.
_MAKE_ERROR_PATTERN = re.compile(
    r"^make(?:\[\d+\])?:\s+\*\*\*\s+"
    r"\[(?:(?P<makefile>[^:\]]+):(?P<makefile_line>\d+):\s+)?"
    r"(?P<target>[^\]]+)\]\s+Error\s+(?P<exit_code>\d+)\s*$"
)


class MakeBuildErrorDetector:
    """Detects ``make: *** [...] Error N`` recipe-failure lines."""

    name: str = "build_error_make"
    failure_type: str = "build_error_make"

    def scan(
        self,
        parsed_lines: Sequence[ParsedLine],
        _job_context: JobContext,
    ) -> list[DetectedFailure]:
        failures: list[DetectedFailure] = []
        for line in parsed_lines:
            stripped = strip_timestamp_prefix(line.content)
            match = _MAKE_ERROR_PATTERN.match(stripped)
            if not match:
                continue
            failures.append(_build_failure(line, match))
        return failures


def _build_failure(
    error_line: ParsedLine,
    match: "re.Match[str]",
) -> DetectedFailure:
    try:
        exit_code = int(match.group("exit_code"))
    except ValueError:  # pragma: no cover -- regex guarantees digits
        exit_code = 0
    extracted: dict[str, Any] = {
        "language": "make",
        "target": match.group("target"),
        "exit_code": exit_code,
    }
    makefile = match.group("makefile")
    makefile_line = match.group("makefile_line")
    if makefile is not None:
        extracted["makefile"] = makefile
    if makefile_line is not None:
        try:
            extracted["makefile_line"] = int(makefile_line)
        except ValueError:  # pragma: no cover -- regex guarantees digits
            pass
    return DetectedFailure(
        type="build_error_make",
        anchor_lines=[error_line.line_number],
        severity=3,
        classification_claim="root_cause",
        extracted_fields=extracted,
        suggested_block_range=None,
        anchor_type="build_error_make",
    )


__all__ = ["MakeBuildErrorDetector"]
