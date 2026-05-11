"""End-to-end pipeline tests for the test-framework detectors.

Each test exercises the full reduce -> _build_report pipeline against a
realistic small fixture and asserts the resulting FailureRecord carries
the right type, classification, and extracted_fields.

These tests also encode the coordination contracts between detectors --
most importantly that ``HashMismatchDetector`` and ``GoTestFailDetector``
do not double-emit on the same ``--- FAIL:`` line.
"""

from __future__ import annotations

import unittest

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


def _parse(content: str):
    backend = InMemoryStorage()
    stored = ingest_log(content, backend)
    return parse_log(stored, backend)


def _build_single_report(
    content: str,
    *,
    job_name: str = "ci-job",
    run_id: int = 42,
):
    parsed = _parse(content)
    job_context = JobContext(job_name=job_name, run_id=run_id, repo="r/x")
    result = reduce_parsed_lines(parsed, job_context=job_context)
    analysis = FailedLogAnalysis(
        log=NormalizedLog(
            run_id=run_id,
            job_id=run_id * 100,
            job_name=job_name,
            status="failed",
            content=content,
        ),
        logical_job_name=job_name,
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
    return result, report


GO_TEST_LOG = """\
STEP: integration-test
2024-01-15T12:34:56.789Z running tests
2024-01-15T12:34:57.000Z some output
2024-01-15T12:34:58.000Z --- FAIL: TestStandalone (1.23s)
2024-01-15T12:34:58.100Z FAIL\tgithub.com/owner/repo/pkg\t1.23s
"""


class GoTestFailPipelineTests(unittest.TestCase):
    def test_standalone_go_test_fail_emits_typed_failure_record(self) -> None:
        result, report = _build_single_report(GO_TEST_LOG)

        go_failures = [
            f for f in result.detected_failures if f.type == "go_test_fail"
        ]
        self.assertEqual(len(go_failures), 1)
        only = go_failures[0]
        self.assertEqual(only.extracted_fields["test_name"], "TestStandalone")
        self.assertEqual(only.extracted_fields["framework"], "go")
        self.assertEqual(only.extracted_fields["duration_seconds"], 1.23)
        self.assertEqual(
            only.extracted_fields["package"], "github.com/owner/repo/pkg"
        )

        records = [r for r in report.failures if r.type == "go_test_fail"]
        self.assertEqual(len(records), 1)
        record = records[0]
        self.assertEqual(record.classification, "root_cause")
        self.assertEqual(record.extracted_fields["test_name"], "TestStandalone")
        self.assertEqual(record.extracted_fields["framework"], "go")


HASH_MISMATCH_PLUS_STANDALONE_GO_LOG = """\
STEP: integration-test
2024-01-15T12:34:56.789Z setting up
2024-01-15T12:34:57.000Z common.go:1058: file hashes don't match for /tmp/Material_X.yaml and ../samples/Material_X_HASH_1.yaml
2024-01-15T12:34:57.500Z some intermediate output
2024-01-15T12:34:58.000Z --- FAIL: TestPaired (5.0s)
2024-01-15T12:34:58.100Z (large gap of unrelated output)
2024-01-15T12:34:58.200Z noise line 1
2024-01-15T12:34:58.300Z noise line 2
2024-01-15T12:34:58.400Z noise line 3
2024-01-15T12:34:58.500Z noise line 4
2024-01-15T12:34:58.600Z noise line 5
2024-01-15T12:34:58.700Z noise line 6
2024-01-15T12:34:58.800Z noise line 7
2024-01-15T12:34:58.900Z noise line 8
2024-01-15T12:34:59.000Z noise line 9
2024-01-15T12:34:59.100Z noise line 10
2024-01-15T12:34:59.200Z noise line 11
2024-01-15T12:34:59.300Z noise line 12
2024-01-15T12:34:59.400Z noise line 13
2024-01-15T12:34:59.500Z noise line 14
2024-01-15T12:34:59.600Z noise line 15
2024-01-15T12:34:59.700Z noise line 16
2024-01-15T12:34:59.800Z noise line 17
2024-01-15T12:34:59.900Z noise line 18
2024-01-15T12:35:00.000Z noise line 19
2024-01-15T12:35:00.100Z noise line 20
2024-01-15T12:35:00.200Z noise line 21
2024-01-15T12:35:00.300Z noise line 22
2024-01-15T12:35:00.400Z noise line 23
2024-01-15T12:35:00.500Z noise line 24
2024-01-15T12:35:00.600Z noise line 25
2024-01-15T12:35:00.700Z noise line 26
2024-01-15T12:35:00.800Z noise line 27
2024-01-15T12:35:00.900Z noise line 28
2024-01-15T12:35:01.000Z noise line 29
2024-01-15T12:35:01.100Z noise line 30
2024-01-15T12:35:01.200Z noise line 31
2024-01-15T12:35:01.300Z noise line 32
2024-01-15T12:35:01.400Z noise line 33
2024-01-15T12:35:01.500Z noise line 34
2024-01-15T12:35:01.600Z noise line 35
2024-01-15T12:35:01.700Z noise line 36
2024-01-15T12:35:01.800Z noise line 37
2024-01-15T12:35:01.900Z noise line 38
2024-01-15T12:35:02.000Z noise line 39
2024-01-15T12:35:02.100Z noise line 40
2024-01-15T12:35:02.200Z noise line 41
2024-01-15T12:35:02.300Z noise line 42
2024-01-15T12:35:02.400Z noise line 43
2024-01-15T12:35:02.500Z noise line 44
2024-01-15T12:35:02.600Z noise line 45
2024-01-15T12:35:02.700Z noise line 46
2024-01-15T12:35:02.800Z noise line 47
2024-01-15T12:35:02.900Z noise line 48
2024-01-15T12:35:03.000Z noise line 49
2024-01-15T12:35:03.100Z noise line 50
2024-01-15T12:35:03.200Z noise line 51
2024-01-15T12:35:03.300Z noise line 52
2024-01-15T12:35:03.400Z --- FAIL: TestStandalone (3.0s)
2024-01-15T12:35:03.500Z FAIL\tgithub.com/owner/repo/pkg\t3.0s
"""


class HashMismatchAndGoTestFailCoordinationTests(unittest.TestCase):
    def test_paired_hash_mismatch_and_standalone_go_fail_yield_distinct_records(self) -> None:
        result, report = _build_single_report(
            HASH_MISMATCH_PLUS_STANDALONE_GO_LOG,
            job_name="postgres-test (bundling)",
        )

        hash_records = [r for r in report.failures if r.type == "hash_mismatch"]
        go_records = [r for r in report.failures if r.type == "go_test_fail"]

        self.assertEqual(len(hash_records), 1, "expected one hash_mismatch FailureRecord")
        self.assertEqual(len(go_records), 1, "expected one go_test_fail FailureRecord")

        # Hash record gets the paired test name.
        self.assertEqual(hash_records[0].extracted_fields["test_name"], "TestPaired")
        # Go record gets the standalone test name.
        self.assertEqual(go_records[0].extracted_fields["test_name"], "TestStandalone")
        self.assertEqual(go_records[0].extracted_fields["framework"], "go")

    def test_paired_fail_does_not_emit_duplicate_go_test_fail_detection(self) -> None:
        result, _ = _build_single_report(
            HASH_MISMATCH_PLUS_STANDALONE_GO_LOG,
            job_name="postgres-test (bundling)",
        )

        # Only one go_test_fail detection (for the standalone), proving the
        # coordination skip works at the detector level.
        go_failures = [f for f in result.detected_failures if f.type == "go_test_fail"]
        self.assertEqual(len(go_failures), 1)
        self.assertEqual(go_failures[0].extracted_fields["test_name"], "TestStandalone")


PYTEST_LOG = """\
STEP: pytest
2024-01-15T12:34:56.789Z collecting tests
2024-01-15T12:34:57.000Z ===== FAILURES =====
2024-01-15T12:34:57.100Z _______________________ test_bar _______________________
2024-01-15T12:34:57.200Z   def test_bar():
2024-01-15T12:34:57.300Z >    assert 1 == 2
2024-01-15T12:34:57.400Z E    AssertionError: assert 1 == 2
2024-01-15T12:34:57.500Z tests/test_foo.py:12: AssertionError
2024-01-15T12:34:57.600Z ===== short test summary =====
2024-01-15T12:34:57.700Z FAILED tests/test_foo.py::test_bar - AssertionError: assert 1 == 2
"""


class PytestPipelineTests(unittest.TestCase):
    def test_pytest_failure_emits_typed_record_with_test_id(self) -> None:
        result, report = _build_single_report(PYTEST_LOG)

        pytest_failures = [
            f for f in result.detected_failures if f.type == "pytest_fail"
        ]
        self.assertEqual(len(pytest_failures), 1)
        self.assertEqual(
            pytest_failures[0].extracted_fields["test_id"],
            "tests/test_foo.py::test_bar",
        )
        self.assertEqual(pytest_failures[0].extracted_fields["framework"], "pytest")
        self.assertIn(
            "AssertionError", pytest_failures[0].extracted_fields["assertion_message"]
        )

        records = [r for r in report.failures if r.type == "pytest_fail"]
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].classification, "root_cause")
        self.assertEqual(
            records[0].extracted_fields["test_id"], "tests/test_foo.py::test_bar"
        )


RUST_LOG = """\
STEP: cargo-test
2024-01-15T12:34:56.789Z running tests
2024-01-15T12:34:57.000Z thread 'tests::it_handles_empty_input' panicked at 'assertion failed: actual == expected', src/lib.rs:42:5
2024-01-15T12:34:57.100Z note: run with `RUST_BACKTRACE=1` environment variable to display a backtrace
2024-01-15T12:34:57.200Z test tests::it_handles_empty_input ... FAILED
2024-01-15T12:34:57.300Z test result: FAILED. 0 passed; 1 failed
"""


class RustPipelineTests(unittest.TestCase):
    def test_rust_failure_pairs_panic_with_failed_line(self) -> None:
        result, report = _build_single_report(RUST_LOG)

        rust_failures = [
            f for f in result.detected_failures if f.type == "rust_test_fail"
        ]
        self.assertEqual(len(rust_failures), 1)
        only = rust_failures[0]
        self.assertEqual(
            only.extracted_fields["test_name"], "tests::it_handles_empty_input"
        )
        self.assertEqual(only.extracted_fields["framework"], "rust")
        self.assertEqual(
            only.extracted_fields["panic_message"],
            "assertion failed: actual == expected",
        )
        self.assertEqual(only.extracted_fields["panic_location"], "src/lib.rs:42:5")

        records = [r for r in report.failures if r.type == "rust_test_fail"]
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].classification, "root_cause")
        self.assertEqual(
            records[0].extracted_fields["test_name"], "tests::it_handles_empty_input"
        )


JUNIT_LOG = """\
STEP: junit
2024-01-15T12:34:56.789Z running junit
2024-01-15T12:34:57.000Z <testcase name="test_thing" classname="my.TestClass"><failure message="expected 1 got 2" type="AssertionError"/></testcase>
"""


class JUnitPipelineTests(unittest.TestCase):
    def test_junit_failure_emits_typed_record(self) -> None:
        result, report = _build_single_report(JUNIT_LOG)

        junit_failures = [
            f for f in result.detected_failures if f.type == "junit_xml"
        ]
        self.assertEqual(len(junit_failures), 1)
        fields = junit_failures[0].extracted_fields
        self.assertEqual(fields["test_name"], "test_thing")
        self.assertEqual(fields["classname"], "my.TestClass")
        self.assertEqual(fields["element_type"], "failure")
        self.assertEqual(fields["message"], "expected 1 got 2")
        self.assertEqual(fields["framework"], "junit_xml")

        records = [r for r in report.failures if r.type == "junit_xml"]
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].classification, "root_cause")
        self.assertEqual(records[0].extracted_fields["test_name"], "test_thing")


if __name__ == "__main__":
    unittest.main()
