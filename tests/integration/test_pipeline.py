from __future__ import annotations

import unittest

from fastapi.testclient import TestClient

from ci_log_intelligence import analyze_log
from ci_log_intelligence.api import app

PIPELINE_LOG = """STEP: test
Running pytest
tests/test_api.py::test_login FAILED
Traceback (most recent call last):
  File "tests/test_api.py", line 12, in test_login
    assert 200 == 500
AssertionError
STEP: retry
Retrying request
WARNING transient error
"""


class PipelineIntegrationTests(unittest.TestCase):
    def test_full_pipeline_returns_ranked_blocks_and_summary(self) -> None:
        result = analyze_log(PIPELINE_LOG)

        self.assertEqual(len(result.blocks), 2)
        self.assertEqual(result.blocks[0].classification, "root_cause")
        self.assertEqual(result.blocks[1].classification, "flaky")
        self.assertIn("Identified 2 failure blocks", result.summary or "")

    def test_http_api_exposes_expected_contract(self) -> None:
        client = TestClient(app)

        response = client.post("/analyze", json={"log": PIPELINE_LOG})

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(len(payload["blocks"]), 2)
        self.assertEqual(
            set(payload["blocks"][0]),
            {"start_line", "end_line", "score", "classification"},
        )
        self.assertIsInstance(payload["summary"], str)


if __name__ == "__main__":
    unittest.main()
