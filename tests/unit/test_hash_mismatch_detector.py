from __future__ import annotations

import unittest

from ci_log_intelligence.models import ParsedLine
from ci_log_intelligence.reducer.detectors import JobContext
from ci_log_intelligence.reducer.detectors.hash_mismatch import HashMismatchDetector


def _line(line_number: int, content: str, step_id: str | None = "test") -> ParsedLine:
    return ParsedLine(line_number, content, None, step_id, [])


def _ctx(job_name: str | None) -> JobContext:
    return JobContext(job_name=job_name, run_id=None, repo=None)


_MISMATCH_TEXT = (
    "common.go:1058: file hashes don't match for /tmp/x/Material_X.yaml and "
    "../samples/test_output/Material_X_HASH_1.yaml"
)


class PairedDetectionTests(unittest.TestCase):
    def test_paired_mismatch_and_fail_within_window(self) -> None:
        lines = [
            _line(100, _MISMATCH_TEXT),
            _line(130, "--- FAIL: TestX (1.0s)"),
        ]

        failures = HashMismatchDetector().scan(lines, _ctx("postgres-test (bundling)"))

        self.assertEqual(len(failures), 1)
        only = failures[0]
        self.assertEqual(only.type, "hash_mismatch")
        self.assertEqual(only.anchor_lines, [100, 130])
        self.assertEqual(only.severity, 2)
        self.assertEqual(only.classification_claim, "root_cause")
        self.assertEqual(only.anchor_type, "hash_mismatch")
        self.assertEqual(only.extracted_fields["test_name"], "TestX")
        self.assertEqual(only.extracted_fields["warehouse_target"], "postgres")
        self.assertEqual(only.extracted_fields["job_name"], "postgres-test (bundling)")
        self.assertEqual(only.suggested_block_range, (100, 130))

    def test_subtest_name_is_extracted(self) -> None:
        lines = [
            _line(10, _MISMATCH_TEXT),
            _line(11, "--- FAIL: TestX/subtest_name (1.2s)"),
        ]

        failures = HashMismatchDetector().scan(lines, _ctx(None))

        self.assertEqual(len(failures), 1)
        self.assertEqual(failures[0].extracted_fields["test_name"], "TestX/subtest_name")


class StepBoundaryTests(unittest.TestCase):
    def test_mismatch_and_fail_in_different_steps_do_not_pair(self) -> None:
        lines = [
            _line(100, _MISMATCH_TEXT, step_id="step-a"),
            _line(110, "--- FAIL: TestY (1.0s)", step_id="step-b"),
        ]

        failures = HashMismatchDetector().scan(lines, _ctx("postgres-test"))

        self.assertEqual(len(failures), 1)
        failure = failures[0]
        self.assertEqual(failure.anchor_lines, [100])
        self.assertNotIn("test_name", failure.extracted_fields)
        # Degraded record is still typed.
        self.assertEqual(failure.type, "hash_mismatch")
        self.assertEqual(failure.severity, 2)
        self.assertEqual(failure.classification_claim, "root_cause")
        self.assertEqual(failure.extracted_fields["warehouse_target"], "postgres")


class WindowBoundaryTests(unittest.TestCase):
    def test_pair_exactly_at_50_lines_apart_pairs(self) -> None:
        lines = [
            _line(100, _MISMATCH_TEXT),
            _line(150, "--- FAIL: TestEdge (0.1s)"),
        ]

        failures = HashMismatchDetector().scan(lines, _ctx(None))

        self.assertEqual(len(failures), 1)
        self.assertEqual(failures[0].extracted_fields.get("test_name"), "TestEdge")

    def test_pair_51_lines_apart_does_not_pair(self) -> None:
        lines = [
            _line(100, _MISMATCH_TEXT),
            _line(151, "--- FAIL: TestFar (0.1s)"),
        ]

        failures = HashMismatchDetector().scan(lines, _ctx(None))

        self.assertEqual(len(failures), 1)
        self.assertEqual(failures[0].anchor_lines, [100])
        self.assertNotIn("test_name", failures[0].extracted_fields)


class NearestPairingTests(unittest.TestCase):
    def test_two_fail_candidates_within_window_closer_one_wins(self) -> None:
        lines = [
            _line(100, _MISMATCH_TEXT),
            _line(120, "--- FAIL: TestFar (0.1s)"),
            _line(105, "--- FAIL: TestNear (0.1s)"),
        ]

        failures = HashMismatchDetector().scan(lines, _ctx(None))

        self.assertEqual(len(failures), 1)
        self.assertEqual(failures[0].extracted_fields["test_name"], "TestNear")
        self.assertEqual(sorted(failures[0].anchor_lines), [100, 105])


class MultipleMismatchTests(unittest.TestCase):
    def test_multiple_mismatch_lines_each_get_their_own_pair(self) -> None:
        lines = [
            _line(10, _MISMATCH_TEXT),
            _line(12, "--- FAIL: TestA (1.0s)"),
            _line(40, _MISMATCH_TEXT),
            _line(42, "--- FAIL: TestB (1.0s)"),
            _line(70, _MISMATCH_TEXT),
            _line(72, "--- FAIL: TestC (1.0s)"),
        ]

        failures = HashMismatchDetector().scan(lines, _ctx("snowflake-test"))

        self.assertEqual(len(failures), 3)
        test_names = [f.extracted_fields["test_name"] for f in failures]
        self.assertEqual(test_names, ["TestA", "TestB", "TestC"])
        self.assertTrue(
            all(f.extracted_fields["warehouse_target"] == "snowflake" for f in failures)
        )

    def test_multiple_mismatches_pair_with_same_fail_line(self) -> None:
        # Real WHT case: one failing test can emit several "file hashes don't match"
        # lines (one per divergent golden file), then a single "--- FAIL: TestX".
        parsed_lines = [
            _line(1, "STEP: integration-test", step_id="integration-test"),
            _line(2, "common.go:1058: file hashes don't match for Material_A.yaml", step_id="integration-test"),
            _line(3, "common.go:1058: file hashes don't match for Material_B.yaml", step_id="integration-test"),
            _line(4, "--- FAIL: TestRunSetPartial (45.3s)", step_id="integration-test"),
        ]

        failures = HashMismatchDetector().scan(parsed_lines, JobContext(None, None, None))

        self.assertEqual(len(failures), 2)
        for failure in failures:
            self.assertEqual(failure.extracted_fields.get("test_name"), "TestRunSetPartial")
            self.assertIn(4, failure.anchor_lines)


class UnpairedMismatchTests(unittest.TestCase):
    def test_unpaired_mismatch_still_emits_typed_record(self) -> None:
        lines = [_line(42, _MISMATCH_TEXT)]

        failures = HashMismatchDetector().scan(lines, _ctx("redshift-test"))

        self.assertEqual(len(failures), 1)
        failure = failures[0]
        self.assertEqual(failure.type, "hash_mismatch")
        self.assertEqual(failure.anchor_lines, [42])
        self.assertNotIn("test_name", failure.extracted_fields)
        self.assertEqual(failure.extracted_fields["warehouse_target"], "redshift")
        self.assertEqual(failure.classification_claim, "root_cause")
        self.assertIsNone(failure.suggested_block_range)


class WarehouseInferenceTests(unittest.TestCase):
    def test_postgres_job_name_infers_postgres(self) -> None:
        lines = [_line(1, _MISMATCH_TEXT)]
        failures = HashMismatchDetector().scan(lines, _ctx("postgres-test (bundling)"))
        self.assertEqual(failures[0].extracted_fields["warehouse_target"], "postgres")

    def test_snowflake_job_name_infers_snowflake(self) -> None:
        lines = [_line(1, _MISMATCH_TEXT)]
        failures = HashMismatchDetector().scan(lines, _ctx("snowflake-test"))
        self.assertEqual(failures[0].extracted_fields["warehouse_target"], "snowflake")

    def test_redshift_databricks_bigquery_inferred(self) -> None:
        for keyword in ("redshift", "databricks", "bigquery"):
            with self.subTest(keyword=keyword):
                lines = [_line(1, _MISMATCH_TEXT)]
                failures = HashMismatchDetector().scan(
                    lines, _ctx(f"{keyword}-test (bundling)")
                )
                self.assertEqual(
                    failures[0].extracted_fields["warehouse_target"], keyword
                )

    def test_unknown_job_name_omits_warehouse_target(self) -> None:
        lines = [_line(1, _MISMATCH_TEXT)]
        failures = HashMismatchDetector().scan(lines, _ctx("some-random-job"))
        self.assertNotIn("warehouse_target", failures[0].extracted_fields)
        # job_name is still preserved when known.
        self.assertEqual(failures[0].extracted_fields["job_name"], "some-random-job")


class NoJobNameTests(unittest.TestCase):
    def test_empty_job_context_omits_warehouse_and_job_name(self) -> None:
        lines = [_line(1, _MISMATCH_TEXT)]
        failures = HashMismatchDetector().scan(lines, _ctx(None))
        self.assertNotIn("warehouse_target", failures[0].extracted_fields)
        self.assertNotIn("job_name", failures[0].extracted_fields)


class ClassificationClaimAndSeverityTests(unittest.TestCase):
    def test_every_record_claims_root_cause_severity_two_and_anchor_type(self) -> None:
        lines = [
            _line(1, _MISMATCH_TEXT),
            _line(2, "--- FAIL: TestA (0.1s)"),
            _line(100, _MISMATCH_TEXT),  # unpaired
        ]

        failures = HashMismatchDetector().scan(lines, _ctx("postgres-test"))

        self.assertEqual(len(failures), 2)
        for failure in failures:
            self.assertEqual(failure.classification_claim, "root_cause")
            self.assertEqual(failure.severity, 2)
            self.assertEqual(failure.anchor_type, "hash_mismatch")
            self.assertEqual(failure.type, "hash_mismatch")


class NoMismatchInLogTests(unittest.TestCase):
    def test_log_with_no_hash_mismatch_returns_no_failures(self) -> None:
        lines = [
            _line(1, "starting build"),
            _line(2, "--- FAIL: TestX (1.0s)"),
            _line(3, "PASS"),
        ]

        failures = HashMismatchDetector().scan(lines, _ctx("postgres-test"))

        self.assertEqual(failures, [])


class EmptyInputTests(unittest.TestCase):
    def test_empty_input_returns_no_failures(self) -> None:
        self.assertEqual(
            HashMismatchDetector().scan([], JobContext(None, None, None)),
            [],
        )


class NoneStepIdTests(unittest.TestCase):
    def test_pair_when_both_lines_have_no_step_id(self) -> None:
        # Logs with no step markers leave step_id=None on every line. The pairing
        # rule is structural equality, so None == None must still pair.
        parsed_lines = [
            _line(10, "file hashes don't match for Material_X.yaml", step_id=None),
            _line(12, "--- FAIL: TestX (1.2s)", step_id=None),
        ]

        failures = HashMismatchDetector().scan(parsed_lines, JobContext(None, None, None))

        self.assertEqual(len(failures), 1)
        self.assertEqual(failures[0].extracted_fields.get("test_name"), "TestX")
        self.assertEqual(sorted(failures[0].anchor_lines), [10, 12])


if __name__ == "__main__":
    unittest.main()
