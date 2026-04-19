from __future__ import annotations

import unittest

from ci_log_intelligence.ingestion.github.models import FailedLogAnalysis, NormalizedLog, PassedContextExcerpt
from ci_log_intelligence.models import Anchor, LogBlock, ReductionResult, ScoredBlock
from ci_log_intelligence.parsing import ParsedLine
from ci_log_intelligence.reducer.comparison.analyzer import analyze_cross_run


class ComparisonAnalyzerTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
