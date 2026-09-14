"""Shared pytest fixtures and setup for InvoiceMatch AI OS test suite."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
EVAL_FIXTURES = ROOT / "evaluation" / "fixtures"


@pytest.fixture(autouse=True)
def _isolate_database(tmp_path):
    """Each test runs in an isolated temporary SQLite database."""
    db_file = tmp_path / "test.db"
    from app.config import reload_settings
    # Force SQLite: a configured DATABASE_URL must not leak into tests.
    os.environ.pop("DATABASE_URL", None)
    os.environ["DB_PATH"] = str(db_file)
    os.environ["EXTRACTOR_MODE"] = "deterministic_eval"
    reload_settings()
    from app.db.database import Database, use_database
    db = Database(str(db_file))
    db.init_db()
    use_database(db)
    yield
    from app.db.database import reset_db
    reset_db()


@pytest.fixture()
def client(_isolate_database):
    """FastAPI TestClient wired to the isolated database."""
    from fastapi.testclient import TestClient
    from app.main import app
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture()
def fixture_path(tmp_path):
    """Return a helper that writes bytes to a temp .pdf and returns Path."""
    def _write(name: str, content: bytes) -> Path:
        p = tmp_path / name
        p.write_bytes(content)
        return p
    return _write


def fixture_bytes(case_id: int, kind: str) -> bytes:
    """Read the generated fixture PDF for a given case."""
    p = EVAL_FIXTURES / f"{kind}_{case_id:02d}.pdf"
    return p.read_bytes() if p.exists() else b""


def make_doc_pdf(lines: list[str], title: str = "Test Document") -> bytes:
    """Render text lines to an in-memory PDF in the fixture PDF format."""
    from io import BytesIO

    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas

    buf = BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    text = c.beginText(72, 720)
    text.textLine(title)
    text.textLine("")
    for line in lines:
        text.textLine(line)
    c.drawText(text)
    c.showPage()
    c.save()
    return buf.getvalue()