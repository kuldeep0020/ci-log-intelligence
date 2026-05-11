from __future__ import annotations

import unittest

from ci_log_intelligence.models import ParsedLine
from ci_log_intelligence.reducer.detectors import JobContext
from ci_log_intelligence.reducer.detectors.build_error_go import (
    GoBuildErrorDetector,
)


def _line(line_number: int, content: str, step_id: str | None = "build") -> ParsedLine:
    return ParsedLine(line_number, content, None, step_id, [])


def _ctx() -> JobContext:
    return JobContext(job_name=None, run_id=None, repo=None)


class EmptyInputTests(unittest.TestCase):
    def test_empty_input_returns_no_failures(self) -> None:
        self.assertEqual(GoBuildErrorDetector().scan([], _ctx()), [])


class SingleErrorTests(unittest.TestCase):
    def test_undefined_symbol_emits_one_record(self) -> None:
        lines = [_line(10, "./pkg/foo.go:42:5: undefined: SomeFunc")]
        failures = GoBuildErrorDetector().scan(lines, _ctx())

        self.assertEqual(len(failures), 1)
        only = failures[0]
        self.assertEqual(only.type, "build_error_go")
        self.assertEqual(only.severity, 3)
        self.assertEqual(only.classification_claim, "root_cause")
        self.assertEqual(only.anchor_type, "build_error_go")
        self.assertEqual(only.extracted_fields["language"], "go")
        self.assertEqual(only.extracted_fields["file_path"], "./pkg/foo.go")
        self.assertEqual(only.extracted_fields["line"], 42)
        self.assertEqual(only.extracted_fields["column"], 5)
        self.assertEqual(only.extracted_fields["message"], "undefined: SomeFunc")

    def test_type_mismatch_message_preserved(self) -> None:
        lines = [
            _line(
                10,
                "./pkg/bar.go:10:3: cannot use x (type int) as type string in argument to fmt.Println",
            )
        ]
        failures = GoBuildErrorDetector().scan(lines, _ctx())

        self.assertEqual(len(failures), 1)
        self.assertIn("cannot use x", failures[0].extracted_fields["message"])


class MultipleErrorsTests(unittest.TestCase):
    def test_three_errors_emit_three_records(self) -> None:
        lines = [
            _line(10, "./pkg/foo.go:1:1: error one"),
            _line(11, "./pkg/bar.go:2:2: error two"),
            _line(12, "./pkg/baz.go:3:3: error three"),
        ]
        failures = GoBuildErrorDetector().scan(lines, _ctx())

        self.assertEqual(len(failures), 3)
        files = [f.extracted_fields["file_path"] for f in failures]
        self.assertEqual(files, ["./pkg/foo.go", "./pkg/bar.go", "./pkg/baz.go"])


class StackTraceCoordinationTests(unittest.TestCase):
    def test_indented_stack_trace_line_does_not_match(self) -> None:
        # A line that LOOKS like a Go compile error but is actually a stack
        # trace frame (leading whitespace) must NOT match the top-anchored
        # regex.
        lines = [
            _line(10, "  /path/to/file.go:42:5 in func"),
            _line(11, "\t./pkg/foo.go:1:1: tab-indented also rejected"),
        ]
        failures = GoBuildErrorDetector().scan(lines, _ctx())
        self.assertEqual(failures, [])


class TimestampPrefixToleranceTests(unittest.TestCase):
    def test_gha_prefixed_go_error_still_matches(self) -> None:
        lines = [
            _line(
                10,
                "2024-01-15T12:34:56.789Z ./pkg/foo.go:42:5: undefined: SomeFunc",
            )
        ]
        failures = GoBuildErrorDetector().scan(lines, _ctx())

        self.assertEqual(len(failures), 1)
        self.assertEqual(failures[0].extracted_fields["file_path"], "./pkg/foo.go")
        self.assertEqual(failures[0].extracted_fields["line"], 42)


class CoordinationEdgeCaseTests(unittest.TestCase):
    def test_non_go_file_path_with_same_shape_does_not_match(self) -> None:
        # The Go regex requires ``\.go`` -- a C/C++ file with the same shape
        # must NOT match this detector (it belongs to GccBuildErrorDetector).
        lines = [_line(10, "./src/foo.c:10:5: error: undeclared")]
        failures = GoBuildErrorDetector().scan(lines, _ctx())
        self.assertEqual(failures, [])

    def test_suggested_block_range_is_none(self) -> None:
        lines = [_line(10, "./pkg/foo.go:42:5: undefined: SomeFunc")]
        failures = GoBuildErrorDetector().scan(lines, _ctx())
        self.assertIsNone(failures[0].suggested_block_range)


if __name__ == "__main__":
    unittest.main()
