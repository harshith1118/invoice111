"""Persistence layer.

SQLite (default) and managed PostgreSQL are both supported behind one stable
``Database`` interface, so services/routes/tests never care which backend is
active:

- local dev, pytest and the evaluation suite use SQLite (unchanged)
- deployments configure ``DATABASE_URL`` (e.g. Neon) for durable Postgres

Design kept deliberately simple:
- stdlib ``sqlite3`` (no ORM) or psycopg 3 behind the same method surface
- one connection per operation (safe across FastAPI's thread pool)
- WAL mode enabled on SQLite
- schema created idempotently on startup (per-backend DDL)
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

# PostgreSQL variant of the same schema: identical table/column/index names
# so every query in the public methods works verbatim. JSON stays TEXT.
_SCHEMA_POSTGRES = """
CREATE TABLE IF NOT EXISTS documents (
    id             BIGSERIAL PRIMARY KEY,
    document_type  TEXT NOT NULL,
    filename       TEXT NOT NULL,
    created_at     TEXT NOT NULL,
    extracted_text TEXT
);

CREATE TABLE IF NOT EXISTS analyses (
    id                  BIGSERIAL PRIMARY KEY,
    po_document_id      BIGINT,
    invoice_document_id BIGINT,
    po_data_json        TEXT,
    invoice_data_json   TEXT,
    created_at          TEXT NOT NULL,
    processing_time_ms  INTEGER
);

CREATE TABLE IF NOT EXISTS comparison_results (
    id          BIGSERIAL PRIMARY KEY,
    analysis_id BIGINT NOT NULL,
    status      TEXT NOT NULL,
    result_json TEXT,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS review_decisions (
    id            BIGSERIAL PRIMARY KEY,
    comparison_id BIGINT NOT NULL,
    decision      TEXT NOT NULL,
    note          TEXT,
    reviewer      TEXT,
    created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS processing_logs (
    id          BIGSERIAL PRIMARY KEY,
    analysis_id BIGINT,
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
    """Thin wrapper around SQLite (or PostgreSQL) with connection-per-operation
    semantics. The public method surface is identical for both backends."""

    def __init__(self, db_path: str | Path | None = None) -> None:
        settings = get_settings()
        self.backend: str = "sqlite"
        if db_path is not None:
            # An explicit path always means SQLite (tests / evaluation isolation).
            self.backend = "sqlite"
            path = Path(db_path)
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
            except OSError:
                path = Path(tempfile.gettempdir()) / path.name
                path.parent.mkdir(parents=True, exist_ok=True)
            self.db_path = path
        elif settings.storage_backend == "postgres":
            self.backend = "postgres"
            self.db_path = Path("data/invoicematch.db")  # unused; kept stable
            self._postgres_url = settings.postgres_url
        else:
            path = settings.db_path_abs
            # Serverless filesystems (e.g. Vercel) are read-only except the temp
            # dir; never crash on an unwritable location - fall back to temp.
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
            except OSError:
                path = Path(tempfile.gettempdir()) / path.name
                path.parent.mkdir(parents=True, exist_ok=True)
            self.db_path = path

    def _connect(self) -> Any:
        if self.backend == "postgres":
            return _PgConnection(self._postgres_url)
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA foreign_keys=ON;")
        return conn

    # ----- lifecycle -------------------------------------------------
    def init_db(self) -> None:
        schema = _SCHEMA_POSTGRES if self.backend == "postgres" else _SCHEMA
        with self._connect() as conn:
            conn.executescript(schema)

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


# ---------------------------------------------------------------------------
# PostgreSQL backend facade
#
# Presents a sqlite3-compatible surface (execute/executescript/context manager
# + cursor.fetchone/fetchall/lastrowid) over psycopg 3 so the public Database
# methods above work unmodified. psycopg is imported lazily so SQLite-only
# installs/tests never require it.
# ---------------------------------------------------------------------------
def _translate_params(sql: str) -> str:
    # This module's SQL uses `?` placeholders only (never inside literals).
    return sql.replace("?", "%s")


class _PgCursor:
    def __init__(self, conn: Any, sql: str, params: Iterable[Any] | None) -> None:
        stmt = sql.lstrip()
        key = stmt[:6].upper() if len(stmt) >= 6 else stmt.upper()
        if key == "PRAGMA":  # SQLite-only pragmas are no-ops on Postgres
            self._cur = _NoopCursor()
            return
        t_sql = _translate_params(stmt)
        self._inserting = key == "INSERT"
        if self._inserting:
            t_sql += " RETURNING id"
        self._cur = conn.cursor()
        self._cur.execute(t_sql, tuple(params or ()))

    @property
    def lastrowid(self) -> int:
        if not self._inserting:
            raise AttributeError("lastrowid is only available on INSERT")
        row = self._cur.fetchone()
        return int(row["id"])

    def fetchone(self) -> Any:
        return self._cur.fetchone()

    def fetchall(self) -> list[Any]:
        return list(self._cur.fetchall())


class _NoopCursor:
    lastrowid = None

    def fetchone(self) -> None:
        return None

    def fetchall(self) -> list[Any]:
        return []


class _PgConnection:
    """Context-managed PostgreSQL connection with sqlite3-like API."""

    def __init__(self, url: str | None) -> None:
        import psycopg
        from psycopg.rows import dict_row

        if not url:
            raise RuntimeError("storage_backend=postgres requires DATABASE_URL")
        self._conn = psycopg.connect(url, autocommit=True, row_factory=dict_row)

    def execute(self, sql: str, params: Iterable[Any] | None = None) -> _PgCursor:
        return _PgCursor(self._conn, sql, params)

    def executescript(self, script: str) -> None:
        for stmt in script.split(";"):
            if stmt.strip():
                with self._conn.cursor() as cur:
                    cur.execute(stmt)

    def __enter__(self) -> "_PgConnection":
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> bool:
        return False


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