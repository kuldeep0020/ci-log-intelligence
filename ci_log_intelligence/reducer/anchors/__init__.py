from __future__ import annotations

import re
from typing import Iterable, List

from ...models import Anchor, ParsedLine

_ANCHOR_PATTERNS: list[tuple[str, int, re.Pattern[str]]] = [
    ("traceback", 3, re.compile(r"Traceback \(most recent call last\):")),
    ("exception", 3, re.compile(r"Exception")),
    ("error", 3, re.compile(r"ERROR")),
    ("failed", 2, re.compile(r"FAILED")),
    ("assertion_error", 2, re.compile(r"AssertionError")),
    ("warning", 1, re.compile(r"WARNING")),
    ("retrying", 1, re.compile(r"Retrying")),
]


def detect_anchors(lines: Iterable[ParsedLine]) -> List[Anchor]:
    anchors: list[Anchor] = []

    for line in lines:
        for anchor_type, severity, pattern in _ANCHOR_PATTERNS:
            if pattern.search(line.content):
                anchors.append(
                    Anchor(
                        line_number=line.line_number,
                        type=anchor_type,
                        severity=severity,
                    )
                )

    return anchors


__all__ = ["detect_anchors"]
