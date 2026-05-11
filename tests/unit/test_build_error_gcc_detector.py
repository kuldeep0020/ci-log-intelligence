from __future__ import annotations

import unittest

from ci_log_intelligence.models import ParsedLine
from ci_log_intelligence.reducer.detectors import JobContext
from ci_log_intelligence.reducer.detectors.build_error_gcc import (
    GccBuildErrorDetector,
)


def _line(line_number: int, content: str, step_id: str | None = "build") -> ParsedLine:
    return ParsedLine(line_number, content, None, step_id, [])


def _ctx() -> JobContext:
    return JobContext(job_name=None, run_id=None, repo=None)


class EmptyInputTests(unittest.TestCase):
    def test_empty_input_returns_no_failures(self) -> None:
        self.assertEqual(GccBuildErrorDetector().scan([], _ctx()), [])


class SingleErrorTests(unittest.TestCase):
    def test_undeclared_identifier_emits_one_record(self) -> None:
        lines = [
            _line(
                10,
                "src/foo.c:42:10: error: 'x' undeclared (first use in this function)",
            )
        ]
        failures = GccBuildErrorDetector().scan(lines, _ctx())

        self.assertEqual(len(failures), 1)
        only = failures[0]
        self.assertEqual(only.type, "build_error_gcc")
        self.assertEqual(only.severity, 3)
        self.assertEqual(only.classification_claim, "root_cause")
        self.assertEqual(only.anchor_type, "build_error_gcc")
        self.assertEqual(only.extracted_fields["language"], "c_cpp")
        self.assertEqual(only.extracted_fields["file_path"], "src/foo.c")
        self.assertEqual(only.extracted_fields["line"], 42)
        self.assertEqual(only.extracted_fields["column"], 10)
        self.assertEqual(only.extracted_fields["severity_text"], "error")
        self.assertIn("'x' undeclared", only.extracted_fields["message"])

    def test_fatal_error_recognized(self) -> None:
        lines = [
            _line(10, "src/foo.c:1:10: fatal error: nonexistent.h: No such file"),
        ]
        failures = GccBuildErrorDetector().scan(lines, _ctx())

        self.assertEqual(len(failures), 1)
        self.assertEqual(
            failures[0].extracted_fields["severity_text"], "fatal error"
        )

    def test_internal_compiler_error_recognized(self) -> None:
        lines = [
            _line(10, "src/foo.cpp:50:1: internal compiler error: in foo, at bar.c:5"),
        ]
        failures = GccBuildErrorDetector().scan(lines, _ctx())

        self.assertEqual(len(failures), 1)
        self.assertEqual(
            failures[0].extracted_fields["severity_text"],
            "internal compiler error",
        )


class MultipleErrorsTests(unittest.TestCase):
    def test_two_gcc_errors_emit_two_records(self) -> None:
        lines = [
            _line(10, "src/a.c:1:1: error: first error"),
            _line(11, "src/b.c:2:2: error: second error"),
        ]
        failures = GccBuildErrorDetector().scan(lines, _ctx())

        self.assertEqual(len(failures), 2)
        files = sorted(f.extracted_fields["file_path"] for f in failures)
        self.assertEqual(files, ["src/a.c", "src/b.c"])


class CaretContinuationTests(unittest.TestCase):
    def test_caret_continuation_captured_in_suggested_range(self) -> None:
        lines = [
            _line(10, "src/foo.c:42:10: error: 'x' undeclared"),
            _line(11, "   42 |     return x;"),
            _line(12, "      |            ^"),
            _line(
                13,
                "src/foo.c:42:10: note: each undeclared identifier is reported only once",
            ),
            _line(20, "unrelated content"),
        ]
        failures = GccBuildErrorDetector().scan(lines, _ctx())

        # The note line on 13 is itself a continuation. The error is on line 10.
        suggested = failures[0].suggested_block_range
        assert suggested is not None
        self.assertEqual(suggested[0], 10)
        # Continuation should extend at least through the indented caret lines.
        self.assertGreaterEqual(suggested[1], 12)


class GoCoordinationTests(unittest.TestCase):
    def test_go_style_file_line_col_does_not_fire_gcc(self) -> None:
        # ``foo.go:1:1: error: ...`` shape is owned by GoBuildErrorDetector.
        # GCC's negative lookahead must reject it.
        lines = [_line(10, "./pkg/foo.go:42:5: undefined: SomeFunc")]
        failures = GccBuildErrorDetector().scan(lines, _ctx())
        self.assertEqual(failures, [])

    def test_non_go_extension_with_go_in_name_is_still_matched(self) -> None:
        # ``foo.cgo.c`` -- not a Go file. Make sure the lookahead doesn't
        # over-reject substrings.
        lines = [_line(10, "src/foo.cgo.c:1:1: error: bad")]
        failures = GccBuildErrorDetector().scan(lines, _ctx())
        self.assertEqual(len(failures), 1)
        self.assertEqual(failures[0].extracted_fields["file_path"], "src/foo.cgo.c")


class WarningExclusionTests(unittest.TestCase):
    def test_warning_lines_do_not_fire(self) -> None:
        # Warnings are NOT in scope -- they would over-fire on routine builds.
        lines = [_line(10, "src/foo.c:1:1: warning: implicit declaration")]
        failures = GccBuildErrorDetector().scan(lines, _ctx())
        self.assertEqual(failures, [])


class TimestampPrefixToleranceTests(unittest.TestCase):
    def test_gha_prefixed_gcc_error_still_matches(self) -> None:
        lines = [
            _line(
                10,
                "2024-01-15T12:34:56.789Z src/foo.c:42:10: error: 'x' undeclared",
            )
        ]
        failures = GccBuildErrorDetector().scan(lines, _ctx())

        self.assertEqual(len(failures), 1)
        self.assertEqual(failures[0].extracted_fields["file_path"], "src/foo.c")
        self.assertEqual(failures[0].extracted_fields["line"], 42)


if __name__ == "__main__":
    unittest.main()
