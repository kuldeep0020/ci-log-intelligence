from __future__ import annotations

import unittest

from ci_log_intelligence.models import ParsedLine
from ci_log_intelligence.reducer.detectors import JobContext
from ci_log_intelligence.reducer.detectors.go_test_fail import GoTestFailDetector


def _line(line_number: int, content: str, step_id: str | None = "test") -> ParsedLine:
    return ParsedLine(line_number, content, None, step_id, [])


def _ctx() -> JobContext:
    return JobContext(job_name=None, run_id=None, repo=None)


_HASH_MISMATCH_TEXT = (
    "common.go:1058: file hashes don't match for /tmp/x/Material_X.yaml "
    "and ../samples/test_output/Material_X_HASH_1.yaml"
)


class EmptyInputTests(unittest.TestCase):
    def test_empty_input_returns_no_failures(self) -> None:
        self.assertEqual(GoTestFailDetector().scan([], _ctx()), [])


class SingleFailureTests(unittest.TestCase):
    def test_single_fail_marker_emits_one_record(self) -> None:
        lines = [_line(5, "--- FAIL: TestThing (1.2s)")]

        failures = GoTestFailDetector().scan(lines, _ctx())

        self.assertEqual(len(failures), 1)
        only = failures[0]
        self.assertEqual(only.type, "go_test_fail")
        self.assertEqual(only.severity, 2)
        self.assertEqual(only.classification_claim, "root_cause")
        self.assertEqual(only.anchor_type, "go_test_fail")
        self.assertEqual(only.anchor_lines, [5])

    def test_record_extracts_test_name(self) -> None:
        lines = [_line(10, "--- FAIL: TestRunSetPartial (45.3s)")]

        failures = GoTestFailDetector().scan(lines, _ctx())

        self.assertEqual(failures[0].extracted_fields["test_name"], "TestRunSetPartial")

    def test_framework_constant_is_go(self) -> None:
        lines = [_line(1, "--- FAIL: TestX (0.1s)")]

        failures = GoTestFailDetector().scan(lines, _ctx())

        self.assertEqual(failures[0].extracted_fields["framework"], "go")


class DurationExtractionTests(unittest.TestCase):
    def test_duration_parsed_when_present(self) -> None:
        lines = [_line(1, "--- FAIL: TestX (45.3s)")]
        failures = GoTestFailDetector().scan(lines, _ctx())
        self.assertEqual(failures[0].extracted_fields["duration_seconds"], 45.3)

    def test_duration_omitted_when_not_present(self) -> None:
        lines = [_line(1, "--- FAIL: TestX")]
        failures = GoTestFailDetector().scan(lines, _ctx())
        self.assertNotIn("duration_seconds", failures[0].extracted_fields)


class PackageExtractionTests(unittest.TestCase):
    def test_package_parsed_when_within_lookahead(self) -> None:
        lines = [
            _line(10, "--- FAIL: TestX (1.0s)"),
            _line(15, "FAIL\tgithub.com/owner/repo/integration\t45.3s"),
        ]
        failures = GoTestFailDetector().scan(lines, _ctx())
        self.assertEqual(
            failures[0].extracted_fields["package"],
            "github.com/owner/repo/integration",
        )

    def test_package_omitted_when_in_different_step(self) -> None:
        lines = [
            _line(10, "--- FAIL: TestX (1.0s)", step_id="step-a"),
            _line(11, "FAIL\tgithub.com/owner/repo/x\t1.0s", step_id="step-b"),
        ]
        failures = GoTestFailDetector().scan(lines, _ctx())
        self.assertNotIn("package", failures[0].extracted_fields)

    def test_package_omitted_when_beyond_lookahead(self) -> None:
        lines = [
            _line(10, "--- FAIL: TestX (1.0s)"),
            _line(200, "FAIL\tgithub.com/owner/repo/x\t1.0s"),
        ]
        failures = GoTestFailDetector().scan(lines, _ctx())
        self.assertNotIn("package", failures[0].extracted_fields)


class SubtestNameTests(unittest.TestCase):
    def test_subtest_path_is_captured(self) -> None:
        lines = [_line(1, "--- FAIL: TestX/incremental_phantom (0.5s)")]
        failures = GoTestFailDetector().scan(lines, _ctx())
        self.assertEqual(
            failures[0].extracted_fields["test_name"], "TestX/incremental_phantom"
        )


class MultipleFailuresTests(unittest.TestCase):
    def test_multiple_unpaired_fails_each_get_record(self) -> None:
        lines = [
            _line(10, "--- FAIL: TestA (1.0s)"),
            _line(100, "--- FAIL: TestB (2.0s)"),
            _line(200, "--- FAIL: TestC (3.0s)"),
        ]
        failures = GoTestFailDetector().scan(lines, _ctx())

        self.assertEqual(len(failures), 3)
        test_names = [f.extracted_fields["test_name"] for f in failures]
        self.assertEqual(test_names, ["TestA", "TestB", "TestC"])


class HashMismatchCoordinationTests(unittest.TestCase):
    def test_fail_within_50_lines_of_hash_mismatch_is_skipped(self) -> None:
        lines = [
            _line(100, _HASH_MISMATCH_TEXT),
            _line(120, "--- FAIL: TestPaired (1.0s)"),
        ]
        failures = GoTestFailDetector().scan(lines, _ctx())

        # HashMismatchDetector owns this FAIL; we must NOT double-emit.
        self.assertEqual(failures, [])

    def test_fail_just_outside_window_is_emitted(self) -> None:
        lines = [
            _line(100, _HASH_MISMATCH_TEXT),
            _line(151, "--- FAIL: TestStandalone (1.0s)"),
        ]
        failures = GoTestFailDetector().scan(lines, _ctx())

        self.assertEqual(len(failures), 1)
        self.assertEqual(
            failures[0].extracted_fields["test_name"], "TestStandalone"
        )

    def test_fail_in_different_step_is_emitted(self) -> None:
        lines = [
            _line(100, _HASH_MISMATCH_TEXT, step_id="step-a"),
            _line(110, "--- FAIL: TestOther (1.0s)", step_id="step-b"),
        ]
        failures = GoTestFailDetector().scan(lines, _ctx())

        self.assertEqual(len(failures), 1)
        self.assertEqual(failures[0].extracted_fields["test_name"], "TestOther")

    def test_paired_and_standalone_coexist(self) -> None:
        lines = [
            _line(100, _HASH_MISMATCH_TEXT),
            _line(120, "--- FAIL: TestPaired (1.0s)"),
            _line(500, "--- FAIL: TestStandalone (1.0s)"),
        ]
        failures = GoTestFailDetector().scan(lines, _ctx())

        # Only the standalone gets a go_test_fail record.
        self.assertEqual(len(failures), 1)
        self.assertEqual(
            failures[0].extracted_fields["test_name"], "TestStandalone"
        )

    def test_only_the_nearest_fail_to_a_mismatch_is_claimed_by_hash_mismatch(self) -> None:
        # Real WHT case: a single mismatch with two FAIL markers nearby (e.g. one for
        # the current test, one from a previous test still echoing). HashMismatch only
        # pairs the nearest. GoTestFail must emit the OTHER one -- not skip both.
        parsed_lines = [
            _line(10, "common.go:1058: file hashes don't match for Material_X.yaml", step_id="step-a"),
            _line(20, "--- FAIL: TestA (1.0s)", step_id="step-a"),
            _line(25, "--- FAIL: TestB (1.0s)", step_id="step-a"),
        ]

        failures = GoTestFailDetector().scan(parsed_lines, JobContext(None, None, None))

        # TestA is the nearest to the mismatch -> claimed by hash_mismatch -> skipped here.
        # TestB is NOT claimed -> must be emitted.
        test_names = {f.extracted_fields.get("test_name") for f in failures}
        self.assertIn("TestB", test_names)
        self.assertNotIn("TestA", test_names)
        self.assertEqual(len(failures), 1)


if __name__ == "__main__":
    unittest.main()
