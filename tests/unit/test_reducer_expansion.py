from __future__ import annotations

import unittest

from ci_log_intelligence.models import Anchor, AnchorCluster, ParsedLine
from ci_log_intelligence.reducer.expansion import expand_context


class ExpandContextTests(unittest.TestCase):
    def test_respects_step_boundary_and_extends_stack_trace(self) -> None:
        parsed_lines = [
            ParsedLine(1, "STEP: setup", None, "setup", []),
            ParsedLine(2, "setup line", None, "setup", []),
            ParsedLine(3, "STEP: test", None, "test", []),
            ParsedLine(4, "Running tests", None, "test", []),
            ParsedLine(5, "Traceback (most recent call last):", None, "test", ["traceback"]),
            ParsedLine(6, '  File "app.py", line 1, in <module>', None, "test", []),
            ParsedLine(7, "    main()", None, "test", []),
            ParsedLine(8, "ValueError: bad input", None, "test", []),
            ParsedLine(9, "STEP: deploy", None, "deploy", []),
            ParsedLine(10, "deploy line", None, "deploy", []),
        ]
        clusters = [
            AnchorCluster(
                cluster_id="cluster-1",
                anchors=[Anchor(5, "traceback", 3)],
                step_id="test",
            )
        ]

        blocks = expand_context(parsed_lines, clusters)

        self.assertEqual(len(blocks), 1)
        self.assertEqual(blocks[0].start_line, 3)
        self.assertEqual(blocks[0].end_line, 8)


if __name__ == "__main__":
    unittest.main()
