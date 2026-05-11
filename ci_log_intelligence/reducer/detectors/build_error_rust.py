"""Detector for ``rustc`` and ``cargo`` build errors.

Two shapes are handled:

1. Coded form ``error[E####]: <message>`` followed by ``  --> file:line:col``.
2. Bare cargo summary ``error: could not compile ...``, ``error: aborting
   due to previous error``, etc. -- plus the rustc internal-error case where
   a bare ``error:`` is followed within ``_ARROW_LOOKAHEAD_LINES`` by a
   ``-->`` location.

Bare ``error: <msg>`` is otherwise ambiguous with the generic ``error:``
keyword that fires across many tools, so we only claim a bare-form detection
when the message matches one of ``_BARE_CARGO_KEYWORDS`` OR a ``-->`` follows.
Lines like ``error: missing module`` from unrelated tooling are routed to
the generic detector only.

After the location line rustc emits source/caret/note continuation lines.
We capture the contiguous run up to ``_CONTINUATION_MAX_LINES`` past the
error anchor as the ``suggested_block_range`` so ``expand_context`` keeps
the diagnostic chain in one block.

Extracted fields: ``language`` (constant ``"rust"``), ``error_code``
(when coded), ``message``, ``file_path``/``line``/``column`` (when arrow
follows).
"""

from __future__ import annotations

import re
from typing import Any, Optional, Sequence

from ...models import ParsedLine
from ...parsing import strip_timestamp_prefix
from .base import DetectedFailure, JobContext
from .patterns import RUST_ARROW_PATTERN

# Coded form: ``error[E0382]: message``. ``error_code`` and ``message`` separate.
# Bare form: ``error: message``. We distinguish them with optional group.
# Anchored with ``^\s*`` so we tolerate the leading whitespace cargo sometimes
# emits inside ``> error: ...`` block headers in colored output -- and the
# caller must pass strip_timestamp_prefix(content) to also handle GHA prefixes.
_ERROR_PATTERN = re.compile(
    r"^\s*error(?:\[(?P<error_code>E\d+)\])?:\s+(?P<message>.+?)\s*$"
)

# How far past the error line to look for the ``-->`` location line. rustc
# always puts it on the next line; we allow up to 3 to tolerate intervening
# blank lines or cargo color-reset noise.
_ARROW_LOOKAHEAD_LINES = 3

# Hard cap on the diagnostic continuation span (lines past the error anchor).
_CONTINUATION_MAX_LINES = 30

# Substrings (case-sensitive, matched against the message body) that make a
# bare ``error: ...`` line unambiguously a rustc/cargo summary line. Keeps
# generic tool noise like ``error: missing module`` from being misrouted to
# this detector.
_BARE_CARGO_KEYWORDS: tuple[str, ...] = (
    "could not compile",
    "aborting due to",
    "linking with",
    "failed to compile",
    "Could not compile",
)


class RustBuildErrorDetector:
    """Detects ``error[E####]:`` and bare ``error:`` lines from rustc/cargo output."""

    name: str = "build_error_rust"
    failure_type: str = "build_error_rust"

    def scan(
        self,
        parsed_lines: Sequence[ParsedLine],
        _job_context: JobContext,
    ) -> list[DetectedFailure]:
        lines = list(parsed_lines)
        failures: list[DetectedFailure] = []
        for index, line in enumerate(lines):
            stripped = strip_timestamp_prefix(line.content)
            match = _ERROR_PATTERN.match(stripped)
            if not match:
                continue
            error_code = match.group("error_code")
            arrow_info = _find_arrow_location(index, lines)
            if error_code is None and not _bare_form_is_rust(
                match.group("message"), arrow_info
            ):
                continue
            failure = _build_failure(line, match, index, lines, arrow_info)
            failures.append(failure)
        return failures


def _build_failure(
    error_line: ParsedLine,
    match: "re.Match[str]",
    error_index: int,
    lines: Sequence[ParsedLine],
    arrow_info: Optional[tuple[str, int, int]],
) -> DetectedFailure:
    extracted: dict[str, Any] = {
        "language": "rust",
        "message": match.group("message"),
    }
    error_code = match.group("error_code")
    if error_code:
        extracted["error_code"] = error_code

    if arrow_info is not None:
        file_path, line_no, column = arrow_info
        extracted["file_path"] = file_path
        extracted["line"] = line_no
        extracted["column"] = column

    last_continuation = _find_last_continuation(error_index, lines, error_line.step_id)
    if last_continuation > error_line.line_number:
        suggested: Optional[tuple[int, int]] = (
            error_line.line_number,
            last_continuation,
        )
    else:
        suggested = None

    return DetectedFailure(
        type="build_error_rust",
        anchor_lines=[error_line.line_number],
        severity=3,
        classification_claim="root_cause",
        extracted_fields=extracted,
        suggested_block_range=suggested,
        anchor_type="build_error_rust",
    )


def _bare_form_is_rust(
    message: str,
    arrow_info: Optional[tuple[str, int, int]],
) -> bool:
    """Return True if a bare ``error: <message>`` line is rust-origin.

    A bare error is unambiguously rust if either:

    * The message contains a known cargo summary keyword
      (``could not compile``, ``aborting due to``, etc.), or
    * A ``-->`` location follows the line within the lookahead window
      (the rustc internal-error path).
    """
    if arrow_info is not None:
        return True
    return any(keyword in message for keyword in _BARE_CARGO_KEYWORDS)


def _find_arrow_location(
    error_index: int,
    lines: Sequence[ParsedLine],
) -> Optional[tuple[str, int, int]]:
    """Return ``(file_path, line, col)`` from a nearby ``-->`` line.

    Looks at the next ``_ARROW_LOOKAHEAD_LINES`` lines (same step) for the
    rustc ``--> file:line:col`` location indicator.
    """
    anchor = lines[error_index]
    end_index = min(len(lines), error_index + 1 + _ARROW_LOOKAHEAD_LINES)
    for offset in range(error_index + 1, end_index):
        candidate = lines[offset]
        if candidate.step_id != anchor.step_id:
            break
        stripped = strip_timestamp_prefix(candidate.content)
        arrow_match = RUST_ARROW_PATTERN.match(stripped)
        if arrow_match:
            try:
                line_no = int(arrow_match.group("line"))
                column = int(arrow_match.group("col"))
            except ValueError:
                return None
            return arrow_match.group("file"), line_no, column
    return None


def _find_last_continuation(
    error_index: int,
    lines: Sequence[ParsedLine],
    step_id: Optional[str],
) -> int:
    """Walk forward across contiguous diagnostic continuation lines.

    Continuation = ``-->`` line, whitespace-indented body, ``= note:`` /
    ``= help:`` lines, or source-line gutter (``3  |     ...``). Stops at
    the first non-continuation line, step boundary, or
    ``_CONTINUATION_MAX_LINES`` past the anchor.
    """
    anchor = lines[error_index]
    last = anchor.line_number
    upper_bound_index = min(len(lines), error_index + 1 + _CONTINUATION_MAX_LINES)
    for offset in range(error_index + 1, upper_bound_index):
        candidate = lines[offset]
        if candidate.step_id != step_id:
            break
        stripped = strip_timestamp_prefix(candidate.content)
        if _is_diagnostic_continuation(stripped):
            last = candidate.line_number
            continue
        break
    return last


def _is_diagnostic_continuation(stripped: str) -> bool:
    """True if ``stripped`` looks like a rustc diagnostic continuation line."""
    if not stripped.strip():
        return False
    if RUST_ARROW_PATTERN.match(stripped):
        return True
    if stripped.startswith(" ") or stripped.startswith("\t"):
        return True
    if _DIGIT_GUTTER_PATTERN.match(stripped):
        return True
    return False


# Source-line gutter: ``3  |     let s = ...``. rustc puts the source line
# number in the leftmost column followed by whitespace and a ``|`` separator.
_DIGIT_GUTTER_PATTERN = re.compile(r"^\d+\s*\|")


__all__ = ["RustBuildErrorDetector"]
