from __future__ import annotations

import re
from collections import defaultdict
from typing import Any, Iterable, Literal, Mapping

from .models import NormalizedLog, WorkflowJob, WorkflowRun
from .transports import GitHubTransport


_ANSI_ESCAPE_PATTERN = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def normalize_log_content(content: str) -> str:
    normalized = content.replace("\r\n", "\n").replace("\r", "\n")
    normalized = _ANSI_ESCAPE_PATTERN.sub("", normalized)
    return normalized.strip("\n")


def plan_log_fetches(
    jobs: Iterable[tuple[WorkflowRun, WorkflowJob, str, Literal["passed", "failed"]]],
    *,
    include_passed: bool,
    max_passed_runs: int,
) -> list[tuple[WorkflowRun, WorkflowJob, str, Literal["passed", "failed"]]]:
    job_list = sorted(
        jobs,
        key=lambda item: (item[2], -item[0].run_id, item[1].job_name.lower(), item[1].job_id),
    )
    failed_groups = {
        logical_name for _, _, logical_name, status in job_list if status == "failed"
    }

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


def format_log_fetch_error(
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


def parse_workflow_run(payload: Mapping[str, Any]) -> WorkflowRun:
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


def parse_workflow_job(run_id: int, payload: Mapping[str, Any]) -> WorkflowJob:
    return WorkflowJob(
        run_id=run_id,
        job_id=int(payload["id"]),
        job_name=str(payload["name"]),
        status=payload.get("status"),
        conclusion=payload.get("conclusion"),
    )


def sort_runs(runs: Iterable[WorkflowRun]) -> list[WorkflowRun]:
    return sorted(runs, key=lambda run: run.run_id, reverse=True)


def sort_logs_by_job(
    logs: Iterable[NormalizedLog],
    *,
    normalize_job_name,
) -> list[NormalizedLog]:
    return sorted(
        logs,
        key=lambda log: (
            normalize_job_name(log.job_name),
            -log.run_id,
            log.job_name.lower(),
            log.job_id,
        ),
    )


__all__ = [
    "format_log_fetch_error",
    "normalize_log_content",
    "parse_workflow_job",
    "parse_workflow_run",
    "plan_log_fetches",
    "sort_logs_by_job",
    "sort_runs",
]
