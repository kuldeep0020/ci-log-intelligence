from __future__ import annotations

import re
from typing import Sequence

from ...models import ParsedLine
from .base import DetectedFailure, JobContext


_GENERIC_PATTERNS: list[tuple[str, int, re.Pattern[str]]] = [
    ("traceback", 3, re.compile(r"Traceback \(most recent call last\):")),
    ("exception", 3, re.compile(r"Exception")),
    ("error", 3, re.compile(r"ERROR")),
    ("failed", 2, re.compile(r"FAILED")),
    ("assertion_error", 2, re.compile(r"AssertionError")),
    ("warning", 1, re.compile(r"WARNING")),
    ("retrying", 1, re.compile(r"Retrying")),
]


class GenericDetector:
    name: str = "generic"
    failure_type: str = "generic"

    def scan(
        self,
        parsed_lines: Sequence[ParsedLine],
        _job_context: JobContext,
    ) -> list[DetectedFailure]:
        failures: list[DetectedFailure] = []
        for line in parsed_lines:
            for signal_name, severity, pattern in _GENERIC_PATTERNS:
                if pattern.search(line.content):
                    failures.append(
                        DetectedFailure(
                            type="generic",
                            anchor_lines=[line.line_number],
                            severity=severity,
                            extracted_fields={"signal_name": signal_name},
                            anchor_type=signal_name,
                        )
                    )
        return failures


__all__ = ["GenericDetector"]
