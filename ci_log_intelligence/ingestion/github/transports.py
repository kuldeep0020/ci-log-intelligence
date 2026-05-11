from __future__ import annotations

import json
import os
import shutil
import subprocess
from abc import ABC, abstractmethod
from typing import Any, Mapping, Optional
from urllib.parse import urlencode

import requests


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
        target = build_endpoint(endpoint, params)
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


def create_github_transport(token: Optional[str] = None) -> GitHubTransport:
    if shutil.which("gh"):
        return GhCLITransport()
    return RequestsTransport(token=token)


def build_endpoint(endpoint: str, params: Optional[Mapping[str, object]]) -> str:
    if not params:
        return endpoint
    sorted_params = sorted((key, value) for key, value in params.items() if value is not None)
    return f"{endpoint}?{urlencode(sorted_params)}"


__all__ = [
    "GhCLITransport",
    "GitHubTransport",
    "RequestsTransport",
    "build_endpoint",
    "create_github_transport",
]
