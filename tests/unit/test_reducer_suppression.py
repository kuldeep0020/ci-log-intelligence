from __future__ import annotations

import unittest

from ci_log_intelligence.models import Anchor, LogBlock, ParsedLine
from ci_log_intelligence.reducer.suppression import suppress_noise


class SuppressNoiseTests(unittest.TestCase):
    def test_removes_blank_separator_and_duplicate_lines(self) -> None:
        block = LogBlock(
            start_line=1,
            end_line=6,
            lines=[
                ParsedLine(1, "ERROR issue", None, "test", ["error"]),
                ParsedLine(2, "", None, "test", []),
                ParsedLine(3, "-----", None, "test", []),
                ParsedLine(4, "detail", None, "test", []),
                ParsedLine(5, "detail", None, "test", []),
                ParsedLine(6, "FAILED test", None, "test", ["failed"]),
            ],
            anchors=[Anchor(1, "error", 3), Anchor(6, "failed", 2)],
        )

        suppressed = suppress_noise([block])

        self.assertEqual(len(suppressed), 1)
        self.assertEqual([line.line_number for line in suppressed[0].lines], [1, 4, 6])


if __name__ == "__main__":
    unittest.main()
