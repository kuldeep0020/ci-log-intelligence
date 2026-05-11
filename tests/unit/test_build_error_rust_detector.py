from __future__ import annotations

import unittest

from ci_log_intelligence.models import ParsedLine
from ci_log_intelligence.reducer.detectors import JobContext
from ci_log_intelligence.reducer.detectors.build_error_rust import (
    RustBuildErrorDetector,
)


def _line(line_number: int, content: str, step_id: str | None = "build") -> ParsedLine:
    return ParsedLine(line_number, content, None, step_id, [])


def _ctx() -> JobContext:
    return JobContext(job_name=None, run_id=None, repo=None)


class EmptyInputTests(unittest.TestCase):
    def test_empty_input_returns_no_failures(self) -> None:
        self.assertEqual(RustBuildErrorDetector().scan([], _ctx()), [])


class SingleErrorTests(unittest.TestCase):
    def test_coded_error_emits_one_record(self) -> None:
        lines = [
            _line(10, "error[E0382]: borrow of moved value: `s`"),
            _line(11, "  --> src/main.rs:5:20"),
        ]
        failures = RustBuildErrorDetector().scan(lines, _ctx())

        self.assertEqual(len(failures), 1)
        only = failures[0]
        self.assertEqual(only.type, "build_error_rust")
        self.assertEqual(only.severity, 3)
        self.assertEqual(only.classification_claim, "root_cause")
        self.assertEqual(only.anchor_type, "build_error_rust")
        self.assertEqual(only.extracted_fields["language"], "rust")
        self.assertEqual(only.extracted_fields["error_code"], "E0382")
        self.assertEqual(
            only.extracted_fields["message"], "borrow of moved value: `s`"
        )
        self.assertEqual(only.extracted_fields["file_path"], "src/main.rs")
        self.assertEqual(only.extracted_fields["line"], 5)
        self.assertEqual(only.extracted_fields["column"], 20)

    def test_coded_error_without_arrow_omits_location_fields(self) -> None:
        lines = [_line(10, "error[E0382]: borrow of moved value")]
        failures = RustBuildErrorDetector().scan(lines, _ctx())

        self.assertEqual(len(failures), 1)
        self.assertNotIn("file_path", failures[0].extracted_fields)
        self.assertNotIn("line", failures[0].extracted_fields)
        self.assertNotIn("column", failures[0].extracted_fields)


class MultipleErrorsTests(unittest.TestCase):
    def test_two_errors_emit_two_records(self) -> None:
        lines = [
            _line(10, "error[E0382]: borrow of moved value: `s`"),
            _line(11, "  --> src/main.rs:5:20"),
            _line(20, "error[E0425]: cannot find value `q` in this scope"),
            _line(21, "  --> src/main.rs:7:5"),
        ]
        failures = RustBuildErrorDetector().scan(lines, _ctx())

        self.assertEqual(len(failures), 2)
        codes = sorted(f.extracted_fields["error_code"] for f in failures)
        self.assertEqual(codes, ["E0382", "E0425"])


class BareCargoFormTests(unittest.TestCase):
    def test_could_not_compile_summary_emits_record(self) -> None:
        lines = [
            _line(
                10,
                'error: could not compile `my-crate` (bin "my-crate") due to 1 previous error',
            ),
        ]
        failures = RustBuildErrorDetector().scan(lines, _ctx())

        self.assertEqual(len(failures), 1)
        self.assertEqual(failures[0].type, "build_error_rust")
        self.assertNotIn("error_code", failures[0].extracted_fields)
        self.assertIn("could not compile", failures[0].extracted_fields["message"])

    def test_aborting_due_to_summary_emits_record(self) -> None:
        lines = [_line(10, "error: aborting due to previous error")]
        failures = RustBuildErrorDetector().scan(lines, _ctx())
        self.assertEqual(len(failures), 1)
        self.assertEqual(failures[0].extracted_fields["language"], "rust")

    def test_generic_error_message_without_cargo_keyword_is_skipped(self) -> None:
        # ``error: missing module`` is a generic tool error -- belongs to the
        # GenericDetector signal stream, not to RustBuildErrorDetector.
        lines = [_line(10, "error: missing module")]
        failures = RustBuildErrorDetector().scan(lines, _ctx())
        self.assertEqual(failures, [])

    def test_bare_error_with_arrow_following_emits_record(self) -> None:
        # rustc internal error path: bare ``error:`` followed by ``-->``.
        lines = [
            _line(10, "error: internal compiler error: something bad"),
            _line(11, "  --> src/lib.rs:1:1"),
        ]
        failures = RustBuildErrorDetector().scan(lines, _ctx())
        self.assertEqual(len(failures), 1)
        self.assertEqual(failures[0].extracted_fields["file_path"], "src/lib.rs")


class SuggestedRangeTests(unittest.TestCase):
    def test_diagnostic_continuation_captured_in_suggested_range(self) -> None:
        lines = [
            _line(10, "error[E0382]: borrow of moved value: `s`"),
            _line(11, "  --> src/main.rs:5:20"),
            _line(12, "   |"),
            _line(13, "3  |     let s = String::from(\"hello\");"),
            _line(14, "   |         - move occurs because `s` has type `String`"),
            _line(15, "   = note: see chapter on ownership"),
            _line(20, "other unrelated content"),  # ends the continuation
        ]
        failures = RustBuildErrorDetector().scan(lines, _ctx())

        self.assertEqual(len(failures), 1)
        suggested = failures[0].suggested_block_range
        self.assertIsNotNone(suggested)
        assert suggested is not None
        self.assertEqual(suggested[0], 10)
        # Last continuation is line 15; line 20 breaks the run.
        self.assertEqual(suggested[1], 15)

    def test_continuation_cap_at_30_lines(self) -> None:
        # 30 contiguous continuation lines -- the cap.
        lines = [_line(10, "error[E0382]: msg")]
        for i in range(11, 60):
            lines.append(_line(i, "   | continuation"))
        failures = RustBuildErrorDetector().scan(lines, _ctx())

        suggested = failures[0].suggested_block_range
        assert suggested is not None
        self.assertEqual(suggested[0], 10)
        # Cap caps last line at error_line + 30.
        self.assertLessEqual(suggested[1] - suggested[0], 30)


class TimestampPrefixToleranceTests(unittest.TestCase):
    def test_gha_prefixed_error_line_still_matches(self) -> None:
        lines = [
            _line(
                10,
                "2024-01-15T12:34:56.789Z error[E0382]: borrow of moved value: `s`",
            ),
            _line(11, "2024-01-15T12:34:56.800Z   --> src/main.rs:5:20"),
        ]
        failures = RustBuildErrorDetector().scan(lines, _ctx())

        self.assertEqual(len(failures), 1)
        self.assertEqual(failures[0].extracted_fields["error_code"], "E0382")
        self.assertEqual(failures[0].extracted_fields["file_path"], "src/main.rs")


class CoordinationEdgeCaseTests(unittest.TestCase):
    def test_step_boundary_terminates_continuation(self) -> None:
        # Continuation across step_ids must terminate.
        lines = [
            _line(10, "error[E0382]: msg", step_id="build-a"),
            _line(11, "  --> src/main.rs:5:20", step_id="build-a"),
            _line(12, "   | continuation in different step", step_id="build-b"),
        ]
        failures = RustBuildErrorDetector().scan(lines, _ctx())
        # Continuation in different step does not extend the suggested range
        # past the same-step lines. The ``-->`` line is line 11.
        suggested = failures[0].suggested_block_range
        assert suggested is not None
        self.assertEqual(suggested, (10, 11))


if __name__ == "__main__":
    unittest.main()
