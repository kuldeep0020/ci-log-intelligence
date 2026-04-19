from __future__ import annotations

import re
from urllib.parse import urlparse

from .models import GitHubTarget

_PR_URL_PATTERN = re.compile(r"^(?P<owner>[^/]+)/(?P<repo>[^/]+)/pull/(?P<pr>\d+)(?:/.*)?$")
_RUN_URL_PATTERN = re.compile(
    r"^(?P<owner>[^/]+)/(?P<repo>[^/]+)/actions/runs/(?P<run>\d+)(?:/.*)?$"
)
_JOB_URL_PATTERN = re.compile(
    r"^(?P<owner>[^/]+)/(?P<repo>[^/]+)/actions/runs/(?P<run>\d+)/job/(?P<job>\d+)(?:/.*)?$"
)


def resolve_github_url(url: str) -> GitHubTarget:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or parsed.netloc != "github.com":
        raise ValueError(f"Unsupported GitHub URL: {url}")

    normalized_path = parsed.path.strip("/")
    job_match = _JOB_URL_PATTERN.match(normalized_path)
    if job_match:
        return GitHubTarget(
            repo=f"{job_match.group('owner')}/{job_match.group('repo')}",
            run_id=int(job_match.group("run")),
            job_id=int(job_match.group("job")),
            pr_number=None,
        )

    run_match = _RUN_URL_PATTERN.match(normalized_path)
    if run_match:
        return GitHubTarget(
            repo=f"{run_match.group('owner')}/{run_match.group('repo')}",
            run_id=int(run_match.group("run")),
            job_id=None,
            pr_number=None,
        )

    pr_match = _PR_URL_PATTERN.match(normalized_path)
    if pr_match:
        return GitHubTarget(
            repo=f"{pr_match.group('owner')}/{pr_match.group('repo')}",
            run_id=None,
            job_id=None,
            pr_number=int(pr_match.group("pr")),
        )

    raise ValueError(f"Unsupported GitHub URL: {url}")
