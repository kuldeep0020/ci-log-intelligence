from __future__ import annotations

import unittest

from ci_log_intelligence.models import (
    Anchor,
    LogBlock,
    ParsedLine,
    ScoreComponents,
    ScoredBlock,
)
from ci_log_intelligence.reducer.classification import classify_blocks
from ci_log_intelligence.reducer.detectors import DetectedFailure


def _trivial_components() -> ScoreComponents:
    return ScoreComponents(severity_weight=0.0, signal_density=0.0, duplicate_penalty=0.0)


class ClassifyBlocksTests(unittest.TestCase):
    def test_assigns_root_cause_symptom_and_flaky(self) -> None:
        root_block = ScoredBlock(
            block=LogBlock(
                start_line=1,
                end_line=1,
                lines=[ParsedLine(1, "ERROR failure", None, "test", ["error"])],
                anchors=[Anchor(1, "error", 3)],
            ),
            score=10.0,
            classification="unclassified",
            score_components=_trivial_components(),
        )
        symptom_block = ScoredBlock(
            block=LogBlock(
                start_line=2,
                end_line=2,
                lines=[ParsedLine(2, "FAILED test_x", None, "test", ["failed"])],
                anchors=[Anchor(2, "failed", 2)],
            ),
            score=5.0,
            classification="unclassified",
            score_components=_trivial_components(),
        )
        flaky_block = ScoredBlock(
            block=LogBlock(
                start_line=3,
                end_line=3,
                lines=[ParsedLine(3, "Retrying request", None, "test", ["retrying"])],
                anchors=[Anchor(3, "retrying", 1)],
            ),
            score=3.0,
            classification="unclassified",
            score_components=_trivial_components(),
        )

        classified = classify_blocks([root_block, symptom_block, flaky_block])

        self.assertEqual(
            [block.classification for block in classified],
            ["root_cause", "symptom", "flaky"],
        )


class ClassificationClaimTests(unittest.TestCase):
    """``classify_blocks`` honors a ``DetectedFailure.classification_claim`` whose
    anchor falls inside the block. Used by detectors like hash-mismatch whose
    lines carry no generic signals but ARE root-cause-worthy."""

    def _block(
        self, *, start: int, end: int, content: str, signals: list[str]
    ) -> ScoredBlock:
        return ScoredBlock(
            block=LogBlock(
                start_line=start,
                end_line=end,
                lines=[ParsedLine(start, content, None, "test", signals)],
                anchors=[Anchor(start, "hash_mismatch", 2)],
            ),
            score=8.0,
            classification="unclassified",
            score_components=_trivial_components(),
        )

    def test_classification_claim_root_cause_overrides_symptom_fallback(self) -> None:
        # No generic signals => fallback would be "symptom". The claim wins.
        scored = self._block(
            start=42, end=42, content="file hashes don't match for /tmp/x", signals=[]
        )
        detected = [
            DetectedFailure(
                type="hash_mismatch",
                anchor_lines=[42],
                severity=2,
                classification_claim="root_cause",
                anchor_type="hash_mismatch",
            )
        ]

        classified = classify_blocks([scored], detected_failures=detected)

        self.assertEqual(classified[0].classification, "root_cause")

    def test_no_claim_falls_back_to_signal_heuristic(self) -> None:
        # Detected failure with NO claim, block has ``error`` signal -> root_cause via fallback.
        scored = self._block(
            start=5, end=5, content="ERROR boom", signals=["error"]
        )
        detected = [
            DetectedFailure(
                type="generic",
                anchor_lines=[5],
                severity=3,
                classification_claim=None,
                extracted_fields={"signal_name": "error"},
                anchor_type="error",
            )
        ]

        classified = classify_blocks([scored], detected_failures=detected)

        self.assertEqual(classified[0].classification, "root_cause")

    def test_claim_outside_block_range_is_ignored(self) -> None:
        scored = self._block(
            start=10, end=15, content="file hashes don't match", signals=[]
        )
        # Anchor at line 99 is far from the block (10-15) -> claim must be ignored.
        detected = [
            DetectedFailure(
                type="hash_mismatch",
                anchor_lines=[99],
                severity=2,
                classification_claim="root_cause",
                anchor_type="hash_mismatch",
            )
        ]

        classified = classify_blocks([scored], detected_failures=detected)

        # Falls back to signal heuristic; no generic signals => "symptom".
        self.assertEqual(classified[0].classification, "symptom")

    def test_highest_severity_claim_wins_when_multiple_claimants(self) -> None:
        scored = self._block(
            start=10, end=20, content="x", signals=[]
        )
        detected = [
            DetectedFailure(
                type="hash_mismatch",
                anchor_lines=[12],
                severity=1,
                classification_claim="flaky",
            ),
            DetectedFailure(
                type="hash_mismatch",
                anchor_lines=[15],
                severity=3,
                classification_claim="root_cause",
            ),
        ]

        classified = classify_blocks([scored], detected_failures=detected)

        self.assertEqual(classified[0].classification, "root_cause")

    def test_classify_blocks_without_detected_failures_kwarg_still_works(self) -> None:
        scored = self._block(
            start=1, end=1, content="ERROR x", signals=["error"]
        )
        # Backward-compatible: no kwarg => signal heuristic only.
        classified = classify_blocks([scored])
        self.assertEqual(classified[0].classification, "root_cause")


if __name__ == "__main__":
    unittest.main()
