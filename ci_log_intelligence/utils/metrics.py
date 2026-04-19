from __future__ import annotations

from contextlib import contextmanager
from time import perf_counter
from typing import Dict, Iterator, Optional

from ..models import StageMetric
from .logging import log_stage_event


class MetricsCollector:
    def __init__(self) -> None:
        self._stage_metrics: list[StageMetric] = []
        self._metrics: Dict[str, float] = {}

    def record_stage(self, stage_name: str, duration_ms: float, **values: float) -> None:
        self._stage_metrics.append(
            StageMetric(stage_name=stage_name, duration_ms=duration_ms, values=dict(values))
        )

    def record_metric(self, name: str, value: float) -> None:
        self._metrics[name] = value

    @property
    def stage_metrics(self) -> list[StageMetric]:
        return list(self._stage_metrics)

    def snapshot(self) -> Dict[str, object]:
        return {
            "stages": [
                {
                    "stage_name": item.stage_name,
                    "duration_ms": item.duration_ms,
                    "values": dict(item.values),
                }
                for item in self._stage_metrics
            ],
            "metrics": dict(self._metrics),
        }


@contextmanager
def measure_stage(
    stage_name: str,
    metrics: MetricsCollector,
    logger,
    **values: float,
) -> Iterator[None]:
    start = perf_counter()
    log_stage_event(logger, stage_name, event="start")
    try:
        yield
    finally:
        duration_ms = (perf_counter() - start) * 1000.0
        metrics.record_stage(stage_name, duration_ms, **values)
        log_stage_event(logger, stage_name, event="finish", duration_ms=round(duration_ms, 3), **values)
