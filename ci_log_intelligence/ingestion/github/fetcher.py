from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor
from collections import defaultdict
from dataclasses import dataclass
from typing import Iterable, Literal, Optional

import requests

from ...utils.logging import get_structured_logger, log_stage_event
from .fetcher_helpers import (
    format_log_fetch_error as _format_log_fetch_error,
    normalize_log_content,
    parse_workflow_job as _parse_workflow_job,
    parse_workflow_run as _parse_workflow_run,
    plan_log_fetches as _plan_log_fetches,
    sort_logs_by_job,
    sort_runs as _sort_runs,
)
from .models import FetchedGitHubData, GitHubTarget, NormalizedLog, WorkflowJob, WorkflowRun
from .transports import (
    GhCLITransport,
    GitHubTransport,
    RequestsTransport,
    create_github_transport,
)


@dataclass(slots=True, frozen=True)
class LogFetchPlan:
    """A resolved fetch plan: runs + planned-job tuples, no log content yet.

    ``planned_jobs`` carries ``(WorkflowRun, WorkflowJob, logical_name, status)``
    tuples in the order the original ``_fetch_planned_logs`` loop would
    process. Callers can consult a cache against ``(repo, run_id, job_id)``
    before invoking ``fetch_planned_log_content`` to skip already-cached jobs.
    """

    target: GitHubTarget
    selected_runs: list[WorkflowRun]
    planned_jobs: list[tuple[WorkflowRun, WorkflowJob, str, Literal["passed", "failed"]]]

class GitHubLogFetcher:
    def __init__(
        self,
        transport: Optional[GitHubTransport] = None,
        logger=None,
    ) -> None:
        self._transport = transport or create_github_transport()
        self._logger = logger or get_structured_logger("ci_log_intelligence.github")

    def fetch_logs(
        self,
        target: GitHubTarget,
        *,
        include_passed: bool = True,
        max_runs: int = 5,
        max_passed_runs: int = 3,
    ) -> FetchedGitHubData:
        plan = self.plan_logs(
            target,
            include_passed=include_passed,
            max_runs=max_runs,
            max_passed_runs=max_passed_runs,
        )
        logs = self._fetch_planned_logs(target.repo, plan.planned_jobs)
        return self.assemble_fetched_data(plan, logs, include_passed=include_passed, max_passed_runs=max_passed_runs)

    def plan_logs(
        self,
        target: GitHubTarget,
        *,
        include_passed: bool = True,
        max_runs: int = 5,
        max_passed_runs: int = 3,
    ) -> "LogFetchPlan":
        """Resolve runs and jobs without fetching any log content.

        Returns a ``LogFetchPlan`` that callers (e.g. ``analyze_ci_url``) can
        consult against a cache before paying for log-content fetches. The
        plan exposes the ``planned_jobs`` list -- the same tuples the
        internal log-fetch loop iterates -- plus the ``selected_runs`` list
        used to build the final report's run metadata.
        """
        selected_runs = self._resolve_runs(target, max_runs=max_runs)
        log_stage_event(
            self._logger,
            "fetch_runs",
            repo=target.repo,
            runs=len(selected_runs),
            transport=type(self._transport).__name__,
        )

        selected_group: Optional[str] = None
        if target.job_id is not None and target.run_id is not None:
            target_jobs = self.fetch_jobs_for_run(target.repo, target.run_id)
            for job in target_jobs:
                if job.job_id == target.job_id:
                    selected_group = normalize_job_name(job.job_name)
                    break
            if selected_group is None:
                raise ValueError(f"Job {target.job_id} not found in run {target.run_id}.")

        jobs_processed = 0
        all_jobs: list[tuple[WorkflowRun, WorkflowJob, str, Literal["passed", "failed"]]] = []
        for run in selected_runs:
            jobs = self.fetch_jobs_for_run(target.repo, run.run_id)
            for job in jobs:
                jobs_processed += 1
                logical_name = normalize_job_name(job.job_name)
                if selected_group is not None and logical_name != selected_group:
                    continue

                status = classify_job_status(job.conclusion)
                if status is None:
                    log_stage_event(
                        self._logger,
                        "skip_job_without_logs",
                        run_id=run.run_id,
                        job_id=job.job_id,
                        job_name=job.job_name,
                        conclusion=job.conclusion,
                    )
                    continue
                all_jobs.append((run, job, logical_name, status))

        fetch_plan = _plan_log_fetches(
            all_jobs,
            include_passed=include_passed,
            max_passed_runs=max_passed_runs,
        )

        log_stage_event(
            self._logger,
            "plan_jobs",
            jobs_processed=jobs_processed,
            planned_failed_jobs=len([item for item in fetch_plan if item[3] == "failed"]),
            planned_passed_jobs=len([item for item in fetch_plan if item[3] == "passed"]),
        )

        return LogFetchPlan(
            target=target,
            selected_runs=selected_runs,
            planned_jobs=fetch_plan,
        )

    def fetch_planned_log_content(
        self,
        repo: str,
        planned_jobs: list[tuple[WorkflowRun, WorkflowJob, str, Literal["passed", "failed"]]],
    ) -> list[NormalizedLog]:
        """Fetch the log content for the supplied planned-job tuples.

        Public so callers that want to consult a cache before paying for the
        content fetch can iterate the plan themselves and call this with the
        subset of jobs that are NOT cached.
        """
        return self._fetch_planned_logs(repo, planned_jobs)

    def assemble_fetched_data(
        self,
        plan: "LogFetchPlan",
        logs: list[NormalizedLog],
        *,
        include_passed: bool,
        max_passed_runs: int,
    ) -> FetchedGitHubData:
        """Build the final ``FetchedGitHubData`` from a plan + log list.

        Public so callers (e.g. ``_fetch_with_cache_awareness``) that mix
        cached placeholder logs with freshly-fetched logs can reuse the same
        grouping/cap logic that ``fetch_logs`` applies internally.
        """
        log_stage_event(
            self._logger,
            "fetch_jobs",
            logs=len(logs),
            planned_failed_jobs=len([item for item in plan.planned_jobs if item[3] == "failed"]),
            planned_passed_jobs=len([item for item in plan.planned_jobs if item[3] == "passed"]),
        )

        if not include_passed:
            failed_only = [log for log in logs if log.status == "failed"]
            return FetchedGitHubData(runs=plan.selected_runs, logs=_sort_logs(failed_only))

        grouped_logs = group_logs_by_job(logs)
        selected_logs: list[NormalizedLog] = []
        for logical_name in sorted(grouped_logs):
            group_logs = grouped_logs[logical_name]
            failed_logs = [log for log in group_logs if log.status == "failed"]
            passed_logs = [log for log in group_logs if log.status == "passed"]
            selected_logs.extend(_sort_logs(failed_logs))
            selected_logs.extend(_sort_logs(passed_logs)[:max_passed_runs])

        return FetchedGitHubData(runs=plan.selected_runs, logs=_sort_logs(selected_logs))

    def fetch_workflow_runs_for_pr(
        self,
        repo: str,
        pr_number: int,
        max_runs: int,
    ) -> list[WorkflowRun]:
        pull = self._transport.get_json(f"repos/{repo}/pulls/{pr_number}")
        head_sha = pull.get("head", {}).get("sha")
        if not head_sha:
            return []

        payload = self._transport.get_json(
            f"repos/{repo}/actions/runs",
            params={"head_sha": head_sha, "per_page": max_runs},
        )
        runs = [_parse_workflow_run(item) for item in payload.get("workflow_runs", [])]
        return _sort_runs(runs)[:max_runs]

    def fetch_related_runs(
        self,
        repo: str,
        run_id: int,
        max_runs: int,
    ) -> list[WorkflowRun]:
        target_run = self.fetch_run(repo, run_id)
        if max_runs <= 1:
            return [target_run]

        params: dict[str, object] = {"per_page": max_runs * 3}
        if target_run.head_branch:
            params["branch"] = target_run.head_branch

        payload = self._transport.get_json(f"repos/{repo}/actions/runs", params=params)
        related_runs = []
        for item in payload.get("workflow_runs", []):
            run = _parse_workflow_run(item)
            if target_run.workflow_id is not None and run.workflow_id != target_run.workflow_id:
                continue
            related_runs.append(run)

        deduped: dict[int, WorkflowRun] = {target_run.run_id: target_run}
        for run in _sort_runs(related_runs):
            deduped.setdefault(run.run_id, run)

        ordered = [deduped[target_run.run_id]]
        ordered.extend(
            run for run_id, run in sorted(deduped.items(), key=lambda item: item[0], reverse=True)
            if run_id != target_run.run_id
        )
        return ordered[:max_runs]

    def fetch_run(self, repo: str, run_id: int) -> WorkflowRun:
        payload = self._transport.get_json(f"repos/{repo}/actions/runs/{run_id}")
        return _parse_workflow_run(payload)

    def fetch_jobs_for_run(self, repo: str, run_id: int) -> list[WorkflowJob]:
        payload = self._transport.get_json(
            f"repos/{repo}/actions/runs/{run_id}/jobs",
            params={"per_page": 100},
        )
        jobs = [_parse_workflow_job(run_id, item) for item in payload.get("jobs", [])]
        return sorted(jobs, key=lambda job: (job.job_name.lower(), job.job_id))

    def fetch_job_log(self, repo: str, job_id: int) -> str:
        endpoint = f"repos/{repo}/actions/jobs/{job_id}/logs"
        try:
            content = self._transport.get_text(endpoint)
        except (RuntimeError, requests.HTTPError) as exc:
            raise RuntimeError(_format_log_fetch_error(repo, job_id, exc, self._transport)) from exc
        return normalize_log_content(content)

    def _fetch_planned_logs(
        self,
        repo: str,
        planned_jobs: list[tuple[WorkflowRun, WorkflowJob, str, Literal["passed", "failed"]]],
    ) -> list[NormalizedLog]:
        if not planned_jobs:
            return []

        if self._supports_parallel_log_fetch():
            max_workers = min(4, len(planned_jobs))
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                logs = list(executor.map(lambda item: self._fetch_single_log(repo, item), planned_jobs))
            return _sort_logs(logs)

        return _sort_logs([self._fetch_single_log(repo, item) for item in planned_jobs])

    def _fetch_single_log(
        self,
        repo: str,
        planned_job: tuple[WorkflowRun, WorkflowJob, str, Literal["passed", "failed"]],
    ) -> NormalizedLog:
        run, job, _, status = planned_job
        return NormalizedLog(
            run_id=run.run_id,
            job_id=job.job_id,
            job_name=job.job_name,
            status=status,
            content=self.fetch_job_log(repo, job.job_id),
        )

    def _supports_parallel_log_fetch(self) -> bool:
        return isinstance(self._transport, GhCLITransport)

    def _resolve_runs(self, target: GitHubTarget, *, max_runs: int) -> list[WorkflowRun]:
        if target.pr_number is not None:
            return self.fetch_workflow_runs_for_pr(target.repo, target.pr_number, max_runs)
        if target.run_id is not None:
            return self.fetch_related_runs(target.repo, target.run_id, max_runs)
        raise ValueError("GitHub target must include a PR number or workflow run id.")


def classify_job_status(conclusion: Optional[str]) -> Optional[Literal["passed", "failed"]]:
    normalized = (conclusion or "").strip().lower()
    if normalized == "success":
        return "passed"
    if normalized in {"failure", "timed_out", "cancelled", "action_required", "startup_failure"}:
        return "failed"
    return None


def normalize_job_name(job_name: str) -> str:
    normalized = re.sub(r"[\s_]+", "-", job_name.strip().lower())
    if "-" not in normalized:
        return normalized

    head, tail = normalized.rsplit("-", 1)
    if tail.isalpha():
        return head
    return normalized


def group_logs_by_job(logs: Iterable[NormalizedLog]) -> dict[str, list[NormalizedLog]]:
    grouped: dict[str, list[NormalizedLog]] = defaultdict(list)
    for log in logs:
        grouped[normalize_job_name(log.job_name)].append(log)

    return {
        logical_name: _sort_logs(job_logs)
        for logical_name, job_logs in sorted(grouped.items(), key=lambda item: item[0])
    }


def _sort_logs(logs: Iterable[NormalizedLog]) -> list[NormalizedLog]:
    return sort_logs_by_job(logs, normalize_job_name=normalize_job_name)
