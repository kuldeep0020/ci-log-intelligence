"""Detector for GCC and Clang compile errors.

Both GCC and Clang emit ``file:line:col: error: <message>`` lines for C/C++
compile failures. Variants include ``fatal error:`` (preprocessor cannot
find a header) and ``internal compiler error:`` (compiler bug). Warnings
(``warning:``) are NOT in scope here -- those are severity 1 and would
fire on routine compiles.

To avoid colliding with :class:`GoBuildErrorDetector`, the file-path token
excludes paths ending in ``.go`` via a negative lookahead. Go's compiler
emits errors of the same ``file:line:col:`` shape, but with ``.go``
extensions; the lookahead routes those lines to the Go detector exclusively.

After the error line, GCC emits caret-continuation lines indented with
spaces (the ``   42 |     return x;`` source line followed by ``      |  ^``
caret line) and optional ``note:`` continuations on later lines. We capture
the contiguous indented continuation up to 10 lines past the error anchor
as the ``suggested_block_range``.

Extracted fields:

* ``language`` -- constant ``"c_cpp"``.
* ``file_path`` -- matched file.
* ``line`` -- int.
* ``column`` -- int.
* ``severity_text`` -- ``"error"`` / ``"fatal error"`` / ``"internal compiler error"``.
* ``message`` -- text after the severity-text colon.
"""

from __future__ import annotations

import re
from typing import Any, Optional, Sequence

from ...models import ParsedLine
from ...parsing import strip_timestamp_prefix
from .base import DetectedFailure, JobContext

# Negative lookahead excludes ``foo.go:`` paths so the Go detector retains
# ownership of Go compile errors. ``\S+`` covers absolute paths, ``./pkg``,
# subdirectories, etc. The lookahead bites against the entire path token --
# anything that ends in ``.go:`` is rejected.
_GCC_ERROR_PATTERN = re.compile(
    r"^(?P<file>(?!\S+\.go:)\S+):(?P<line>\d+):(?P<col>\d+):\s+"
    r"(?P<severity_text>error|fatal error|internal compiler error):\s+"
    r"(?P<message>.+?)\s*$"
)

# Cap on continuation lines past the error anchor.
_CONTINUATION_MAX_LINES = 10


class GccBuildErrorDetector:
    """Detects ``file:line:col: [error|fatal error|internal compiler error]:`` lines."""

    name: str = "build_error_gcc"
    failure_type: str = "build_error_gcc"

    def scan(
        self,
        parsed_lines: Sequence[ParsedLine],
        _job_context: JobContext,
    ) -> list[DetectedFailure]:
        lines = list(parsed_lines)
        failures: list[DetectedFailure] = []
        for index, line in enumerate(lines):
            stripped = strip_timestamp_prefix(line.content)
            match = _GCC_ERROR_PATTERN.match(stripped)
            if not match:
                continue
            failures.append(_build_failure(line, match, index, lines))
        return failures


def _build_failure(
    error_line: ParsedLine,
    match: "re.Match[str]",
    error_index: int,
    lines: Sequence[ParsedLine],
) -> DetectedFailure:
    try:
        line_no = int(match.group("line"))
        column = int(match.group("col"))
    except ValueError:  # pragma: no cover -- regex guarantees digits
        line_no = 0
        column = 0
    extracted: dict[str, Any] = {
        "language": "c_cpp",
        "file_path": match.group("file"),
        "line": line_no,
        "column": column,
        "severity_text": match.group("severity_text"),
        "message": match.group("message"),
    }
    last_continuation = _find_last_continuation(error_index, lines, error_line.step_id)
    if last_continuation > error_line.line_number:
        suggested: Optional[tuple[int, int]] = (
            error_line.line_number,
            last_continuation,
        )
    else:
        suggested = None
    return DetectedFailure(
        type="build_error_gcc",
        anchor_lines=[error_line.line_number],
        severity=3,
        classification_claim="root_cause",
        extracted_fields=extracted,
        suggested_block_range=suggested,
        anchor_type="build_error_gcc",
    )


def _find_last_continuation(
    error_index: int,
    lines: Sequence[ParsedLine],
    step_id: Optional[str],
) -> int:
    """Walk forward across contiguous GCC caret/note continuation lines.

    Continuations are:

    * Lines starting with at least 3 spaces (caret/source lines).
    * Lines matching ``file:line:col: note: ...`` for the same compile unit.

    Hard cap: ``_CONTINUATION_MAX_LINES`` past the error anchor.
    """
    anchor = lines[error_index]
    last = anchor.line_number
    upper_bound_index = min(
        len(lines),
        error_index + 1 + _CONTINUATION_MAX_LINES,
    )
    for offset in range(error_index + 1, upper_bound_index):
        candidate = lines[offset]
        if candidate.step_id != step_id:
            break
        stripped = strip_timestamp_prefix(candidate.content)
        if _is_continuation(stripped):
            last = candidate.line_number
            continue
        break
    return last


def _is_continuation(stripped: str) -> bool:
    """True if ``stripped`` looks like a GCC caret/note continuation line."""
    if not stripped.strip():
        return False
    if stripped.startswith("   "):
        return True
    # ``file:line:col: note: ...`` continuations.
    if _GCC_NOTE_PATTERN.match(stripped):
        return True
    return False


_GCC_NOTE_PATTERN = re.compile(
    r"^(?!\S+\.go:)\S+:\d+:\d+:\s+note:\s+"
)


__all__ = ["GccBuildErrorDetector"]
