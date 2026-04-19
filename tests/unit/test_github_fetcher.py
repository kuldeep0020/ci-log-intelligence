from __future__ import annotations

import unittest

from ci_log_intelligence.ingestion.github.fetcher import GitHubLogFetcher, GitHubTransport, classify_job_status


class ErrorTransport(GitHubTransport):
    def get_json(self, endpoint: str, params=None):
        raise AssertionError("get_json should not be called in this test")

    def get_text(self, endpoint: str, params=None) -> str:
        raise RuntimeError("gh: HTTP 404")


class GitHubFetcherTests(unittest.TestCase):
    def test_classify_job_status_returns_none_for_skipped_and_neutral_jobs(self) -> None:
        self.assertIsNone(classify_job_status("skipped"))
        self.assertIsNone(classify_job_status("neutral"))
        self.assertIsNone(classify_job_status(None))

    def test_classify_job_status_maps_failure_like_conclusions_to_failed(self) -> None:
        self.assertEqual(classify_job_status("failure"), "failed")
        self.assertEqual(classify_job_status("timed_out"), "failed")
        self.assertEqual(classify_job_status("cancelled"), "failed")
        self.assertEqual(classify_job_status("action_required"), "failed")
        self.assertEqual(classify_job_status("startup_failure"), "failed")
        self.assertEqual(classify_job_status("success"), "passed")

    def test_fetch_job_log_wraps_not_found_with_context(self) -> None:
        fetcher = GitHubLogFetcher(transport=ErrorTransport())

        with self.assertRaisesRegex(
            RuntimeError,
            r"GitHub returned 404 while fetching logs for job 501 in acme/widgets",
        ):
            fetcher.fetch_job_log("acme/widgets", 501)


if __name__ == "__main__":
    unittest.main()
