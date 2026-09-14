"""Evaluation suite for InvoiceMatch AI OS.

Contains the 10 reproducible test cases plus the runner that executes the
real analysis pipeline against the synthetic PDF fixtures.
"""

from __future__ import annotations

import json
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
FIXTURES_DIR = BASE_DIR / "fixtures"


def load_test_cases() -> list[dict]:
    with open(BASE_DIR / "test_cases.json", encoding="utf-8") as fh:
        return json.load(fh)


def load_expected_results() -> list[dict]:
    with open(BASE_DIR / "expected_results.json", encoding="utf-8") as fh:
        return json.load(fh)


def load_cases() -> list[dict]:
    """Combine test-case metadata and expected results for the UI."""
    cases = load_test_cases()
    expected = {e["case_id"]: e for e in load_expected_results()}
    merged = []
    for case in cases:
        e = expected.get(case["case_id"], {})
        item = {**case, **{k: v for k, v in e.items() if k != "case_id"}}
        item["notes"] = item.pop("note", None) or item.get("notes")
        item.pop("note", None)
        merged.append(item)
    return merged


def fixture_bytes(case_id: int, kind: str) -> bytes:
    """Read a generated fixture as bytes.

    kind: "po" | "inv"
    """
    path = FIXTURES_DIR / f"{kind}_{case_id:02d}.pdf"
    return path.read_bytes()


def fixture_filename(case_id: int, kind: str) -> str:
    return f"{kind}_{case_id:02d}.pdf"