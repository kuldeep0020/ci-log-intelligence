"""Detector for JUnit XML failure/error fragments in log streams.

JUnit XML in logs is typically either a single compact line containing the
``<testcase>`` element and its ``<failure>``/``<error>`` child or a small
multi-line block. We do NOT attempt to parse a full document -- just scan
each line for the relevant elements.

Two patterns are used:

* ``_TESTCASE_PATTERN`` finds ``<testcase ... name="..." classname="...">``
  opening tags. Attribute order in XML is arbitrary, so ``name`` and
  ``classname`` are parsed via separate ``_ATTR_PATTERN`` matches inside
  the testcase tag.
* ``_FAILURE_ELEMENT_PATTERN`` finds the ``<failure ... />`` or
  ``<error ... />`` element. If it lives on the same line as the
  ``<testcase>`` opening tag, we emit a paired record. Otherwise we look
  on the next 5 lines.

Cap: at most 50 records per ``scan()``. If exceeded, the LAST emitted
record carries ``extracted_fields["truncated"] = True``.
"""

from __future__ import annotations

import re
from typing import Any, Optional, Sequence

from ...models import ParsedLine
from .base import DetectedFailure, JobContext

_TESTCASE_PATTERN = re.compile(r"<testcase\b(?P<attrs>[^>]*)>")
_FAILURE_ELEMENT_PATTERN = re.compile(
    r"<(?P<element>failure|error)\b(?P<attrs>[^>]*?)\s*/?>"
)
_NAME_ATTR_PATTERN = re.compile(r'\bname="(?P<value>[^"]*)"')
_CLASSNAME_ATTR_PATTERN = re.compile(r'\bclassname="(?P<value>[^"]*)"')
_MESSAGE_ATTR_PATTERN = re.compile(r'\bmessage="(?P<value>[^"]*)"')

_FAILURE_LOOKAHEAD_LINES = 5
_MAX_RECORDS = 50


class JUnitXmlDetector:
    """Best-effort detection of JUnit XML ``<failure>`` and ``<error>`` elements.

    ``extracted_fields["truncated"]`` is set to ``True`` on the LAST emitted
    record (highest anchor line number) when the 50-record cap was reached
    and additional matches were dropped. The key is absent otherwise.
    Consumers should use ``extracted_fields.get("truncated", False)``.
    """

    name: str = "junit_xml"
    failure_type: str = "junit_xml"

    def scan(
        self,
        parsed_lines: Sequence[ParsedLine],
        _job_context: JobContext,
    ) -> list[DetectedFailure]:
        lines_by_index = list(parsed_lines)
        failures: list[DetectedFailure] = []
        for index, line in enumerate(lines_by_index):
            testcase_match = _TESTCASE_PATTERN.search(line.content)
            if not testcase_match:
                continue
            failure = _emit_for_testcase(line, testcase_match, index, lines_by_index)
            if failure is not None:
                failures.append(failure)
            if len(failures) >= _MAX_RECORDS:
                break

        if len(failures) >= _MAX_RECORDS:
            failures.sort(key=lambda f: min(f.anchor_lines, default=0))
            failures = failures[:_MAX_RECORDS]
            # Mark the LAST emitted record (highest anchor line) as truncated.
            failures[-1].extracted_fields["truncated"] = True
        return failures


def _emit_for_testcase(
    testcase_line: ParsedLine,
    testcase_match: "re.Match[str]",
    testcase_index: int,
    lines_by_index: Sequence[ParsedLine],
) -> Optional[DetectedFailure]:
    """Build a DetectedFailure if a ``<failure>`` or ``<error>`` accompanies the testcase."""
    attrs = testcase_match.group("attrs")
    name_match = _NAME_ATTR_PATTERN.search(attrs)
    if not name_match:
        return None
    test_name = name_match.group("value")
    classname_match = _CLASSNAME_ATTR_PATTERN.search(attrs)
    classname = classname_match.group("value") if classname_match else None

    failure_info = _find_failure_element(
        testcase_line, testcase_index, lines_by_index
    )
    if failure_info is None:
        return None
    failure_line, element_type, message = failure_info

    extracted: dict[str, Any] = {
        "framework": "junit_xml",
        "test_name": test_name,
        "element_type": element_type,
    }
    if classname:
        extracted["classname"] = classname
    if message:
        extracted["message"] = message

    anchor_line = failure_line.line_number
    return DetectedFailure(
        type="junit_xml",
        anchor_lines=[anchor_line],
        severity=2,
        classification_claim="root_cause",
        extracted_fields=extracted,
        suggested_block_range=None,
        anchor_type="junit_xml",
    )


def _find_failure_element(
    testcase_line: ParsedLine,
    testcase_index: int,
    lines_by_index: Sequence[ParsedLine],
) -> Optional[tuple[ParsedLine, str, Optional[str]]]:
    """Return ``(line, element_type, message)`` for a ``<failure>``/``<error>`` element.

    Checks the testcase line itself first, then the next 5 lines. The
    testcase line is anchored if the failure element shares it (the
    common single-line case); otherwise the failure element's own line
    is anchored.
    """
    end_index = min(len(lines_by_index), testcase_index + 1 + _FAILURE_LOOKAHEAD_LINES)
    for index in range(testcase_index, end_index):
        candidate = lines_by_index[index]
        element_match = _FAILURE_ELEMENT_PATTERN.search(candidate.content)
        if not element_match:
            continue
        element_type = element_match.group("element")
        message_match = _MESSAGE_ATTR_PATTERN.search(element_match.group("attrs"))
        message = message_match.group("value") if message_match else None
        # If the failure element is on the testcase line, anchor that line;
        # otherwise anchor the failure-element line so the agent jumps to
        # where the failure detail actually lives.
        anchor = testcase_line if index == testcase_index else candidate
        return anchor, element_type, message
    return None


__all__ = ["JUnitXmlDetector"]
