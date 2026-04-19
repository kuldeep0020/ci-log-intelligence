from __future__ import annotations

from fastapi import FastAPI
from pydantic import BaseModel, Field

from .. import analyze_log


class AnalyzeRequest(BaseModel):
    log: str = Field(..., min_length=1)


class AnalyzeBlockResponse(BaseModel):
    start_line: int
    end_line: int
    score: float
    classification: str


class AnalyzeResponse(BaseModel):
    blocks: list[AnalyzeBlockResponse]
    summary: str


def create_app() -> FastAPI:
    app = FastAPI(title="CI Log Intelligence")

    @app.post("/analyze", response_model=AnalyzeResponse)
    def analyze(request: AnalyzeRequest) -> AnalyzeResponse:
        result = analyze_log(request.log)
        blocks = [
            AnalyzeBlockResponse(
                start_line=scored.block.start_line,
                end_line=scored.block.end_line,
                score=scored.score,
                classification=scored.classification,
            )
            for scored in result.blocks
        ]
        return AnalyzeResponse(blocks=blocks, summary=result.summary or "")

    return app


app = create_app()

__all__ = ["AnalyzeRequest", "AnalyzeResponse", "app", "create_app"]
