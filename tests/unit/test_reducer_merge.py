from __future__ import annotations

import unittest

from ci_log_intelligence.models import Anchor, LogBlock, ParsedLine
from ci_log_intelligence.reducer.merge import merge_blocks


class MergeBlocksTests(unittest.TestCase):
    def test_merges_when_gap_is_under_ten_lines_in_same_step(self) -> None:
        first = LogBlock(
            start_line=1,
            end_line=3,
            lines=[
                ParsedLine(1, "ERROR one", None, "test", ["error"]),
                ParsedLine(2, "detail", None, "test", []),
                ParsedLine(3, "detail", None, "test", []),
            ],
            anchors=[Anchor(1, "error", 3)],
        )
        second = LogBlock(
            start_line=8,
            end_line=9,
            lines=[
                ParsedLine(8, "FAILED two", None, "test", ["failed"]),
                ParsedLine(9, "detail", None, "test", []),
            ],
            anchors=[Anchor(8, "failed", 2)],
        )

        merged = merge_blocks([first, second])

        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0].start_line, 1)
        self.assertEqual(merged[0].end_line, 9)


if __name__ == "__main__":
    unittest.main()
