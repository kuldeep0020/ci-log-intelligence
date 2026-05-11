"""Detector for ``npm ERR!`` and ``yarn error`` build-error blocks.

Both tools emit multi-line error blocks prefixed by a stable marker:

* npm: every line starts with ``npm ERR!``.
* yarn: every line starts with ``yarn error``.

Adjacent lines sharing the same prefix and step form a single block; each block
emits one DetectedFailure. The block boundary is "contiguous same-prefix lines"
- a single intervening unrelated line ends the block.

Special npm metadata lines:

* ``npm ERR! code ELIFECYCLE`` -- captured into ``error_code``.
* ``npm ERR! errno 1`` -- captured into ``errno`` as an int.

Extracted fields:

* ``language`` -- constant ``"javascript"``.
* ``tool`` -- ``"npm"`` or ``"yarn"``.
* ``error_code`` -- npm-only, optional.
* ``errno`` -- npm-only int, optional.
* ``message`` -- the FIRST prefix line's payload (concise summary).
"""

from __future__ import annotations

import re
from typing import Any, Optional, Sequence

from ...models import ParsedLine
from ...parsing import strip_timestamp_prefix
from .base import DetectedFailure, JobContext

# Each prefix is matched at the start of the stripped line. The trailing
# whitespace is optional so ``npm ERR!`` alone (no payload) still matches.
_NPM_PREFIX_PATTERN = re.compile(r"^npm ERR!\s?(?P<rest>.*)$")
_YARN_PREFIX_PATTERN = re.compile(r"^yarn error\s?(?P<rest>.*)$")

# Inside an npm block, look for these metadata payloads on any line.
_NPM_CODE_PATTERN = re.compile(r"^code\s+(?P<code>\S+)\s*$")
_NPM_ERRNO_PATTERN = re.compile(r"^errno\s+(?P<errno>-?\d+)\s*$")


class NpmBuildErrorDetector:
    """Detects contiguous ``npm ERR!`` / ``yarn error`` blocks as build errors."""

    name: str = "build_error_npm"
    failure_type: str = "build_error_npm"

    def scan(
        self,
        parsed_lines: Sequence[ParsedLine],
        _job_context: JobContext,
    ) -> list[DetectedFailure]:
        lines = list(parsed_lines)
        failures: list[DetectedFailure] = []
        index = 0
        while index < len(lines):
            block = _consume_block(index, lines)
            if block is None:
                index += 1
                continue
            tool, block_lines, block_payloads, next_index = block
            failures.append(_build_failure(tool, block_lines, block_payloads))
            index = next_index
        return failures


def _consume_block(
    start_index: int,
    lines: Sequence[ParsedLine],
) -> Optional[tuple[str, list[ParsedLine], list[str], int]]:
    """If ``lines[start_index]`` begins an npm/yarn block, consume it.

    Returns ``(tool, block_lines, block_payloads, next_index)`` or ``None``
    when this line is not a block start. ``block_payloads`` is the captured
    text after the prefix for each line, in order.
    """
    start_line = lines[start_index]
    stripped = strip_timestamp_prefix(start_line.content)
    tool, first_payload = _match_prefix(stripped)
    if tool is None:
        return None
    block_lines = [start_line]
    block_payloads = [first_payload]
    index = start_index + 1
    while index < len(lines):
        candidate = lines[index]
        if candidate.step_id != start_line.step_id:
            break
        next_stripped = strip_timestamp_prefix(candidate.content)
        next_tool, next_payload = _match_prefix(next_stripped)
        if next_tool != tool:
            break
        block_lines.append(candidate)
        block_payloads.append(next_payload)
        index += 1
    return tool, block_lines, block_payloads, index


def _match_prefix(stripped: str) -> tuple[Optional[str], str]:
    """Return ``("npm" | "yarn" | None, payload_after_prefix)``."""
    npm_match = _NPM_PREFIX_PATTERN.match(stripped)
    if npm_match:
        return "npm", npm_match.group("rest")
    yarn_match = _YARN_PREFIX_PATTERN.match(stripped)
    if yarn_match:
        return "yarn", yarn_match.group("rest")
    return None, ""


def _build_failure(
    tool: str,
    block_lines: Sequence[ParsedLine],
    block_payloads: Sequence[str],
) -> DetectedFailure:
    first_line = block_lines[0]
    last_line = block_lines[-1]
    extracted: dict[str, Any] = {
        "language": "javascript",
        "tool": tool,
        "message": block_payloads[0].strip(),
    }
    if tool == "npm":
        _enrich_npm_metadata(block_payloads, extracted)
    if len(block_lines) > 1:
        suggested: Optional[tuple[int, int]] = (
            first_line.line_number,
            last_line.line_number,
        )
    else:
        suggested = None
    return DetectedFailure(
        type="build_error_npm",
        anchor_lines=[first_line.line_number],
        severity=3,
        classification_claim="root_cause",
        extracted_fields=extracted,
        suggested_block_range=suggested,
        anchor_type="build_error_npm",
    )


def _enrich_npm_metadata(
    block_payloads: Sequence[str],
    extracted: dict[str, Any],
) -> None:
    for payload in block_payloads:
        code_match = _NPM_CODE_PATTERN.match(payload)
        if code_match and "error_code" not in extracted:
            extracted["error_code"] = code_match.group("code")
            continue
        errno_match = _NPM_ERRNO_PATTERN.match(payload)
        if errno_match and "errno" not in extracted:
            try:
                extracted["errno"] = int(errno_match.group("errno"))
            except ValueError:  # pragma: no cover -- regex guarantees digits
                pass


__all__ = ["NpmBuildErrorDetector"]
