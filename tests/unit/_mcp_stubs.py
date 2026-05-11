"""Shared test stubs and fixtures for MCP-tool unit tests.

Kept separate from the test modules themselves so multiple test files can
share the same fixture set (and so each test file stays under the 400-LOC
project limit).
"""

from __future__ import annotations

from ci_log_intelligence.ingestion.github.fetcher import (
    GitHubLogFetcher,
    GitHubTransport,
)


PR_URL = "https://github.com/acme/widgets/pull/42"
JOB_URL = "https://github.com/acme/widgets/actions/runs/200/job/501"


PYTHON_FAIL_LOG = """STEP: execute
tests/test_query.py::test_query FAILED
Traceback (most recent call last):
  File "tests/test_query.py", line 10, in test_query
    assert query_result == expected_result
AssertionError: hash mismatch detected
expected hash: abc
actual hash: def
"""


HASH_MISMATCH_LOG = """STEP: execute
common.go:1058: file hashes don't match for golden output
    /tmp/test/Material_X.yaml and
    ../samples/test_partial.xtra/test_output/Material_X_HASH_1.yaml
--- FAIL: TestRunSetPartialFeatureTable (45.3s)
FAIL    github.com/acme/widgets/internal/runset 45.301s
"""


_LOG_BY_JOB_ID: dict[int, str] = {
    501: PYTHON_FAIL_LOG,
    502: HASH_MISMATCH_LOG,
}


_RUN_PAYLOAD = {
    "id": 200,
    "workflow_id": 10,
    "head_branch": "main",
    "head_sha": "abc",
    "html_url": "https://github.com/acme/widgets/actions/runs/200",
    "status": "completed",
    "conclusion": "failure",
    "display_title": "CI",
}


class CallCountingTransport(GitHubTransport):
    """Stub transport with deterministic payloads and per-endpoint call counts."""

    def __init__(self) -> None:
        self.json_calls: list[tuple[str, tuple]] = []
        self.text_calls: list[str] = []

        self._json_payloads = {
            ("repos/acme/widgets/pulls/42", ()): {"head": {"sha": "abc"}},
            (
                "repos/acme/widgets/actions/runs",
                (("head_sha", "abc"), ("per_page", 5)),
            ): {"workflow_runs": [_RUN_PAYLOAD]},
            (
                "repos/acme/widgets/actions/runs/200/jobs",
                (("per_page", 100),),
            ): {
                "jobs": [
                    {
                        "id": 501,
                        "name": "postgres-test (bundling)",
                        "status": "completed",
                        "conclusion": "failure",
                    },
                    {
                        "id": 502,
                        "name": "snowflake-test (non-bundling)",
                        "status": "completed",
                        "conclusion": "failure",
                    },
                ]
            },
            "repos/acme/widgets/actions/runs/200": _RUN_PAYLOAD,
        }

    def get_json(self, endpoint, params=None):
        normalized = tuple(sorted((params or {}).items()))
        if endpoint == "repos/acme/widgets/actions/runs/200" and not normalized:
            self.json_calls.append((endpoint, ()))
            return self._json_payloads[endpoint]
        key: tuple = (endpoint, normalized)
        self.json_calls.append(key)
        if key in self._json_payloads:
            return self._json_payloads[key]
        if endpoint == "repos/acme/widgets/actions/runs":
            return {"workflow_runs": [_RUN_PAYLOAD]}
        raise KeyError(f"Unhandled get_json call: {key}")

    def get_text(self, endpoint, params=None) -> str:
        self.text_calls.append(endpoint)
        parts = endpoint.strip("/").split("/")
        job_id = int(parts[-2])
        try:
            return _LOG_BY_JOB_ID[job_id]
        except KeyError as exc:
            raise KeyError(f"No fixture log for job {job_id}") from exc


def make_fetcher() -> tuple[GitHubLogFetcher, CallCountingTransport]:
    transport = CallCountingTransport()
    return GitHubLogFetcher(transport=transport), transport


__all__ = [
    "CallCountingTransport",
    "HASH_MISMATCH_LOG",
    "JOB_URL",
    "PR_URL",
    "PYTHON_FAIL_LOG",
    "make_fetcher",
]
