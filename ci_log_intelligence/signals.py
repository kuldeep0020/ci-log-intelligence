"""Canonical signal patterns shared by parsing and the generic detector.

Two consumers read these:

* :mod:`ci_log_intelligence.parsing` -- fills ``ParsedLine.signals`` so the
  classifier and signal-density scorer have something to look at.
* :mod:`ci_log_intelligence.reducer.detectors.generic` -- emits anchors with
  matching severity tiers.

Both must always agree on (a) which patterns match a line and (b) which
keywords are filtered as benign mentions. Defining them once here makes that
invariant load-bearing.
"""

from __future__ import annotations

import re
from typing import Final


SignalSpec = tuple[str, int, "re.Pattern[str]"]


SIGNAL_PATTERNS: Final[list[SignalSpec]] = [
    ("traceback", 3, re.compile(r"Traceback \(most recent call last\):")),
    ("exception", 3, re.compile(r"\bException\b")),
    ("error", 3, re.compile(r"\bERROR\b", re.IGNORECASE)),
    ("failed", 2, re.compile(r"\bFAILED\b", re.IGNORECASE)),
    ("assertion_error", 2, re.compile(r"\bAssertionError\b")),
    ("warning", 1, re.compile(r"\bWARNING\b", re.IGNORECASE)),
    ("retrying", 1, re.compile(r"\bRetrying\b", re.IGNORECASE)),
]


# Signals that are suppressed when the line reads as a benign report (e.g. "0 errors").
_FILTERABLE_BENIGN_SIGNALS: Final[frozenset[str]] = frozenset({"error", "failed", "warning"})


BENIGN_PATTERNS: Final[list["re.Pattern[str]"]] = [
    re.compile(r"\b(0|no)\s+errors?\b", re.IGNORECASE),
    re.compile(r"\b(0|no)\s+(failures?|failed)\b", re.IGNORECASE),
    re.compile(r"\b(errors?|failures?)\s*[:=]\s*0\b", re.IGNORECASE),
    re.compile(r"\bno\s+failures\b", re.IGNORECASE),
]


def is_benign_mention(content: str) -> bool:
    """Return True if ``content`` reads as a benign report (e.g. "0 errors found")."""
    return any(pattern.search(content) for pattern in BENIGN_PATTERNS)


def signal_is_filterable(signal_name: str) -> bool:
    """Whether this signal is one of the ones the benign-mention filter applies to."""
    return signal_name in _FILTERABLE_BENIGN_SIGNALS


__all__ = [
    "BENIGN_PATTERNS",
    "SIGNAL_PATTERNS",
    "SignalSpec",
    "is_benign_mention",
    "signal_is_filterable",
]
