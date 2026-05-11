from __future__ import annotations

from typing import Iterable, List, Optional, Sequence

from ...models import ScoredBlock
from ..detectors.base import DetectedFailure

_ROOT_CAUSE_SIGNALS = {"traceback", "exception", "error", "assertion_error"}


def classify_blocks(
    blocks: Iterable[ScoredBlock],
    detected_failures: Optional[Sequence[DetectedFailure]] = None,
) -> List[ScoredBlock]:
    """Assign each block one of ``root_cause`` / ``symptom`` / ``flaky``.

    Resolution order:
        1. If any contributing ``DetectedFailure`` (anchor falls inside the
           block) carries a ``classification_claim``, use the claim from the
           highest-severity contributor (ties broken by earliest anchor line).
           Detectors that know what they detect (hash-mismatch, panics,
           assertion failures) override the signal heuristic.
        2. Otherwise fall back to the existing signal heuristic:
           ``retrying``-only -> ``flaky``; any root-cause signal present
           -> ``root_cause``; else ``symptom``.
    """
    failures_list = list(detected_failures or [])
    classified: list[ScoredBlock] = []

    for scored_block in blocks:
        claim = _classification_claim_for(scored_block, failures_list)
        if claim is not None:
            classification = claim
        else:
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


def _classification_claim_for(
    scored_block: ScoredBlock,
    detected_failures: Sequence[DetectedFailure],
) -> Optional[str]:
    block_line_range = range(
        scored_block.block.start_line, scored_block.block.end_line + 1
    )
    claimants = [
        failure
        for failure in detected_failures
        if failure.classification_claim is not None
        and any(line in block_line_range for line in failure.anchor_lines)
    ]
    if not claimants:
        return None
    primary = min(
        claimants,
        key=lambda failure: (-failure.severity, min(failure.anchor_lines, default=0)),
    )
    return primary.classification_claim


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
