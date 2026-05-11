from __future__ import annotations

import unittest

from ci_log_intelligence.models import ParsedLine
from ci_log_intelligence.reducer.detectors import (
    DetectedFailure,
    GenericDetector,
    JobContext,
    detected_failures_to_anchors,
    get_detectors,
    run_detectors,
)


def _make_line(line_number: int, content: str) -> ParsedLine:
    return ParsedLine(line_number, content, None, "build", [])


def _empty_context() -> JobContext:
    return JobContext(job_name=None, run_id=None, repo=None)


class DetectorFrameworkEmptyInputTests(unittest.TestCase):
    def test_empty_input_returns_empty_failures_and_anchors(self) -> None:
        failures = run_detectors([], _empty_context())
        anchors = detected_failures_to_anchors(failures)

        self.assertEqual(failures, [])
        self.assertEqual(anchors, [])


class DetectorFrameworkSingleSignalTests(unittest.TestCase):
    def test_single_line_single_signal_emits_one_failure_one_anchor(self) -> None:
        lines = [_make_line(1, "ERROR something broke")]

        failures = run_detectors(lines, _empty_context())
        anchors = detected_failures_to_anchors(failures)

        self.assertEqual(len(failures), 1)
        only_failure = failures[0]
        self.assertEqual(only_failure.type, "generic")
        self.assertEqual(only_failure.anchor_lines, [1])
        self.assertEqual(only_failure.severity, 3)
        self.assertEqual(only_failure.extracted_fields, {"signal_name": "error"})
        self.assertIsNone(only_failure.classification_claim)
        self.assertIsNone(only_failure.suggested_block_range)

        self.assertEqual(len(anchors), 1)
        self.assertEqual(anchors[0].line_number, 1)
        self.assertEqual(anchors[0].type, "error")
        self.assertEqual(anchors[0].severity, 3)

    def test_generic_detector_emits_anchor_type_per_signal(self) -> None:
        lines = [
            _make_line(1, "Traceback (most recent call last):"),
            _make_line(2, "FAILED something"),
        ]

        failures = run_detectors(lines, _empty_context())

        # Every generic failure carries ``type="generic"`` but distinct ``anchor_type`` values.
        self.assertEqual({f.type for f in failures}, {"generic"})
        self.assertEqual(
            [f.anchor_type for f in failures],
            ["traceback", "failed"],
        )


class DetectorFrameworkMultiSignalLineTests(unittest.TestCase):
    def test_single_line_multiple_signals_emits_multiple_failures(self) -> None:
        lines = [_make_line(7, "ERROR FAILED AssertionError happened")]

        failures = run_detectors(lines, _empty_context())
        anchors = detected_failures_to_anchors(failures)

        signal_names = [f.extracted_fields["signal_name"] for f in failures]
        self.assertEqual(signal_names, ["error", "failed", "assertion_error"])
        for failure in failures:
            self.assertEqual(failure.anchor_lines, [7])
            self.assertEqual(failure.type, "generic")

        anchor_keys = [(a.line_number, a.type, a.severity) for a in anchors]
        self.assertEqual(
            anchor_keys,
            [(7, "error", 3), (7, "failed", 2), (7, "assertion_error", 2)],
        )


class DetectorFrameworkHardenedPatternTests(unittest.TestCase):
    def test_exception_word_boundary_skips_identifiers(self) -> None:
        lines = [
            _make_line(1, "class MyExceptionHandler: pass"),
            _make_line(2, "OperationsExceptional was called"),
        ]

        failures = run_detectors(lines, _empty_context())

        self.assertEqual(failures, [])

    def test_error_keyword_is_case_insensitive(self) -> None:
        lines = [
            _make_line(1, "Error: file not found"),
            _make_line(2, "error: file not found"),
            _make_line(3, "ERROR: file not found"),
        ]

        failures = run_detectors(lines, _empty_context())

        self.assertEqual(len(failures), 3)
        self.assertTrue(
            all(f.extracted_fields["signal_name"] == "error" for f in failures)
        )

    def test_error_word_boundary_skips_identifiers(self) -> None:
        lines = [_make_line(1, "ErrorContext was constructed by errorBuilder")]

        failures = run_detectors(lines, _empty_context())

        # ``Error`` here is part of CamelCase identifiers -- the ``\b`` boundary
        # should not promote that to a standalone ``ERROR`` keyword match.
        self.assertEqual(failures, [])

    def test_benign_no_errors_phrase_suppresses_error_anchor(self) -> None:
        lines = [
            _make_line(1, "[INFO] No errors found"),
            _make_line(2, "[INFO] No error in build"),
            _make_line(3, "0 error reports"),
        ]

        failures = run_detectors(lines, _empty_context())

        self.assertEqual(failures, [])

    def test_benign_zero_failures_phrase_suppresses_failed_anchor(self) -> None:
        lines = [
            _make_line(1, "0 failures, 0 errors"),
            _make_line(2, "errors: 0"),
            _make_line(3, "no failures detected this run"),
        ]

        failures = run_detectors(lines, _empty_context())

        self.assertEqual(failures, [])

    def test_benign_filter_only_suppresses_error_failed_warning_signals(self) -> None:
        # ``Traceback``/``Exception``/``AssertionError``/``Retrying`` signals
        # must still anchor even when the line also matches the benign filter,
        # because real Python tracebacks never appear inside zero-count reports
        # but the tokens are unambiguous when they do appear.
        lines = [
            _make_line(
                1,
                "Traceback (most recent call last): Exception with 0 errors AssertionError",
            )
        ]

        failures = run_detectors(lines, _empty_context())

        signal_names = sorted(f.extracted_fields["signal_name"] for f in failures)
        self.assertEqual(
            signal_names, ["assertion_error", "exception", "traceback"]
        )

    def test_failed_keyword_is_case_insensitive(self) -> None:
        lines = [
            _make_line(1, "test Failed for reason X"),
            _make_line(2, "build failed: missing dep"),
        ]

        failures = run_detectors(lines, _empty_context())

        self.assertEqual(len(failures), 2)
        self.assertTrue(
            all(f.extracted_fields["signal_name"] == "failed" for f in failures)
        )


class DetectorFrameworkRegistryTests(unittest.TestCase):
    def test_registry_contains_generic_detector(self) -> None:
        detectors = get_detectors()

        self.assertEqual(len(detectors), 1)
        self.assertIsInstance(detectors[0], GenericDetector)
        self.assertEqual(detectors[0].name, "generic")
        self.assertEqual(detectors[0].failure_type, "generic")

    def test_run_detectors_accepts_explicit_detector_sequence(self) -> None:
        lines = [_make_line(1, "ERROR x")]

        failures = run_detectors(lines, _empty_context(), detectors=[GenericDetector()])

        self.assertEqual(len(failures), 1)
        self.assertIsInstance(failures[0], DetectedFailure)


if __name__ == "__main__":
    unittest.main()
