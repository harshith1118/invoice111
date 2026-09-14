"""Workflow stage logging.

Stages follow the PRD (START, PDF_EXTRACTION, GROQ_EXTRACTION, VALIDATION,
FX, MATCHING, REVIEW, COMPLETE, ERROR). Logs persist into the
``processing_logs`` SQLite table. Messages here must NEVER include
secrets, credentials or authentication headers.
"""

from __future__ import annotations

import time

from app.db.database import get_db


class StageTimer:
    """Context manager that records a processing stage with duration."""

    def __init__(self, analysis_id: int | None, stage: str, message: str | None = None):
        self.analysis_id = analysis_id
        self.stage = stage
        self.message = message
        self._started = None

    def __enter__(self):
        self._started = time.monotonic()
        return self

    def __exit__(self, exc_type, exc, tb):
        duration_ms = int((time.monotonic() - self._started) * 1000)
        status = "error" if exc_type else "ok"
        get_db().insert_log(
            analysis_id=self.analysis_id,
            stage=self.stage,
            status=status,
            message=self.message,
            duration_ms=duration_ms,
        )
        return False


def log_stage(
    analysis_id: int | None,
    stage: str,
    status: str,
    message: str | None = None,
    duration_ms: int | None = None,
) -> None:
    get_db().insert_log(analysis_id, stage, status, message, duration_ms)