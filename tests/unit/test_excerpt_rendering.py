from __future__ import annotations

import unittest

from ci_log_intelligence import analyze_log
from ci_log_intelligence.models import Anchor, LogBlock, ParsedLine, ScoredBlock
from ci_log_intelligence.reducer.comparison import render_block_excerpt


def _line(line_number: int, content: str, signals=None) -> ParsedLine:
    return ParsedLine(line_number, content, None, "test", list(signals or []))


def _block(start: int, end: int, contents, anchors) -> ScoredBlock:
    return ScoredBlock(
        block=LogBlock(
            start_line=start,
            end_line=end,
            lines=[_line(start + offset, c) for offset, c in enumerate(contents)],
            anchors=anchors,
        ),
        score=0.0,
        classification="root_cause",
    )


class RenderBlockExcerptTests(unittest.TestCase):
    def test_anchor_at_end_of_long_block_includes_anchor_context(self) -> None:
        contents = [f"line {n}" for n in range(1, 31)]
        contents[19] = "ERROR boom"
        block = _block(1, 30, contents, [Anchor(20, "error", 3)])

        excerpt = render_block_excerpt(block, max_lines=20, context_around_anchor=5)
        lines = excerpt.split("\n")

        # Window is 15..25 inclusive (11 lines, clipped to block bounds).
        self.assertIn("ERROR boom", lines)
        self.assertIn("line 15", lines)
        self.assertIn("line 25", lines)
        # The first content lines must NOT be in the excerpt -- this is the
        # central correctness property (anchor-centric, not head-N).
        self.assertNotIn("line 1", lines)
        self.assertNotIn("line 2", lines)

    def test_two_anchors_with_overlapping_windows_emit_single_contiguous_excerpt(self) -> None:
        contents = [f"line {n}" for n in range(1, 21)]
        contents[9] = "ERROR first"
        contents[12] = "FAILED second"
        block = _block(
            1,
            20,
            contents,
            [Anchor(10, "error", 3), Anchor(13, "failed", 2)],
        )

        excerpt = render_block_excerpt(block, max_lines=20, context_around_anchor=5)

        # No "..." separator should appear because windows overlap (5..15 and 8..18).
        self.assertNotIn("...", excerpt)
        self.assertIn("ERROR first", excerpt)
        self.assertIn("FAILED second", excerpt)

    def test_two_anchors_far_apart_emit_two_windows_with_separator(self) -> None:
        contents = [f"line {n}" for n in range(1, 51)]
        contents[4] = "ERROR early"
        contents[44] = "FAILED late"
        block = _block(
            1,
            50,
            contents,
            [Anchor(5, "error", 3), Anchor(45, "failed", 2)],
        )

        excerpt = render_block_excerpt(block, max_lines=50, context_around_anchor=3)
        lines = excerpt.split("\n")

        # Expect both windows and a "..." separator between them.
        self.assertIn("...", lines)
        self.assertIn("ERROR early", lines)
        self.assertIn("FAILED late", lines)
        # The lines between the two windows must be omitted.
        self.assertNotIn("line 20", lines)
        self.assertNotIn("line 30", lines)

    def test_anchor_near_block_boundary_clips_to_block_bounds(self) -> None:
        contents = ["ERROR start"] + [f"line {n}" for n in range(2, 8)]
        block = _block(1, 7, contents, [Anchor(1, "error", 3)])

        excerpt = render_block_excerpt(block, max_lines=20, context_around_anchor=5)
        lines = excerpt.split("\n")

        # The window would be 1..6 (-4 clipped to 1, +5 capped to 6 inside the block).
        self.assertIn("ERROR start", lines)
        self.assertEqual(lines[0], "ERROR start")

    def test_block_with_no_anchors_falls_back_to_head_truncation(self) -> None:
        contents = [f"line {n}" for n in range(1, 31)]
        block = _block(1, 30, contents, [])

        excerpt = render_block_excerpt(block, max_lines=5)

        self.assertEqual(excerpt, "line 1\nline 2\nline 3\nline 4\nline 5")

    def test_max_lines_caps_combined_windows(self) -> None:
        contents = [f"line {n}" for n in range(1, 51)]
        contents[9] = "ERROR a"
        contents[24] = "ERROR b"
        contents[39] = "ERROR c"
        block = _block(
            1,
            50,
            contents,
            [Anchor(10, "error", 3), Anchor(25, "error", 3), Anchor(40, "error", 3)],
        )

        excerpt = render_block_excerpt(block, max_lines=10, context_around_anchor=5)
        # 10 content lines exactly (no "..." counted toward cap is fine; we
        # assert that content-line count does not exceed max_lines).
        content_lines = [line for line in excerpt.split("\n") if line != "..."]
        self.assertEqual(len(content_lines), 10)

    def test_anchor_line_is_preserved_when_max_lines_truncates_window(self) -> None:
        # Anchor at line 10, context_around_anchor=5 means window [5..15].
        # With max_lines=3, the naive left-to-right emit would output lines 5, 6, 7
        # and silently drop the anchor at line 10. The excerpt is *about* the
        # anchor, so the anchor line must always be present.
        contents = [f"content {n}" for n in range(1, 21)]
        block = _block(1, 20, contents, [Anchor(10, "error", 3)])

        excerpt = render_block_excerpt(block, max_lines=3, context_around_anchor=5)

        self.assertIn("content 10", excerpt)

    def test_anchor_line_is_preserved_for_each_window_under_tight_cap(self) -> None:
        # Two anchors far apart: each window must include its anchor even
        # when ``max_lines`` is tight enough to permit only the anchor lines
        # themselves (plus separator).
        contents = [f"content {n}" for n in range(1, 101)]
        block = _block(
            1,
            100,
            contents,
            [Anchor(10, "error", 3), Anchor(80, "error", 3)],
        )

        excerpt = render_block_excerpt(block, max_lines=4, context_around_anchor=5)

        self.assertIn("content 10", excerpt)
        self.assertIn("content 80", excerpt)


class AnchorCentricExcerptSmokeTest(unittest.TestCase):
    def test_analyze_log_excerpt_renders_around_anchor_past_head(self) -> None:
        # 100-line synthetic log with the Traceback at line 80.
        lines = []
        for n in range(1, 101):
            if n == 78:
                lines.append("STEP: failing")
            elif n == 80:
                lines.append("Traceback (most recent call last):")
            elif n == 81:
                lines.append("  File 'foo.py', line 1, in bar")
            elif n == 82:
                lines.append("ValueError: boom")
            else:
                lines.append(f"informational line number {n}")
        log = "\n".join(lines) + "\n"

        result = analyze_log(log)
        self.assertGreater(len(result.blocks), 0)

        # Identify the block that contains the traceback.
        target_block = None
        for scored in result.blocks:
            if any("Traceback" in line.content for line in scored.block.lines):
                target_block = scored
                break
        self.assertIsNotNone(target_block)

        excerpt = render_block_excerpt(target_block)
        self.assertIn("Traceback (most recent call last):", excerpt)
        self.assertIn("ValueError: boom", excerpt)
        # Anchor-centric: the excerpt must contain lines AROUND 80, not
        # the head of the log file.
        self.assertNotIn("informational line number 1\n", excerpt)
        self.assertNotIn("informational line number 2\n", excerpt)


if __name__ == "__main__":
    unittest.main()
