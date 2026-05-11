"""End-to-end pipeline tests for the build-error detectors.

Each test runs the full reduce + ``_build_report`` pipeline against a
realistic small fixture and asserts the resulting FailureRecord carries
the right type, classification, severity, and extracted fields.

The last test in this file exercises the load-bearing claim that
``severity=3`` build errors rank above ``severity=2`` test failures when
both fire on the same log -- ``select_root_cause`` should pick the build
error as the root cause.
"""

from __future__ import annotations

import unittest

from ci_log_intelligence.ci_analysis import _build_report
from ci_log_intelligence.ingestion import ingest_log
from ci_log_intelligence.ingestion.github.models import (
    FailedLogAnalysis,
    NormalizedLog,
)
from ci_log_intelligence.parsing import parse_log
from ci_log_intelligence.reducer import reduce_parsed_lines
from ci_log_intelligence.reducer.detectors import JobContext
from ci_log_intelligence.storage import InMemoryStorage


def _parse(content: str):
    backend = InMemoryStorage()
    stored = ingest_log(content, backend)
    return parse_log(stored, backend)


def _build_single_report(
    content: str,
    *,
    job_name: str = "ci-job",
    run_id: int = 42,
):
    parsed = _parse(content)
    job_context = JobContext(job_name=job_name, run_id=run_id, repo="r/x")
    result = reduce_parsed_lines(parsed, job_context=job_context)
    analysis = FailedLogAnalysis(
        log=NormalizedLog(
            run_id=run_id,
            job_id=run_id * 100,
            job_name=job_name,
            status="failed",
            content=content,
        ),
        logical_job_name=job_name,
        result=result,
    )
    report = _build_report(
        runs=[],
        failed_logs=[analysis.log],
        passed_logs=[],
        failed_analyses=[analysis],
        passed_contexts=[],
        insights=[],
    )
    return result, report


RUST_LOG = """\
STEP: cargo-build
2024-01-15T12:34:56.789Z compiling my-crate
2024-01-15T12:34:57.000Z error[E0382]: borrow of moved value: `s`
2024-01-15T12:34:57.100Z   --> src/main.rs:5:20
2024-01-15T12:34:57.200Z    |
2024-01-15T12:34:57.300Z 3  |     let s = String::from("hello");
2024-01-15T12:34:57.400Z    = note: see chapter on ownership
"""


class RustBuildErrorPipelineTests(unittest.TestCase):
    def test_rust_build_error_emits_typed_failure_record(self) -> None:
        result, report = _build_single_report(RUST_LOG)

        rust_failures = [
            f for f in result.detected_failures if f.type == "build_error_rust"
        ]
        self.assertEqual(len(rust_failures), 1)
        only = rust_failures[0]
        self.assertEqual(only.severity, 3)
        self.assertEqual(only.classification_claim, "root_cause")
        self.assertEqual(only.extracted_fields["language"], "rust")
        self.assertEqual(only.extracted_fields["error_code"], "E0382")
        self.assertEqual(only.extracted_fields["file_path"], "src/main.rs")
        self.assertEqual(only.extracted_fields["line"], 5)
        self.assertEqual(only.extracted_fields["column"], 20)

        records = [r for r in report.failures if r.type == "build_error_rust"]
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].classification, "root_cause")
        self.assertEqual(records[0].extracted_fields["error_code"], "E0382")


GO_BUILD_LOG = """\
STEP: go-build
2024-01-15T12:34:56.789Z compiling
2024-01-15T12:34:57.000Z ./pkg/foo.go:42:5: undefined: SomeFunc
2024-01-15T12:34:57.100Z ./pkg/bar.go:10:3: cannot use x (type int) as type string
"""


class GoBuildErrorPipelineTests(unittest.TestCase):
    def test_go_build_errors_emit_two_typed_failure_records(self) -> None:
        result, report = _build_single_report(GO_BUILD_LOG)

        go_failures = [
            f for f in result.detected_failures if f.type == "build_error_go"
        ]
        self.assertEqual(len(go_failures), 2)
        files = sorted(f.extracted_fields["file_path"] for f in go_failures)
        self.assertEqual(files, ["./pkg/bar.go", "./pkg/foo.go"])

        records = [r for r in report.failures if r.type == "build_error_go"]
        self.assertGreaterEqual(len(records), 1)
        for record in records:
            self.assertEqual(record.classification, "root_cause")
            self.assertEqual(record.extracted_fields["language"], "go")


NPM_LOG = """\
STEP: npm-build
2024-01-15T12:34:56.789Z running build
2024-01-15T12:34:57.000Z npm ERR! code ELIFECYCLE
2024-01-15T12:34:57.100Z npm ERR! errno 1
2024-01-15T12:34:57.200Z npm ERR! my-app@1.0.0 build: `webpack`
2024-01-15T12:34:57.300Z npm ERR! Exit status 1
"""


class NpmBuildErrorPipelineTests(unittest.TestCase):
    def test_npm_block_emits_one_typed_failure_record(self) -> None:
        result, report = _build_single_report(NPM_LOG)

        npm_failures = [
            f for f in result.detected_failures if f.type == "build_error_npm"
        ]
        self.assertEqual(len(npm_failures), 1)
        only = npm_failures[0]
        self.assertEqual(only.extracted_fields["tool"], "npm")
        self.assertEqual(only.extracted_fields["error_code"], "ELIFECYCLE")
        self.assertEqual(only.extracted_fields["errno"], 1)

        records = [r for r in report.failures if r.type == "build_error_npm"]
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].classification, "root_cause")
        self.assertEqual(records[0].extracted_fields["tool"], "npm")


MAKE_LOG = """\
STEP: make-build
2024-01-15T12:34:56.789Z compiling
2024-01-15T12:34:57.000Z gcc: error
2024-01-15T12:34:57.100Z make: *** [Makefile:42: build] Error 1
"""


class MakeBuildErrorPipelineTests(unittest.TestCase):
    def test_make_recipe_failure_emits_typed_record(self) -> None:
        result, report = _build_single_report(MAKE_LOG)

        make_failures = [
            f for f in result.detected_failures if f.type == "build_error_make"
        ]
        self.assertEqual(len(make_failures), 1)
        only = make_failures[0]
        self.assertEqual(only.extracted_fields["target"], "build")
        self.assertEqual(only.extracted_fields["exit_code"], 1)
        self.assertEqual(only.extracted_fields["makefile"], "Makefile")
        self.assertEqual(only.extracted_fields["makefile_line"], 42)

        records = [r for r in report.failures if r.type == "build_error_make"]
        self.assertGreaterEqual(len(records), 1)
        target_record = next(
            (r for r in records if r.extracted_fields.get("target") == "build"),
            None,
        )
        self.assertIsNotNone(target_record)
        assert target_record is not None
        self.assertEqual(target_record.classification, "root_cause")


GCC_LOG = """\
STEP: gcc-build
2024-01-15T12:34:56.789Z compiling
2024-01-15T12:34:57.000Z src/foo.c:42:10: error: 'x' undeclared (first use in this function)
2024-01-15T12:34:57.100Z    42 |     return x;
2024-01-15T12:34:57.200Z       |            ^
"""


class GccBuildErrorPipelineTests(unittest.TestCase):
    def test_gcc_error_emits_typed_record(self) -> None:
        result, report = _build_single_report(GCC_LOG)

        gcc_failures = [
            f for f in result.detected_failures if f.type == "build_error_gcc"
        ]
        self.assertEqual(len(gcc_failures), 1)
        only = gcc_failures[0]
        self.assertEqual(only.extracted_fields["language"], "c_cpp")
        self.assertEqual(only.extracted_fields["file_path"], "src/foo.c")
        self.assertEqual(only.extracted_fields["severity_text"], "error")

        records = [r for r in report.failures if r.type == "build_error_gcc"]
        self.assertGreaterEqual(len(records), 1)
        gcc_record = next(
            (r for r in records if r.extracted_fields.get("language") == "c_cpp"),
            None,
        )
        self.assertIsNotNone(gcc_record)
        assert gcc_record is not None
        self.assertEqual(gcc_record.classification, "root_cause")


BUILD_PLUS_TEST_LOG = """\
STEP: cargo-build
2024-01-15T12:34:56.789Z compiling my-crate
2024-01-15T12:34:57.000Z error[E0382]: borrow of moved value: `s`
2024-01-15T12:34:57.100Z   --> src/main.rs:5:20
STEP: cargo-test
2024-01-15T12:34:58.000Z running tests
2024-01-15T12:34:58.100Z thread 'tests::it_works' panicked at 'assertion failed', src/lib.rs:42:5
2024-01-15T12:34:58.200Z test tests::it_works ... FAILED
"""


class BuildErrorOutranksTestFailureTests(unittest.TestCase):
    """Verify that a build error (severity 3) outranks a test failure (severity 2).

    This is the load-bearing claim of Step 5b: compile errors are upstream of
    test failures, so when both fire the build error must be selected as
    root_cause.
    """

    def test_select_root_cause_picks_build_error_over_test_failure(self) -> None:
        result, report = _build_single_report(BUILD_PLUS_TEST_LOG)

        # Both detector types fired.
        rust_build = [
            f for f in result.detected_failures if f.type == "build_error_rust"
        ]
        rust_test = [
            f for f in result.detected_failures if f.type == "rust_test_fail"
        ]
        self.assertEqual(len(rust_build), 1)
        self.assertEqual(len(rust_test), 1)
        self.assertEqual(rust_build[0].severity, 3)
        self.assertEqual(rust_test[0].severity, 2)

        # And the root cause selected at the report level is the build error.
        # The summary line references the build error location.
        self.assertIn("error[E0382]", report.root_cause.log_excerpt)
        # The highest-scoring failure record carries the build_error_rust type.
        sorted_failures = sorted(report.failures, key=lambda r: -r.score)
        self.assertTrue(sorted_failures)
        top = sorted_failures[0]
        self.assertEqual(top.type, "build_error_rust")
        self.assertEqual(top.classification, "root_cause")
        self.assertGreaterEqual(top.severity, 3)


if __name__ == "__main__":
    unittest.main()
