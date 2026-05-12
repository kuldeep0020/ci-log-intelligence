"""Tests for the FastMCP ``Context`` -> sync ``ProgressCallback`` bridge.

The bridge lives in ``ci_log_intelligence.mcp.server._make_progress_bridge``
and is the seam that lets the synchronous tool body (running on a worker
thread via ``asyncio.to_thread``) emit progress events that FastMCP can
relay to the MCP client as ``notifications/progress`` messages. Each test
uses ``unittest.IsolatedAsyncioTestCase`` so the bridge sees a real running
event loop without leaking state between tests.
"""

from __future__ import annotations

import asyncio
import unittest

from ci_log_intelligence.mcp.server import _make_progress_bridge


class _FakeContext:
    """Minimal stand-in for FastMCP's Context that records progress calls."""

    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def report_progress(
        self,
        *,
        progress: float,
        total: float | None = None,
        message: str | None = None,
    ) -> None:
        self.calls.append({"progress": progress, "total": total, "message": message})


class BridgeTests(unittest.IsolatedAsyncioTestCase):
    async def test_bridge_schedules_progress_on_loop(self) -> None:
        """A sync bridge call from a worker thread reaches the event loop."""
        ctx = _FakeContext()
        bridge = _make_progress_bridge(ctx)
        self.assertIsNotNone(bridge)
        assert bridge is not None  # narrow for type-checker / runtime safety

        # Simulate the same path the real tool body takes: a sync invocation
        # of the bridge from inside a worker thread.
        await asyncio.to_thread(bridge, 1, 5, "fetching job 1")
        # Yield once so the scheduled coroutine on the main loop can run.
        await asyncio.sleep(0.05)

        self.assertEqual(len(ctx.calls), 1)
        self.assertEqual(ctx.calls[0]["progress"], 1)
        self.assertEqual(ctx.calls[0]["total"], 5)
        self.assertEqual(ctx.calls[0]["message"], "fetching job 1")

    async def test_bridge_returns_none_when_ctx_missing(self) -> None:
        """No Context -> no bridge -> direct callers handle the None case."""
        self.assertIsNone(_make_progress_bridge(None))

    async def test_bridge_relays_multiple_events_in_order(self) -> None:
        """Several sync invocations reach the loop in the order they were called."""
        ctx = _FakeContext()
        bridge = _make_progress_bridge(ctx)
        assert bridge is not None

        def emit_several() -> None:
            bridge(1, 3, "fetching job 1")
            bridge(2, 3, "fetching job 2")
            bridge(3, 3, "All logs fetched")

        await asyncio.to_thread(emit_several)
        # Three coroutines were scheduled; give the loop time to run them all.
        await asyncio.sleep(0.05)

        self.assertEqual(len(ctx.calls), 3)
        self.assertEqual(
            [call["progress"] for call in ctx.calls],
            [1, 2, 3],
        )
        self.assertEqual(
            [call["message"] for call in ctx.calls],
            ["fetching job 1", "fetching job 2", "All logs fetched"],
        )


if __name__ == "__main__":
    unittest.main()
