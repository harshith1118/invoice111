"""FastAPI application factory."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.api.routes import router as api_router
from app.config import BASE_DIR, get_settings
from app.db.database import get_db

FRONTEND_DIR = BASE_DIR / "frontend"


def create_app() -> FastAPI:
    settings = get_settings()
    application = FastAPI(
        title="InvoiceMatch AI OS",
        version="0.1.0",
        docs_url="/docs",
        redoc_url=None,
    )

    application.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    application.include_router(api_router)
    application.add_api_route("/health", methods=["GET"], endpoint=lambda: {"status": "ok"}, tags=["internal"])

    # Initialise DB on startup (safe to call repeatedly)
    @application.on_event("startup")
    def startup_db() -> None:
        get_db().init_db()

    # Mount static frontend *after* API routes so /api/* matches first.
    application.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="static")

    return application


app = create_app()