from __future__ import annotations

import unittest

from ci_log_intelligence.models import ParsedLine
from ci_log_intelligence.reducer.detectors import JobContext
from ci_log_intelligence.reducer.detectors.build_error_npm import (
    NpmBuildErrorDetector,
)


def _line(line_number: int, content: str, step_id: str | None = "build") -> ParsedLine:
    return ParsedLine(line_number, content, None, step_id, [])


def _ctx() -> JobContext:
    return JobContext(job_name=None, run_id=None, repo=None)


class EmptyInputTests(unittest.TestCase):
    def test_empty_input_returns_no_failures(self) -> None:
        self.assertEqual(NpmBuildErrorDetector().scan([], _ctx()), [])


class SingleBlockTests(unittest.TestCase):
    def test_npm_block_emits_one_record(self) -> None:
        lines = [
            _line(10, "npm ERR! code ELIFECYCLE"),
            _line(11, "npm ERR! errno 1"),
            _line(12, "npm ERR! my-app@1.0.0 build: `webpack`"),
            _line(13, "npm ERR! Exit status 1"),
        ]
        failures = NpmBuildErrorDetector().scan(lines, _ctx())

        self.assertEqual(len(failures), 1)
        only = failures[0]
        self.assertEqual(only.type, "build_error_npm")
        self.assertEqual(only.severity, 3)
        self.assertEqual(only.classification_claim, "root_cause")
        self.assertEqual(only.anchor_type, "build_error_npm")
        self.assertEqual(only.extracted_fields["language"], "javascript")
        self.assertEqual(only.extracted_fields["tool"], "npm")
        self.assertEqual(only.extracted_fields["error_code"], "ELIFECYCLE")
        self.assertEqual(only.extracted_fields["errno"], 1)
        # ``message`` is the FIRST line's payload.
        self.assertEqual(only.extracted_fields["message"], "code ELIFECYCLE")
        # anchor_lines is the FIRST line of the block.
        self.assertEqual(only.anchor_lines, [10])
        # suggested_block_range covers the whole block.
        self.assertEqual(only.suggested_block_range, (10, 13))

    def test_yarn_block_uses_yarn_tool(self) -> None:
        lines = [
            _line(10, "yarn error Internal Error: ENOENT"),
            _line(11, "yarn error     at ..."),
        ]
        failures = NpmBuildErrorDetector().scan(lines, _ctx())

        self.assertEqual(len(failures), 1)
        self.assertEqual(failures[0].extracted_fields["tool"], "yarn")
        # No npm-specific metadata.
        self.assertNotIn("error_code", failures[0].extracted_fields)
        self.assertNotIn("errno", failures[0].extracted_fields)


class MultipleBlocksTests(unittest.TestCase):
    def test_two_separated_blocks_emit_two_records(self) -> None:
        lines = [
            _line(10, "npm ERR! code A"),
            _line(11, "npm ERR! Exit status 1"),
            _line(15, "some unrelated noise"),
            _line(20, "npm ERR! code B"),
            _line(21, "npm ERR! Exit status 2"),
        ]
        failures = NpmBuildErrorDetector().scan(lines, _ctx())

        self.assertEqual(len(failures), 2)
        self.assertEqual(failures[0].anchor_lines, [10])
        self.assertEqual(failures[1].anchor_lines, [20])
        self.assertEqual(failures[0].extracted_fields["error_code"], "A")
        self.assertEqual(failures[1].extracted_fields["error_code"], "B")

    def test_single_line_npm_error_no_continuation_omits_suggested_range(self) -> None:
        lines = [_line(10, "npm ERR! something went wrong")]
        failures = NpmBuildErrorDetector().scan(lines, _ctx())

        self.assertEqual(len(failures), 1)
        self.assertIsNone(failures[0].suggested_block_range)


class TimestampPrefixToleranceTests(unittest.TestCase):
    def test_gha_prefixed_npm_line_still_matches(self) -> None:
        lines = [
            _line(10, "2024-01-15T12:34:56.789Z npm ERR! code ELIFECYCLE"),
            _line(11, "2024-01-15T12:34:56.800Z npm ERR! errno 1"),
        ]
        failures = NpmBuildErrorDetector().scan(lines, _ctx())

        self.assertEqual(len(failures), 1)
        self.assertEqual(failures[0].extracted_fields["error_code"], "ELIFECYCLE")
        self.assertEqual(failures[0].extracted_fields["errno"], 1)


class CoordinationEdgeCaseTests(unittest.TestCase):
    def test_yarn_line_does_not_merge_into_npm_block(self) -> None:
        # Mixed npm and yarn prefixes: each becomes its own block.
        lines = [
            _line(10, "npm ERR! code X"),
            _line(11, "yarn error Y"),
        ]
        failures = NpmBuildErrorDetector().scan(lines, _ctx())

        self.assertEqual(len(failures), 2)
        tools = sorted(f.extracted_fields["tool"] for f in failures)
        self.assertEqual(tools, ["npm", "yarn"])

    def test_step_boundary_breaks_block(self) -> None:
        # Same prefix, different step -> two separate blocks.
        lines = [
            _line(10, "npm ERR! code A", step_id="install"),
            _line(11, "npm ERR! code B", step_id="build"),
        ]
        failures = NpmBuildErrorDetector().scan(lines, _ctx())

        self.assertEqual(len(failures), 2)


if __name__ == "__main__":
    unittest.main()
