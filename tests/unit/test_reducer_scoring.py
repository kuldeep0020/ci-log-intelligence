from __future__ import annotations

import unittest

from ci_log_intelligence.models import Anchor, LogBlock, ParsedLine
from ci_log_intelligence.reducer.scoring import score_blocks


class ScoreBlocksTests(unittest.TestCase):
    def test_scores_are_deterministic_without_recency_bias(self) -> None:
        # Two blocks with identical signal content and identical anchor severity
        # but different positions in the log should now receive the SAME score
        # because the former position-based weighting term has been removed.
        # This is the deliberate behavior change for step 2.
        early_block = LogBlock(
            start_line=1,
            end_line=2,
            lines=[
                ParsedLine(1, "ERROR early", None, "test", ["error"]),
                ParsedLine(2, "detail", None, "test", []),
            ],
            anchors=[Anchor(1, "error", 3)],
        )
        late_block = LogBlock(
            start_line=8,
            end_line=9,
            lines=[
                ParsedLine(8, "ERROR late", None, "test", ["error"]),
                ParsedLine(9, "detail", None, "test", []),
            ],
            anchors=[Anchor(8, "error", 3)],
        )

        scored = score_blocks([early_block, late_block])

        self.assertEqual(len(scored), 2)
        # New formula: severity*5 + signal_density - duplicate_penalty
        # Both blocks: 3*5 + (1/2) - 0 = 15.5
        self.assertEqual(scored[0].score, 15.5)
        self.assertEqual(scored[1].score, 15.5)
        # Determinism: running again with the same single block yields the same result.
        self.assertEqual(scored[0].score, score_blocks([early_block])[0].score)

    def test_severity_dominates_score(self) -> None:
        warn_block = LogBlock(
            start_line=1,
            end_line=1,
            lines=[ParsedLine(1, "WARNING something", None, "test", ["warning"])],
            anchors=[Anchor(1, "warning", 1)],
        )
        error_block = LogBlock(
            start_line=1,
            end_line=1,
            lines=[ParsedLine(1, "ERROR something", None, "test", ["error"])],
            anchors=[Anchor(1, "error", 3)],
        )

        scored = score_blocks([warn_block, error_block])

        self.assertGreater(scored[1].score, scored[0].score)


if __name__ == "__main__":
    unittest.main()
