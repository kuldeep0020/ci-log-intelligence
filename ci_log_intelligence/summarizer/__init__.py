from __future__ import annotations

from collections import Counter

from ..models import ReductionResult


def summarize_reduction_result(result: ReductionResult) -> str:
    if not result.blocks:
        return "No high-signal failure blocks detected."

    classifications = Counter(block.classification for block in result.blocks)
    top_block = result.blocks[0]
    counts = ", ".join(
        f"{name}={classifications[name]}"
        for name in sorted(classifications)
    )
    return (
        f"Identified {len(result.blocks)} failure blocks ({counts}). "
        f"Top block spans lines {top_block.block.start_line}-{top_block.block.end_line} "
        f"with classification {top_block.classification} and score {top_block.score:.2f}."
    )


__all__ = ["summarize_reduction_result"]
