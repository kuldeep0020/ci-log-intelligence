from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional


@dataclass(slots=True)
class ParsedLine:
    line_number: int
    content: str
    timestamp: Optional[datetime]
    step_id: Optional[str]
    signals: List[str]


@dataclass(slots=True)
class Anchor:
    line_number: int
    type: str
    severity: int


@dataclass(slots=True)
class LogBlock:
    start_line: int
    end_line: int
    lines: List[ParsedLine]
    anchors: List[Anchor]


@dataclass(slots=True)
class ScoredBlock:
    block: LogBlock
    score: float
    classification: str


@dataclass(slots=True)
class ReductionResult:
    blocks: List[ScoredBlock]
    summary: Optional[str]


@dataclass(slots=True)
class StoredLog:
    reference: str
    byte_size: int
    backend_name: str


@dataclass(slots=True)
class AnchorCluster:
    cluster_id: str
    anchors: List[Anchor]
    step_id: Optional[str]


@dataclass(slots=True)
class StageMetric:
    stage_name: str
    duration_ms: float
    values: Dict[str, float] = field(default_factory=dict)
