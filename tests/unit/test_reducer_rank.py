from __future__ import annotations

import unittest

from ci_log_intelligence.models import Anchor, LogBlock, ParsedLine, ScoredBlock
from ci_log_intelligence.reducer.classification import rank_blocks


class RankBlocksTests(unittest.TestCase):
    def test_ranks_by_score_then_classification_then_position(self) -> None:
        root = ScoredBlock(
            block=LogBlock(
                start_line=5,
                end_line=6,
                lines=[ParsedLine(5, "ERROR", None, "test", ["error"])],
                anchors=[Anchor(5, "error", 3)],
            ),
            score=10.0,
            classification="root_cause",
        )
        symptom = ScoredBlock(
            block=LogBlock(
                start_line=1,
                end_line=2,
                lines=[ParsedLine(1, "FAILED", None, "test", ["failed"])],
                anchors=[Anchor(1, "failed", 2)],
            ),
            score=10.0,
            classification="symptom",
        )
        flaky = ScoredBlock(
            block=LogBlock(
                start_line=3,
                end_line=4,
                lines=[ParsedLine(3, "Retrying", None, "test", ["retrying"])],
                anchors=[Anchor(3, "retrying", 1)],
            ),
            score=9.0,
            classification="flaky",
        )

        ranked = rank_blocks([flaky, symptom, root])

        self.assertEqual([block.classification for block in ranked], ["root_cause", "symptom", "flaky"])


if __name__ == "__main__":
    unittest.main()
