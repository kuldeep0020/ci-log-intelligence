"""End-to-end MCP-tool sequencing scenarios.

* A3 -- cache behavior under the MCP-tool sequencing ``list_failed_jobs ->
  analyze_ci_failure -> get_block``: subsequent calls trigger zero new
  text fetches.
* A4 -- ``failure_types`` filter and ``top_k`` truncation, with the
  ``metadata.failures_total`` invariant that it reflects the PRE-filter
  count so the agent can detect truncation.

The detector-layer scenarios live in ``test_end_to_end_scenarios.py``.
"""

from __future__ import annotations

import unittest

from ci_log_intelligence.ingestion.github.fetcher import (
    GitHubLogFetcher,
    GitHubTransport,
)
from ci_log_intelligence.mcp import tools
from ci_log_intelligence.mcp.cache import JobCache

from ._e2e_helpers import build_single_report


# ---------------------------------------------------------------------------
# A3 -- Cache behavior under MCP tool sequencing
# ---------------------------------------------------------------------------


_PR_URL = "https://github.com/acme/widgets/pull/42"
_RUN_PAYLOAD = {
    "id": 200,
    "workflow_id": 10,
    "head_branch": "main",
    "head_sha": "abc",
    "html_url": "https://github.com/acme/widgets/actions/runs/200",
    "status": "completed",
    "conclusion": "failure",
    "display_title": "CI",
}

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
FAILED tests/test_query.py::test_query - AssertionError: query mismatch
"""


class _ToolSequenceTransport(GitHubTransport):
    """Scripted transport that lets the test count log-content fetches per endpoint."""

    _LOG_BY_JOB_ID = {
        501: _PYTEST_FAIL_LOG,
        502: _HASH_MISMATCH_LOG,
    }

    def __init__(self) -> None:
        self.text_calls: list[str] = []
        self.json_calls: list[tuple] = []
        self._json_payloads: dict = {
            ("repos/acme/widgets/pulls/42", ()): {"head": {"sha": "abc"}},
            (
                "repos/acme/widgets/actions/runs",
                (("head_sha", "abc"), ("per_page", 5)),
            ): {"workflow_runs": [_RUN_PAYLOAD]},
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
            "repos/acme/widgets/actions/runs/200": _RUN_PAYLOAD,
        }

    def get_json(self, endpoint, params=None):
        normalized = tuple(sorted((params or {}).items()))
        self.json_calls.append((endpoint, normalized))
        if endpoint == "repos/acme/widgets/actions/runs/200" and not normalized:
            return self._json_payloads[endpoint]
        return self._json_payloads[(endpoint, normalized)]

    def get_text(self, endpoint, params=None) -> str:
        self.text_calls.append(endpoint)
        parts = endpoint.strip("/").split("/")
        job_id = int(parts[-2])
        return self._LOG_BY_JOB_ID[job_id]


class CacheBehaviorUnderToolSequencingTests(unittest.TestCase):
    """A3: subsequent MCP calls must NOT trigger new log-content fetches.

    Threading the same ``JobCache`` and stub transport through
    ``list_failed_jobs -> analyze_ci_failure -> get_block`` keeps the
    ``text_calls`` count at N (one per failed job) throughout. The cache
    contract is the value proposition of the explore-then-drill surface;
    if it breaks, agent latency and cost balloon.
    """

    def test_text_fetch_count_stays_at_n_across_three_calls(self) -> None:
        transport = _ToolSequenceTransport()
        fetcher = GitHubLogFetcher(transport=transport)
        cache = JobCache()

        # Call 1: list_failed_jobs populates the cache for both failed jobs.
        list_response = tools.list_failed_jobs(
            _PR_URL, cache=cache, fetcher=fetcher
        )
        self.assertEqual(list_response["metadata"]["failed_jobs"], 2)
        text_calls_after_list = len(transport.text_calls)
        self.assertEqual(
            text_calls_after_list, 2,
            "list_failed_jobs must fetch each failed job's log exactly once",
        )

        # Call 2: analyze_ci_failure on the same URL -- zero new text fetches.
        analyze_response = tools.analyze_ci_failure(
            _PR_URL,
            top_k=10,
            include_passed=False,
            cache=cache,
            fetcher=fetcher,
        )
        self.assertGreaterEqual(len(analyze_response["failures"]), 1)
        self.assertEqual(
            len(transport.text_calls), text_calls_after_list,
            "analyze_ci_failure must NOT trigger new text fetches when cache covers all failed jobs",
        )

        # Call 3: get_block on the snowflake job, block 0 -- still zero new fetches.
        snowflake_job_url = next(
            item["job_url"]
            for item in list_response["jobs"]
            if item["job_id"] == 502
        )
        block_response = tools.get_block(
            snowflake_job_url, block_index=0, surround=3,
            cache=cache, fetcher=fetcher,
        )
        self.assertNotIn("error", block_response)
        self.assertEqual(
            len(transport.text_calls), text_calls_after_list,
            "get_block must NOT trigger new text fetches when cache covers the job",
        )


# ---------------------------------------------------------------------------
# A4 -- Filter and top_k behavior
# ---------------------------------------------------------------------------


def _build_a4_log() -> str:
    """Fixture for A4: 5 generic blocks (each in its own step so clustering
    keeps them separate), 2 pytest failures (separate steps), 1 hash mismatch.

    Total expected ``FailureRecord`` count before filter: 8. The filter
    ``["pytest_fail", "hash_mismatch"]`` retains 3.
    """
    lines: list[str] = []
    for index in range(5):
        lines.append(f"STEP: generic-step-{index}")
        lines.append(f"some setup output {index}")
        lines.append(f"ERROR generic error number {index}")
        lines.append(f"trailing output {index}")
    lines.append("STEP: pytest-one")
    lines.append("collecting tests")
    lines.append("FAILED tests/test_alpha.py::test_alpha - AssertionError: alpha")
    lines.append("STEP: pytest-two")
    lines.append("collecting tests")
    lines.append("FAILED tests/test_beta.py::test_beta - AssertionError: beta")
    lines.append("STEP: integration-test")
    lines.append(
        "common.go:1058: file hashes don't match for "
        "/tmp/Material_X.yaml and ../samples/Material_X_HASH_1.yaml"
    )
    lines.append("--- FAIL: TestHashThing (1.0s)")
    return "\n".join(lines) + "\n"


class FilterAndTopKBehaviorTests(unittest.TestCase):
    """A4: ``failure_types`` and ``top_k`` produce the documented record count."""

    A4_LOG = _build_a4_log()

    def test_pre_filter_record_count_is_eight(self) -> None:
        """Sanity-check the fixture: 5 generic + 2 pytest + 1 hash_mismatch == 8."""
        _result, report, _analysis = build_single_report(self.A4_LOG)
        type_counts: dict[str, int] = {}
        for record in report.failures:
            type_counts[record.type] = type_counts.get(record.type, 0) + 1
        self.assertEqual(
            type_counts.get("generic", 0), 5,
            f"expected 5 generic FailureRecords; got {type_counts}",
        )
        self.assertEqual(
            type_counts.get("pytest_fail", 0), 2,
            f"expected 2 pytest_fail FailureRecords; got {type_counts}",
        )
        self.assertEqual(
            type_counts.get("hash_mismatch", 0), 1,
            f"expected 1 hash_mismatch FailureRecord; got {type_counts}",
        )

    def test_filter_with_topk_three_returns_three_typed_records(self) -> None:
        _result, report, _analysis = build_single_report(
            self.A4_LOG,
            failure_types=["pytest_fail", "hash_mismatch"],
            top_k=3,
        )

        self.assertEqual(len(report.failures), 3)
        for record in report.failures:
            self.assertIn(
                record.type, {"pytest_fail", "hash_mismatch"},
                f"unexpected type after filter: {record.type}",
            )
        self.assertEqual(report.metadata.failures_returned, 3)
        # ``failures_total`` is the PRE-filter count so callers can detect
        # truncation. With 5 generic + 2 pytest + 1 hash_mismatch the
        # unfiltered total is 8.
        self.assertEqual(
            report.metadata.failures_total, 8,
            "failures_total must report the PRE-filter count",
        )

    def test_filter_with_topk_one_truncates_to_one(self) -> None:
        _result, report, _analysis = build_single_report(
            self.A4_LOG,
            failure_types=["pytest_fail", "hash_mismatch"],
            top_k=1,
        )

        self.assertEqual(len(report.failures), 1)
        self.assertEqual(report.metadata.failures_returned, 1)
        self.assertEqual(
            report.metadata.failures_total, 8,
            "failures_total stays at the PRE-filter count regardless of top_k",
        )
        self.assertIn(report.failures[0].type, {"pytest_fail", "hash_mismatch"})


if __name__ == "__main__":
    unittest.main()
