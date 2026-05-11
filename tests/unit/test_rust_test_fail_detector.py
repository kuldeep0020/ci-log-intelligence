from __future__ import annotations

import unittest

from ci_log_intelligence.models import ParsedLine
from ci_log_intelligence.reducer.detectors import JobContext
from ci_log_intelligence.reducer.detectors.rust_test_fail import RustTestFailDetector


def _line(line_number: int, content: str, step_id: str | None = "test") -> ParsedLine:
    return ParsedLine(line_number, content, None, step_id, [])


def _ctx() -> JobContext:
    return JobContext(job_name=None, run_id=None, repo=None)


class EmptyInputTests(unittest.TestCase):
    def test_empty_input_returns_no_failures(self) -> None:
        self.assertEqual(RustTestFailDetector().scan([], _ctx()), [])


class SingleFailureTests(unittest.TestCase):
    def test_failed_line_emits_one_record(self) -> None:
        lines = [_line(20, "test tests::it_handles_empty_input ... FAILED")]
        failures = RustTestFailDetector().scan(lines, _ctx())

        self.assertEqual(len(failures), 1)
        only = failures[0]
        self.assertEqual(only.type, "rust_test_fail")
        self.assertEqual(only.severity, 2)
        self.assertEqual(only.classification_claim, "root_cause")
        self.assertEqual(only.anchor_type, "rust_test_fail")
        self.assertEqual(only.anchor_lines, [20])

    def test_test_name_extracted(self) -> None:
        lines = [_line(1, "test tests::it_handles_empty_input ... FAILED")]
        failures = RustTestFailDetector().scan(lines, _ctx())
        self.assertEqual(
            failures[0].extracted_fields["test_name"],
            "tests::it_handles_empty_input",
        )

    def test_framework_constant_is_rust(self) -> None:
        lines = [_line(1, "test tests::x ... FAILED")]
        failures = RustTestFailDetector().scan(lines, _ctx())
        self.assertEqual(failures[0].extracted_fields["framework"], "rust")


class PanicPairingTests(unittest.TestCase):
    def test_panic_paired_with_matching_failed(self) -> None:
        lines = [
            _line(
                10,
                "thread 'tests::it_handles_empty_input' panicked at "
                "'assertion failed: x', src/lib.rs:42:5",
            ),
            _line(50, "test tests::it_handles_empty_input ... FAILED"),
        ]
        failures = RustTestFailDetector().scan(lines, _ctx())

        self.assertEqual(len(failures), 1)
        only = failures[0]
        self.assertEqual(sorted(only.anchor_lines), [10, 50])
        self.assertEqual(only.suggested_block_range, (10, 50))
        self.assertEqual(only.extracted_fields["panic_message"], "assertion failed: x")
        self.assertEqual(only.extracted_fields["panic_location"], "src/lib.rs:42:5")

    def test_unpaired_failed_anchors_only_failed_line(self) -> None:
        lines = [_line(50, "test tests::x ... FAILED")]
        failures = RustTestFailDetector().scan(lines, _ctx())

        self.assertEqual(failures[0].anchor_lines, [50])
        self.assertIsNone(failures[0].suggested_block_range)
        self.assertNotIn("panic_message", failures[0].extracted_fields)
        self.assertNotIn("panic_location", failures[0].extracted_fields)

    def test_panic_outside_500_line_window_does_not_pair(self) -> None:
        lines = [
            _line(10, "thread 'tests::x' panicked at 'boom', src/lib.rs:1:1"),
            _line(700, "test tests::x ... FAILED"),
        ]
        failures = RustTestFailDetector().scan(lines, _ctx())

        self.assertEqual(failures[0].anchor_lines, [700])
        self.assertNotIn("panic_message", failures[0].extracted_fields)

    def test_panic_with_different_thread_name_does_not_pair(self) -> None:
        lines = [
            _line(10, "thread 'tests::y' panicked at 'boom', src/lib.rs:1:1"),
            _line(50, "test tests::x ... FAILED"),
        ]
        failures = RustTestFailDetector().scan(lines, _ctx())

        self.assertEqual(failures[0].anchor_lines, [50])
        self.assertNotIn("panic_message", failures[0].extracted_fields)

    def test_panic_in_different_step_does_not_pair(self) -> None:
        lines = [
            _line(
                10,
                "thread 'tests::x' panicked at 'boom', src/lib.rs:1:1",
                step_id="step-a",
            ),
            _line(20, "test tests::x ... FAILED", step_id="step-b"),
        ]
        failures = RustTestFailDetector().scan(lines, _ctx())

        self.assertEqual(failures[0].anchor_lines, [20])
        self.assertNotIn("panic_message", failures[0].extracted_fields)


class PanicLocationOptionalTests(unittest.TestCase):
    def test_panic_without_location_omits_location_field(self) -> None:
        lines = [
            _line(10, "thread 'tests::x' panicked at 'boom'"),
            _line(20, "test tests::x ... FAILED"),
        ]
        failures = RustTestFailDetector().scan(lines, _ctx())

        self.assertEqual(failures[0].extracted_fields["panic_message"], "boom")
        self.assertNotIn("panic_location", failures[0].extracted_fields)


class MultipleFailuresTests(unittest.TestCase):
    def test_multiple_failures_with_each_paired_panic(self) -> None:
        lines = [
            _line(10, "thread 'tests::a' panicked at 'boom_a', src/lib.rs:1:1"),
            _line(20, "test tests::a ... FAILED"),
            _line(30, "thread 'tests::b' panicked at 'boom_b', src/lib.rs:2:2"),
            _line(40, "test tests::b ... FAILED"),
        ]
        failures = RustTestFailDetector().scan(lines, _ctx())

        self.assertEqual(len(failures), 2)
        names = [f.extracted_fields["test_name"] for f in failures]
        self.assertEqual(names, ["tests::a", "tests::b"])
        messages = [f.extracted_fields["panic_message"] for f in failures]
        self.assertEqual(messages, ["boom_a", "boom_b"])


if __name__ == "__main__":
    unittest.main()
