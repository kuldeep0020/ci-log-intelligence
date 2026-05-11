from __future__ import annotations

import unittest
from datetime import datetime

from ci_log_intelligence.parsing import detect_step_id, parse_timestamp


class DetectStepIdGitHubActionsTests(unittest.TestCase):
    def test_strips_timestamp_prefix_with_milliseconds(self) -> None:
        line = "2024-01-15T12:34:56.789Z ##[group]Run actions/checkout@v3"

        self.assertEqual(detect_step_id(line), "Run actions/checkout@v3")

    def test_strips_timestamp_prefix_without_milliseconds(self) -> None:
        line = "2024-01-15T12:34:56Z ##[group]Run actions/checkout@v3"

        self.assertEqual(detect_step_id(line), "Run actions/checkout@v3")

    def test_strips_timestamp_prefix_without_trailing_z(self) -> None:
        line = "2024-01-15T12:34:56.789 ##[group]Build the project"

        self.assertEqual(detect_step_id(line), "Build the project")

    def test_supports_group_marker_without_brackets(self) -> None:
        line = "2024-01-15T12:34:56.789Z ::group::Compile sources"

        self.assertEqual(detect_step_id(line), "Compile sources")


class DetectStepIdOtherProvidersTests(unittest.TestCase):
    def test_circleci_marker(self) -> None:
        line = "====>> install_deps"

        self.assertEqual(detect_step_id(line), "install_deps")

    def test_circleci_marker_with_three_equals(self) -> None:
        line = "===>> setup_environment"

        self.assertEqual(detect_step_id(line), "setup_environment")

    def test_buildkite_marker(self) -> None:
        line = "--- :hammer: Building"

        self.assertEqual(detect_step_id(line), ":hammer: Building")


class DetectStepIdBackwardCompatTests(unittest.TestCase):
    def test_plain_group_marker_still_works(self) -> None:
        self.assertEqual(detect_step_id("##[group]Build"), "Build")

    def test_plain_step_marker_still_works(self) -> None:
        self.assertEqual(detect_step_id("STEP: deploy"), "deploy")

    def test_plain_bracket_step_marker_still_works(self) -> None:
        self.assertEqual(detect_step_id("[step:deploy]"), "deploy")

    def test_non_step_line_returns_none(self) -> None:
        self.assertIsNone(detect_step_id("just some log output"))


class ParseTimestampPrefixTests(unittest.TestCase):
    def test_parses_timestamp_from_plain_line(self) -> None:
        self.assertEqual(
            parse_timestamp("2024-01-15T12:34:56 some content"),
            datetime(2024, 1, 15, 12, 34, 56),
        )

    def test_returns_none_when_timestamp_absent(self) -> None:
        self.assertIsNone(parse_timestamp("no timestamp here"))


if __name__ == "__main__":
    unittest.main()
