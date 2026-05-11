from __future__ import annotations

import unittest

from ci_log_intelligence.models import ParsedLine
from ci_log_intelligence.reducer.detectors import JobContext
from ci_log_intelligence.reducer.detectors.junit_xml import JUnitXmlDetector


def _line(line_number: int, content: str, step_id: str | None = "test") -> ParsedLine:
    return ParsedLine(line_number, content, None, step_id, [])


def _ctx() -> JobContext:
    return JobContext(job_name=None, run_id=None, repo=None)


class EmptyInputTests(unittest.TestCase):
    def test_empty_input_returns_no_failures(self) -> None:
        self.assertEqual(JUnitXmlDetector().scan([], _ctx()), [])


class SingleLineFailureTests(unittest.TestCase):
    def test_single_line_failure_emits_record(self) -> None:
        lines = [
            _line(
                3,
                '<testcase name="test_x" classname="my.TestClass">'
                '<failure message="expected 1 got 2" type="AssertionError"/>'
                "</testcase>",
            )
        ]
        failures = JUnitXmlDetector().scan(lines, _ctx())

        self.assertEqual(len(failures), 1)
        only = failures[0]
        self.assertEqual(only.type, "junit_xml")
        self.assertEqual(only.severity, 2)
        self.assertEqual(only.classification_claim, "root_cause")
        self.assertEqual(only.anchor_type, "junit_xml")
        self.assertEqual(only.anchor_lines, [3])

    def test_name_classname_message_and_element_type_extracted(self) -> None:
        lines = [
            _line(
                1,
                '<testcase name="test_x" classname="my.TestClass">'
                '<failure message="expected 1 got 2"/></testcase>',
            )
        ]
        failures = JUnitXmlDetector().scan(lines, _ctx())

        fields = failures[0].extracted_fields
        self.assertEqual(fields["test_name"], "test_x")
        self.assertEqual(fields["classname"], "my.TestClass")
        self.assertEqual(fields["element_type"], "failure")
        self.assertEqual(fields["message"], "expected 1 got 2")

    def test_framework_constant_is_junit_xml(self) -> None:
        lines = [
            _line(
                1,
                '<testcase name="t"><failure message="m"/></testcase>',
            )
        ]
        failures = JUnitXmlDetector().scan(lines, _ctx())
        self.assertEqual(failures[0].extracted_fields["framework"], "junit_xml")

    def test_error_element_recognized(self) -> None:
        lines = [
            _line(
                1,
                '<testcase name="t" classname="C"><error message="went wrong"/></testcase>',
            )
        ]
        failures = JUnitXmlDetector().scan(lines, _ctx())
        self.assertEqual(failures[0].extracted_fields["element_type"], "error")
        self.assertEqual(failures[0].extracted_fields["message"], "went wrong")


class AttributeOrderTests(unittest.TestCase):
    def test_classname_before_name_still_parsed(self) -> None:
        lines = [
            _line(
                1,
                '<testcase classname="my.TestClass" name="test_x">'
                '<failure message="boom"/></testcase>',
            )
        ]
        failures = JUnitXmlDetector().scan(lines, _ctx())

        fields = failures[0].extracted_fields
        self.assertEqual(fields["test_name"], "test_x")
        self.assertEqual(fields["classname"], "my.TestClass")


class MultilineSpanTests(unittest.TestCase):
    def test_failure_element_on_next_line_anchors_failure_line(self) -> None:
        lines = [
            _line(10, '<testcase name="test_x" classname="C">'),
            _line(11, '  <failure message="boom" type="AssertionError">'),
            _line(12, "  </failure>"),
            _line(13, "</testcase>"),
        ]
        failures = JUnitXmlDetector().scan(lines, _ctx())

        self.assertEqual(len(failures), 1)
        # Anchor lives on the line containing the <failure> element.
        self.assertEqual(failures[0].anchor_lines, [11])
        self.assertEqual(failures[0].extracted_fields["test_name"], "test_x")

    def test_failure_beyond_5_line_lookahead_is_skipped(self) -> None:
        lines = [
            _line(10, '<testcase name="test_x" classname="C">'),
            _line(11, "  other content"),
            _line(12, "  other content"),
            _line(13, "  other content"),
            _line(14, "  other content"),
            _line(15, "  other content"),
            _line(16, "  other content"),
            _line(17, '  <failure message="too far"/>'),
        ]
        failures = JUnitXmlDetector().scan(lines, _ctx())

        # The failure element is 7 lines after the testcase opening tag, past
        # the 5-line lookahead window.
        self.assertEqual(failures, [])


class TestcaseWithoutFailureTests(unittest.TestCase):
    def test_testcase_with_no_failure_is_ignored(self) -> None:
        lines = [_line(1, '<testcase name="passing_test" classname="C"/>')]
        failures = JUnitXmlDetector().scan(lines, _ctx())
        self.assertEqual(failures, [])

    def test_testcase_with_no_name_attribute_is_ignored(self) -> None:
        lines = [
            _line(1, '<testcase classname="C"><failure message="m"/></testcase>'),
        ]
        failures = JUnitXmlDetector().scan(lines, _ctx())
        self.assertEqual(failures, [])


class MultipleFailuresTests(unittest.TestCase):
    def test_multiple_testcases_each_emit_record(self) -> None:
        lines = [
            _line(
                1,
                '<testcase name="test_a"><failure message="boom_a"/></testcase>',
            ),
            _line(
                2,
                '<testcase name="test_b"><error message="boom_b"/></testcase>',
            ),
        ]
        failures = JUnitXmlDetector().scan(lines, _ctx())

        self.assertEqual(len(failures), 2)
        names = [f.extracted_fields["test_name"] for f in failures]
        self.assertEqual(names, ["test_a", "test_b"])


class TruncationTests(unittest.TestCase):
    def test_at_most_50_records_emitted_and_last_marked_truncated(self) -> None:
        lines = [
            _line(
                index + 1,
                f'<testcase name="t_{index}"><failure message="boom_{index}"/></testcase>',
            )
            for index in range(75)
        ]
        failures = JUnitXmlDetector().scan(lines, _ctx())

        self.assertEqual(len(failures), 50)
        self.assertEqual(failures[-1].extracted_fields["truncated"], True)
        # Earlier records do not carry the truncated flag.
        for failure in failures[:-1]:
            self.assertNotIn("truncated", failure.extracted_fields)


if __name__ == "__main__":
    unittest.main()
