from __future__ import annotations

import unittest
from pathlib import Path

from ci_log_intelligence import analyze_log

GOLDEN_DIR = Path(__file__).parent


class GoldenPipelineTests(unittest.TestCase):
    def test_python_failure_golden_output(self) -> None:
        log = (GOLDEN_DIR / "python_failure.log").read_text(encoding="utf-8")

        result = analyze_log(log)

        self.assertEqual(len(result.blocks), 2)
        self.assertEqual(
            [
                (
                    block.block.start_line,
                    block.block.end_line,
                    block.classification,
                )
                for block in result.blocks
            ],
            [
                (4, 11, "root_cause"),
                (12, 16, "flaky"),
            ],
        )
        self.assertGreater(result.blocks[0].score, result.blocks[1].score)

    def test_java_symptom_golden_output(self) -> None:
        log = (GOLDEN_DIR / "java_symptom.log").read_text(encoding="utf-8")

        result = analyze_log(log)

        self.assertEqual(len(result.blocks), 2)
        self.assertEqual(result.blocks[0].classification, "symptom")
        self.assertEqual(result.blocks[0].block.start_line, 4)
        self.assertEqual(result.blocks[0].block.end_line, 7)
        self.assertEqual(result.blocks[1].classification, "symptom")
        self.assertEqual(result.blocks[1].block.start_line, 1)
        self.assertEqual(result.blocks[1].block.end_line, 3)


if __name__ == "__main__":
    unittest.main()
