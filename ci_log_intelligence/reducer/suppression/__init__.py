from __future__ import annotations

import re
from typing import Iterable, List

from ...models import LogBlock, ParsedLine

_SEPARATOR_PATTERN = re.compile(r"^[=\-_*#.]{3,}$")


def suppress_noise(blocks: Iterable[LogBlock]) -> List[LogBlock]:
    suppressed: list[LogBlock] = []

    for block in blocks:
        anchor_line_numbers = {anchor.line_number for anchor in block.anchors}
        retained_lines: list[ParsedLine] = []
        previous_content: str | None = None

        for line in block.lines:
            keep_line = (
                line.line_number in anchor_line_numbers
                or bool(line.signals)
                or not _is_noise(line.content, previous_content)
            )
            if keep_line:
                retained_lines.append(line)
                previous_content = line.content

        if not retained_lines:
            continue

        suppressed.append(
            LogBlock(
                start_line=retained_lines[0].line_number,
                end_line=retained_lines[-1].line_number,
                lines=retained_lines,
                anchors=list(block.anchors),
            )
        )

    return suppressed


def _is_noise(content: str, previous_content: str | None) -> bool:
    stripped = content.strip()
    if not stripped:
        return True
    if previous_content == content:
        return True
    if _SEPARATOR_PATTERN.match(stripped):
        return True
    return False


__all__ = ["suppress_noise"]
