from __future__ import annotations

import unittest

from ci_log_intelligence.ingestion.github.resolver import resolve_github_url


class GitHubResolverTests(unittest.TestCase):
    def test_resolves_pull_request_url(self) -> None:
        target = resolve_github_url("https://github.com/acme/widgets/pull/42")

        self.assertEqual(target.repo, "acme/widgets")
        self.assertEqual(target.pr_number, 42)
        self.assertIsNone(target.run_id)
        self.assertIsNone(target.job_id)

    def test_resolves_workflow_run_url(self) -> None:
        target = resolve_github_url("https://github.com/acme/widgets/actions/runs/123456")

        self.assertEqual(target.repo, "acme/widgets")
        self.assertEqual(target.run_id, 123456)
        self.assertIsNone(target.job_id)
        self.assertIsNone(target.pr_number)

    def test_resolves_job_url(self) -> None:
        target = resolve_github_url("https://github.com/acme/widgets/actions/runs/123456/job/789")

        self.assertEqual(target.repo, "acme/widgets")
        self.assertEqual(target.run_id, 123456)
        self.assertEqual(target.job_id, 789)
        self.assertIsNone(target.pr_number)


if __name__ == "__main__":
    unittest.main()
