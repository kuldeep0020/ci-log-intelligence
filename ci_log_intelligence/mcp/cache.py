from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from threading import Lock
from typing import Optional

from ..models import ParsedLine, ReductionResult


@dataclass(slots=True, frozen=True)
class CacheKey:
    repo: str
    run_id: int
    job_id: int


@dataclass(slots=True)
class CachedJob:
    job_name: str
    parsed_lines: list[ParsedLine]
    reduction_result: ReductionResult


class JobCache:
    """Thread-safe LRU cache keyed on ``(repo, run_id, job_id)``.

    CI job logs are immutable once a job finishes, so no TTL is needed --
    LRU eviction by ``max_entries`` is sufficient. The cache stores both
    the raw ``parsed_lines`` and the reduced ``ReductionResult`` so the
    ``get_block`` tool can answer drill-down queries without re-parsing.
    """

    def __init__(self, max_entries: int = 32) -> None:
        self._store: "OrderedDict[CacheKey, CachedJob]" = OrderedDict()
        self._max_entries = max_entries
        self._lock = Lock()

    def get(self, key: CacheKey) -> Optional[CachedJob]:
        with self._lock:
            value = self._store.get(key)
            if value is not None:
                self._store.move_to_end(key)
            return value

    def put(self, key: CacheKey, value: CachedJob) -> None:
        with self._lock:
            self._store[key] = value
            self._store.move_to_end(key)
            while len(self._store) > self._max_entries:
                self._store.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._store.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._store)


_DEFAULT_CACHE = JobCache()


def get_default_cache() -> JobCache:
    return _DEFAULT_CACHE


__all__ = ["CacheKey", "CachedJob", "JobCache", "get_default_cache"]
