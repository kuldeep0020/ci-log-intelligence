from __future__ import annotations

from typing import Iterable, List

from ...models import ScoredBlock

_ROOT_CAUSE_SIGNALS = {"traceback", "exception", "error", "assertion_error"}


def classify_blocks(blocks: Iterable[ScoredBlock]) -> List[ScoredBlock]:
    classified: list[ScoredBlock] = []

    for scored_block in blocks:
        block_signals = {signal for line in scored_block.block.lines for signal in line.signals}
        if "retrying" in block_signals and not (block_signals & _ROOT_CAUSE_SIGNALS):
            classification = "flaky"
        elif block_signals & _ROOT_CAUSE_SIGNALS:
            classification = "root_cause"
        else:
            classification = "symptom"

        classified.append(
            ScoredBlock(
                block=scored_block.block,
                score=scored_block.score,
                classification=classification,
                score_components=scored_block.score_components,
            )
        )

    return classified


def rank_blocks(blocks: Iterable[ScoredBlock]) -> List[ScoredBlock]:
    classification_priority = {"root_cause": 0, "symptom": 1, "flaky": 2, "unclassified": 3}
    # ``start_line`` is the earliest-first tiebreak; this replaces the former
    # recency-based scoring boost. Cascading failures now correctly rank
    # the earliest equivalent-score block first.
    return sorted(
        blocks,
        key=lambda scored_block: (
            -scored_block.score,
            classification_priority.get(scored_block.classification, 99),
            scored_block.block.start_line,
            scored_block.block.end_line,
        ),
    )


__all__ = ["classify_blocks", "rank_blocks"]
