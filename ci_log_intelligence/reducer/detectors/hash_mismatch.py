"""Detector for Go test golden-file hash mismatches.

A WHT-style log emits paired lines of the form::

    common.go:1058: file hashes don't match for ...
        /tmp/.../Material_X.yaml and
        ../samples/test_partial.xtra/test_output/.../Material_X_HASH_1.yaml
    --- FAIL: TestRunSetPartialFeatureTable (45.3s)

The mismatch and the FAIL marker can be separated by 1-30 lines of other
output, and the FAIL marker may be a subtest of the form
``--- FAIL: TestRunSetPartialFeatureTable/incremental_phantom``.

The job name in the GitHub Actions log header encodes the warehouse target,
e.g. ``postgres-test (bundling)``. This detector infers
``warehouse_target`` from the job name when one of the known warehouse
keywords is present.

Pairing rule: for each mismatch line, find the NEAREST FAIL line within
[mismatch_line - 50, mismatch_line + 50] that shares the same step_id.
Unpaired mismatches still emit a typed record (without ``test_name``) so
the calling agent can see them.

A single failing Go test can emit MULTIPLE ``file hashes don't match``
lines (one per divergent golden file). Each mismatch independently
pairs with the same nearest FAIL marker, producing N DetectedFailure
records sharing the same ``test_name``. This is intentional -- the
agent receives one record per divergent golden file so it can scope
``make update_ref_samples`` precisely.

See ``docs/use-case-go-test-hash-mismatch.md`` for the full motivation.
"""

from __future__ import annotations

from typing import Any, Optional, Sequence

from ...models import ParsedLine
from .base import DetectedFailure, JobContext
from .patterns import (
    GO_TEST_FAIL_PATTERN,
    HASH_MISMATCH_PAIRING_WINDOW,
    HASH_MISMATCH_PATTERN,
)


_WAREHOUSE_KEYWORDS = ("postgres", "snowflake", "redshift", "databricks", "bigquery")


class HashMismatchDetector:
    """Pairs ``file hashes don't match`` with the nearest ``--- FAIL:`` in the same step.

    ``extracted_fields`` keys are conditional:

    * ``test_name``: present only when a FAIL line was paired within
      ``HASH_MISMATCH_PAIRING_WINDOW`` lines in the same step. Unpaired
      mismatches omit this key.
    * ``warehouse_target``: present only when ``job_context.job_name``
      contains one of the known warehouse keywords (postgres, snowflake,
      redshift, databricks, bigquery). Unknown / missing job names
      omit this key.
    * ``job_name``: present only when ``job_context.job_name`` is set.

    Consumers must use ``.get(key)`` on ``extracted_fields`` rather than
    indexing -- keys are omitted, not set to ``None``, when absent.
    """

    name: str = "hash_mismatch"
    failure_type: str = "hash_mismatch"

    def scan(
        self,
        parsed_lines: Sequence[ParsedLine],
        job_context: JobContext,
    ) -> list[DetectedFailure]:
        mismatch_lines, fail_lines = _collect_mismatch_and_fail_lines(parsed_lines)

        warehouse_target = _infer_warehouse(job_context.job_name)
        failures: list[DetectedFailure] = []

        for mismatch in mismatch_lines:
            paired = _nearest_fail(mismatch, fail_lines)
            extracted: dict[str, Any] = {}
            if warehouse_target is not None:
                extracted["warehouse_target"] = warehouse_target
            if job_context.job_name:
                extracted["job_name"] = job_context.job_name
            if paired is not None:
                fail_line, test_name = paired
                extracted["test_name"] = test_name
                anchor_lines = sorted({mismatch.line_number, fail_line.line_number})
                suggested = (min(anchor_lines), max(anchor_lines))
            else:
                anchor_lines = [mismatch.line_number]
                suggested = None

            failures.append(
                DetectedFailure(
                    type="hash_mismatch",
                    anchor_lines=anchor_lines,
                    severity=2,
                    classification_claim="root_cause",
                    extracted_fields=extracted,
                    suggested_block_range=suggested,
                    anchor_type="hash_mismatch",
                )
            )

        return failures


def _collect_mismatch_and_fail_lines(
    parsed_lines: Sequence[ParsedLine],
) -> tuple[list[ParsedLine], list[tuple[ParsedLine, str]]]:
    """Single forward pass collecting hash-mismatch and FAIL lines."""
    mismatch_lines: list[ParsedLine] = []
    fail_lines: list[tuple[ParsedLine, str]] = []
    for line in parsed_lines:
        if HASH_MISMATCH_PATTERN.search(line.content):
            mismatch_lines.append(line)
        fail_match = GO_TEST_FAIL_PATTERN.search(line.content)
        if fail_match:
            fail_lines.append((line, fail_match.group("test_name")))
    return mismatch_lines, fail_lines


def _nearest_fail(
    mismatch: ParsedLine,
    fail_lines: Sequence[tuple[ParsedLine, str]],
) -> Optional[tuple[ParsedLine, str]]:
    """Return the FAIL line nearest to ``mismatch`` within ``HASH_MISMATCH_PAIRING_WINDOW`` lines.

    Tie-break: equidistant candidates resolve to the EARLIER (smaller line number)
    one. ``fail_lines`` is populated in the single forward pass over parsed lines,
    so it is already in ascending line-number order; Python's ``min`` returns the
    first encountered on a tie. Do not change ``fail_lines`` to a stable sort
    descending -- it would silently flip this behavior.
    """
    candidates = [
        (fail_line, test_name)
        for fail_line, test_name in fail_lines
        if fail_line.step_id == mismatch.step_id
        and abs(fail_line.line_number - mismatch.line_number) <= HASH_MISMATCH_PAIRING_WINDOW
    ]
    if not candidates:
        return None
    return min(candidates, key=lambda item: abs(item[0].line_number - mismatch.line_number))


def hash_mismatch_claimed_fail_lines(parsed_lines: Sequence[ParsedLine]) -> set[int]:
    """Return the set of FAIL line numbers claimed by HashMismatchDetector.

    Used by GoTestFailDetector to skip exactly the FAIL lines that
    HashMismatchDetector will pair. Mirrors the same nearest-FAIL-per-mismatch
    semantics so the two detectors never disagree on ownership.
    """
    mismatch_lines, fail_lines = _collect_mismatch_and_fail_lines(parsed_lines)
    claimed: set[int] = set()
    for mismatch in mismatch_lines:
        paired = _nearest_fail(mismatch, fail_lines)
        if paired is not None:
            fail_line, _ = paired
            claimed.add(fail_line.line_number)
    return claimed


def _infer_warehouse(job_name: Optional[str]) -> Optional[str]:
    if not job_name:
        return None
    lowered = job_name.lower()
    for keyword in _WAREHOUSE_KEYWORDS:
        if keyword in lowered:
            return keyword
    return None


__all__ = ["HashMismatchDetector", "hash_mismatch_claimed_fail_lines"]
