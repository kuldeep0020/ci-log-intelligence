from __future__ import annotations

import unittest

from ci_log_intelligence.ci_analysis import _summarize_root_cause
from ci_log_intelligence.models import (
    Anchor,
    LogBlock,
    ParsedLine,
    ScoreComponents,
    ScoredBlock,
)
from ci_log_intelligence.reducer.scoring import score_blocks


class ScoreComponentsPopulatedByScoringTests(unittest.TestCase):
    def test_score_blocks_populates_score_components(self) -> None:
        block = LogBlock(
            start_line=1,
            end_line=3,
            lines=[
                ParsedLine(1, "ERROR boom", None, "test", ["error"]),
                ParsedLine(2, "ERROR boom", None, "test", ["error"]),
                ParsedLine(3, "trailing", None, "test", []),
            ],
            anchors=[Anchor(1, "error", 3), Anchor(2, "error", 3)],
        )

        scored = score_blocks([block])

        self.assertEqual(len(scored), 1)
        components = scored[0].score_components
        self.assertIsInstance(components, ScoreComponents)
        # severity 3 * 5.0 = 15.0
        self.assertEqual(components.severity_weight, 15.0)
        # 2 signals across 3 lines = 0.666667
        self.assertAlmostEqual(components.signal_density, 2 / 3, places=5)
        # one duplicate of "ERROR boom" / 3 lines = 0.333333
        self.assertAlmostEqual(components.duplicate_penalty, 1 / 3, places=5)

    def test_score_equals_sum_of_components(self) -> None:
        block = LogBlock(
            start_line=1,
            end_line=2,
            lines=[
                ParsedLine(1, "ERROR thing", None, "test", ["error"]),
                ParsedLine(2, "detail", None, "test", []),
            ],
            anchors=[Anchor(1, "error", 3)],
        )

        scored = score_blocks([block])[0]
        expected = round(
            scored.score_components.severity_weight
            + scored.score_components.signal_density
            - scored.score_components.duplicate_penalty,
            6,
        )

        self.assertEqual(scored.score, expected)

    def test_empty_anchor_block_has_zero_severity_weight(self) -> None:
        block = LogBlock(
            start_line=1,
            end_line=1,
            lines=[ParsedLine(1, "informational", None, "test", [])],
            anchors=[],
        )

        scored = score_blocks([block])[0]

        self.assertEqual(scored.score_components.severity_weight, 0.0)
        self.assertEqual(scored.score_components.signal_density, 0.0)
        self.assertEqual(scored.score_components.duplicate_penalty, 0.0)
        self.assertEqual(scored.score, 0.0)


class SummarizeRootCauseUsesComponentsDirectlyTests(unittest.TestCase):
    def test_summarize_root_cause_does_not_reverse_engineer_components(self) -> None:
        block = LogBlock(
            start_line=10,
            end_line=11,
            lines=[
                ParsedLine(10, "Traceback (most recent call last):", None, "test", ["traceback"]),
                ParsedLine(11, "  File 'a.py', line 1, in main", None, "test", []),
            ],
            anchors=[Anchor(10, "traceback", 3)],
        )
        scored = score_blocks([block])[0]

        summary = _summarize_root_cause(scored, "job-a", 100)

        # Whatever score_components are produced by score_blocks must be
        # the exact same instance/values the summary reports — proving the
        # summary reads them directly rather than recomputing.
        self.assertEqual(summary.score_components, scored.score_components)
        self.assertIsInstance(summary.score_components, ScoreComponents)
        # The recomputation path used to flatten duplicate_penalty to 0.0 when
        # negative; the direct read keeps the actual penalty value.
        self.assertEqual(
            summary.score_components.duplicate_penalty,
            scored.score_components.duplicate_penalty,
        )

    def test_score_components_to_dict_emits_expected_keys(self) -> None:
        components = ScoreComponents(
            severity_weight=15.0, signal_density=0.5, duplicate_penalty=0.25
        )

        self.assertEqual(
            components.to_dict(),
            {
                "severity_weight": 15.0,
                "signal_density": 0.5,
                "duplicate_penalty": 0.25,
            },
        )


if __name__ == "__main__":
    unittest.main()
