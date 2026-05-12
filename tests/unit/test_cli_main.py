from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from ci_log_intelligence.cli.main import build_parser, main


SAMPLE_LOG = "\n".join(
    [
        "npm install",
        "added 100 packages",
        "npm ERR! code ELIFECYCLE",
        "npm ERR! errno 1",
        "FAILED tests/test_foo.py::test_bar - AssertionError: 1 != 2",
        "Traceback (most recent call last):",
        '  File "tests/test_foo.py", line 12, in test_bar',
        "    assert 1 == 2",
        "AssertionError: 1 != 2",
    ]
)


class TestBuildParser(unittest.TestCase):
    def test_file_and_url_are_mutually_exclusive(self):
        parser = build_parser()
        with self.assertRaises(SystemExit):
            parser.parse_args(
                ["analyze", "--url", "https://example.com", "--file", "/tmp/x.log"]
            )

    def test_one_of_file_or_url_is_required(self):
        parser = build_parser()
        with self.assertRaises(SystemExit):
            parser.parse_args(["analyze"])

    def test_file_flag_parses(self):
        parser = build_parser()
        args = parser.parse_args(["analyze", "--file", "/tmp/x.log"])
        self.assertEqual(args.file, "/tmp/x.log")
        self.assertIsNone(args.url)

    def test_url_flag_parses(self):
        parser = build_parser()
        args = parser.parse_args(["analyze", "--url", "https://example.com"])
        self.assertEqual(args.url, "https://example.com")
        self.assertIsNone(args.file)


class TestAnalyzeFile(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.NamedTemporaryFile(
            "w", suffix=".log", delete=False, encoding="utf-8"
        )
        self.tmp.write(SAMPLE_LOG)
        self.tmp.flush()
        self.tmp.close()
        self.path = self.tmp.name

    def tearDown(self) -> None:
        Path(self.path).unlink(missing_ok=True)

    def test_file_mode_human_output_lists_failures(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            exit_code = main(["analyze", "--file", self.path])
        self.assertEqual(exit_code, 0)
        out = buf.getvalue()
        self.assertIn("Summary:", out)
        self.assertIn("Top failure blocks:", out)
        self.assertIn("Detected failures:", out)
        self.assertIn("pytest_fail", out)
        self.assertIn("build_error_npm", out)
        self.assertIn("Metadata: blocks=", out)

    def test_file_mode_json_output_is_valid_json(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            exit_code = main(["analyze", "--file", self.path, "--json"])
        self.assertEqual(exit_code, 0)
        payload = json.loads(buf.getvalue())
        self.assertIn("blocks", payload)
        self.assertIn("detected_failures", payload)
        self.assertIn("summary", payload)
        self.assertGreater(len(payload["blocks"]), 0)
        block = payload["blocks"][0]
        self.assertIn("classification", block)
        self.assertIn("score", block)
        self.assertIn("lines", block)
        self.assertIn("start_line", block)

    def test_file_mode_stdin(self):
        buf = io.StringIO()
        with mock.patch("sys.stdin", io.StringIO(SAMPLE_LOG)):
            with redirect_stdout(buf):
                exit_code = main(["analyze", "--file", "-"])
        self.assertEqual(exit_code, 0)
        self.assertIn("Summary:", buf.getvalue())


class TestAnalyzeUrlDispatch(unittest.TestCase):
    def test_url_mode_calls_analyze_ci_url(self):
        with mock.patch(
            "ci_log_intelligence.cli.main.analyze_ci_url"
        ) as fake_analyze:
            fake_report = mock.MagicMock()
            fake_report.root_cause.summary = "x"
            fake_report.root_cause.log_excerpt = ""
            fake_report.failures = []
            fake_report.passed_context = []
            fake_report.cross_run_insights = []
            fake_report.metadata.total_runs_analyzed = 0
            fake_report.metadata.failed_runs = 0
            fake_report.metadata.passed_runs = 0
            fake_analyze.return_value = fake_report

            buf = io.StringIO()
            with redirect_stdout(buf):
                exit_code = main(["analyze", "--url", "https://example.com"])

            self.assertEqual(exit_code, 0)
            fake_analyze.assert_called_once()
            self.assertEqual(fake_analyze.call_args.args[0], "https://example.com")


if __name__ == "__main__":
    unittest.main()
