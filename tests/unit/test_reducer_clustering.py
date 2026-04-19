from __future__ import annotations

import unittest

from ci_log_intelligence.models import Anchor, ParsedLine
from ci_log_intelligence.reducer.clustering import build_clusters


class BuildClustersTests(unittest.TestCase):
    def test_groups_nearby_same_step_anchors_and_splits_step_changes(self) -> None:
        parsed_lines = [
            ParsedLine(1, "STEP: test", None, "test", []),
            ParsedLine(2, "FAILED first", None, "test", ["failed"]),
            ParsedLine(6, "AssertionError", None, "test", ["assertion_error"]),
            ParsedLine(20, "STEP: deploy", None, "deploy", []),
            ParsedLine(21, "ERROR deploy", None, "deploy", ["error"]),
        ]
        anchors = [
            Anchor(2, "failed", 2),
            Anchor(6, "assertion_error", 2),
            Anchor(21, "error", 3),
        ]

        clusters = build_clusters(anchors, parsed_lines)

        self.assertEqual(len(clusters), 2)
        self.assertEqual([anchor.line_number for anchor in clusters[0].anchors], [2, 6])
        self.assertEqual(clusters[0].step_id, "test")
        self.assertEqual([anchor.line_number for anchor in clusters[1].anchors], [21])
        self.assertEqual(clusters[1].step_id, "deploy")


if __name__ == "__main__":
    unittest.main()
