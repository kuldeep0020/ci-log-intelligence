from __future__ import annotations

import unittest

from ci_log_intelligence import analyze_log
from ci_log_intelligence.ci_analysis import _build_report
from ci_log_intelligence.ingestion import ingest_log
from ci_log_intelligence.ingestion.github.models import (
    FailedLogAnalysis,
    NormalizedLog,
)
from ci_log_intelligence.parsing import parse_log
from ci_log_intelligence.reducer import reduce_parsed_lines
from ci_log_intelligence.reducer.detectors import JobContext
from ci_log_intelligence.storage import InMemoryStorage

# Reproduces the WHT (Warehouse Transformations) Go integration-test pattern:
# a ``file hashes don't match`` line followed a few lines later by a Go test
# runner ``--- FAIL: TestName`` marker, both inside the same step.
HASH_MISMATCH_LOG = """\
STEP: integration-test
2024-01-15T12:34:56.789Z setting up postgres connection
2024-01-15T12:34:57.100Z connection ready
2024-01-15T12:34:58.500Z common.go:1058: file hashes don't match for /tmp/test/Material_X.yaml and ../samples/test_output/Material_X_HASH_1.yaml
2024-01-15T12:34:58.600Z some intermediate output
2024-01-15T12:34:58.700Z more intermediate output
2024-01-15T12:34:58.800Z --- FAIL: TestRunSetPartialFeatureTable (45.3s)
2024-01-15T12:34:58.900Z FAIL    github.com/owner/repo/integration  45.3s
"""


def _parse(content: str):
    backend = InMemoryStorage()
    stored = ingest_log(content, backend)
    return parse_log(stored, backend)


class HashMismatchAnalyzeLogTests(unittest.TestCase):
    """``analyze_log`` is the raw-string entry point. No JobContext => no warehouse_target."""

    def test_analyze_log_detects_hash_mismatch_and_extracts_test_name(self) -> None:
        result = analyze_log(HASH_MISMATCH_LOG)

        hash_failures = [
            failure
            for failure in result.detected_failures
            if failure.type == "hash_mismatch"
        ]
        self.assertEqual(len(hash_failures), 1, "expected one hash_mismatch detection")
        failure = hash_failures[0]
        # Mismatch line (4) and FAIL line (7) are both anchored.
        self.assertEqual(sorted(failure.anchor_lines), [4, 7])
        self.assertEqual(
            failure.extracted_fields["test_name"], "TestRunSetPartialFeatureTable"
        )
        # No job context provided at the top-level => no warehouse inference.
        self.assertNotIn("warehouse_target", failure.extracted_fields)
        self.assertEqual(failure.classification_claim, "root_cause")


class HashMismatchWithJobContextTests(unittest.TestCase):
    def test_reduce_parsed_lines_with_postgres_job_infers_warehouse(self) -> None:
        parsed = _parse(HASH_MISMATCH_LOG)
        job_context = JobContext(
            job_name="postgres-test (bundling)", run_id=1, repo="rl/wht"
        )

        result = reduce_parsed_lines(parsed, job_context=job_context)

        hash_failures = [
            failure
            for failure in result.detected_failures
            if failure.type == "hash_mismatch"
        ]
        self.assertEqual(len(hash_failures), 1)
        fields = hash_failures[0].extracted_fields
        self.assertEqual(fields["warehouse_target"], "postgres")
        self.assertEqual(fields["job_name"], "postgres-test (bundling)")
        self.assertEqual(fields["test_name"], "TestRunSetPartialFeatureTable")


class HashMismatchEndToEndReportTests(unittest.TestCase):
    """Build a ``CIAnalysisReport`` from a minimal ``FailedLogAnalysis`` and assert
    the typed ``FailureRecord`` surfaces the hash_mismatch type and fields."""

    def test_report_failure_record_carries_hash_mismatch_type_and_test_name(self) -> None:
        parsed = _parse(HASH_MISMATCH_LOG)
        job_context = JobContext(
            job_name="postgres-test (bundling)", run_id=42, repo="rl/wht"
        )
        result = reduce_parsed_lines(parsed, job_context=job_context)

        analysis = FailedLogAnalysis(
            log=NormalizedLog(
                run_id=42,
                job_id=4200,
                job_name="postgres-test (bundling)",
                status="failed",
                content=HASH_MISMATCH_LOG,
            ),
            logical_job_name="postgres-test",
            result=result,
        )

        report = _build_report(
            runs=[],
            failed_logs=[analysis.log],
            passed_logs=[],
            failed_analyses=[analysis],
            passed_contexts=[],
            insights=[],
        )

        self.assertGreaterEqual(len(report.failures), 1)
        # The hash_mismatch FailureRecord should be present.
        hash_records = [r for r in report.failures if r.type == "hash_mismatch"]
        self.assertEqual(len(hash_records), 1)
        record = hash_records[0]
        self.assertEqual(record.classification, "root_cause")
        self.assertEqual(
            record.extracted_fields["test_name"], "TestRunSetPartialFeatureTable"
        )
        self.assertEqual(record.extracted_fields["warehouse_target"], "postgres")
        self.assertEqual(record.extracted_fields["job_name"], "postgres-test (bundling)")


if __name__ == "__main__":
    unittest.main()
