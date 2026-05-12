# Contributing

Hi — and thanks for thinking about contributing. This project exists because raw CI logs were eating my coding agent's context window, and the fix turned out to be useful enough to share. If it's useful to you too, contributions back are warmly welcomed and the design tries to make them easy.

## The fastest paths to contributing

You don't need to be a "regular contributor" to send a PR. The single most useful kinds of contribution, in rough order of how much impact they have:

1. **A new detector** for a CI tool / language / build system that isn't covered yet. The framework is designed for this — see "Add a detector" below.
2. **A real-world log fixture** that the current detectors get wrong. Open an issue with a small log snippet and what you expected. Even without a fix, this is gold.
3. **Pattern tightening** for existing detectors. If you see a regex over-matching or under-matching on your CI output, a one-line PR fixing the regex with a test is perfect.
4. **Doc fixes.** README, INSTALL, architecture — typos, unclear sections, missing examples.
5. **MCP client integration guides** for clients we don't cover yet.

If you're not sure where to start, open an issue and ask. "I want to help but I don't know with what" is a totally fine issue.

## Setting up a dev environment

```bash
git clone https://github.com/kuldeep0020/ci-log-intelligence.git
cd ci-log-intelligence
python -m venv .venv && source .venv/bin/activate
pip install -e .
python -m unittest discover -s tests -v
```

You should see 250+ tests passing. If you don't, file an issue with the failure output — that's its own contribution.

## Add a detector

The detector plugin framework is the main extension point. Each detector is one file under `ci_log_intelligence/reducer/detectors/`. The simplest possible detector:

```python
from typing import Sequence
from ...models import ParsedLine
from .base import DetectedFailure, JobContext


class MyDetector:
    name: str = "my_detector"
    failure_type: str = "my_detector"

    def scan(
        self,
        parsed_lines: Sequence[ParsedLine],
        _job_context: JobContext,
    ) -> list[DetectedFailure]:
        failures = []
        for line in parsed_lines:
            if "MY_KEYWORD" in line.content:
                failures.append(
                    DetectedFailure(
                        type="my_detector",
                        anchor_lines=[line.line_number],
                        severity=2,
                        classification_claim="root_cause",
                        extracted_fields={"language": "whatever"},
                        anchor_type="my_detector",
                    )
                )
        return failures
```

Register it by adding an entry to the `_REGISTRY` list in `ci_log_intelligence/reducer/detectors/__init__.py`. Add a unit test under `tests/unit/test_my_detector.py` modeled on the existing detector tests. The framework handles clustering, expansion, scoring, classification, and the typed-record output — you only write `scan()`.

For detectors that need cross-line pairing (e.g., a panic line + a summary line), see `hash_mismatch.py` as a worked example.

For language-specific detectors that need to skip lines already claimed by a more specific detector, see how `go_test_fail.py` calls `hash_mismatch_claimed_fail_lines()`.

See [architecture.md](architecture.md) for the full pipeline description and how `DetectedFailure`, `classification_claim`, and `extracted_fields` flow through to the final `FailureRecord` in the report.

## Tests

Every PR needs tests. The project uses stdlib `unittest`. Match the style of the existing detector tests — a `TestCase` per behavior cluster, descriptive method names, no test framework dependencies beyond `unittest`.

Run the whole suite:

```bash
python -m unittest discover -s tests -v
```

Or a single test file while you iterate:

```bash
python -m unittest tests.unit.test_my_detector -v
```

## Code style

The project is small (~3K LOC). Match the existing patterns:

- `from __future__ import annotations` at the top of every file.
- `@dataclass(slots=True)` for mutable values, `@dataclass(slots=True, frozen=True)` for immutable.
- No file over 400 LOC, no function over 50 LOC. If you're going past that, factor it out — there's prior art for splitting (see `fetcher.py` → `transports.py` + `fetcher_helpers.py`).
- No emojis in code, comments, docstrings, or commit messages.
- Conventional Commits-ish messages (`feat:`, `fix:`, `refactor:`, `docs:`, `chore:`) when convenient — not strictly enforced, just nice for changelog scanning.

There's no linter configured (intentionally minimal tooling). Just follow what you see in neighboring files.

## PR process

1. Fork, branch, code, test.
2. Open a PR against `main`.
3. The CI workflow runs the tests on Python 3.10 through 3.13. Make sure it's green.
4. A reviewer will comment with feedback or merge. Small PRs get reviewed faster than big ones — split if you can.
5. Don't be discouraged if review takes a few days — this is maintained alongside other work.

## Questions

Open an issue. There's no Discord, no Slack, no mailing list — the issue tracker is the conversation.
