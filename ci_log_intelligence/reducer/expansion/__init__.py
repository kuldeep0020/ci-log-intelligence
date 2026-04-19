from __future__ import annotations

import re
from typing import Iterable, List

from ...models import AnchorCluster, LogBlock, ParsedLine

_STACK_TRACE_TERMINAL = re.compile(r"^[A-Za-z_][\w.]*?(Error|Exception)(:|$)")


def expand_context(parsed_lines: Iterable[ParsedLine], clusters: Iterable[AnchorCluster]) -> List[LogBlock]:
    lines = list(parsed_lines)
    if not lines:
        return []

    line_map = {line.line_number: line for line in lines}
    max_line_number = lines[-1].line_number
    blocks: list[LogBlock] = []

    for cluster in clusters:
        anchor_line_numbers = sorted(anchor.line_number for anchor in cluster.anchors)
        anchor_start = anchor_line_numbers[0]
        anchor_end = anchor_line_numbers[-1]
        step_id = cluster.step_id

        start_line = _expand_backward(anchor_start, step_id, line_map)
        end_line = _expand_forward(anchor_end, step_id, max_line_number, line_map)

        if _has_stack_trace(start_line, end_line, line_map):
            end_line = _extend_stack_trace(end_line, step_id, max_line_number, line_map)

        block_lines = [line_map[number] for number in range(start_line, end_line + 1)]
        blocks.append(
            LogBlock(
                start_line=start_line,
                end_line=end_line,
                lines=block_lines,
                anchors=list(cluster.anchors),
            )
        )

    return blocks


def _expand_backward(anchor_start: int, step_id: str | None, line_map: dict[int, ParsedLine]) -> int:
    start_line = anchor_start

    for _ in range(20):
        candidate = start_line - 1
        if candidate < 1:
            break
        if line_map[candidate].step_id != step_id:
            break
        start_line = candidate

    return start_line


def _expand_forward(
    anchor_end: int,
    step_id: str | None,
    max_line_number: int,
    line_map: dict[int, ParsedLine],
) -> int:
    end_line = anchor_end

    for _ in range(20):
        candidate = end_line + 1
        if candidate > max_line_number:
            break
        if line_map[candidate].step_id != step_id:
            break
        end_line = candidate

    return end_line


def _has_stack_trace(start_line: int, end_line: int, line_map: dict[int, ParsedLine]) -> bool:
    for line_number in range(start_line, end_line + 1):
        if "Traceback (most recent call last):" in line_map[line_number].content:
            return True
    return False


def _extend_stack_trace(
    end_line: int,
    step_id: str | None,
    max_line_number: int,
    line_map: dict[int, ParsedLine],
) -> int:
    extended_end = end_line

    while extended_end < max_line_number:
        candidate = extended_end + 1
        candidate_line = line_map[candidate]
        if candidate_line.step_id != step_id:
            break
        if not _is_stack_trace_line(candidate_line.content):
            break
        extended_end = candidate

    return extended_end


def _is_stack_trace_line(content: str) -> bool:
    return (
        content.startswith("  File ")
        or content.startswith("    ")
        or bool(_STACK_TRACE_TERMINAL.search(content))
    )


__all__ = ["expand_context"]
