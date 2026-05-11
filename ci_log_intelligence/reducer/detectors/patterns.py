"""Shared regex patterns used by multiple detectors.

Patterns extracted here are intentionally minimal -- they are shared because
multiple detectors must agree on the exact match shape (e.g. both
``HashMismatchDetector`` and ``GoTestFailDetector`` key off the Go test
runner's ``--- FAIL:`` marker, and any drift between the two would silently
break the coordination contract between them).

If a pattern is used by only one detector, keep it in that detector's module.
"""

from __future__ import annotations

import re
from typing import Final

# Go test runner v1 ``--- FAIL: TestName`` marker. The ``test_name`` capture
# group includes any subtest path (e.g. ``TestX/subtest_y``) because the Go
# runner emits the full path in a single token.
GO_TEST_FAIL_PATTERN: Final["re.Pattern[str]"] = re.compile(
    r"---\s+FAIL:\s+(?P<test_name>\S+)"
)

# The hash-mismatch marker shared with the detector's pairing logic. Listed
# here so the GoTestFailDetector can scan for it without importing the
# detector module (which would create a circular import).
HASH_MISMATCH_PATTERN: Final["re.Pattern[str]"] = re.compile(
    r"file hashes don't match", re.IGNORECASE
)

HASH_MISMATCH_PAIRING_WINDOW: Final[int] = 50
"""Maximum line distance between a hash-mismatch and its paired FAIL marker.

Used by HashMismatchDetector for pairing and by hash_mismatch_claimed_fail_lines
to compute the set of FAIL lines GoTestFailDetector must skip. Both consumers
read the same constant so the coordination contract is single-sourced.
"""


__all__ = [
    "GO_TEST_FAIL_PATTERN",
    "HASH_MISMATCH_PAIRING_WINDOW",
    "HASH_MISMATCH_PATTERN",
]
