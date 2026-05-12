"""Progress reporting types shared across the toolchain.

The MCP server tools wire FastMCP's ``Context.report_progress`` into a
:data:`ProgressCallback` so the slow per-job log-fetch loop in the
analyzer can report "fetching job 3 of 10" back to the client. The
callback is intentionally a plain sync callable so the analyzer and
fetcher modules stay async-free; the FastMCP bridge in
:mod:`ci_log_intelligence.mcp.server` is the only place that turns
sync callback invocations into async progress notifications.
"""

from __future__ import annotations

from typing import Callable, Optional


# (current_step, total_steps, human_readable_message)
ProgressCallback = Callable[[int, int, str], None]


def report(
    callback: Optional[ProgressCallback],
    current: int,
    total: int,
    message: str,
) -> None:
    """Invoke ``callback`` if set. No-op when ``callback`` is None.

    Centralizing the None-check keeps every emission site to a single
    line at the caller, rather than ``if progress: progress(...)``.
    """
    if callback is not None:
        callback(current, total, message)


__all__ = ["ProgressCallback", "report"]
