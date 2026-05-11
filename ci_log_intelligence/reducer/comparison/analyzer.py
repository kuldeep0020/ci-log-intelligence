from __future__ import annotations

import re
from collections import defaultdict
from typing import Iterable, Optional

from ...ingestion import ingest_log
from ...ingestion.github.fetcher import normalize_job_name
from ...ingestion.github.models import FailedLogAnalysis, PassedContextExcerpt
from ...models import ParsedLine, ScoredBlock
from ...parsing import parse_log
from ...storage import InMemoryStorage
from .excerpt import render_block_excerpt

_TEST_NAME_PATTERNS = [
    re.compile(r"\b[\w./-]+::[\w.\[\]-]+\b"),
    re.compile(r"\btest[\w./:-]+\b", re.IGNORECASE),
]
_QUERY_TOKENS = ("query", "result", "expected", "actual", "assert")


def extract_passed_context(
    failed_analyses: Iterable[FailedLogAnalysis],
    passed_logs,
) -> list[PassedContextExcerpt]:
    failed_list = list(failed_analyses)
    criteria_by_job = _build_failed_criteria(failed_list)
    excerpts: list[PassedContextExcerpt] = []

    for log in passed_logs:
        logical_name = normalize_job_name(log.job_name)
        criteria = criteria_by_job.get(logical_name)
        if criteria is None:
            continue

        parsed_lines = _parse_content(log.content)
        selected_line_numbers = _select_relevant_lines(parsed_lines, criteria)
        if not selected_line_numbers:
            continue

        excerpt = _build_excerpt(parsed_lines, selected_line_numbers, max_excerpt_lines=40)
        if not excerpt:
            continue

        excerpts.append(
            PassedContextExcerpt(
                run_id=log.run_id,
                job_id=log.job_id,
                job_name=log.job_name,
                logical_job_name=logical_name,
                excerpt=excerpt,
            )
        )

    return sorted(
        excerpts,
        key=lambda item: (item.logical_job_name, -item.run_id, item.job_name.lower(), item.job_id),
    )


def analyze_cross_run(
    failed_analyses: Iterable[FailedLogAnalysis],
    passed_contexts: Iterable[PassedContextExcerpt],
) -> list[str]:
    failed_list = list(failed_analyses)
    passed_list = list(passed_contexts)
    insights: list[str] = []

    failed_by_job = _group_failed_analyses(failed_list)
    passed_by_job = _group_passed_contexts(passed_list)

    for logical_name in sorted(failed_by_job):
        failed_group = failed_by_job[logical_name]
        passed_group = passed_by_job.get(logical_name, [])

        failed_job_names = {item.log.job_name for item in failed_group}
        passed_job_names = {item.job_name for item in passed_group}
        failed_variants = {_job_variant(name, logical_name) for name in failed_job_names}
        passed_variants = {_job_variant(name, logical_name) for name in passed_job_names}
        failed_only_variants = sorted(variant for variant in failed_variants - passed_variants if variant)
        if failed_only_variants:
            variant_text = ", ".join(failed_only_variants)
            label = "variant" if len(failed_only_variants) == 1 else "variants"
            insights.append(
                f"Failure occurs only in {label} {variant_text} for job group {logical_name}."
            )

        failed_step_ids = {
            line.step_id
            for analysis in failed_group
            for block in analysis.result.blocks
            for line in block.block.lines
            if line.step_id
        }
        passed_step_ids = {
            line.step_id
            for context in passed_group
            for line in _parse_content(context.excerpt)
            if line.step_id
        }
        missing_steps = sorted(step_id for step_id in passed_step_ids - failed_step_ids if step_id)
        if missing_steps:
            insights.append(
                f"Step {missing_steps[0]} is present in passed runs but missing in failing run for job group {logical_name}."
            )

        failed_tests = {
            test_name
            for analysis in failed_group
            for block in analysis.result.blocks
            for line in block.block.lines
            for test_name in _extract_test_names(line.content)
        }
        passed_tests = {
            test_name
            for context in passed_group
            for line in _parse_content(context.excerpt)
            for test_name in _extract_test_names(line.content)
        }
        shared_tests = sorted(failed_tests & passed_tests)
        if shared_tests:
            insights.append(
                f"Test {shared_tests[0]} behaves differently between passed and failed runs for job group {logical_name}."
            )

        if _has_query_difference(failed_group, passed_group):
            insights.append(f"Query result differs between passed and failed runs for job group {logical_name}.")

    return insights


def select_root_cause(
    failed_analyses: Iterable[FailedLogAnalysis],
) -> Optional[tuple[FailedLogAnalysis, ScoredBlock]]:
    candidates: list[tuple[FailedLogAnalysis, ScoredBlock]] = []
    for analysis in failed_analyses:
        for block in analysis.result.blocks:
            candidates.append((analysis, block))

    if not candidates:
        return None

    classification_priority = {"root_cause": 0, "symptom": 1, "flaky": 2}
    # Tiebreaks after score and classification: traceback-bearing first, then
    # deeper stack, then earliest position, then job name, then newest run.
    return sorted(
        candidates,
        key=lambda item: (
            -item[1].score,
            classification_priority.get(item[1].classification, 99),
            not _has_traceback(item[1]),
            -_stack_depth(item[1]),
            item[1].block.start_line,
            item[0].log.job_name.lower(),
            -item[0].log.run_id,
        ),
    )[0]


def summarize_failed_block(scored_block: ScoredBlock, job_name: str, run_id: int) -> str:
    first_interesting_line = _first_interesting_line(scored_block)
    return (
        f"Run {run_id} job {job_name} {scored_block.classification} "
        f"at lines {scored_block.block.start_line}-{scored_block.block.end_line}: "
        f"{first_interesting_line}"
    )


def _block_signals(scored_block: ScoredBlock) -> set[str]:
    return {signal for line in scored_block.block.lines for signal in line.signals}


def _has_traceback(scored_block: ScoredBlock) -> bool:
    return "traceback" in _block_signals(scored_block)


def _stack_depth(scored_block: ScoredBlock) -> int:
    """Approximate Python traceback depth by counting ``  File `` frame lines.

    Python tracebacks emit one ``  File "<path>", line N, in <fn>`` line per
    frame. A previous heuristic also counted any 4-space-indented line, which
    over-matched on indented YAML, JSON, pytest -v output, and prose, and
    inflated ranking signals for blocks that contained no real frames. We
    therefore restrict the count to the canonical frame prefix. This may
    undercount the per-frame continuation line, but is 1:1 with frame *count*
    -- the ranking signal we actually want.
    """
    return sum(
        1
        for line in scored_block.block.lines
        if line.content.startswith("  File ")
    )


def _build_failed_criteria(failed_analyses: Iterable[FailedLogAnalysis]) -> dict[str, dict[str, set[str]]]:
    criteria_by_job: dict[str, dict[str, set[str]]] = defaultdict(
        lambda: {"step_ids": set(), "test_names": set(), "context_lines": set()}
    )
    for analysis in failed_analyses:
        criteria = criteria_by_job[analysis.logical_job_name]
        for scored_block in analysis.result.blocks:
            for line in scored_block.block.lines:
                if line.step_id:
                    criteria["step_ids"].add(line.step_id)
                for test_name in _extract_test_names(line.content):
                    criteria["test_names"].add(test_name.lower())
                if line.signals:
                    normalized = _normalize_line(line.content)
                    if len(normalized) >= 8:
                        criteria["context_lines"].add(normalized)
    return criteria_by_job


def _select_relevant_lines(
    parsed_lines: list[ParsedLine],
    criteria: dict[str, set[str]],
) -> set[int]:
    selected_line_numbers: set[int] = set()
    for line in parsed_lines:
        normalized_content = _normalize_line(line.content)
        line_test_names = {name.lower() for name in _extract_test_names(line.content)}
        if line.step_id and line.step_id in criteria["step_ids"]:
            selected_line_numbers.add(line.line_number)
            continue
        if line_test_names & criteria["test_names"]:
            selected_line_numbers.add(line.line_number)
            continue
        if normalized_content and normalized_content in criteria["context_lines"]:
            selected_line_numbers.add(line.line_number)

    return selected_line_numbers


def _build_excerpt(
    parsed_lines: list[ParsedLine],
    selected_line_numbers: set[int],
    *,
    max_excerpt_lines: int,
) -> str:
    if not selected_line_numbers:
        return ""

    line_map = {line.line_number: line for line in parsed_lines}
    expanded_numbers: set[int] = set()
    for line_number in sorted(selected_line_numbers):
        line = line_map[line_number]
        for candidate in range(max(1, line_number - 3), min(parsed_lines[-1].line_number, line_number + 3) + 1):
            candidate_line = line_map[candidate]
            if line.step_id is not None and candidate_line.step_id != line.step_id:
                continue
            expanded_numbers.add(candidate)

    selected_ordered = sorted(expanded_numbers)
    excerpt_lines: list[str] = []
    previous_number: Optional[int] = None
    lines_emitted = 0
    for line_number in selected_ordered:
        if lines_emitted >= max_excerpt_lines:
            break
        if previous_number is not None and line_number - previous_number > 1:
            excerpt_lines.append("...")
        excerpt_lines.append(line_map[line_number].content)
        previous_number = line_number
        lines_emitted += 1

    return "\n".join(excerpt_lines)


def _group_failed_analyses(
    failed_analyses: Iterable[FailedLogAnalysis],
) -> dict[str, list[FailedLogAnalysis]]:
    grouped: dict[str, list[FailedLogAnalysis]] = defaultdict(list)
    for analysis in failed_analyses:
        grouped[analysis.logical_job_name].append(analysis)
    return {key: grouped[key] for key in sorted(grouped)}


def _group_passed_contexts(
    passed_contexts: Iterable[PassedContextExcerpt],
) -> dict[str, list[PassedContextExcerpt]]:
    grouped: dict[str, list[PassedContextExcerpt]] = defaultdict(list)
    for context in passed_contexts:
        grouped[context.logical_job_name].append(context)
    return {key: grouped[key] for key in sorted(grouped)}


def _has_query_difference(
    failed_group: list[FailedLogAnalysis],
    passed_group: list[PassedContextExcerpt],
) -> bool:
    failed_lines = {
        _normalize_line(line.content)
        for analysis in failed_group
        for block in analysis.result.blocks
        for line in block.block.lines
        if _contains_query_signal(line.content)
    }
    passed_lines = {
        _normalize_line(line.content)
        for context in passed_group
        for line in _parse_content(context.excerpt)
        if _contains_query_signal(line.content)
    }
    return bool(failed_lines and passed_lines and failed_lines != passed_lines)


def _parse_content(content: str) -> list[ParsedLine]:
    backend = InMemoryStorage()
    stored_log = ingest_log(content, backend)
    try:
        return parse_log(stored_log, backend)
    finally:
        backend.delete(stored_log.reference)


def _first_interesting_line(scored_block: ScoredBlock) -> str:
    for line in scored_block.block.lines:
        if line.signals:
            return line.content.strip()
    for line in scored_block.block.lines:
        if line.content.strip():
            return line.content.strip()
    return ""


def _normalize_line(content: str) -> str:
    return " ".join(content.strip().lower().split())


def _extract_test_names(content: str) -> list[str]:
    matches: list[str] = []
    for pattern in _TEST_NAME_PATTERNS:
        matches.extend(match.group(0) for match in pattern.finditer(content))
    return matches


def _contains_query_signal(content: str) -> bool:
    normalized = _normalize_line(content)
    return any(token in normalized for token in _QUERY_TOKENS)


def _job_variant(job_name: str, logical_name: str) -> str:
    normalized_job_name = job_name.strip().lower()
    if normalized_job_name == logical_name:
        return ""
    prefix = f"{logical_name}-"
    if normalized_job_name.startswith(prefix):
        return normalized_job_name[len(prefix) :]
    return normalized_job_name
