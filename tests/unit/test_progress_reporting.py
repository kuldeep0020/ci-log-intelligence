"""Unit tests for the ``ProgressCallback`` plumbing across the MCP tools.

These tests assert that:

* ``list_failed_jobs`` emits an initial ``Resolving CI URL`` event and a final
  ``Done`` event at the coarse 0-100 tool-boundary scale.
* The inner per-job loop in ``fetch_with_cache_awareness`` fires once per
  planned job with a stable ``current/total`` counter that uses the real
  ``total_jobs`` denominator.
* Cache-hit jobs on a second pass emit a ``Cached:`` message instead of a
  ``Fetching log for`` message, so the client can distinguish the two.
* ``get_block`` emits the three documented milestones on the success path.
* ``analyze_ci_failure`` still emits ``Done`` even when ``top_k`` / filter
  truncation throws away every record.
* ``progress=None`` is a no-op end-to-end (the optionality contract holds).
"""

from __future__ import annotations

import unittest

from ci_log_intelligence.mcp import tools
from ci_log_intelligence.mcp.cache import JobCache

from ._mcp_stubs import JOB_URL, PR_URL, make_fetcher


class _Capture:
    """Small helper that captures ``(current, total, message)`` triples."""

    def __init__(self) -> None:
        self.events: list[tuple[int, int, str]] = []

    def __call__(self, current: int, total: int, message: str) -> None:
        self.events.append((current, total, message))


class ListFailedJobsProgressTests(unittest.TestCase):
    def test_list_failed_jobs_reports_resolve_and_done(self) -> None:
        """list_failed_jobs emits an initial Resolving event and final Done."""
        fetcher, _transport = make_fetcher()
        cache = JobCache()
        capture = _Capture()

        tools.list_failed_jobs(
            PR_URL,
            cache=cache,
            fetcher=fetcher,
            progress=capture,
        )

        self.assertGreaterEqual(
            len(capture.events),
            2,
            f"expected at least resolve + done; got: {capture.events}",
        )
        first_current, first_total, first_message = capture.events[0]
        self.assertEqual(first_current, 0)
        self.assertEqual(first_total, 100)
        self.assertIn("Resolv", first_message)
        self.assertEqual(capture.events[-1], (100, 100, "Done"))

    def test_per_job_progress_advances(self) -> None:
        """For a target with 2 failed jobs, per-job progress fires twice."""
        fetcher, _transport = make_fetcher()
        cache = JobCache()
        capture = _Capture()

        tools.list_failed_jobs(
            PR_URL,
            cache=cache,
            fetcher=fetcher,
            progress=capture,
        )

        fetch_events = [
            event for event in capture.events
            if "Fetching" in event[2] or "Cached" in event[2]
        ]
        self.assertEqual(
            len(fetch_events),
            2,
            f"expected one event per job; got: {fetch_events}",
        )
        self.assertTrue(
            all(event[1] == 2 for event in fetch_events),
            f"per-job total should equal job count (2); got: {fetch_events}",
        )
        self.assertEqual(
            [event[0] for event in fetch_events],
            [1, 2],
            "per-job current should advance 1, 2",
        )

    def test_cache_hit_emits_cached_message(self) -> None:
        """On the SECOND call, the job is a cache hit and the message says so."""
        fetcher, _transport = make_fetcher()
        cache = JobCache()

        # Warm the cache.
        tools.list_failed_jobs(PR_URL, cache=cache, fetcher=fetcher)

        # Capture the second pass.
        capture = _Capture()
        tools.list_failed_jobs(
            PR_URL,
            cache=cache,
            fetcher=fetcher,
            progress=capture,
        )

        cached_events = [
            event for event in capture.events if event[2].startswith("Cached:")
        ]
        self.assertEqual(
            len(cached_events),
            2,
            f"both jobs should be cache hits on second pass; got: {capture.events}",
        )
        self.assertEqual(capture.events[-1], (100, 100, "Done"))

    def test_planning_event_fires_first(self) -> None:
        """The inner per-job loop emits ``Planning log fetches`` before per-job events."""
        fetcher, _transport = make_fetcher()
        cache = JobCache()
        capture = _Capture()

        tools.list_failed_jobs(
            PR_URL,
            cache=cache,
            fetcher=fetcher,
            progress=capture,
        )

        messages = [event[2] for event in capture.events]
        self.assertIn("Planning log fetches", messages)
        planning_index = messages.index("Planning log fetches")
        # The first per-job message must come AFTER planning.
        per_job_indices = [
            index
            for index, message in enumerate(messages)
            if "Fetching" in message or message.startswith("Cached:")
        ]
        self.assertTrue(per_job_indices, "expected at least one per-job event")
        self.assertGreater(min(per_job_indices), planning_index)

    def test_final_all_logs_fetched_emitted(self) -> None:
        """fetch_with_cache_awareness emits ``All logs fetched`` after per-job events."""
        fetcher, _transport = make_fetcher()
        cache = JobCache()
        capture = _Capture()

        tools.list_failed_jobs(
            PR_URL,
            cache=cache,
            fetcher=fetcher,
            progress=capture,
        )

        messages = [event[2] for event in capture.events]
        self.assertIn("All logs fetched", messages)
        # The "All logs fetched" event uses total_jobs as the denominator.
        all_done_event = next(
            event for event in capture.events if event[2] == "All logs fetched"
        )
        self.assertEqual(all_done_event[0], 2)
        self.assertEqual(all_done_event[1], 2)


class GetBlockProgressTests(unittest.TestCase):
    def test_get_block_emits_three_milestones(self) -> None:
        """get_block emits Resolving, Fetching/loading, and Done."""
        fetcher, _transport = make_fetcher()
        cache = JobCache()
        capture = _Capture()

        response = tools.get_block(
            JOB_URL,
            block_index=0,
            cache=cache,
            fetcher=fetcher,
            progress=capture,
        )

        self.assertNotIn("error", response, f"unexpected error: {response}")
        # We expect at least the three documented milestones in order.
        self.assertGreaterEqual(len(capture.events), 3)
        self.assertEqual(capture.events[0][:2], (0, 3))
        self.assertIn("Resolv", capture.events[0][2])
        self.assertEqual(capture.events[1][:2], (1, 3))
        self.assertIn("Fetching/loading job", capture.events[1][2])
        self.assertEqual(capture.events[-1], (3, 3, "Done"))

    def test_get_block_error_path_does_not_emit_done(self) -> None:
        """A structured error response skips the final Done event."""
        fetcher, _transport = make_fetcher()
        cache = JobCache()
        capture = _Capture()

        response = tools.get_block(
            PR_URL,  # PR URL is invalid for get_block
            block_index=0,
            cache=cache,
            fetcher=fetcher,
            progress=capture,
        )

        self.assertEqual(response.get("code"), "invalid_url")
        # Only the initial Resolving event fired; no Done.
        self.assertEqual(len(capture.events), 1)
        self.assertIn("Resolv", capture.events[0][2])


class AnalyzeCIFailureProgressTests(unittest.TestCase):
    def test_analyze_ci_failure_with_filter_still_emits_done(self) -> None:
        """Filter + top_k truncation doesn't break the progress contract."""
        fetcher, _transport = make_fetcher()
        cache = JobCache()
        capture = _Capture()

        response = tools.analyze_ci_failure(
            PR_URL,
            top_k=1,
            failure_types=["hash_mismatch"],
            include_passed=False,
            cache=cache,
            fetcher=fetcher,
            progress=capture,
        )

        # Truncation didn't break tool execution.
        self.assertLessEqual(len(response["failures"]), 1)
        # Final event MUST be the (100, 100, "Done") boundary marker.
        self.assertEqual(capture.events[-1], (100, 100, "Done"))
        # First event MUST be the (0, 100, "Resolving CI URL") boundary marker.
        self.assertEqual(capture.events[0], (0, 100, "Resolving CI URL"))


class OptionalityContractTests(unittest.TestCase):
    def test_none_progress_is_no_op_for_list_failed_jobs(self) -> None:
        """progress=None never raises; existing behavior preserved."""
        fetcher, _transport = make_fetcher()
        cache = JobCache()

        response = tools.list_failed_jobs(
            PR_URL, cache=cache, fetcher=fetcher, progress=None
        )

        self.assertEqual(response["metadata"]["failed_jobs"], 2)

    def test_none_progress_is_no_op_for_analyze_ci_failure(self) -> None:
        fetcher, _transport = make_fetcher()
        cache = JobCache()

        response = tools.analyze_ci_failure(
            PR_URL,
            top_k=10,
            include_passed=False,
            cache=cache,
            fetcher=fetcher,
            progress=None,
        )

        self.assertGreater(response["metadata"]["failures_total"], 0)

    def test_none_progress_is_no_op_for_get_block(self) -> None:
        fetcher, _transport = make_fetcher()
        cache = JobCache()

        response = tools.get_block(
            JOB_URL, block_index=0, cache=cache, fetcher=fetcher, progress=None
        )

        self.assertNotIn("error", response)


if __name__ == "__main__":
    unittest.main()
