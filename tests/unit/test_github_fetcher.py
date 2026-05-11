from __future__ import annotations

import unittest

from ci_log_intelligence.ingestion.github.fetcher import (
    GitHubLogFetcher,
    GitHubTransport,
    LogFetchPlan,
    _plan_log_fetches,
    classify_job_status,
)
from ci_log_intelligence.ingestion.github.models import (
    GitHubTarget,
    WorkflowJob,
    WorkflowRun,
)


class ErrorTransport(GitHubTransport):
    def get_json(self, endpoint: str, params=None):
        raise AssertionError("get_json should not be called in this test")

    def get_text(self, endpoint: str, params=None) -> str:
        raise RuntimeError("gh: HTTP 404")


_PLAN_RUN_PAYLOAD = {
    "id": 200,
    "workflow_id": 10,
    "head_branch": "main",
    "head_sha": "abc",
    "html_url": "https://github.com/acme/widgets/actions/runs/200",
    "status": "completed",
    "conclusion": "failure",
    "display_title": "CI",
}


class _PlanOnlyTransport(GitHubTransport):
    """Transport that serves JSON metadata only; ``get_text`` is forbidden.

    Used to prove ``plan_logs`` is JSON-only and never fetches log content.
    """

    def __init__(self) -> None:
        self.json_calls: list[tuple[str, tuple]] = []

    def get_json(self, endpoint: str, params=None):
        normalized = tuple(sorted((params or {}).items()))
        self.json_calls.append((endpoint, normalized))
        if endpoint == "repos/acme/widgets/actions/runs/200":
            return _PLAN_RUN_PAYLOAD
        if endpoint == "repos/acme/widgets/actions/runs":
            return {"workflow_runs": [_PLAN_RUN_PAYLOAD]}
        if endpoint == "repos/acme/widgets/actions/runs/200/jobs":
            return {
                "jobs": [
                    {
                        "id": 501,
                        "name": "postgres-test (bundling)",
                        "status": "completed",
                        "conclusion": "failure",
                    },
                    {
                        "id": 502,
                        "name": "snowflake-test (non-bundling)",
                        "status": "completed",
                        "conclusion": "failure",
                    },
                ]
            }
        raise KeyError(f"Unhandled get_json call: {endpoint} {normalized}")

    def get_text(self, endpoint: str, params=None) -> str:
        raise AssertionError(
            "plan_logs must not fetch log content; got get_text call"
        )


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


class PlanLogsTests(unittest.TestCase):
    """Focused tests for ``GitHubLogFetcher.plan_logs``.

    ``plan_logs`` is the JSON-only counterpart to ``fetch_logs``: it resolves
    runs + jobs without paying for log content. The cache-aware code path in
    ``ci_analysis.fetch_with_cache_awareness`` uses it to skip log fetches
    for already-cached jobs.
    """

    def test_plan_logs_returns_failed_only_plan_without_text_fetch(self) -> None:
        transport = _PlanOnlyTransport()
        fetcher = GitHubLogFetcher(transport=transport)
        target = GitHubTarget(repo="acme/widgets", run_id=200)

        plan = fetcher.plan_logs(
            target, include_passed=False, max_runs=5, max_passed_runs=0
        )

        self.assertIsInstance(plan, LogFetchPlan)
        self.assertEqual(len(plan.selected_runs), 1)
        self.assertEqual(plan.selected_runs[0].run_id, 200)

        # planned_jobs shape: (WorkflowRun, WorkflowJob, str, Literal["passed", "failed"]).
        self.assertEqual(len(plan.planned_jobs), 2)
        for run, job, logical_name, status in plan.planned_jobs:
            self.assertIsInstance(run, WorkflowRun)
            self.assertIsInstance(job, WorkflowJob)
            self.assertIsInstance(logical_name, str)
            self.assertEqual(status, "failed")

        job_ids = sorted(job.job_id for _, job, _, _ in plan.planned_jobs)
        self.assertEqual(job_ids, [501, 502])

        # All JSON; no text endpoint should ever be hit (the stub asserts).
        endpoints = {call[0] for call in transport.json_calls}
        self.assertIn("repos/acme/widgets/actions/runs/200/jobs", endpoints)


if __name__ == "__main__":
    unittest.main()
