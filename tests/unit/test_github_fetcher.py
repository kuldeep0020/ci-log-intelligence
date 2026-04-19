from __future__ import annotations

import unittest

from ci_log_intelligence.ingestion.github.fetcher import (
    GitHubLogFetcher,
    GitHubTransport,
    _plan_log_fetches,
    classify_job_status,
)
from ci_log_intelligence.ingestion.github.models import WorkflowJob, WorkflowRun


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

    def test_plan_log_fetches_limits_passed_jobs_to_failed_groups(self) -> None:
        run = WorkflowRun(
            run_id=10,
            workflow_id=1,
            head_branch="main",
            head_sha="abc",
            html_url="https://github.com/acme/widgets/actions/runs/10",
            status="completed",
            conclusion="failure",
            display_title="CI",
        )
        previous_run = WorkflowRun(
            run_id=9,
            workflow_id=1,
            head_branch="main",
            head_sha="def",
            html_url="https://github.com/acme/widgets/actions/runs/9",
            status="completed",
            conclusion="success",
            display_title="CI",
        )
        jobs = [
            (run, WorkflowJob(10, 501, "test-snowflake", "completed", "failure"), "test", "failed"),
            (previous_run, WorkflowJob(9, 401, "test-redshift", "completed", "success"), "test", "passed"),
            (previous_run, WorkflowJob(9, 402, "lint", "completed", "success"), "lint", "passed"),
        ]

        planned = _plan_log_fetches(jobs, include_passed=True, max_passed_runs=1)

        self.assertEqual([job.job_id for _, job, _, _ in planned], [501, 401])


if __name__ == "__main__":
    unittest.main()
