from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from abc import ABC, abstractmethod
from concurrent.futures import ThreadPoolExecutor
from collections import defaultdict
from typing import Any, Iterable, Literal, Mapping, Optional
from urllib.parse import urlencode

import requests

from ...utils.logging import get_structured_logger, log_stage_event
from .models import FetchedGitHubData, GitHubTarget, NormalizedLog, WorkflowJob, WorkflowRun

_ANSI_ESCAPE_PATTERN = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


class GitHubTransport(ABC):
    @abstractmethod
    def get_json(
        self,
        endpoint: str,
        params: Optional[Mapping[str, object]] = None,
    ) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def get_text(
        self,
        endpoint: str,
        params: Optional[Mapping[str, object]] = None,
    ) -> str:
        raise NotImplementedError


class GhCLITransport(GitHubTransport):
    def get_json(
        self,
        endpoint: str,
        params: Optional[Mapping[str, object]] = None,
    ) -> dict[str, Any]:
        payload = self._run(endpoint, params=params, expect_json=True)
        return json.loads(payload)

    def get_text(
        self,
        endpoint: str,
        params: Optional[Mapping[str, object]] = None,
    ) -> str:
        return self._run(endpoint, params=params, expect_json=False)

    def _run(
        self,
        endpoint: str,
        params: Optional[Mapping[str, object]],
        expect_json: bool,
    ) -> str:
        target = _build_endpoint(endpoint, params)
        completed = subprocess.run(
            ["gh", "api", target],
            check=False,
            capture_output=True,
        )
        if completed.returncode != 0:
            raise RuntimeError(completed.stderr.decode("utf-8", errors="replace").strip())
        return completed.stdout.decode("utf-8", errors="replace")


class RequestsTransport(GitHubTransport):
    def __init__(
        self,
        token: Optional[str] = None,
        session: Optional[requests.Session] = None,
    ) -> None:
        resolved_token = token or os.getenv("GITHUB_TOKEN")
        if not resolved_token:
            raise RuntimeError("GITHUB_TOKEN is required when gh CLI is unavailable.")

        self._session = session or requests.Session()
        self._session.headers.update(
            {
                "Authorization": f"Bearer {resolved_token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            }
        )

    def get_json(
        self,
        endpoint: str,
        params: Optional[Mapping[str, object]] = None,
    ) -> dict[str, Any]:
        response = self._session.get(
            f"https://api.github.com/{endpoint}",
            params=dict(params or {}),
            timeout=30,
        )
        response.raise_for_status()
        return response.json()

    def get_text(
        self,
        endpoint: str,
        params: Optional[Mapping[str, object]] = None,
    ) -> str:
        response = self._session.get(
            f"https://api.github.com/{endpoint}",
            params=dict(params or {}),
            timeout=30,
            allow_redirects=True,
        )
        response.raise_for_status()
        return response.text


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

        logs: list[NormalizedLog] = []
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
        logs = self._fetch_planned_logs(target.repo, fetch_plan)

        log_stage_event(
            self._logger,
            "fetch_jobs",
            jobs_processed=jobs_processed,
            logs=len(logs),
            planned_failed_jobs=len([item for item in fetch_plan if item[3] == "failed"]),
            planned_passed_jobs=len([item for item in fetch_plan if item[3] == "passed"]),
        )

        if not include_passed:
            failed_only = [log for log in logs if log.status == "failed"]
            return FetchedGitHubData(runs=selected_runs, logs=_sort_logs(failed_only))

        grouped_logs = group_logs_by_job(logs)
        selected_logs: list[NormalizedLog] = []
        for logical_name in sorted(grouped_logs):
            group_logs = grouped_logs[logical_name]
            failed_logs = [log for log in group_logs if log.status == "failed"]
            passed_logs = [log for log in group_logs if log.status == "passed"]
            selected_logs.extend(_sort_logs(failed_logs))
            selected_logs.extend(_sort_logs(passed_logs)[:max_passed_runs])

        return FetchedGitHubData(runs=selected_runs, logs=_sort_logs(selected_logs))

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


def create_github_transport(token: Optional[str] = None) -> GitHubTransport:
    if shutil.which("gh"):
        return GhCLITransport()
    return RequestsTransport(token=token)


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


def normalize_log_content(content: str) -> str:
    normalized = content.replace("\r\n", "\n").replace("\r", "\n")
    normalized = _ANSI_ESCAPE_PATTERN.sub("", normalized)
    return normalized.strip("\n")


def _plan_log_fetches(
    jobs: Iterable[tuple[WorkflowRun, WorkflowJob, str, Literal["passed", "failed"]]],
    *,
    include_passed: bool,
    max_passed_runs: int,
) -> list[tuple[WorkflowRun, WorkflowJob, str, Literal["passed", "failed"]]]:
    job_list = sorted(
        jobs,
        key=lambda item: (item[2], -item[0].run_id, item[1].job_name.lower(), item[1].job_id),
    )
    failed_groups = {logical_name for _, _, logical_name, status in job_list if status == "failed"}

    planned: list[tuple[WorkflowRun, WorkflowJob, str, Literal["passed", "failed"]]] = [
        item for item in job_list if item[3] == "failed"
    ]
    if not include_passed or not failed_groups or max_passed_runs <= 0:
        return planned

    passed_per_group: dict[str, int] = defaultdict(int)
    for item in job_list:
        _, _, logical_name, status = item
        if status != "passed" or logical_name not in failed_groups:
            continue
        if passed_per_group[logical_name] >= max_passed_runs:
            continue
        planned.append(item)
        passed_per_group[logical_name] += 1

    return sorted(
        planned,
        key=lambda item: (item[2], -item[0].run_id, item[1].job_name.lower(), item[1].job_id),
    )


def _format_log_fetch_error(
    repo: str,
    job_id: int,
    error: Exception,
    transport: GitHubTransport,
) -> str:
    message = str(error).strip() or error.__class__.__name__
    if "404" in message:
        return (
            f"GitHub returned 404 while fetching logs for job {job_id} in {repo} "
            f"via {type(transport).__name__}. This usually means the job did not emit logs "
            f"(for example, it was skipped) or the current GitHub credentials do not have "
            f"access to this repository or workflow run. Original error: {message}"
        )
    return (
        f"Failed to fetch logs for job {job_id} in {repo} via {type(transport).__name__}: "
        f"{message}"
    )


def _parse_workflow_run(payload: Mapping[str, Any]) -> WorkflowRun:
    return WorkflowRun(
        run_id=int(payload["id"]),
        workflow_id=int(payload["workflow_id"]) if payload.get("workflow_id") is not None else None,
        head_branch=payload.get("head_branch"),
        head_sha=payload.get("head_sha"),
        html_url=payload.get("html_url", ""),
        status=payload.get("status"),
        conclusion=payload.get("conclusion"),
        display_title=payload.get("display_title") or payload.get("name") or "",
    )


def _parse_workflow_job(run_id: int, payload: Mapping[str, Any]) -> WorkflowJob:
    return WorkflowJob(
        run_id=run_id,
        job_id=int(payload["id"]),
        job_name=str(payload["name"]),
        status=payload.get("status"),
        conclusion=payload.get("conclusion"),
    )


def _build_endpoint(endpoint: str, params: Optional[Mapping[str, object]]) -> str:
    if not params:
        return endpoint
    sorted_params = sorted((key, value) for key, value in params.items() if value is not None)
    return f"{endpoint}?{urlencode(sorted_params)}"


def _sort_runs(runs: Iterable[WorkflowRun]) -> list[WorkflowRun]:
    return sorted(runs, key=lambda run: run.run_id, reverse=True)


def _sort_logs(logs: Iterable[NormalizedLog]) -> list[NormalizedLog]:
    return sorted(
        logs,
        key=lambda log: (normalize_job_name(log.job_name), -log.run_id, log.job_name.lower(), log.job_id),
    )
