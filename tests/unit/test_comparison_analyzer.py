from __future__ import annotations

import unittest

from ci_log_intelligence.ingestion.github.models import (
    FailedLogAnalysis,
    NormalizedLog,
    PassedContextExcerpt,
)
from ci_log_intelligence.models import Anchor, LogBlock, ParsedLine, ReductionResult, ScoredBlock
from ci_log_intelligence.reducer.comparison.analyzer import (
    analyze_cross_run,
    select_root_cause,
)


def _make_analysis(
    *,
    run_id: int,
    job_name: str,
    block_lines: list[ParsedLine],
    anchors: list[Anchor],
    score: float = 12.0,
    classification: str = "root_cause",
) -> FailedLogAnalysis:
    return FailedLogAnalysis(
        log=NormalizedLog(
            run_id=run_id,
            job_id=run_id * 10,
            job_name=job_name,
            status="failed",
            content="\n".join(line.content for line in block_lines),
        ),
        logical_job_name=job_name.split("-")[0],
        result=ReductionResult(
            blocks=[
                ScoredBlock(
                    block=LogBlock(
                        start_line=block_lines[0].line_number,
                        end_line=block_lines[-1].line_number,
                        lines=block_lines,
                        anchors=anchors,
                    ),
                    score=score,
                    classification=classification,
                )
            ],
            summary=None,
        ),
    )


class ComparisonAnalyzerCrossRunTests(unittest.TestCase):
    def test_emits_environment_and_step_insights(self) -> None:
        failed_analysis = FailedLogAnalysis(
            log=NormalizedLog(
                run_id=10,
                job_id=100,
                job_name="test-snowflake",
                status="failed",
                content="STEP: execute\nERROR query mismatch",
            ),
            logical_job_name="test",
            result=ReductionResult(
                blocks=[
                    ScoredBlock(
                        block=LogBlock(
                            start_line=1,
                            end_line=2,
                            lines=[
                                ParsedLine(1, "STEP: execute", None, "execute", []),
                                ParsedLine(2, "ERROR query mismatch", None, "execute", ["error"]),
                            ],
                            anchors=[Anchor(2, "error", 3)],
                        ),
                        score=12.0,
                        classification="root_cause",
                    )
                ],
                summary=None,
            ),
        )
        passed_context = PassedContextExcerpt(
            run_id=9,
            job_id=200,
            job_name="test-redshift",
            logical_job_name="test",
            excerpt="STEP: setup\nok\nSTEP: execute\nquery result stable",
        )

        insights = analyze_cross_run([failed_analysis], [passed_context])

        self.assertIn("Failure occurs only in variant snowflake for job group test.", insights)
        self.assertIn(
            "Step setup is present in passed runs but missing in failing run for job group test.",
            insights,
        )


class SelectRootCauseTiebreakTests(unittest.TestCase):
    def test_traceback_bearing_block_wins_over_bare_error(self) -> None:
        bare_error = _make_analysis(
            run_id=10,
            job_name="job-a",
            block_lines=[ParsedLine(1, "ERROR bad thing", None, "test", ["error"])],
            anchors=[Anchor(1, "error", 3)],
        )
        with_traceback = _make_analysis(
            run_id=10,
            job_name="job-b",
            block_lines=[
                ParsedLine(5, "Traceback (most recent call last):", None, "test", ["traceback"]),
                ParsedLine(6, "  File 'foo.py', line 1", None, "test", []),
            ],
            anchors=[Anchor(5, "traceback", 3)],
        )

        choice = select_root_cause([bare_error, with_traceback])

        self.assertIsNotNone(choice)
        analysis, _ = choice
        self.assertEqual(analysis.log.job_name, "job-b")

    def test_deeper_stack_wins_when_both_have_traceback(self) -> None:
        shallow = _make_analysis(
            run_id=10,
            job_name="job-a",
            block_lines=[
                ParsedLine(1, "Traceback (most recent call last):", None, "test", ["traceback"]),
                ParsedLine(2, "  File 'a.py', line 1", None, "test", []),
            ],
            anchors=[Anchor(1, "traceback", 3)],
        )
        deeper = _make_analysis(
            run_id=10,
            job_name="job-b",
            block_lines=[
                ParsedLine(1, "Traceback (most recent call last):", None, "test", ["traceback"]),
                ParsedLine(2, "  File 'a.py', line 1", None, "test", []),
                ParsedLine(3, "  File 'b.py', line 2", None, "test", []),
                ParsedLine(4, "  File 'c.py', line 3", None, "test", []),
            ],
            anchors=[Anchor(1, "traceback", 3)],
        )

        choice = select_root_cause([shallow, deeper])

        analysis, _ = choice
        self.assertEqual(analysis.log.job_name, "job-b")

    def test_earliest_position_wins_at_tied_traceback_depth(self) -> None:
        early = _make_analysis(
            run_id=10,
            job_name="job-a",
            block_lines=[
                ParsedLine(5, "Traceback (most recent call last):", None, "test", ["traceback"]),
                ParsedLine(6, "  File 'a.py', line 1", None, "test", []),
            ],
            anchors=[Anchor(5, "traceback", 3)],
        )
        late = _make_analysis(
            run_id=10,
            job_name="job-b",
            block_lines=[
                ParsedLine(100, "Traceback (most recent call last):", None, "test", ["traceback"]),
                ParsedLine(101, "  File 'a.py', line 1", None, "test", []),
            ],
            anchors=[Anchor(100, "traceback", 3)],
        )

        choice = select_root_cause([late, early])

        analysis, _ = choice
        self.assertEqual(analysis.log.job_name, "job-a")


class SelectRootCauseEmptyTests(unittest.TestCase):
    def test_returns_none_when_no_candidates(self) -> None:
        self.assertIsNone(select_root_cause([]))


if __name__ == "__main__":
    unittest.main()
