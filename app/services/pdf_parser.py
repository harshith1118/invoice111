"""PDF text extraction using pypdf.

Responsibilities:
- validate the file is a PDF (extension + magic bytes)
- enforce a size limit
- extract text
- detect empty / image-only / corrupt PDFs and return a clear error
  (never silently return bogus data)

Only text-based PDFs are supported in the MVP. Scanned/image-only PDFs are
reported as such and must not proceed to extraction.
"""

from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO

import pypdf
from pypdf.errors import PdfReadError

from app.config import get_settings
from app.services.logger import log_stage


class PdfProcessingError(Exception):
    """Raised when a PDF cannot be used for processing.

    ``code`` maps to a user-friendly message (frontend) while ``detail`` is
    logged server-side.
    """

    CODES = ("invalid_type", "too_large", "corrupt", "empty", "no_text")

    def __init__(self, code: str, detail: str):
        if code not in self.CODES:
            raise ValueError(f"Unknown PDF error code: {code}")
        self.code = code
        self.detail = detail
        super().__init__(detail)

    @property
    def user_message(self) -> str:
        return PDF_ERROR_MESSAGES[self.code]


PDF_ERROR_MESSAGES = {
    "invalid_type": "The file is not a PDF. Please upload a valid PDF file.",
    "too_large": "The PDF is too large. Please upload a smaller file.",
    "corrupt": "The PDF could not be read. It may be corrupted.",
    "empty": "The PDF has no content and cannot be read.",
    "no_text": (
        "We couldn't find readable text in this PDF. "
        "It may be a scanned document; scanned PDFs are not supported yet."
    ),
}


@dataclass
class PdfParseResult:
    text: str
    page_count: int
    filename: str


def _looks_like_pdf(filename: str, content: bytes) -> bool:
    # Magic bytes check is the authority; extension is a friendly hint.
    head = content[:5]
    if head == b"%PDF-":
        return True
    if filename is not None and filename.lower().endswith(".pdf"):
        # Allow extension-only detection as a fallback but require the parser
        # to succeed before trusting it.
        return True
    return False


def extract_pdf_text(
    filename: str,
    content: bytes,
    analysis_id: int | None = None,
) -> PdfParseResult:
    """Extract text from an uploaded PDF. Raises PdfProcessingError on failure."""
    settings = get_settings()

    if content.startswith(b"%PDF-") is False and not filename.lower().endswith(".pdf"):
        raise PdfProcessingError(
            "invalid_type", f"Uploaded file '{filename}' is not a PDF."
        )

    max_bytes = settings.pdf_max_size_mb * 1024 * 1024
    if len(content) > max_bytes:
        raise PdfProcessingError(
            "too_large",
            f"PDF size {len(content)} bytes exceeds limit {max_bytes} bytes.",
        )

    try:
        reader = pypdf.PdfReader(BytesIO(content))
    except PdfReadError as exc:
        raise PdfProcessingError("corrupt", f"pypdf could not parse PDF: {exc!r}")
    except Exception as exc:  # pragma: no cover - pypdf raises varied errors
        raise PdfProcessingError("corrupt", f"Unexpected PDF read error: {exc!r}")

    if reader.is_encrypted:
        raise PdfProcessingError(
            "corrupt", "Encrypted PDFs are not supported (no decryption in MVP)."
        )

    pages = len(reader.pages) if getattr(reader, "pages", None) is not None else 0
    if pages == 0:
        raise PdfProcessingError("empty", "PDF contains no pages.")

    parts: list[str] = []
    for page in reader.pages:
        try:
            parts.append(page.extract_text() or "")
        except Exception as exc:  # pragma: no cover - defensive
            raise PdfProcessingError("corrupt", f"Text extraction failed: {exc!r}")

    text = "\n".join(parts).strip()
    # Two failure modes that both mean "no readable text":
    #  - actually empty content
    #  - image-only / scanned PDF (vector or raster graphics, no text layer)
    if not text:
        raise PdfProcessingError(
            "no_text",
            "No extractable text found (empty or image-only/scanned PDF).",
        )

    log_stage(analysis_id, "PDF_EXTRACTION", "ok", f"Extracted {len(text)} chars", None)
    return PdfParseResult(text=text, page_count=pages, filename=filename)