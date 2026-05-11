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


@dataclass(slots=True, frozen=True)
class ScoreComponents:
    """Typed breakdown of the components that compose a ``ScoredBlock.score``.

    Carried alongside the score on ``ScoredBlock`` so consumers can read the
    components directly instead of reverse-engineering them from the score
    formula. The reverse-engineering approach was brittle: any future change
    to the scoring formula would silently desynchronize the recomputed
    components from the actual score.

    Fields:
        severity_weight: ``highest_anchor_severity * 5.0``.
        signal_density: signal count / line count for the block.
        duplicate_penalty: duplicate-line count / line count for the block.
    """

    severity_weight: float
    signal_density: float
    duplicate_penalty: float

    def to_dict(self) -> Dict[str, float]:
        return {
            "severity_weight": self.severity_weight,
            "signal_density": self.signal_density,
            "duplicate_penalty": self.duplicate_penalty,
        }


@dataclass(slots=True)
class ScoredBlock:
    block: LogBlock
    score: float
    classification: str
    score_components: ScoreComponents


@dataclass(slots=True)
class ReductionResult:
    blocks: List[ScoredBlock]
    summary: Optional[str]
    detected_failures: List["DetectedFailure"] = field(default_factory=list)


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
