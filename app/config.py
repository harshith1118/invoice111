"""Central configuration for InvoiceMatch AI OS.

All configuration values load from environment variables (optionally via a
`.env` file). No secrets are hardcoded anywhere in the codebase and no
tolerance / model values are duplicated across modules.
"""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass, field
from decimal import Decimal
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent

# Flask-style: load a local .env if present. Does nothing when absent.
_loaded = load_dotenv(BASE_DIR / ".env")

VALID_EXTRACTOR_MODES = ("groq", "deterministic_eval")


@dataclass(frozen=True)
class Settings:
    # --- AI / extraction ---
    groq_api_key: str | None = None
    groq_model: str = "llama-3.3-70b-versatile"
    extractor_mode: str = "groq"
    groq_max_retries: int = 2
    groq_timeout_seconds: float = 60.0

    # --- Matching engine ---
    amount_tolerance: Decimal = Decimal("0.01")
    quantity_tolerance: Decimal = Decimal("0")

    # --- PDF uploads ---
    pdf_max_size_mb: int = 10

    # --- Storage ---
    # Backend: "sqlite" (local dev / tests / evaluation) or "postgres"
    # (managed PostgreSQL when DATABASE_URL is configured, e.g. Vercel).
    storage_backend: str = "sqlite"
    # SQLite file (unused when storage_backend == "postgres").
    db_path: str = "data/invoicematch.db"
    storage_persistence: str = "durable"  # "durable" | "ephemeral"
    # Managed PostgreSQL connection string. Never logged or exposed via API.
    postgres_url: str | None = None

    # --- Frankfurter exchange rates (second external integration) ---
    # Only queried when a PO and invoice use different currencies. Needs
    # no API key; base URL points at the public Frankfurter API.
    frankfurter_base_url: str = "https://api.frankfurter.dev/v1"
    frankfurter_timeout_seconds: float = 5.0

    # --- Server ---
    host: str = "127.0.0.1"
    port: int = 8000

    # Extra fields captured for tests/overrides (kept free-form).
    extra: dict = field(default_factory=dict)

    @property
    def db_path_abs(self) -> Path:
        p = Path(self.db_path)
        return p if p.is_absolute() else BASE_DIR / p

    def validate_extractor_mode(self) -> None:
        if self.extractor_mode not in VALID_EXTRACTOR_MODES:
            raise ValueError(
                "EXTRACTOR_MODE must be one of: "
                + ", ".join(VALID_EXTRACTOR_MODES)
            )


def _build_settings() -> Settings:
    def dec(name: str, default: str) -> Decimal:
        try:
            return Decimal(os.getenv(name, default))
        except Exception:  # pragma: no cover - defensive
            return Decimal(default)

    # Storage backend selection:
    #   - DATABASE_URL set -> managed PostgreSQL (durable, e.g. Neon on Vercel)
    #   - explicit DB_PATH  -> that SQLite file (always honored, incl. tests)
    #   - Vercel, no DB_PATH-> ephemeral SQLite under the temp dir
    #   - otherwise          -> local SQLite file
    postgres_url = os.getenv("DATABASE_URL") or None
    explicit_db_path = os.getenv("DB_PATH")
    if postgres_url:
        db_path = "data/invoicematch.db"
        storage_backend = "postgres"
        storage_persistence = "durable"
    elif explicit_db_path:
        db_path = explicit_db_path
        storage_backend = "sqlite"
        storage_persistence = "durable"
    elif os.getenv("VERCEL") == "1":
        db_path = str(Path(tempfile.gettempdir()) / "invoicematch.db")
        storage_backend = "sqlite"
        storage_persistence = "ephemeral"
    else:
        db_path = "data/invoicematch.db"
        storage_backend = "sqlite"
        storage_persistence = "durable"

    settings = Settings(
        groq_api_key=os.getenv("GROQ_API_KEY") or None,
        groq_model=os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile"),
        extractor_mode=os.getenv("EXTRACTOR_MODE", "groq").strip().lower(),
        groq_max_retries=int(os.getenv("GROQ_MAX_RETRIES", "2")),
        groq_timeout_seconds=float(os.getenv("GROQ_TIMEOUT_SECONDS", "60")),
        amount_tolerance=dec("AMOUNT_TOLERANCE", "0.01"),
        quantity_tolerance=dec("QUANTITY_TOLERANCE", "0"),
        pdf_max_size_mb=int(os.getenv("PDF_MAX_SIZE_MB", "10")),
        db_path=db_path,
        storage_persistence=storage_persistence,
        storage_backend=storage_backend,
        postgres_url=postgres_url,
        frankfurter_base_url=os.getenv(
            "FRANKFURTER_BASE_URL", "https://api.frankfurter.dev/v1"
        ),
        frankfurter_timeout_seconds=float(
            os.getenv("FRANKFURTER_TIMEOUT_SECONDS", "5")
        ),
        host=os.getenv("HOST", "127.0.0.1"),
        port=int(os.getenv("PORT", "8000")),
    )
    settings.validate_extractor_mode()
    return settings


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return _build_settings()


def reload_settings() -> Settings:
    """Re-load settings (used by tests when environment changes)."""
    get_settings.cache_clear()
    return get_settings()