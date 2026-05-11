from __future__ import annotations

import threading
import unittest

from ci_log_intelligence.mcp.cache import (
    CacheKey,
    CachedJob,
    JobCache,
    get_default_cache,
)
from ci_log_intelligence.models import ReductionResult


def _make_cached(label: str) -> CachedJob:
    return CachedJob(
        job_name=label,
        parsed_lines=[],
        reduction_result=ReductionResult(blocks=[], summary=None),
    )


class JobCacheTests(unittest.TestCase):
    def test_lru_evicts_oldest_entry_when_capacity_exceeded(self) -> None:
        cache = JobCache(max_entries=2)
        key_a = CacheKey(repo="acme/widgets", run_id=1, job_id=1)
        key_b = CacheKey(repo="acme/widgets", run_id=1, job_id=2)
        key_c = CacheKey(repo="acme/widgets", run_id=1, job_id=3)

        cache.put(key_a, _make_cached("a"))
        cache.put(key_b, _make_cached("b"))
        cache.put(key_c, _make_cached("c"))

        self.assertEqual(len(cache), 2)
        self.assertIsNone(cache.get(key_a))
        self.assertIsNotNone(cache.get(key_b))
        self.assertIsNotNone(cache.get(key_c))

    def test_get_promotes_entry_to_most_recently_used(self) -> None:
        cache = JobCache(max_entries=2)
        key_a = CacheKey(repo="acme/widgets", run_id=1, job_id=1)
        key_b = CacheKey(repo="acme/widgets", run_id=1, job_id=2)
        key_c = CacheKey(repo="acme/widgets", run_id=1, job_id=3)

        cache.put(key_a, _make_cached("a"))
        cache.put(key_b, _make_cached("b"))
        # Touch ``a`` -- it becomes MRU; the next insertion should evict ``b``.
        self.assertIsNotNone(cache.get(key_a))
        cache.put(key_c, _make_cached("c"))

        self.assertIsNotNone(cache.get(key_a))
        self.assertIsNone(cache.get(key_b))
        self.assertIsNotNone(cache.get(key_c))

    def test_clear_empties_cache(self) -> None:
        cache = JobCache(max_entries=4)
        cache.put(CacheKey("r", 1, 1), _make_cached("a"))
        cache.put(CacheKey("r", 1, 2), _make_cached("b"))

        cache.clear()

        self.assertEqual(len(cache), 0)
        self.assertIsNone(cache.get(CacheKey("r", 1, 1)))

    def test_thread_safe_concurrent_puts_do_not_corrupt_state(self) -> None:
        cache = JobCache(max_entries=64)

        def worker(start: int) -> None:
            for index in range(start, start + 20):
                cache.put(
                    CacheKey(repo="acme/widgets", run_id=1, job_id=index),
                    _make_cached(f"job-{index}"),
                )

        threads = [threading.Thread(target=worker, args=(offset,)) for offset in (0, 20, 40)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        # 60 unique keys, capacity 64 -- nothing evicted; length must equal 60.
        self.assertEqual(len(cache), 60)

    def test_get_default_cache_returns_module_singleton(self) -> None:
        first = get_default_cache()
        second = get_default_cache()
        self.assertIs(first, second)


if __name__ == "__main__":
    unittest.main()
