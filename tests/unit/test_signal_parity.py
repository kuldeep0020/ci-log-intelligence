from __future__ import annotations

import unittest

from ci_log_intelligence.models import ParsedLine
from ci_log_intelligence.parsing import detect_signals
from ci_log_intelligence.reducer.detectors import JobContext, run_detectors
from ci_log_intelligence.reducer.detectors.generic import GenericDetector


def _line(line_number: int, content: str) -> ParsedLine:
    return ParsedLine(line_number, content, None, None, [])


class SignalAnchorParityTests(unittest.TestCase):
    def _signals_for(self, content: str) -> set[str]:
        return set(detect_signals(content))

    def _anchor_signals_for(self, content: str) -> set[str]:
        failures = run_detectors([_line(1, content)], JobContext(None, None, None))
        anchor_types: set[str] = set()
        for failure in failures:
            anchor_types.add(failure.anchor_type or failure.type)
        return anchor_types

    def test_mixed_case_error_parity(self) -> None:
        cases = [
            "ERROR build failed",
            "error: missing module",
            "Error: file not found",
            "Some prose with error in the middle",
            "WARNING transient error",      # the bug case
        ]
        for content in cases:
            with self.subTest(content=content):
                self.assertEqual(
                    self._signals_for(content),
                    self._anchor_signals_for(content),
                    f"signal/anchor mismatch for: {content!r}",
                )

    def test_benign_mentions_filtered_in_both(self) -> None:
        cases = [
            "[INFO] No errors found",
            "0 errors",
            "errors: 0",
            "0 failures",
            "no failures",
        ]
        for content in cases:
            with self.subTest(content=content):
                self.assertEqual(self._signals_for(content), set())
                self.assertEqual(self._anchor_signals_for(content), set())

    def test_word_boundary_in_both(self) -> None:
        cases = [
            "ErrorContext was used",
            "MyExceptionHandler invoked",
            "OperationsExceptional ran",
        ]
        for content in cases:
            with self.subTest(content=content):
                self.assertEqual(self._signals_for(content), set())
                self.assertEqual(self._anchor_signals_for(content), set())


if __name__ == "__main__":
    unittest.main()
