from __future__ import annotations

from typing import Iterable, List

from ...models import LogBlock, ScoredBlock


def score_blocks(blocks: Iterable[LogBlock], total_lines: int) -> List[ScoredBlock]:
    scored_blocks: list[ScoredBlock] = []

    for block in blocks:
        highest_anchor_severity = max((anchor.severity for anchor in block.anchors), default=0)
        signal_density = _signal_density(block)
        recency_weight = block.end_line / max(total_lines, 1)
        duplicate_penalty = _duplicate_penalty(block)
        score = round(
            (highest_anchor_severity * 5.0)
            + signal_density
            + recency_weight
            - duplicate_penalty,
            6,
        )
        scored_blocks.append(
            ScoredBlock(
                block=block,
                score=score,
                classification="unclassified",
            )
        )

    return scored_blocks


def _signal_density(block: LogBlock) -> float:
    if not block.lines:
        return 0.0
    signal_count = sum(len(line.signals) for line in block.lines)
    return signal_count / len(block.lines)


def _duplicate_penalty(block: LogBlock) -> float:
    if not block.lines:
        return 0.0

    seen: dict[str, int] = {}
    duplicates = 0
    for line in block.lines:
        normalized = line.content.strip()
        previous_count = seen.get(normalized, 0)
        if previous_count:
            duplicates += 1
        seen[normalized] = previous_count + 1

    return duplicates / len(block.lines)


__all__ = ["score_blocks"]
