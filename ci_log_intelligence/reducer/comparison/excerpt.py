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

    Anchor-preservation invariant: for every window in the merged set, the
    anchor line(s) that produced the window are always emitted unless
    ``max_lines == 0``. When the remaining budget is smaller than the window,
    lines closest to the (first) anchor are emitted symmetrically so the
    anchor itself is never silently dropped.
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
) -> list[tuple[int, int, list[int]]]:
    merged: list[tuple[int, int, list[int]]] = []
    for line_number in anchor_line_numbers:
        window_start = max(block_start, line_number - context_around_anchor)
        window_end = min(block_end, line_number + context_around_anchor)
        if merged and window_start <= merged[-1][1] + 1:
            previous_start, previous_end, previous_anchors = merged[-1]
            merged[-1] = (
                previous_start,
                max(previous_end, window_end),
                previous_anchors + [line_number],
            )
        else:
            merged.append((window_start, window_end, [line_number]))
    return merged


def _emit_windows(
    merged_windows: list[tuple[int, int, list[int]]],
    line_map: dict[int, ParsedLine],
    max_lines: int,
) -> str:
    if max_lines <= 0 or not merged_windows:
        return ""

    per_window_budgets = _allocate_window_budgets(merged_windows, max_lines)

    output: list[str] = []
    previous_window_end: Optional[int] = None
    for (window_start, window_end, anchor_lines), budget in zip(
        merged_windows, per_window_budgets
    ):
        if budget <= 0:
            continue
        if previous_window_end is not None and window_start > previous_window_end + 1:
            output.append("...")

        emit_range = _select_emission_range(
            window_start, window_end, anchor_lines[0], budget
        )
        for ln in emit_range:
            if ln in line_map:
                output.append(line_map[ln].content)

        previous_window_end = window_end

    return "\n".join(output)


def _allocate_window_budgets(
    merged_windows: list[tuple[int, int, list[int]]],
    max_lines: int,
) -> list[int]:
    """Distribute ``max_lines`` across merged windows.

    Each window first receives at least one line (its anchor) so the
    anchor-preservation invariant holds for every window. The remaining
    budget is then distributed proportionally to window size and capped at
    each window's natural size.
    """
    window_count = len(merged_windows)
    if max_lines <= 0 or window_count == 0:
        return [0] * window_count

    # Guarantee each window at least one line (the anchor), constrained by
    # the overall cap.
    base_allocation = min(1, max_lines)
    budgets = [base_allocation] * window_count
    # If the cap is smaller than the number of windows, give the first ``max_lines``
    # windows one line each and zero out the rest.
    if max_lines < window_count:
        for index in range(window_count):
            budgets[index] = 1 if index < max_lines else 0
        return budgets

    remaining = max_lines - window_count
    window_sizes = [end - start + 1 for start, end, _ in merged_windows]
    # Top up each window proportionally to its natural size, capped at its
    # actual size. Iterate until ``remaining`` is exhausted or no window can
    # absorb more.
    while remaining > 0:
        absorbed_this_pass = 0
        # Allocate one line at a time, round-robin by largest unfilled window
        # first, to keep the distribution stable and easy to reason about.
        unfilled_indices = [
            index
            for index in range(window_count)
            if budgets[index] < window_sizes[index]
        ]
        if not unfilled_indices:
            break
        unfilled_indices.sort(
            key=lambda index: (-(window_sizes[index] - budgets[index]), index)
        )
        for index in unfilled_indices:
            if remaining <= 0:
                break
            budgets[index] += 1
            remaining -= 1
            absorbed_this_pass += 1
        if absorbed_this_pass == 0:
            break

    return budgets


def _select_emission_range(
    window_start: int,
    window_end: int,
    anchor_line: int,
    budget: int,
) -> range:
    """Pick ``budget`` consecutive line numbers from the window, centred on
    the anchor so the anchor line is always inside the emission range.
    """
    window_size = window_end - window_start + 1
    if budget >= window_size:
        return range(window_start, window_end + 1)
    if budget <= 1:
        return range(anchor_line, anchor_line + 1)

    half = (budget - 1) // 2
    centred_start = anchor_line - half
    centred_end = centred_start + budget - 1
    if centred_start < window_start:
        shift = window_start - centred_start
        centred_start += shift
        centred_end += shift
    if centred_end > window_end:
        shift = centred_end - window_end
        centred_start -= shift
        centred_end -= shift
    centred_start = max(centred_start, window_start)
    centred_end = min(centred_end, window_end)
    return range(centred_start, centred_end + 1)


__all__ = ["render_block_excerpt"]
