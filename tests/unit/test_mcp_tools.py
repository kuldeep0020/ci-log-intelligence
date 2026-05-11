"""Unit tests for the three MCP tool functions in ``ci_log_intelligence.mcp.tools``.

These tests exercise the tool functions directly with a stub
``GitHubTransport`` to avoid hitting GitHub. They cover:

* ``list_failed_jobs`` returns a per-job summary with ``failure_types_present``
  and ``classifications``.
* ``analyze_ci_failure`` applies ``failure_types`` filtering and ``top_k``
  truncation; ``metadata.failures_returned`` / ``failures_total`` reflect both.
* ``get_block`` returns full block content with ``surround`` context lines and
  reports out-of-range / invalid-URL errors as structured dicts.
* The cache short-circuits repeated calls across tools.

Shared stub transport and fixtures live in ``_mcp_stubs.py``.
"""

from __future__ import annotations

import unittest

from ci_log_intelligence.mcp import tools
from ci_log_intelligence.mcp.cache import CacheKey, JobCache

from ._mcp_stubs import JOB_URL, PR_URL, make_fetcher


class ListFailedJobsTests(unittest.TestCase):
    def test_returns_per_job_summary_without_block_content(self) -> None:
        fetcher, _transport = make_fetcher()
        cache = JobCache()

        response = tools.list_failed_jobs(PR_URL, cache=cache, fetcher=fetcher)

        self.assertEqual(response["metadata"]["failed_jobs"], 2)
        self.assertEqual(response["metadata"]["total_runs_analyzed"], 1)

        jobs = response["jobs"]
        job_names = sorted(item["job_name"] for item in jobs)
        self.assertEqual(
            job_names,
            ["postgres-test (bundling)", "snowflake-test (non-bundling)"],
        )

        # No per-block content fields are leaked into the summary.
        for item in jobs:
            self.assertNotIn("log_excerpt", item)
            self.assertNotIn("extracted_fields", item)
            self.assertIn("block_count", item)
            self.assertIn("failure_types_present", item)
            self.assertIn("classifications", item)
            self.assertIn("job_url", item)
            self.assertTrue(
                str(item["job_url"]).startswith(
                    "https://github.com/acme/widgets/actions/runs/200/job/"
                )
            )
            self.assertIsInstance(item["failure_types_present"], list)
            self.assertIsInstance(item["classifications"], dict)


class AnalyzeCIFailureTests(unittest.TestCase):
    def test_top_k_truncates_failures_array(self) -> None:
        fetcher, _transport = make_fetcher()
        cache = JobCache()

        response = tools.analyze_ci_failure(
            PR_URL,
            top_k=1,
            include_passed=False,
            cache=cache,
            fetcher=fetcher,
        )

        self.assertLessEqual(len(response["failures"]), 1)
        metadata = response["metadata"]
        self.assertEqual(metadata["failures_returned"], len(response["failures"]))
        self.assertGreaterEqual(metadata["failures_total"], metadata["failures_returned"])

    def test_failure_types_filter_keeps_only_matching_records(self) -> None:
        fetcher, _transport = make_fetcher()
        cache = JobCache()

        response = tools.analyze_ci_failure(
            PR_URL,
            top_k=10,
            failure_types=["hash_mismatch"],
            include_passed=False,
            cache=cache,
            fetcher=fetcher,
        )

        types = {record["type"] for record in response["failures"]}
        self.assertTrue(
            types.issubset({"hash_mismatch"}),
            f"unexpected types present after filter: {types}",
        )
        self.assertGreaterEqual(
            response["metadata"]["failures_total"],
            response["metadata"]["failures_returned"],
        )

    def test_cache_hit_skips_fetcher_on_repeat_call(self) -> None:
        fetcher, transport = make_fetcher()
        cache = JobCache()

        tools.analyze_ci_failure(
            PR_URL,
            top_k=10,
            include_passed=False,
            cache=cache,
            fetcher=fetcher,
        )
        text_calls_after_first = list(transport.text_calls)

        tools.analyze_ci_failure(
            PR_URL,
            top_k=10,
            include_passed=False,
            cache=cache,
            fetcher=fetcher,
        )

        self.assertEqual(
            transport.text_calls,
            text_calls_after_first,
            "text-endpoint calls should not increase on a cache-hit repeat call",
        )
        self.assertEqual(
            len(transport.text_calls),
            2,
            "two log endpoints should be hit exactly once each",
        )


class GetBlockTests(unittest.TestCase):
    def test_returns_block_content_with_surround_context(self) -> None:
        fetcher, _transport = make_fetcher()
        cache = JobCache()

        # Warm the cache so we know there is at least one block.
        list_response = tools.list_failed_jobs(PR_URL, cache=cache, fetcher=fetcher)
        snowflake_summary = next(
            item for item in list_response["jobs"] if item["job_id"] == 502
        )
        self.assertGreaterEqual(snowflake_summary["block_count"], 1)
        job_url = snowflake_summary["job_url"]

        response = tools.get_block(
            job_url, block_index=0, surround=2, cache=cache, fetcher=fetcher
        )

        self.assertNotIn("error", response)
        self.assertEqual(response["job_id"], 502)
        self.assertEqual(response["run_id"], 200)
        self.assertEqual(response["block_index"], 0)
        self.assertIn("start_line", response)
        self.assertIn("end_line", response)

        lines = response["lines"]
        self.assertTrue(lines, "lines array should not be empty")
        self.assertTrue(any(item["is_anchor"] for item in lines))
        self.assertTrue(any(item["in_block"] for item in lines))
        numbers = [item["line_number"] for item in lines]
        self.assertEqual(numbers, sorted(numbers))

    def test_out_of_range_index_returns_structured_error(self) -> None:
        fetcher, _transport = make_fetcher()
        cache = JobCache()

        response = tools.get_block(JOB_URL, block_index=999, cache=cache, fetcher=fetcher)

        self.assertEqual(response.get("code"), "index_out_of_range")
        self.assertIn("block_count", response)

    def test_invalid_url_returns_structured_error(self) -> None:
        cache = JobCache()
        fetcher, _ = make_fetcher()

        response = tools.get_block(PR_URL, block_index=0, cache=cache, fetcher=fetcher)

        self.assertEqual(response.get("code"), "invalid_url")
        self.assertIn("error", response)

    def test_get_block_cache_miss_fetches_single_job(self) -> None:
        fetcher, transport = make_fetcher()
        cache = JobCache()

        response = tools.get_block(JOB_URL, block_index=0, cache=cache, fetcher=fetcher)

        self.assertNotIn("error", response, f"unexpected error: {response}")
        log_calls = [endpoint for endpoint in transport.text_calls if endpoint.endswith("/logs")]
        self.assertEqual(len(log_calls), 1)
        self.assertIn("/jobs/501/logs", log_calls[0])

        # The cache-miss path must POPULATE the cache so subsequent calls can
        # short-circuit; without this assertion the test only proves a single
        # fetch occurred, not that the result was retained.
        self.assertIsNotNone(
            cache.get(CacheKey(repo="acme/widgets", run_id=200, job_id=501)),
            "cache-miss fetch must populate the cache with the (repo, run, job) entry",
        )


class CrossToolCachingTests(unittest.TestCase):
    def test_list_then_analyze_does_not_refetch_logs(self) -> None:
        fetcher, transport = make_fetcher()
        cache = JobCache()

        tools.list_failed_jobs(PR_URL, cache=cache, fetcher=fetcher)
        text_calls_after_list = list(transport.text_calls)
        self.assertEqual(len(text_calls_after_list), 2)

        tools.analyze_ci_failure(
            PR_URL,
            top_k=10,
            include_passed=False,
            cache=cache,
            fetcher=fetcher,
        )

        self.assertEqual(transport.text_calls, text_calls_after_list)

    def test_analyze_then_get_block_shares_cache(self) -> None:
        fetcher, transport = make_fetcher()
        cache = JobCache()

        tools.analyze_ci_failure(
            PR_URL,
            top_k=10,
            include_passed=False,
            cache=cache,
            fetcher=fetcher,
        )
        text_calls_after_analyze = list(transport.text_calls)

        tools.get_block(JOB_URL, block_index=0, cache=cache, fetcher=fetcher)

        self.assertEqual(transport.text_calls, text_calls_after_analyze)


class AnalyzeCIFailureFilterDefaultsTests(unittest.TestCase):
    def test_no_failure_types_keeps_all_records(self) -> None:
        fetcher, _ = make_fetcher()
        cache = JobCache()

        response = tools.analyze_ci_failure(
            PR_URL,
            top_k=10,
            failure_types=None,
            include_passed=False,
            cache=cache,
            fetcher=fetcher,
        )

        types = {record["type"] for record in response["failures"]}
        self.assertIn("hash_mismatch", types)
        self.assertEqual(
            response["metadata"]["failures_returned"],
            response["metadata"]["failures_total"],
        )

    def test_unknown_failure_type_filter_empties_failures_but_keeps_root_cause(self) -> None:
        fetcher, _ = make_fetcher()
        cache = JobCache()

        response = tools.analyze_ci_failure(
            PR_URL,
            top_k=10,
            failure_types=["nonexistent_type"],
            include_passed=False,
            cache=cache,
            fetcher=fetcher,
        )

        self.assertEqual(response["failures"], [])
        self.assertNotEqual(response["root_cause"]["summary"], "")
        self.assertGreater(response["metadata"]["failures_total"], 0)
        self.assertEqual(response["metadata"]["failures_returned"], 0)


class GetBlockSurroundTests(unittest.TestCase):
    def test_surround_zero_returns_only_block_lines(self) -> None:
        fetcher, _ = make_fetcher()
        cache = JobCache()

        response = tools.get_block(
            JOB_URL, block_index=0, surround=0, cache=cache, fetcher=fetcher
        )

        self.assertNotIn("error", response)
        lines = response["lines"]
        self.assertTrue(lines)
        for entry in lines:
            self.assertTrue(entry["in_block"])

    def test_surround_clamps_to_first_line(self) -> None:
        fetcher, _ = make_fetcher()
        cache = JobCache()

        response = tools.get_block(
            JOB_URL, block_index=0, surround=999, cache=cache, fetcher=fetcher
        )

        self.assertNotIn("error", response)
        line_numbers = [item["line_number"] for item in response["lines"]]
        self.assertGreaterEqual(min(line_numbers), 1)


if __name__ == "__main__":
    unittest.main()
