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


if __name__ == "__main__":
    unittest.main()
