from __future__ import annotations

from typing import Optional

from ...models import ParsedLine, ScoredBlock


def render_block_excerpt(
    scored_block: ScoredBlock,
    *,
    max_lines: int = 20,
    context_around_anchor: int = 5,
) -> str:
    """Render an anchor-centric excerpt: +/- N lines around each anchor with
    overlapping windows merged and non-adjacent windows separated by ``...``.
    Falls back to head-N truncation when no anchors are present.
    """
    if not scored_block.block.lines:
        return ""

    line_map = {line.line_number: line for line in scored_block.block.lines}
    anchor_line_numbers = sorted({a.line_number for a in scored_block.block.anchors})
    if not anchor_line_numbers:
        head = [line.content for line in scored_block.block.lines[:max_lines]]
        return "\n".join(head)

    merged_windows = _merged_anchor_windows(
        anchor_line_numbers,
        scored_block.block.start_line,
        scored_block.block.end_line,
        context_around_anchor,
    )
    return _emit_windows(merged_windows, line_map, max_lines)


def _merged_anchor_windows(
    anchor_line_numbers: list[int],
    block_start: int,
    block_end: int,
    context_around_anchor: int,
) -> list[tuple[int, int]]:
    merged: list[tuple[int, int]] = []
    for line_number in anchor_line_numbers:
        window_start = max(block_start, line_number - context_around_anchor)
        window_end = min(block_end, line_number + context_around_anchor)
        if merged and window_start <= merged[-1][1] + 1:
            merged[-1] = (merged[-1][0], max(merged[-1][1], window_end))
        else:
            merged.append((window_start, window_end))
    return merged


def _emit_windows(
    merged_windows: list[tuple[int, int]],
    line_map: dict[int, ParsedLine],
    max_lines: int,
) -> str:
    output: list[str] = []
    lines_emitted = 0
    previous_window_end: Optional[int] = None
    for window_start, window_end in merged_windows:
        if previous_window_end is not None and window_start > previous_window_end + 1:
            output.append("...")
        for ln in range(window_start, window_end + 1):
            if lines_emitted >= max_lines:
                break
            if ln in line_map:
                output.append(line_map[ln].content)
                lines_emitted += 1
        previous_window_end = window_end
        if lines_emitted >= max_lines:
            break
    return "\n".join(output)


__all__ = ["render_block_excerpt"]
