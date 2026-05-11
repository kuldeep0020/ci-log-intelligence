from __future__ import annotations

import unittest

from ci_log_intelligence.models import ParsedLine
from ci_log_intelligence.reducer.detectors import JobContext
from ci_log_intelligence.reducer.detectors.pytest_fail import PytestFailDetector


def _line(line_number: int, content: str, step_id: str | None = "test") -> ParsedLine:
    return ParsedLine(line_number, content, None, step_id, [])


def _ctx() -> JobContext:
    return JobContext(job_name=None, run_id=None, repo=None)


class EmptyInputTests(unittest.TestCase):
    def test_empty_input_returns_no_failures(self) -> None:
        self.assertEqual(PytestFailDetector().scan([], _ctx()), [])


class SingleFailureTests(unittest.TestCase):
    def test_summary_line_emits_one_record(self) -> None:
        lines = [
            _line(50, "FAILED tests/test_foo.py::test_bar - AssertionError: expected 1 got 2"),
        ]
        failures = PytestFailDetector().scan(lines, _ctx())

        self.assertEqual(len(failures), 1)
        only = failures[0]
        self.assertEqual(only.type, "pytest_fail")
        self.assertEqual(only.severity, 2)
        self.assertEqual(only.classification_claim, "root_cause")
        self.assertEqual(only.anchor_type, "pytest_fail")

    def test_test_id_extraction(self) -> None:
        lines = [_line(1, "FAILED tests/test_foo.py::test_bar - AssertionError: boom")]
        failures = PytestFailDetector().scan(lines, _ctx())

        self.assertEqual(
            failures[0].extracted_fields["test_id"], "tests/test_foo.py::test_bar"
        )
        self.assertEqual(
            failures[0].extracted_fields["assertion_message"], "AssertionError: boom"
        )

    def test_framework_constant_is_pytest(self) -> None:
        lines = [_line(1, "FAILED a.py::test_x - boom")]
        failures = PytestFailDetector().scan(lines, _ctx())
        self.assertEqual(failures[0].extracted_fields["framework"], "pytest")


class AssertionMessageOptionalTests(unittest.TestCase):
    def test_summary_without_dash_assertion_omits_assertion_message(self) -> None:
        lines = [_line(1, "FAILED tests/test_foo.py::test_bar")]
        failures = PytestFailDetector().scan(lines, _ctx())

        self.assertEqual(len(failures), 1)
        self.assertEqual(
            failures[0].extracted_fields["test_id"], "tests/test_foo.py::test_bar"
        )
        self.assertNotIn("assertion_message", failures[0].extracted_fields)


class MultipleFailuresTests(unittest.TestCase):
    def test_multiple_summaries_each_emit_one_record(self) -> None:
        lines = [
            _line(10, "FAILED a.py::test_one - AssertionError: x"),
            _line(11, "FAILED b.py::test_two - ValueError: y"),
            _line(12, "FAILED c.py::test_three - RuntimeError: z"),
        ]
        failures = PytestFailDetector().scan(lines, _ctx())

        self.assertEqual(len(failures), 3)
        test_ids = [f.extracted_fields["test_id"] for f in failures]
        self.assertEqual(
            test_ids,
            ["a.py::test_one", "b.py::test_two", "c.py::test_three"],
        )


class TracebackPairingTests(unittest.TestCase):
    def test_summary_with_preceding_separator_pairs_them(self) -> None:
        lines = [
            _line(10, "===== FAILURES ====="),
            _line(
                11,
                "_______________________ test_bar _______________________",
            ),
            _line(12, "  some traceback content"),
            _line(13, "  E AssertionError: x"),
            _line(50, "FAILED tests/test_foo.py::test_bar - AssertionError: x"),
        ]
        failures = PytestFailDetector().scan(lines, _ctx())

        self.assertEqual(len(failures), 1)
        only = failures[0]
        self.assertEqual(sorted(only.anchor_lines), [11, 50])
        self.assertEqual(only.suggested_block_range, (11, 50))

    def test_summary_without_separator_anchors_summary_only(self) -> None:
        lines = [_line(50, "FAILED tests/test_foo.py::test_bar - boom")]
        failures = PytestFailDetector().scan(lines, _ctx())

        self.assertEqual(failures[0].anchor_lines, [50])
        self.assertIsNone(failures[0].suggested_block_range)

    def test_separator_outside_500_line_window_does_not_pair(self) -> None:
        lines = [
            _line(
                10,
                "_______________________ test_bar _______________________",
            ),
            _line(700, "FAILED tests/test_foo.py::test_bar - boom"),
        ]
        failures = PytestFailDetector().scan(lines, _ctx())

        self.assertEqual(failures[0].anchor_lines, [700])
        self.assertIsNone(failures[0].suggested_block_range)

    def test_separator_in_different_step_does_not_pair(self) -> None:
        lines = [
            _line(
                10,
                "_______________________ test_bar _______________________",
                step_id="step-a",
            ),
            _line(20, "FAILED a.py::test_bar - boom", step_id="step-b"),
        ]
        failures = PytestFailDetector().scan(lines, _ctx())

        self.assertEqual(failures[0].anchor_lines, [20])


class SummaryBoundaryTests(unittest.TestCase):
    def test_substring_failed_token_does_not_register_as_summary(self) -> None:
        # Embedded inside arbitrary text, ``FAILED a.py::b`` must NOT trigger.
        lines = [_line(1, ">>>> reported FAILED tests/x.py::test_y - boom")]
        failures = PytestFailDetector().scan(lines, _ctx())
        self.assertEqual(failures, [])

    def test_summary_with_leading_whitespace_still_registers(self) -> None:
        lines = [_line(1, "  FAILED tests/x.py::test_y - boom")]
        failures = PytestFailDetector().scan(lines, _ctx())
        self.assertEqual(len(failures), 1)
        self.assertEqual(
            failures[0].extracted_fields["test_id"], "tests/x.py::test_y"
        )

    def test_summary_at_start_of_line_still_registers(self) -> None:
        lines = [_line(1, "FAILED tests/x.py::test_y - boom")]
        failures = PytestFailDetector().scan(lines, _ctx())
        self.assertEqual(len(failures), 1)


class PairOnceTests(unittest.TestCase):
    def test_pair_once_when_bare_test_names_collide(self) -> None:
        parsed_lines = [
            _line(10, "_______________________ test_x _______________________"),
            _line(15, "  some traceback for a.py"),
            _line(20, "_______________________ test_x _______________________"),
            _line(25, "  some traceback for b.py"),
            _line(50, "FAILED a.py::test_x - error A"),
            _line(51, "FAILED b.py::test_x - error B"),
        ]
        failures = PytestFailDetector().scan(parsed_lines, _ctx())
        self.assertEqual(len(failures), 2)
        # The two summary records pair with DIFFERENT separators.
        ranges = sorted(f.suggested_block_range for f in failures if f.suggested_block_range)
        self.assertEqual(len(ranges), 2)
        self.assertNotEqual(ranges[0], ranges[1])


class ParametrizedTestNameTests(unittest.TestCase):
    def test_parametrized_test_id_extracted(self) -> None:
        lines = [
            _line(
                1,
                "FAILED tests/test_x.py::test_y[param1] - AssertionError: nope",
            )
        ]
        failures = PytestFailDetector().scan(lines, _ctx())

        self.assertEqual(
            failures[0].extracted_fields["test_id"],
            "tests/test_x.py::test_y[param1]",
        )

    def test_parametrized_separator_pairs_with_summary(self) -> None:
        lines = [
            _line(10, "____________ test_y[param1] ____________"),
            _line(50, "FAILED tests/test_x.py::test_y[param1] - boom"),
        ]
        failures = PytestFailDetector().scan(lines, _ctx())

        self.assertEqual(sorted(failures[0].anchor_lines), [10, 50])


if __name__ == "__main__":
    unittest.main()
