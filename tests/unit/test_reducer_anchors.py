from __future__ import annotations

import unittest

from ci_log_intelligence.models import ParsedLine
from ci_log_intelligence.reducer.anchors import detect_anchors


class DetectAnchorsTests(unittest.TestCase):
    def test_detects_all_anchor_tiers_with_expected_severity(self) -> None:
        lines = [
            ParsedLine(1, "ERROR build failed", None, "build", ["error"]),
            ParsedLine(2, "FAILED test_example", None, "build", ["failed"]),
            ParsedLine(3, "Retrying request", None, "build", ["retrying"]),
        ]

        anchors = detect_anchors(lines)

        self.assertEqual(
            [(anchor.line_number, anchor.type, anchor.severity) for anchor in anchors],
            [
                (1, "error", 3),
                (2, "failed", 2),
                (3, "retrying", 1),
            ],
        )


if __name__ == "__main__":
    unittest.main()
