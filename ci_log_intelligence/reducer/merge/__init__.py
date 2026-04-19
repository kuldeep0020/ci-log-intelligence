from __future__ import annotations

from typing import Iterable, List

from ...models import Anchor, LogBlock, ParsedLine


def merge_blocks(blocks: Iterable[LogBlock]) -> List[LogBlock]:
    ordered_blocks = sorted(blocks, key=lambda block: (block.start_line, block.end_line))
    if not ordered_blocks:
        return []

    merged: list[LogBlock] = [ordered_blocks[0]]

    for block in ordered_blocks[1:]:
        current = merged[-1]
        current_step = _block_step_id(current)
        next_step = _block_step_id(block)
        overlaps = block.start_line <= current.end_line
        gap = block.start_line - current.end_line

        if overlaps or (gap < 10 and current_step == next_step):
            merged[-1] = _merge_pair(current, block)
            continue

        merged.append(block)

    return merged


def _merge_pair(left: LogBlock, right: LogBlock) -> LogBlock:
    line_map: dict[int, ParsedLine] = {
        line.line_number: line for line in [*left.lines, *right.lines]
    }
    anchor_map: dict[tuple[int, str, int], Anchor] = {
        (anchor.line_number, anchor.type, anchor.severity): anchor
        for anchor in [*left.anchors, *right.anchors]
    }
    merged_lines = [line_map[number] for number in sorted(line_map)]
    merged_anchors = [
        anchor_map[key]
        for key in sorted(anchor_map, key=lambda item: (item[0], -item[2], item[1]))
    ]

    return LogBlock(
        start_line=merged_lines[0].line_number,
        end_line=merged_lines[-1].line_number,
        lines=merged_lines,
        anchors=merged_anchors,
    )


def _block_step_id(block: LogBlock) -> str | None:
    if not block.lines:
        return None
    return block.lines[0].step_id


__all__ = ["merge_blocks"]
