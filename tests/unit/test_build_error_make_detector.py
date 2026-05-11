from __future__ import annotations

import unittest

from ci_log_intelligence.models import ParsedLine
from ci_log_intelligence.reducer.detectors import JobContext
from ci_log_intelligence.reducer.detectors.build_error_make import (
    MakeBuildErrorDetector,
)


def _line(line_number: int, content: str, step_id: str | None = "build") -> ParsedLine:
    return ParsedLine(line_number, content, None, step_id, [])


def _ctx() -> JobContext:
    return JobContext(job_name=None, run_id=None, repo=None)


class EmptyInputTests(unittest.TestCase):
    def test_empty_input_returns_no_failures(self) -> None:
        self.assertEqual(MakeBuildErrorDetector().scan([], _ctx()), [])


class SingleErrorTests(unittest.TestCase):
    def test_full_form_with_makefile_and_line(self) -> None:
        lines = [_line(10, "make: *** [Makefile:42: build] Error 1")]
        failures = MakeBuildErrorDetector().scan(lines, _ctx())

        self.assertEqual(len(failures), 1)
        only = failures[0]
        self.assertEqual(only.type, "build_error_make")
        self.assertEqual(only.severity, 3)
        self.assertEqual(only.classification_claim, "root_cause")
        self.assertEqual(only.anchor_type, "build_error_make")
        self.assertEqual(only.extracted_fields["language"], "make")
        self.assertEqual(only.extracted_fields["target"], "build")
        self.assertEqual(only.extracted_fields["exit_code"], 1)
        self.assertEqual(only.extracted_fields["makefile"], "Makefile")
        self.assertEqual(only.extracted_fields["makefile_line"], 42)
        self.assertEqual(only.anchor_lines, [10])
        self.assertIsNone(only.suggested_block_range)

    def test_submake_form_with_bracketed_depth(self) -> None:
        lines = [_line(10, "make[1]: *** [tests/Makefile:10: test] Error 2")]
        failures = MakeBuildErrorDetector().scan(lines, _ctx())

        self.assertEqual(len(failures), 1)
        self.assertEqual(failures[0].extracted_fields["target"], "test")
        self.assertEqual(failures[0].extracted_fields["exit_code"], 2)
        self.assertEqual(failures[0].extracted_fields["makefile"], "tests/Makefile")

    def test_old_form_without_makefile_path(self) -> None:
        lines = [_line(10, "make: *** [build] Error 1")]
        failures = MakeBuildErrorDetector().scan(lines, _ctx())

        self.assertEqual(len(failures), 1)
        self.assertEqual(failures[0].extracted_fields["target"], "build")
        self.assertEqual(failures[0].extracted_fields["exit_code"], 1)
        self.assertNotIn("makefile", failures[0].extracted_fields)
        self.assertNotIn("makefile_line", failures[0].extracted_fields)


class MultipleErrorsTests(unittest.TestCase):
    def test_multiple_make_errors_emit_multiple_records(self) -> None:
        lines = [
            _line(10, "make: *** [Makefile:42: build] Error 1"),
            _line(20, "make[1]: *** [tests/Makefile:10: test] Error 2"),
        ]
        failures = MakeBuildErrorDetector().scan(lines, _ctx())

        self.assertEqual(len(failures), 2)
        targets = sorted(f.extracted_fields["target"] for f in failures)
        self.assertEqual(targets, ["build", "test"])


class TimestampPrefixToleranceTests(unittest.TestCase):
    def test_gha_prefixed_make_error_still_matches(self) -> None:
        lines = [
            _line(
                10,
                "2024-01-15T12:34:56.789Z make: *** [Makefile:42: build] Error 1",
            )
        ]
        failures = MakeBuildErrorDetector().scan(lines, _ctx())

        self.assertEqual(len(failures), 1)
        self.assertEqual(failures[0].extracted_fields["target"], "build")
        self.assertEqual(failures[0].extracted_fields["exit_code"], 1)


class CoordinationEdgeCaseTests(unittest.TestCase):
    def test_make_message_without_recipe_error_does_not_match(self) -> None:
        # A different make output shape -- ``Error`` in the middle of a sentence.
        lines = [
            _line(10, "make: Entering directory '/tmp/build'"),
            _line(11, "make: Nothing to be done for 'all'."),
            # Even an ``Error`` keyword without the recipe shape should be ignored.
            _line(12, "make: Error reading input"),
        ]
        failures = MakeBuildErrorDetector().scan(lines, _ctx())
        self.assertEqual(failures, [])

    def test_substring_make_in_other_tool_output_does_not_match(self) -> None:
        # Some other tool emits a line containing "make: ***" -- but not at
        # start-of-line. Anchored regex must reject it.
        lines = [_line(10, "  prefixed make: *** [build] Error 1")]
        failures = MakeBuildErrorDetector().scan(lines, _ctx())
        self.assertEqual(failures, [])


if __name__ == "__main__":
    unittest.main()
