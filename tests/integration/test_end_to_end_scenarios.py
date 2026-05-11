"""End-to-end integration tests for the detector framework pipeline.

Scenario-shaped tests that exercise the full ``parse -> reduce ->
build_report`` flow against realistic synthetic CI logs.

This module covers the detector-layer scenarios:

* A1 -- hash-mismatch primary use case (paired + unpaired detections, with
  ``JobContext`` threading the warehouse target through to the typed record).
* A2 -- multi-detector co-existence: build error, go test failure, pytest
  failure, and a bare ``ERROR`` line in the same log.
* A5 -- anchor-centric excerpt skips benign zero-count lines and ranges
  around the traceback anchor rather than the head of the block.

The MCP-tool sequencing scenarios (A3 cache behavior, A4 filter + top_k)
live in ``test_end_to_end_mcp_scenarios.py`` to keep each file under the
400-LOC cap.
"""

from __future__ import annotations

import unittest

from ci_log_intelligence import analyze_log
from ci_log_intelligence.reducer.comparison import select_root_cause

from ._e2e_helpers import build_single_report


# ---------------------------------------------------------------------------
# A1 -- Hash-mismatch primary use case (happy path)
# ---------------------------------------------------------------------------


# A 60-line GHA-prefixed log with:
#   line 1:  STEP marker (integration-test)
#   lines 4-5:   pair 1 (hash mismatch + FAIL)
#   lines 8-9:   pair 2 (hash mismatch + FAIL)
#   lines 12-13: pair 3 (hash mismatch + FAIL)
#   line 31: second STEP marker (cleanup-step)
#   line 40: unpaired hash mismatch (no FAIL in this step;
#            pair 3's FAIL is in a different step, so pairing rejects).
def _ts(seconds: float) -> str:
    """Render a GHA-style ISO timestamp ``YYYY-MM-DDTHH:MM:SS.sssZ``."""
    base_seconds = 56.0 + seconds
    return f"2024-01-15T12:34:{base_seconds:06.3f}Z"


def _build_hash_mismatch_60_line_log() -> str:
    """Construct the 60-line GHA-prefixed fixture for A1.

    Keeping construction in a helper rather than a hand-written string keeps
    the line-number invariants enforceable: any test that asserts on exact
    line positions can read the constant definitions below to stay in sync.
    """
    lines: list[str] = []
    # Line 1 -- STEP marker.
    lines.append("STEP: integration-test")
    # Lines 2-3 -- setup output.
    lines.append(f"{_ts(0.1)} setting up postgres connection")
    lines.append(f"{_ts(0.2)} connection ready")
    # Lines 4-5 -- pair 1: hash mismatch + nearby FAIL.
    lines.append(
        f"{_ts(0.3)} common.go:1058: file hashes don't match for "
        "/tmp/Material_A.yaml and ../samples/Material_A_HASH_1.yaml"
    )
    lines.append(f"{_ts(0.4)} --- FAIL: TestRunSetPartialAlpha (1.0s)")
    # Lines 6-7 -- filler.
    lines.append(f"{_ts(0.5)} resuming")
    lines.append(f"{_ts(0.6)} continuing")
    # Lines 8-9 -- pair 2.
    lines.append(
        f"{_ts(0.7)} common.go:1058: file hashes don't match for "
        "/tmp/Material_B.yaml and ../samples/Material_B_HASH_1.yaml"
    )
    lines.append(f"{_ts(0.8)} --- FAIL: TestRunSetPartialBeta (2.0s)")
    # Lines 10-11 -- filler.
    lines.append(f"{_ts(0.9)} resuming")
    lines.append(f"{_ts(1.0)} continuing")
    # Lines 12-13 -- pair 3.
    lines.append(
        f"{_ts(1.1)} common.go:1058: file hashes don't match for "
        "/tmp/Material_C.yaml and ../samples/Material_C_HASH_1.yaml"
    )
    lines.append(f"{_ts(1.2)} --- FAIL: TestRunSetPartialGamma (3.0s)")
    # Lines 14-30 -- filler keeping the unpaired mismatch outside the
    # merge-window of pair 3; the second STEP marker on line 31 closes
    # the step for pairing purposes.
    for index in range(17):
        lines.append(f"{_ts(1.3 + 0.01 * index)} filler integration-test line {index}")
    # Line 31 -- STEP marker for cleanup. Step transition means the
    # unpaired mismatch (later in this step) cannot pair with any FAIL
    # from the previous step.
    lines.append("STEP: cleanup-step")
    # Lines 32-39 -- cleanup output.
    for index in range(8):
        lines.append(f"{_ts(2.0 + 0.01 * index)} cleanup step line {index}")
    # Line 40 -- UNPAIRED hash mismatch.
    lines.append(
        f"{_ts(2.1)} common.go:1058: file hashes don't match for "
        "/tmp/Material_Z.yaml and ../samples/Material_Z_HASH_1.yaml"
    )
    # Lines 41-60 -- trailing cleanup filler.
    for index in range(20):
        lines.append(f"{_ts(2.2 + 0.01 * index)} cleanup tail line {index}")
    assert len(lines) == 60, f"fixture length drift: {len(lines)} (expected 60)"
    return "\n".join(lines) + "\n"


class HashMismatchPrimaryUseCaseTests(unittest.TestCase):
    """A1: 3 paired + 1 unpaired hash mismatches surface as 4 typed detections."""

    HASH_FIXTURE = _build_hash_mismatch_60_line_log()
    JOB_NAME = "postgres-test (bundling)"

    def test_detected_failures_contain_three_paired_and_one_unpaired_hash_mismatch(self) -> None:
        result, _report, _analysis = build_single_report(
            self.HASH_FIXTURE, job_name=self.JOB_NAME
        )

        hash_failures = [
            failure
            for failure in result.detected_failures
            if failure.type == "hash_mismatch"
        ]
        self.assertEqual(
            len(hash_failures), 4,
            "expected 3 paired + 1 unpaired hash_mismatch detections",
        )

        paired = [f for f in hash_failures if "test_name" in f.extracted_fields]
        unpaired = [f for f in hash_failures if "test_name" not in f.extracted_fields]

        self.assertEqual(len(paired), 3, "expected 3 paired detections")
        self.assertEqual(len(unpaired), 1, "expected 1 unpaired detection")

        expected_names = {
            "TestRunSetPartialAlpha",
            "TestRunSetPartialBeta",
            "TestRunSetPartialGamma",
        }
        for failure in paired:
            self.assertEqual(failure.extracted_fields["warehouse_target"], "postgres")
            self.assertIn(failure.extracted_fields["test_name"], expected_names)

        # Unpaired retains warehouse_target but not test_name.
        self.assertEqual(unpaired[0].extracted_fields["warehouse_target"], "postgres")
        self.assertNotIn("test_name", unpaired[0].extracted_fields)

    def test_failure_records_are_root_cause_and_severity_two(self) -> None:
        _result, report, _analysis = build_single_report(
            self.HASH_FIXTURE, job_name=self.JOB_NAME
        )

        hash_records = [r for r in report.failures if r.type == "hash_mismatch"]
        self.assertGreaterEqual(
            len(hash_records), 1,
            "expected at least one hash_mismatch FailureRecord in report",
        )

        for record in hash_records:
            self.assertEqual(record.classification, "root_cause")
            self.assertEqual(
                record.severity, 2,
                f"hash_mismatch FailureRecord severity should be 2, got {record.severity}",
            )

        # Sorted in score-descending order at the report level.
        scores = [record.score for record in report.failures]
        self.assertEqual(scores, sorted(scores, reverse=True))


# ---------------------------------------------------------------------------
# A2 -- Multi-detector co-existence
# ---------------------------------------------------------------------------


MULTI_DETECTOR_LOG = """\
STEP: cargo-build
2024-01-15T12:34:56.789Z compiling my-crate
2024-01-15T12:34:57.000Z error[E0382]: borrow of moved value: `s`
2024-01-15T12:34:57.100Z   --> src/main.rs:5:20
2024-01-15T12:34:57.200Z    |
2024-01-15T12:34:57.300Z 3  |     let s = String::from("hello");
STEP: go-test
2024-01-15T12:35:00.000Z running go integration tests
2024-01-15T12:35:00.100Z --- FAIL: TestSomething (1.5s)
2024-01-15T12:35:00.200Z FAIL\tgithub.com/owner/repo/pkg\t1.5s
STEP: pytest
2024-01-15T12:35:10.000Z collecting tests
2024-01-15T12:35:10.100Z ===== FAILURES =====
2024-01-15T12:35:10.200Z _______________________ test_thing _______________________
2024-01-15T12:35:10.300Z   def test_thing():
2024-01-15T12:35:10.400Z >    assert 1 == 2
2024-01-15T12:35:10.500Z E    AssertionError: assert 1 == 2
2024-01-15T12:35:10.600Z tests/test_mod.py:9: AssertionError
2024-01-15T12:35:10.700Z ===== short test summary =====
2024-01-15T12:35:10.800Z FAILED tests/test_mod.py::test_thing - AssertionError: assert 1 == 2
STEP: misc
2024-01-15T12:35:20.000Z some unrelated phase
2024-01-15T12:35:20.100Z noise filler line
2024-01-15T12:35:20.200Z noise filler line
2024-01-15T12:35:20.300Z noise filler line
2024-01-15T12:35:20.400Z noise filler line
2024-01-15T12:35:20.500Z ERROR something happened on its own
2024-01-15T12:35:20.600Z trailing tail line
"""


class MultiDetectorCoexistenceTests(unittest.TestCase):
    """A2: each detector emits its own typed record; build error outranks tests."""

    def test_each_detector_emits_its_own_typed_failure_record(self) -> None:
        result, report, _analysis = build_single_report(MULTI_DETECTOR_LOG)

        types_detected = {f.type for f in result.detected_failures}
        for expected in {"build_error_rust", "go_test_fail", "pytest_fail", "generic"}:
            self.assertIn(
                expected, types_detected,
                f"expected detector {expected} to fire on the multi-detector fixture; "
                f"got {sorted(types_detected)}",
            )

        record_types = [r.type for r in report.failures]
        for expected in {"build_error_rust", "go_test_fail", "pytest_fail"}:
            self.assertIn(expected, record_types)

    def test_build_error_outranks_test_failures_and_is_root_cause(self) -> None:
        _result, report, analysis = build_single_report(MULTI_DETECTOR_LOG)

        # Rust build error severity 3 > test severity 2, so it ranks first.
        sorted_failures = sorted(report.failures, key=lambda r: -r.score)
        self.assertTrue(sorted_failures, "expected at least one failure record")
        top = sorted_failures[0]
        self.assertEqual(
            top.type, "build_error_rust",
            f"expected build_error_rust to rank first; got {top.type} (score={top.score})",
        )
        self.assertEqual(top.classification, "root_cause")
        self.assertGreaterEqual(top.severity, 3)

        # ``select_root_cause`` agrees with the score-sorted top.
        chosen = select_root_cause([analysis])
        self.assertIsNotNone(chosen)
        assert chosen is not None
        _, root_scored_block = chosen
        anchor_severities = [a.severity for a in root_scored_block.block.anchors]
        self.assertIn(3, anchor_severities)


# ---------------------------------------------------------------------------
# A5 -- Anchor-centric excerpt + benign filter end-to-end
# ---------------------------------------------------------------------------


def _build_a5_log() -> str:
    """Fixture for A5: 50-line log with benign output up front, a traceback
    at line 30, and stack-frame continuation after it.

    Lines 1-20:  benign output (``0 errors``, ``no failures``). These match
                 the generic detector's ``error``/``failed`` patterns but
                 are suppressed by the benign-mention filter, so they MUST
                 NOT produce anchors.
    Line 30:     ``Traceback (most recent call last):`` -- the anchor.
    Lines 31-40: stack frames following the traceback.
    Lines 41-50: trailing filler.
    """
    lines: list[str] = []
    lines.append("STEP: pytest")
    for index in range(9):
        lines.append(f"[INFO] 0 errors found in module_{index}")
    for index in range(10):
        lines.append(f"[INFO] no failures detected in submodule_{index}")
    for index in range(9):
        lines.append(f"  collecting test fixtures from suite_{index}")
    lines.append("Traceback (most recent call last):")
    for index in range(10):
        lines.append(
            f'  File "src/module.py", line {100 + index}, in func_{index}'
        )
    for index in range(10):
        lines.append(f"  trailing line {index}")
    assert len(lines) == 50, f"fixture length drift: {len(lines)} (expected 50)"
    return "\n".join(lines) + "\n"


class AnchorCentricExcerptAndBenignFilterTests(unittest.TestCase):
    """A5: benign lines do not anchor; excerpt centres on the traceback at line 30."""

    A5_LOG = _build_a5_log()

    def test_benign_lines_do_not_produce_anchors(self) -> None:
        result = analyze_log(self.A5_LOG)

        for scored in result.blocks:
            for anchor in scored.block.anchors:
                self.assertGreaterEqual(
                    anchor.line_number, 21,
                    f"anchor at line {anchor.line_number} fell inside the benign region",
                )

    def test_traceback_block_has_severity_weight_fifteen(self) -> None:
        result = analyze_log(self.A5_LOG)

        traceback_blocks = [
            scored
            for scored in result.blocks
            if any(anchor.line_number == 30 for anchor in scored.block.anchors)
        ]
        self.assertEqual(
            len(traceback_blocks), 1,
            "expected exactly one block to anchor on the traceback line",
        )
        traceback_block = traceback_blocks[0]
        self.assertEqual(
            traceback_block.score_components.severity_weight, 15.0,
            "traceback anchor has severity 3, so severity_weight == 15.0",
        )

    def test_excerpt_centres_on_traceback_not_head_of_block(self) -> None:
        _result, report, _analysis = build_single_report(self.A5_LOG, job_name="ci-job")

        self.assertIn(
            "Traceback (most recent call last):",
            report.root_cause.log_excerpt,
            "root_cause excerpt must include the anchor line",
        )
        self.assertNotIn(
            "0 errors found",
            report.root_cause.log_excerpt,
            "anchor-centric excerpt must NOT include benign zero-count lines",
        )
        self.assertNotIn(
            "no failures detected",
            report.root_cause.log_excerpt,
            "anchor-centric excerpt must NOT include benign no-failures lines",
        )


if __name__ == "__main__":
    unittest.main()
