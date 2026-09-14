"""SQLite persistence layer.

Kept deliberately simple:
- stdlib ``sqlite3`` (no ORM)
- a fresh connection per operation (safe across FastAPI's thread pool)
- WAL mode enabled
- schema created idempotently on startup
"""

from __future__ import annotations

import json
import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from app.config import get_settings

_SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    document_type  TEXT NOT NULL,
    filename       TEXT NOT NULL,
    created_at     TEXT NOT NULL,
    extracted_text TEXT
);

CREATE TABLE IF NOT EXISTS analyses (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    po_document_id     INTEGER,
    invoice_document_id INTEGER,
    po_data_json       TEXT,
    invoice_data_json  TEXT,
    created_at         TEXT NOT NULL,
    processing_time_ms INTEGER
);

CREATE TABLE IF NOT EXISTS comparison_results (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    analysis_id INTEGER NOT NULL,
    status      TEXT NOT NULL,
    result_json TEXT,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS review_decisions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    comparison_id INTEGER NOT NULL,
    decision      TEXT NOT NULL,
    note          TEXT,
    reviewer      TEXT,
    created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS processing_logs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    analysis_id INTEGER,
    stage       TEXT NOT NULL,
    status      TEXT NOT NULL,
    message     TEXT,
    created_at  TEXT NOT NULL,
    duration_ms INTEGER
);

CREATE INDEX IF NOT EXISTS idx_comparisons_analysis
    ON comparison_results (analysis_id);
CREATE INDEX IF NOT EXISTS idx_reviews_comparison
    ON review_decisions (comparison_id);
CREATE INDEX IF NOT EXISTS idx_logs_analysis
    ON processing_logs (analysis_id);
"""

_PROCESSING_STAGES = (
    "START",
    "PDF_EXTRACTION",
    "GROQ_EXTRACTION",
    "VALIDATION",
    "FX",
    "MATCHING",
    "REVIEW",
    "COMPLETE",
    "ERROR",
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _dumps(data: Any) -> str | None:
    if data is None:
        return None
    return json.dumps(data, ensure_ascii=False)


def _loads(raw: str | None) -> Any:
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


class Database:
    """Thin wrapper around sqlite3 with connection-per-operation semantics."""

    def __init__(self, db_path: str | Path | None = None) -> None:
        settings = get_settings()
        path = Path(db_path) if db_path else settings.db_path_abs
        # Serverless filesystems (e.g. Vercel) are read-only except the temp
        # dir; never crash on an unwritable location - fall back to temp.
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
        except OSError:
            path = Path(tempfile.gettempdir()) / path.name
            path.parent.mkdir(parents=True, exist_ok=True)
        self.db_path = path

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA foreign_keys=ON;")
        return conn

    # ----- lifecycle -------------------------------------------------
    def init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(_SCHEMA)

    # ----- documents --------------------------------------------------
    def insert_document(
        self,
        document_type: str,
        filename: str,
        extracted_text: str | None = None,
    ) -> int:
        with self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO documents (document_type, filename, created_at, extracted_text)"
                " VALUES (?, ?, ?, ?)",
                (document_type, filename, _now(), extracted_text),
            )
            return int(cur.lastrowid)

    def get_document(self, document_id: int) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM documents WHERE id = ?", (document_id,)
            ).fetchone()
            return dict(row) if row else None

    # ----- analyses ----------------------------------------------------
    def insert_analysis(
        self,
        po_document_id: int | None,
        invoice_document_id: int | None,
        po_data: Any = None,
        invoice_data: Any = None,
        processing_time_ms: int | None = None,
    ) -> int:
        with self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO analyses (po_document_id, invoice_document_id, "
                " po_data_json, invoice_data_json, created_at, processing_time_ms)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (
                    po_document_id,
                    invoice_document_id,
                    _dumps(po_data),
                    _dumps(invoice_data),
                    _now(),
                    processing_time_ms,
                ),
            )
            return int(cur.lastrowid)

    def update_analysis(
        self,
        analysis_id: int,
        po_data: Any = None,
        invoice_data: Any = None,
        processing_time_ms: int | None = None,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE analyses SET po_data_json = ?, invoice_data_json = ?,"
                " processing_time_ms = COALESCE(?, processing_time_ms)"
                " WHERE id = ?",
                (_dumps(po_data), _dumps(invoice_data), processing_time_ms, analysis_id),
            )

    def get_analysis(self, analysis_id: int) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM analyses WHERE id = ?", (analysis_id,)
            ).fetchone()
            return dict(row) if row else None

    # ----- comparison results ------------------------------------------
    def insert_comparison(
        self,
        analysis_id: int,
        status: str,
        result: Any = None,
    ) -> int:
        with self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO comparison_results (analysis_id, status, result_json, created_at)"
                " VALUES (?, ?, ?, ?)",
                (analysis_id, status, _dumps(result), _now()),
            )
            return int(cur.lastrowid)

    def get_comparison(self, comparison_id: int) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM comparison_results WHERE id = ?", (comparison_id,)
            ).fetchone()
            return dict(row) if row else None

    def get_comparison_by_analysis(self, analysis_id: int) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM comparison_results WHERE analysis_id = ? ORDER BY id DESC",
                (analysis_id,),
            ).fetchone()
            return dict(row) if row else None

    # ----- reviews ------------------------------------------------------
    def insert_review(
        self,
        comparison_id: int,
        decision: str,
        note: str | None = None,
        reviewer: str | None = None,
    ) -> int:
        with self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO review_decisions (comparison_id, decision, note, reviewer, created_at)"
                " VALUES (?, ?, ?, ?, ?)",
                (comparison_id, decision, note, reviewer, _now()),
            )
            return int(cur.lastrowid)

    def get_reviews(self, comparison_id: int) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM review_decisions WHERE comparison_id = ? ORDER BY id DESC",
                (comparison_id,),
            ).fetchall()
            return [dict(r) for r in rows]

    # ----- processing logs ----------------------------------------------
    def insert_log(
        self,
        analysis_id: int | None,
        stage: str,
        status: str,
        message: str | None = None,
        duration_ms: int | None = None,
    ) -> int:
        if stage not in _PROCESSING_STAGES:
            raise ValueError(f"Unknown processing stage: {stage}")
        with self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO processing_logs (analysis_id, stage, status, message, created_at, duration_ms)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (analysis_id, stage, status, message, _now(), duration_ms),
            )
            return int(cur.lastrowid)

    def get_logs(self, analysis_id: int) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM processing_logs WHERE analysis_id = ? ORDER BY id ASC",
                (analysis_id,),
            ).fetchall()
            return [dict(r) for r in rows]

    # ----- history / dashboard ------------------------------------------
    def list_comparisons(
        self,
        result: str | None = None,
        review_status: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return history rows joined with analysis + review context."""
        query = """
            SELECT cr.id AS comparison_id, cr.status, cr.created_at,
                   a.id AS analysis_id,
                   a.po_data_json, a.invoice_data_json,
                   (SELECT decision FROM review_decisions rd
                     WHERE rd.comparison_id = cr.id ORDER BY rd.id DESC LIMIT 1)
                     AS review_decision
            FROM comparison_results cr
            JOIN analyses a ON a.id = cr.analysis_id
        """
        clauses: list[str] = []
        args: list[Any] = []
        if result:
            clauses.append("cr.status = ?")
            args.append(result)
        if review_status in ("approved", "rejected"):
            clauses.append(
                "(SELECT decision FROM review_decisions rd"
                "  WHERE rd.comparison_id = cr.id ORDER BY rd.id DESC LIMIT 1) = ?"
            )
            args.append(review_status)
        elif review_status == "pending":
            clauses.append(
                "NOT EXISTS (SELECT 1 FROM review_decisions rd WHERE rd.comparison_id = cr.id)"
            )
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY cr.id DESC"
        with self._connect() as conn:
            rows = conn.execute(query, args).fetchall()
        items = []
        for row in rows:
            po_data = _loads(row["po_data_json"]) or {}
            inv_data = _loads(row["invoice_data_json"]) or {}
            review_decision = row["review_decision"]
            items.append(
                {
                    "comparison_id": row["comparison_id"],
                    "analysis_id": row["analysis_id"],
                    "date": row["created_at"],
                    "result": row["status"],
                    "po_number": (po_data or {}).get("po_number"),
                    "invoice_number": (inv_data or {}).get("invoice_number"),
                    "vendor": (po_data or {}).get("vendor_name"),
                    "review_status": (
                        review_decision
                        if review_decision
                        else ("pending" if row["status"] == "EXCEPTION" else "none")
                    ),
                }
            )
        return items

    def dashboard_stats(self) -> dict[str, Any]:
        with self._connect() as conn:
            total = conn.execute("SELECT COUNT(*) c FROM comparison_results").fetchone()["c"]
            matches = conn.execute(
                "SELECT COUNT(*) c FROM comparison_results WHERE status = 'MATCH'"
            ).fetchone()["c"]
            exceptions = conn.execute(
                "SELECT COUNT(*) c FROM comparison_results WHERE status = 'EXCEPTION'"
            ).fetchone()["c"]
            invalid = conn.execute(
                "SELECT COUNT(*) c FROM comparison_results WHERE status = 'INVALID DOCUMENT'"
            ).fetchone()["c"]
            reviewed = conn.execute(
                "SELECT COUNT(*) c FROM review_decisions"
            ).fetchone()["c"]
            pending = conn.execute(
                "SELECT COUNT(*) c FROM comparison_results cr"
                " WHERE cr.status = 'EXCEPTION'"
                "   AND NOT EXISTS (SELECT 1 FROM review_decisions rd"
                "                   WHERE rd.comparison_id = cr.id)"
            ).fetchone()["c"]
        return {
            "total": total,
            "matches": matches,
            "exceptions": exceptions,
            "invalid": invalid,
            "reviewed": reviewed,
            "pending_reviews": pending,
        }


# ---- module-level access (replaces get_settings at runtime) -----------
_db: Database | None = None


def get_db() -> Database:
    global _db
    if _db is None:
        _db = Database()
        _db.init_db()
    return _db


def use_database(db: Database | None) -> None:
    """Swap the active database (used by tests / evaluation isolation)."""
    global _db
    _db = db
    if db is not None:
        db.init_db()


def reset_db():
    global _db
    _db = None


def iter_stages() -> Iterable[str]:
    return _PROCESSING_STAGES