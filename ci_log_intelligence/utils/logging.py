from __future__ import annotations

import json
import logging
from typing import Any, Dict


def get_structured_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler()
        formatter = logging.Formatter("%(message)s")
        handler.setFormatter(formatter)
        logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    return logger


def log_stage_event(logger: logging.Logger, stage_name: str, **fields: Any) -> None:
    payload: Dict[str, Any] = {"stage": stage_name, **fields}
    logger.info(json.dumps(payload, sort_keys=True, default=str))
