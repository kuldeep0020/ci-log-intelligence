from __future__ import annotations

import re
from datetime import datetime
from typing import Iterable, Optional

from ..models import ParsedLine, StoredLog
from ..storage import StorageBackend

_STEP_PATTERNS = [
    re.compile(r"^STEP:\s*(?P<step>.+?)\s*$"),
    re.compile(r"^\[step:(?P<step>[^\]]+)\]\s*$"),
    re.compile(r"^::group::(?P<step>.+?)\s*$"),
    re.compile(r"^##\[group\](?P<step>.+?)\s*$"),
]

_TIMESTAMP_PATTERNS = [
    ("%Y-%m-%d %H:%M:%S", re.compile(r"^(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})")),
    ("%Y-%m-%dT%H:%M:%S", re.compile(r"^(?P<ts>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})")),
]

_SIGNAL_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("traceback", re.compile(r"Traceback \(most recent call last\):")),
    ("exception", re.compile(r"Exception")),
    ("error", re.compile(r"ERROR")),
    ("failed", re.compile(r"FAILED")),
    ("assertion_error", re.compile(r"AssertionError")),
    ("warning", re.compile(r"WARNING")),
    ("retrying", re.compile(r"Retrying")),
]


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
    for pattern in _STEP_PATTERNS:
        match = pattern.search(content)
        if match:
            return match.group("step").strip()
    return None


def parse_timestamp(content: str) -> Optional[datetime]:
    for time_format, pattern in _TIMESTAMP_PATTERNS:
        match = pattern.search(content)
        if match:
            return datetime.strptime(match.group("ts"), time_format)
    return None


def detect_signals(content: str) -> list[str]:
    signals = [name for name, pattern in _SIGNAL_PATTERNS if pattern.search(content)]
    return signals


def iter_signal_lines(lines: Iterable[ParsedLine]) -> list[ParsedLine]:
    return [line for line in lines if line.signals]
