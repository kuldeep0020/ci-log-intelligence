from __future__ import annotations

import re
from datetime import datetime
from typing import Iterable, Optional

from ..models import ParsedLine, StoredLog
from ..signals import SIGNAL_PATTERNS, is_benign_mention, signal_is_filterable
from ..storage import StorageBackend

_STEP_PATTERNS = [
    re.compile(r"^STEP:\s*(?P<step>.+?)\s*$"),
    re.compile(r"^\[step:(?P<step>[^\]]+)\]\s*$"),
    re.compile(r"^::group::(?P<step>.+?)\s*$"),
    re.compile(r"^##\[group\](?P<step>.+?)\s*$"),
    re.compile(r"^=+>>?\s+(?P<step>.+?)\s*$"),
    re.compile(r"^---\s+(?P<step>\S.+?)\s*$"),
]

_TIMESTAMP_PATTERNS = [
    ("%Y-%m-%d %H:%M:%S", re.compile(r"^(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})")),
    ("%Y-%m-%dT%H:%M:%S", re.compile(r"^(?P<ts>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})")),
]

# GitHub Actions raw log lines are prefixed with a UTC timestamp of the form
# ``2024-01-15T12:34:56.789Z`` (milliseconds optional, trailing ``Z`` optional).
# Step-marker and timestamp detection patterns are anchored with ``^`` so they
# must be applied to a prefix-stripped variant of the line as well as the raw
# original.
_TIMESTAMP_PREFIX_PATTERN = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z?\s+"
)

def _strip_timestamp_prefix(content: str) -> str:
    return _TIMESTAMP_PREFIX_PATTERN.sub("", content, count=1)


def parse_log(stored_log: StoredLog, storage_backend: StorageBackend) -> list[ParsedLine]:
    parsed_lines: list[ParsedLine] = []
    current_step: Optional[str] = None

    for line_number, line in enumerate(storage_backend.iter_lines(stored_log.reference), start=1):
        next_step = detect_step_id(line)
        if next_step is not None:
            current_step = next_step

        parsed_lines.append(
            ParsedLine(
                line_number=line_number,
                content=line,
                timestamp=parse_timestamp(line),
                step_id=current_step,
                signals=detect_signals(line),
            )
        )

    return parsed_lines


def detect_step_id(content: str) -> Optional[str]:
    candidates = (content, _strip_timestamp_prefix(content))
    for candidate in candidates:
        for pattern in _STEP_PATTERNS:
            match = pattern.search(candidate)
            if match:
                return match.group("step").strip()
    return None


def parse_timestamp(content: str) -> Optional[datetime]:
    candidates = (content, _strip_timestamp_prefix(content))
    for candidate in candidates:
        for time_format, pattern in _TIMESTAMP_PATTERNS:
            match = pattern.search(candidate)
            if match:
                return datetime.strptime(match.group("ts"), time_format)
    return None


def detect_signals(content: str) -> list[str]:
    benign = is_benign_mention(content)
    signals: list[str] = []
    for name, _severity, pattern in SIGNAL_PATTERNS:
        if not pattern.search(content):
            continue
        if benign and signal_is_filterable(name):
            continue
        signals.append(name)
    return signals


def iter_signal_lines(lines: Iterable[ParsedLine]) -> list[ParsedLine]:
    return [line for line in lines if line.signals]
