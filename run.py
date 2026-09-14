#!/usr/bin/env python3
"""Start InvoiceMatch AI OS.

Usage:
    python run.py
"""

from __future__ import annotations

import uvicorn

from app.config import get_settings


def main() -> None:
    settings = get_settings()
    print(f"Starting InvoiceMatch AI OS on http://{settings.host}:{settings.port}")
    print(f"  extractor mode : {settings.extractor_mode}")
    print(f"  fx provider    : frankfurter ({settings.frankfurter_base_url})")
    if settings.storage_backend == "postgres":
        from urllib.parse import urlsplit

        parts = urlsplit(settings.postgres_url or "")
        print(f"  database       : postgres://{parts.hostname or ''}/{(parts.path or '').lstrip('/')}")
    else:
        print(f"  database       : sqlite ({settings.db_path_abs})")
    print(f"  press Ctrl+C to stop\n")
    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        reload=False,
        log_level="info",
    )


if __name__ == "__main__":
    main()