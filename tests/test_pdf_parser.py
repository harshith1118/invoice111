"""Tests for app.services.pdf_parser"""

from io import BytesIO

import pytest
from reportlab.pdfgen import canvas as rc

from app.services.pdf_parser import PdfProcessingError, PdfParseResult, extract_pdf_text


def _minimal_pdf():
    c = rc.Canvas(BytesIO(), pagesize=(400, 400))
    c.drawString(72, 360, "Test PDF content")
    c.save()
    return c._code if hasattr(c, "_code") else b""


def _text_pdf(text: str = "Hello world"):
    buf = BytesIO()
    c = rc.Canvas(buf, pagesize=(400, 400))
    c.drawString(72, 360, text)
    c.save()
    return buf.getvalue()


def _empty_pdf():
    """PDF with zero pages — triggers 'empty' code."""
    buf = BytesIO()
    c = rc.Canvas(buf, pagesize=(400, 400))
    c.save()
    return buf.getvalue()


def _no_text_pdf():
    """PDF with one page but no extractable text — triggers 'no_text' code."""
    buf = BytesIO()
    c = rc.Canvas(buf, pagesize=(400, 400))
    c.showPage()  # blank page with text layer
    c.save()
    return buf.getvalue()


def _corrupt_pdf():
    return b"%PDF-1.4 garbage\x00\x00\x00E"


class TestExtractPdfText:

    def test_valid_text_pdf(self):
        content = _text_pdf("Invoice 1001 Acme Supplies $500.00")
        r = extract_pdf_text("invoice.pdf", content)
        assert isinstance(r, PdfParseResult)
        assert "Invoice" in r.text
        assert r.page_count >= 1

    def test_valid_non_pdf_raises(self):
        with pytest.raises(PdfProcessingError, match="not a PDF"):
            extract_pdf_text("file.txt", b"hello")

    def test_empty_pdf_raises(self):
        with pytest.raises(PdfProcessingError) as exc_info:
            extract_pdf_text("scanned.pdf", _empty_pdf())
        assert exc_info.value.code == "empty"

    def test_no_text_layer_pdf_raises(self):
        """Page exists but no text → no_text code."""
        with pytest.raises(PdfProcessingError) as exc_info:
            extract_pdf_text("image_only.pdf", _no_text_pdf())
        assert exc_info.value.code == "no_text"

    def test_corrupt_pdf_raises(self):
        with pytest.raises(PdfProcessingError) as exc_info:
            extract_pdf_text("bad.pdf", _corrupt_pdf())
        assert exc_info.value.code == "corrupt"

    def test_encrypted_pdf_raises(self):
        """Synthetic corrupt-ish bytes still map to a PdfReadError -> corrupt."""
        with pytest.raises(PdfProcessingError) as exc_info:
            extract_pdf_text("enc.pdf", b"\x00" * 20)
        assert exc_info.value.code == "corrupt"

    def test_filesize_limit_respected(self, monkeypatch):
        from app.services import pdf_parser
        monkeypatch.setattr("app.services.pdf_parser.get_settings", lambda: type(
            "S", (), {"pdf_max_size_mb": 0, "pdf_extract_timeout": 30}
        )())
        with pytest.raises(PdfProcessingError) as exc_info:
            extract_pdf_text("huge.pdf", _text_pdf("x" * 500))
        assert exc_info.value.code == "too_large"

    def test_magic_bytes_checked(self):
        """PDF identified by %PDF- magic bytes even if filename is wrong."""
        content = _text_pdf("OK")
        r = extract_pdf_text("file.dat", content)
        assert "OK" in r.text

    def test_filename_lower_extension_valid(self):
        content = _text_pdf("OK")
        r = extract_pdf_text("invoice.PDF", content)
        assert r.filename == "invoice.PDF"