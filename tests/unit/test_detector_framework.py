from __future__ import annotations

import unittest

from ci_log_intelligence.models import ParsedLine
from ci_log_intelligence.reducer.anchors import detect_anchors
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


def _normalized_anchor_keys(anchors):
    keys = [(a.line_number, a.type, a.severity) for a in anchors]
    return sorted(keys, key=lambda item: (item[0], -item[2], item[1]))


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

    def test_repeated_token_on_same_line_emits_single_anchor(self) -> None:
        lines = [_make_line(1, "ERROR ERROR ERROR")]

        failures = run_detectors(lines, _empty_context())
        anchors = detected_failures_to_anchors(failures)

        # ``re.search`` matches once per pattern per line; both legacy and new path must agree.
        legacy_anchors = detect_anchors(lines)
        self.assertEqual(len(anchors), len(legacy_anchors))
        self.assertEqual(len(anchors), 1)
        self.assertEqual(anchors[0].type, "error")


class DetectorFrameworkAllTiersTests(unittest.TestCase):
    def test_all_seven_tiers_match_legacy_detect_anchors_counts(self) -> None:
        lines = [
            _make_line(1, "Traceback (most recent call last):"),
            _make_line(2, "Exception raised in module"),
            _make_line(3, "ERROR build failed"),
            _make_line(4, "FAILED test_example"),
            _make_line(5, "AssertionError mismatch"),
            _make_line(6, "WARNING flaky cache"),
            _make_line(7, "Retrying request"),
        ]

        legacy_anchors = detect_anchors(lines)
        failures = run_detectors(lines, _empty_context())
        new_anchors = detected_failures_to_anchors(failures)

        self.assertEqual(len(new_anchors), len(legacy_anchors))
        self.assertEqual(
            _normalized_anchor_keys(new_anchors),
            _normalized_anchor_keys(legacy_anchors),
        )


class DetectorFrameworkParityTests(unittest.TestCase):
    def test_parity_with_legacy_detect_anchors_on_mixed_input(self) -> None:
        lines = [
            _make_line(1, "starting build"),
            _make_line(2, "Traceback (most recent call last):"),
            _make_line(3, "  File 'foo.py', line 10, in bar"),
            _make_line(4, "Exception: bad input"),
            _make_line(5, "ERROR could not read config"),
            _make_line(6, "FAILED test_alpha"),
            _make_line(7, "AssertionError: 1 != 2"),
            _make_line(8, "WARNING deprecated flag"),
            _make_line(9, "Retrying request after timeout"),
            _make_line(10, "informational output"),
            _make_line(11, "ERROR FAILED AssertionError combined"),
            _make_line(12, "Exception while Retrying"),
            _make_line(13, "WARNING and ERROR on same line"),
            _make_line(14, "clean line"),
            _make_line(15, "another clean line"),
            _make_line(16, "Traceback (most recent call last): with Exception"),
            _make_line(17, "FAILED again"),
            _make_line(18, "ERROR"),
            _make_line(19, "Retrying"),
            _make_line(20, "done"),
        ]

        legacy_anchors = detect_anchors(lines)
        failures = run_detectors(lines, _empty_context())
        new_anchors = detected_failures_to_anchors(failures)

        self.assertEqual(len(new_anchors), len(legacy_anchors))

        sorted_legacy = _normalized_anchor_keys(legacy_anchors)
        sorted_new = _normalized_anchor_keys(new_anchors)
        self.assertEqual(sorted_new, sorted_legacy)
        for new_key, legacy_key in zip(sorted_new, sorted_legacy):
            self.assertEqual(new_key, legacy_key)


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
