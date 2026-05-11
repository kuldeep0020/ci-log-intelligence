"""End-to-end MCP-tool pipeline test.

Walks the explore-then-drill flow:
    list_failed_jobs(url) -> analyze_ci_failure(url, failure_types=[...])
                          -> get_block(job_url, 0)

and verifies that:

* ``list_failed_jobs`` returns the expected per-job map.
* ``analyze_ci_failure`` returns the filtered + top-k truncated failures.
* ``get_block`` returns the expected anchor-bearing block content.
* All three calls share the cache: the fetcher's log-content endpoint is hit
  exactly once per failed job total, not once per tool call.
"""

from __future__ import annotations

import unittest

from ci_log_intelligence.ingestion.github.fetcher import (
    GitHubLogFetcher,
    GitHubTransport,
)
from ci_log_intelligence.mcp import tools
from ci_log_intelligence.mcp.cache import JobCache


_PR_URL = "https://github.com/acme/widgets/pull/42"


_HASH_MISMATCH_LOG = """STEP: execute
common.go:1058: file hashes don't match for golden output
    /tmp/test/Material_X.yaml and
    ../samples/test_partial.xtra/test_output/Material_X_HASH_1.yaml
--- FAIL: TestRunSetPartialFeatureTable (45.3s)
FAIL    github.com/acme/widgets/internal/runset 45.301s
"""


_PYTEST_FAIL_LOG = """STEP: execute
tests/test_query.py::test_query FAILED
Traceback (most recent call last):
  File "tests/test_query.py", line 10, in test_query
    assert result == expected
AssertionError: query mismatch
"""


_JOB_LOGS: dict[int, str] = {
    501: _PYTEST_FAIL_LOG,
    502: _HASH_MISMATCH_LOG,
}


class _ScriptedTransport(GitHubTransport):
    """Records every call so the integration test can assert per-endpoint counts."""

    def __init__(self) -> None:
        self.text_call_counts: dict[str, int] = {}
        self.json_calls: list[str] = []
        self._json_payloads = {
            ("repos/acme/widgets/pulls/42", ()): {"head": {"sha": "abc"}},
            (
                "repos/acme/widgets/actions/runs",
                (("head_sha", "abc"), ("per_page", 5)),
            ): {
                "workflow_runs": [
                    {
                        "id": 200,
                        "workflow_id": 10,
                        "head_branch": "main",
                        "head_sha": "abc",
                        "html_url": "https://github.com/acme/widgets/actions/runs/200",
                        "status": "completed",
                        "conclusion": "failure",
                        "display_title": "CI",
                    }
                ]
            },
            (
                "repos/acme/widgets/actions/runs/200/jobs",
                (("per_page", 100),),
            ): {
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
            },
            "repos/acme/widgets/actions/runs/200": {
                "id": 200,
                "workflow_id": 10,
                "head_branch": "main",
                "head_sha": "abc",
                "html_url": "https://github.com/acme/widgets/actions/runs/200",
                "status": "completed",
                "conclusion": "failure",
                "display_title": "CI",
            },
        }

    def get_json(self, endpoint, params=None):
        normalized = tuple(sorted((params or {}).items()))
        self.json_calls.append(endpoint)
        if endpoint == "repos/acme/widgets/actions/runs/200" and not normalized:
            return self._json_payloads[endpoint]
        return self._json_payloads[(endpoint, normalized)]

    def get_text(self, endpoint, params=None) -> str:
        self.text_call_counts[endpoint] = self.text_call_counts.get(endpoint, 0) + 1
        parts = endpoint.strip("/").split("/")
        job_id = int(parts[-2])
        return _JOB_LOGS[job_id]


class MCPPipelineIntegrationTests(unittest.TestCase):
    def test_list_failed_jobs_repeat_call_uses_cache(self) -> None:
        """A second ``list_failed_jobs`` against the same URL must NOT refetch logs.

        ``list_failed_jobs`` claims to be cheap. The first call populates the
        per-job cache; the second call should hit the cache and trigger zero
        additional log-content fetches. Without the cache-aware fetch path
        this regresses to 2x text fetches on the repeat call.
        """
        transport = _ScriptedTransport()
        fetcher = GitHubLogFetcher(transport=transport)
        cache = JobCache()

        tools.list_failed_jobs(_PR_URL, cache=cache, fetcher=fetcher)
        text_calls_after_first = sum(transport.text_call_counts.values())
        self.assertEqual(
            text_calls_after_first,
            2,
            "first list_failed_jobs call must fetch each failed job's log exactly once",
        )

        tools.list_failed_jobs(_PR_URL, cache=cache, fetcher=fetcher)
        text_calls_after_second = sum(transport.text_call_counts.values())

        self.assertEqual(
            text_calls_after_second,
            text_calls_after_first,
            "repeat list_failed_jobs call must hit the cache and trigger ZERO new text fetches",
        )

    def test_explore_then_drill_with_shared_cache(self) -> None:
        transport = _ScriptedTransport()
        fetcher = GitHubLogFetcher(transport=transport)
        cache = JobCache()

        # Stage 1: list_failed_jobs (cheap map).
        list_response = tools.list_failed_jobs(_PR_URL, cache=cache, fetcher=fetcher)

        self.assertEqual(list_response["metadata"]["failed_jobs"], 2)
        self.assertEqual(list_response["metadata"]["total_runs_analyzed"], 1)
        job_summaries = list_response["jobs"]
        self.assertEqual(
            sorted(item["job_name"] for item in job_summaries),
            ["postgres-test (bundling)", "snowflake-test (non-bundling)"],
        )
        snowflake = next(
            item for item in job_summaries if item["job_id"] == 502
        )
        self.assertGreaterEqual(snowflake["block_count"], 1)
        self.assertIn("hash_mismatch", snowflake["failure_types_present"])

        text_calls_after_list = dict(transport.text_call_counts)
        self.assertEqual(sum(text_calls_after_list.values()), 2)

        # Stage 2: analyze_ci_failure with failure_types filter.
        analyze_response = tools.analyze_ci_failure(
            _PR_URL,
            top_k=10,
            failure_types=["hash_mismatch"],
            include_passed=False,
            cache=cache,
            fetcher=fetcher,
        )

        self.assertEqual(
            transport.text_call_counts,
            text_calls_after_list,
            "log-content fetcher must NOT be called again on cache-hit analyze",
        )
        failures = analyze_response["failures"]
        self.assertTrue(failures, "expected at least one hash_mismatch failure")
        for record in failures:
            self.assertEqual(record["type"], "hash_mismatch")
        metadata = analyze_response["metadata"]
        self.assertEqual(metadata["failures_returned"], len(failures))
        self.assertGreaterEqual(metadata["failures_total"], metadata["failures_returned"])

        # Stage 3: get_block on the snowflake job, block 0.
        block_response = tools.get_block(
            snowflake["job_url"],
            block_index=0,
            surround=3,
            cache=cache,
            fetcher=fetcher,
        )

        self.assertEqual(
            transport.text_call_counts,
            text_calls_after_list,
            "log-content fetcher must NOT be called again on cache-hit get_block",
        )
        self.assertNotIn("error", block_response)
        self.assertEqual(block_response["job_id"], 502)
        self.assertEqual(block_response["block_index"], 0)
        self.assertEqual(block_response["type"], "hash_mismatch")
        lines = block_response["lines"]
        self.assertTrue(any(item["is_anchor"] for item in lines))
        self.assertTrue(any(item["in_block"] for item in lines))


if __name__ == "__main__":
    unittest.main()
