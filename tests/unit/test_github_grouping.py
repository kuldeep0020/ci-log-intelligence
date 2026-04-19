from __future__ import annotations

import unittest

from ci_log_intelligence.ingestion.github.fetcher import group_logs_by_job, normalize_job_name
from ci_log_intelligence.ingestion.github.models import NormalizedLog


class GitHubGroupingTests(unittest.TestCase):
    def test_normalizes_environment_suffixes(self) -> None:
        self.assertEqual(normalize_job_name("test-redshift"), "test")
        self.assertEqual(normalize_job_name("test_snowflake"), "test")
        self.assertEqual(normalize_job_name("lint"), "lint")

    def test_groups_logs_by_logical_job_name(self) -> None:
        logs = [
            NormalizedLog(10, 1, "test-redshift", "failed", "ERROR one"),
            NormalizedLog(9, 2, "test-snowflake", "passed", "STEP: test"),
            NormalizedLog(8, 3, "lint", "passed", "ok"),
        ]

        grouped = group_logs_by_job(logs)

        self.assertEqual(sorted(grouped), ["lint", "test"])
        self.assertEqual([log.job_name for log in grouped["test"]], ["test-redshift", "test-snowflake"])


if __name__ == "__main__":
    unittest.main()
