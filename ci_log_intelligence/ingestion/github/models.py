from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional

from ...models import ReductionResult


@dataclass(slots=True, frozen=True)
class GitHubTarget:
    repo: str
    run_id: Optional[int] = None
    job_id: Optional[int] = None
    pr_number: Optional[int] = None


@dataclass(slots=True, frozen=True)
class WorkflowRun:
    run_id: int
    workflow_id: Optional[int]
    head_branch: Optional[str]
    head_sha: Optional[str]
    html_url: str
    status: Optional[str]
    conclusion: Optional[str]
    display_title: str


@dataclass(slots=True, frozen=True)
class WorkflowJob:
    run_id: int
    job_id: int
    job_name: str
    status: Optional[str]
    conclusion: Optional[str]


@dataclass(slots=True, frozen=True)
class NormalizedLog:
    run_id: int
    job_id: int
    job_name: str
    status: Literal["passed", "failed"]
    content: str


@dataclass(slots=True, frozen=True)
class FetchedGitHubData:
    runs: list[WorkflowRun]
    logs: list[NormalizedLog]


@dataclass(slots=True, frozen=True)
class FailedLogAnalysis:
    log: NormalizedLog
    logical_job_name: str
    result: ReductionResult


@dataclass(slots=True, frozen=True)
class PassedContextExcerpt:
    run_id: int
    job_id: int
    job_name: str
    logical_job_name: str
    excerpt: str


@dataclass(slots=True, frozen=True)
class RootCauseSummary:
    summary: str
    log_excerpt: str
    has_traceback: bool
    has_stack_trace: bool
    has_assertion: bool
    score: float
    score_components: dict[str, float]

    def to_dict(self) -> dict[str, object]:
        return {
            "summary": self.summary,
            "log_excerpt": self.log_excerpt,
            "has_traceback": self.has_traceback,
            "has_stack_trace": self.has_stack_trace,
            "has_assertion": self.has_assertion,
            "score": self.score,
            "score_components": dict(self.score_components),
        }


@dataclass(slots=True, frozen=True)
class FailedBlockView:
    start_line: int
    end_line: int
    summary: str

    def to_dict(self) -> dict[str, object]:
        return {
            "start_line": self.start_line,
            "end_line": self.end_line,
            "summary": self.summary,
        }


@dataclass(slots=True, frozen=True)
class PassedContextView:
    job_name: str
    excerpt: str

    def to_dict(self) -> dict[str, object]:
        return {
            "job_name": self.job_name,
            "excerpt": self.excerpt,
        }


@dataclass(slots=True, frozen=True)
class AnalysisMetadata:
    total_runs_analyzed: int
    failed_runs: int
    passed_runs: int

    def to_dict(self) -> dict[str, int]:
        return {
            "total_runs_analyzed": self.total_runs_analyzed,
            "failed_runs": self.failed_runs,
            "passed_runs": self.passed_runs,
        }


@dataclass(slots=True, frozen=True)
class CIAnalysisReport:
    root_cause: RootCauseSummary
    failed_blocks: list[FailedBlockView]
    passed_context: list[PassedContextView]
    cross_run_insights: list[str]
    metadata: AnalysisMetadata

    def to_dict(self) -> dict[str, object]:
        return {
            "root_cause": self.root_cause.to_dict(),
            "failed_blocks": [item.to_dict() for item in self.failed_blocks],
            "passed_context": [item.to_dict() for item in self.passed_context],
            "cross_run_insights": list(self.cross_run_insights),
            "metadata": self.metadata.to_dict(),
        }
