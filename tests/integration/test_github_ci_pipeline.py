from __future__ import annotations

import unittest

from ci_log_intelligence.ci_analysis import analyze_ci_url
from ci_log_intelligence.ingestion.github.fetcher import GitHubLogFetcher, GitHubTransport


class FakeGitHubTransport(GitHubTransport):
    def __init__(self) -> None:
        self._json_payloads = {
            ("repos/acme/widgets/pulls/42", ()): {
                "head": {"sha": "abc123"},
            },
            ("repos/acme/widgets/actions/runs", (("head_sha", "abc123"), ("per_page", 5))): {
                "workflow_runs": [
                    {
                        "id": 200,
                        "workflow_id": 10,
                        "head_branch": "main",
                        "head_sha": "abc123",
                        "html_url": "https://github.com/acme/widgets/actions/runs/200",
                        "status": "completed",
                        "conclusion": "failure",
                        "display_title": "CI",
                    },
                    {
                        "id": 199,
                        "workflow_id": 10,
                        "head_branch": "main",
                        "head_sha": "abc123",
                        "html_url": "https://github.com/acme/widgets/actions/runs/199",
                        "status": "completed",
                        "conclusion": "success",
                        "display_title": "CI",
                    },
                ]
            },
            ("repos/acme/widgets/actions/runs/200/jobs", (("per_page", 100),)): {
                "jobs": [
                    {"id": 501, "name": "test-snowflake", "status": "completed", "conclusion": "failure"},
                    {"id": 502, "name": "audit-integration-test", "status": "completed", "conclusion": "skipped"},
                ]
            },
            ("repos/acme/widgets/actions/runs/199/jobs", (("per_page", 100),)): {
                "jobs": [
                    {"id": 401, "name": "test-redshift", "status": "completed", "conclusion": "success"},
                ]
            },
        }
        self._text_payloads = {
            "repos/acme/widgets/actions/jobs/501/logs": """STEP: execute
tests/test_query.py::test_query FAILED
Traceback (most recent call last):
  File "tests/test_query.py", line 10, in test_query
    assert query_result == expected_result
AssertionError
""",
            "repos/acme/widgets/actions/jobs/401/logs": """STEP: setup
connected
STEP: execute
tests/test_query.py::test_query PASSED
query result stable
""",
        }

    def get_json(self, endpoint: str, params=None):
        normalized_params = tuple(sorted((params or {}).items()))
        return self._json_payloads[(endpoint, normalized_params)]

    def get_text(self, endpoint: str, params=None) -> str:
        return self._text_payloads[endpoint]


class GitHubCIPipelineIntegrationTests(unittest.TestCase):
    def test_full_ci_analysis_pipeline_with_mocked_github(self) -> None:
        fetcher = GitHubLogFetcher(transport=FakeGitHubTransport())

        report = analyze_ci_url(
            "https://github.com/acme/widgets/pull/42",
            include_passed=True,
            max_passed_runs=2,
            fetcher=fetcher,
        )

        payload = report.to_dict()

        self.assertIn("test-snowflake", payload["root_cause"]["summary"])
        self.assertEqual(payload["metadata"]["total_runs_analyzed"], 2)
        self.assertEqual(payload["metadata"]["failed_runs"], 1)
        self.assertEqual(payload["metadata"]["passed_runs"], 1)
        self.assertEqual(len(payload["failed_blocks"]), 1)
        self.assertEqual(payload["passed_context"][0]["job_name"], "test-redshift")
        self.assertIn(
            "Failure occurs only in variant snowflake for job group test.",
            payload["cross_run_insights"],
        )


if __name__ == "__main__":
    unittest.main()
