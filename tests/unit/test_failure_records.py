from __future__ import annotations

import unittest

from ci_log_intelligence.ci_analysis import _build_report, _resolve_failure_type
from ci_log_intelligence.ingestion.github.models import (
    CIAnalysisReport,
    FailedLogAnalysis,
    FailureRecord,
    NormalizedLog,
)
from ci_log_intelligence.models import (
    Anchor,
    LogBlock,
    ParsedLine,
    ReductionResult,
    ScoreComponents,
    ScoredBlock,
)
from ci_log_intelligence.reducer.detectors import DetectedFailure


def _trivial_components() -> ScoreComponents:
    return ScoreComponents(severity_weight=0.0, signal_density=0.0, duplicate_penalty=0.0)


def _scored_block(
    *, start: int, end: int, lines: list[ParsedLine], anchors: list[Anchor]
) -> ScoredBlock:
    return ScoredBlock(
        block=LogBlock(start_line=start, end_line=end, lines=lines, anchors=anchors),
        score=15.5,
        classification="root_cause",
        score_components=ScoreComponents(
            severity_weight=15.0, signal_density=0.5, duplicate_penalty=0.0
        ),
    )


def _make_analysis(
    *,
    run_id: int,
    job_name: str,
    block: ScoredBlock,
    detected_failures: list[DetectedFailure],
) -> FailedLogAnalysis:
    return FailedLogAnalysis(
        log=NormalizedLog(
            run_id=run_id,
            job_id=run_id * 10,
            job_name=job_name,
            status="failed",
            content="\n".join(line.content for line in block.block.lines),
        ),
        logical_job_name=job_name,
        result=ReductionResult(
            blocks=[block],
            summary=None,
            detected_failures=detected_failures,
        ),
    )


class ResolveFailureTypeTests(unittest.TestCase):
    def test_generic_only_block_returns_signal_names_list(self) -> None:
        block = _scored_block(
            start=1,
            end=2,
            lines=[
                ParsedLine(1, "ERROR x", None, "test", ["error"]),
                ParsedLine(2, "FAILED y", None, "test", ["failed"]),
            ],
            anchors=[Anchor(1, "error", 3), Anchor(2, "failed", 2)],
        )
        detected = [
            DetectedFailure(
                type="generic",
                anchor_lines=[1],
                severity=3,
                extracted_fields={"signal_name": "error"},
                anchor_type="error",
            ),
            DetectedFailure(
                type="generic",
                anchor_lines=[2],
                severity=2,
                extracted_fields={"signal_name": "failed"},
                anchor_type="failed",
            ),
        ]

        failure_type, extracted = _resolve_failure_type(block, detected)

        self.assertEqual(failure_type, "generic")
        self.assertEqual(extracted, {"signal_names": ["error", "failed"]})

    def test_multiple_signals_on_same_line_dedupe_signal_names(self) -> None:
        block = _scored_block(
            start=7,
            end=7,
            lines=[ParsedLine(7, "ERROR FAILED AssertionError", None, "test", ["error", "failed", "assertion_error"])],
            anchors=[Anchor(7, "error", 3), Anchor(7, "failed", 2)],
        )
        detected = [
            DetectedFailure(
                type="generic",
                anchor_lines=[7],
                severity=3,
                extracted_fields={"signal_name": "error"},
                anchor_type="error",
            ),
            DetectedFailure(
                type="generic",
                anchor_lines=[7],
                severity=2,
                extracted_fields={"signal_name": "failed"},
                anchor_type="failed",
            ),
            DetectedFailure(
                type="generic",
                anchor_lines=[7],
                severity=2,
                extracted_fields={"signal_name": "error"},  # duplicate
                anchor_type="error",
            ),
        ]

        failure_type, extracted = _resolve_failure_type(block, detected)

        self.assertEqual(failure_type, "generic")
        # ``error`` appears twice in the detections but only once in the result.
        self.assertEqual(extracted, {"signal_names": ["error", "failed"]})

    def test_no_contributing_failures_returns_empty_generic_payload(self) -> None:
        block = _scored_block(
            start=50,
            end=51,
            lines=[ParsedLine(50, "no anchor here", None, "test", [])],
            anchors=[],
        )
        # Detected failures land outside the block's line range.
        detected = [
            DetectedFailure(
                type="generic",
                anchor_lines=[1],
                severity=3,
                extracted_fields={"signal_name": "error"},
                anchor_type="error",
            )
        ]

        failure_type, extracted = _resolve_failure_type(block, detected)

        self.assertEqual(failure_type, "generic")
        self.assertEqual(extracted, {})

    def test_specialized_type_overrides_generic_when_both_contribute(self) -> None:
        # Forward-compatibility: when step 4 introduces a non-generic detector,
        # the resolver must prefer its type over ``generic``.
        block = _scored_block(
            start=10,
            end=12,
            lines=[
                ParsedLine(10, "ERROR x", None, "test", ["error"]),
                ParsedLine(11, "Hash mismatch detected", None, "test", []),
                ParsedLine(12, "trailing", None, "test", []),
            ],
            anchors=[Anchor(10, "error", 3), Anchor(11, "hash_mismatch", 3)],
        )
        detected = [
            DetectedFailure(
                type="generic",
                anchor_lines=[10],
                severity=3,
                extracted_fields={"signal_name": "error"},
                anchor_type="error",
            ),
            DetectedFailure(
                type="hash_mismatch",
                anchor_lines=[11],
                severity=3,
                extracted_fields={"warehouse_target": "snowflake"},
            ),
        ]

        failure_type, extracted = _resolve_failure_type(block, detected)

        self.assertEqual(failure_type, "hash_mismatch")
        self.assertEqual(extracted, {"warehouse_target": "snowflake"})


class FailureRecordShapeTests(unittest.TestCase):
    def test_failure_record_carries_block_line_range(self) -> None:
        block = _scored_block(
            start=10,
            end=20,
            lines=[ParsedLine(10, "ERROR x", None, "test", ["error"])],
            anchors=[Anchor(10, "error", 3)],
        )
        analysis = _make_analysis(
            run_id=1,
            job_name="job-a",
            block=block,
            detected_failures=[
                DetectedFailure(
                    type="generic",
                    anchor_lines=[10],
                    severity=3,
                    extracted_fields={"signal_name": "error"},
                    anchor_type="error",
                )
            ],
        )

        report = _build_report(
            runs=[],
            failed_logs=[analysis.log],
            passed_logs=[],
            failed_analyses=[analysis],
            passed_contexts=[],
            insights=[],
        )

        self.assertEqual(len(report.failures), 1)
        record = report.failures[0]
        self.assertIsInstance(record, FailureRecord)
        self.assertEqual(record.start_line, 10)
        self.assertEqual(record.end_line, 20)
        self.assertEqual(record.classification, "root_cause")
        self.assertEqual(record.severity, 3)
        self.assertEqual(record.type, "generic")
        self.assertEqual(record.extracted_fields, {"signal_names": ["error"]})

    def test_failure_record_to_dict_emits_expected_keys(self) -> None:
        record = FailureRecord(
            type="generic",
            classification="root_cause",
            severity=3,
            score=15.5,
            start_line=1,
            end_line=2,
            summary="Run 1 job job-a root_cause at lines 1-2: ERROR x",
            log_excerpt="ERROR x",
            extracted_fields={"signal_names": ["error"]},
        )

        payload = record.to_dict()

        self.assertEqual(
            set(payload),
            {
                "type",
                "classification",
                "severity",
                "score",
                "start_line",
                "end_line",
                "summary",
                "log_excerpt",
                "extracted_fields",
            },
        )
        self.assertEqual(payload["extracted_fields"], {"signal_names": ["error"]})


class CIAnalysisReportSerializationTests(unittest.TestCase):
    def test_report_to_dict_emits_failures_under_new_key(self) -> None:
        block = _scored_block(
            start=1,
            end=1,
            lines=[ParsedLine(1, "ERROR x", None, "test", ["error"])],
            anchors=[Anchor(1, "error", 3)],
        )
        analysis = _make_analysis(
            run_id=1,
            job_name="job-a",
            block=block,
            detected_failures=[
                DetectedFailure(
                    type="generic",
                    anchor_lines=[1],
                    severity=3,
                    extracted_fields={"signal_name": "error"},
                    anchor_type="error",
                )
            ],
        )

        report = _build_report(
            runs=[],
            failed_logs=[analysis.log],
            passed_logs=[],
            failed_analyses=[analysis],
            passed_contexts=[],
            insights=[],
        )
        payload = report.to_dict()

        self.assertIn("failures", payload)
        # Old schema key must be absent under the new contract.
        legacy_key = "failed" + "_" + "blocks"
        self.assertNotIn(legacy_key, payload)
        self.assertEqual(len(payload["failures"]), 1)
        self.assertIsInstance(report, CIAnalysisReport)


if __name__ == "__main__":
    unittest.main()
