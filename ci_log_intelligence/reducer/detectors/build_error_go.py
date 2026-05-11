"""Detector for Go compile errors.

Go's compiler emits one-line errors of the form::

    ./pkg/foo.go:42:5: undefined: SomeFunc
    ./pkg/bar.go:10:3: cannot use x (type int) as type string in argument to fmt.Println

These are anchored to start-of-line: any line beginning ``<path>.go:<line>:<col>:``
is a top-level compiler error. Stack-trace lines (e.g.
``  /path/to/file.go:42:5 in func``) cannot match because they have leading
whitespace.

Optional ``# package/path`` headers precede a block of errors in cargo-like
multi-package builds; v1 does not consume them (see Step 6/7 notes), but the
regex is unaffected because those lines begin with ``#``.

Extracted fields:

* ``language`` -- constant ``"go"``.
* ``file_path`` -- matched ``file`` group.
* ``line`` -- int.
* ``column`` -- int.
* ``message`` -- everything after ``file:line:col: ``.
"""

from __future__ import annotations

import re
from typing import Any, Sequence

from ...models import ParsedLine
from ...parsing import strip_timestamp_prefix
from .base import DetectedFailure, JobContext

# Anchored to start-of-line (after timestamp strip). The ``\.go`` requirement
# disambiguates from GCC's generic file:line:col errors -- and stack-trace
# lines have leading whitespace, so they cannot match ``^``.
_GO_BUILD_ERROR_PATTERN = re.compile(
    r"^(?P<file>\S+\.go):(?P<line>\d+):(?P<col>\d+):\s+(?P<message>.+?)\s*$"
)


class GoBuildErrorDetector:
    """Detects top-level ``<file>.go:<line>:<col>: <message>`` Go compile errors."""

    name: str = "build_error_go"
    failure_type: str = "build_error_go"

    def scan(
        self,
        parsed_lines: Sequence[ParsedLine],
        _job_context: JobContext,
    ) -> list[DetectedFailure]:
        failures: list[DetectedFailure] = []
        for line in parsed_lines:
            stripped = strip_timestamp_prefix(line.content)
            match = _GO_BUILD_ERROR_PATTERN.match(stripped)
            if not match:
                continue
            failures.append(_build_failure(line, match))
        return failures


def _build_failure(
    error_line: ParsedLine,
    match: "re.Match[str]",
) -> DetectedFailure:
    try:
        line_no = int(match.group("line"))
        column = int(match.group("col"))
    except ValueError:  # pragma: no cover -- regex guarantees digits
        line_no = 0
        column = 0
    extracted: dict[str, Any] = {
        "language": "go",
        "file_path": match.group("file"),
        "line": line_no,
        "column": column,
        "message": match.group("message"),
    }
    return DetectedFailure(
        type="build_error_go",
        anchor_lines=[error_line.line_number],
        severity=3,
        classification_claim="root_cause",
        extracted_fields=extracted,
        suggested_block_range=None,
        anchor_type="build_error_go",
    )


__all__ = ["GoBuildErrorDetector"]
