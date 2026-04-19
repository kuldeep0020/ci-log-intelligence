from __future__ import annotations

import unittest

from ci_log_intelligence.models import Anchor, LogBlock, ParsedLine
from ci_log_intelligence.reducer.scoring import score_blocks


class ScoreBlocksTests(unittest.TestCase):
    def test_scores_are_deterministic_and_reward_recency(self) -> None:
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

        scored = score_blocks([early_block, late_block], total_lines=10)

        self.assertEqual(len(scored), 2)
        self.assertGreater(scored[1].score, scored[0].score)
        self.assertEqual(scored[0].score, score_blocks([early_block], total_lines=10)[0].score)


if __name__ == "__main__":
    unittest.main()
