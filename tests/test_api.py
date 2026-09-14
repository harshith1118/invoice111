"""Tests for the HTTP API endpoints."""

from pathlib import Path

import pytest
from tests.conftest import fixture_bytes, EVAL_FIXTURES


def _upload_files(client, po_id=1, inv_id=1):
    po_bytes = fixture_bytes(po_id, "po")
    inv_bytes = fixture_bytes(inv_id, "inv")
    return client.post(
        "/api/analyze",
        files={"po": ("po.pdf", po_bytes, "application/pdf"),
               "invoice": ("inv.pdf", inv_bytes, "application/pdf")},
    )


class TestHealth:
    def test_health_ok(self, client):
        r = client.get("/api/health")
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "ok"
        assert "extractor" in body
        assert body["fx_provider"] == "frankfurter"


class TestAnalyze:
    def test_match_returns_200(self, client):
        r = _upload_files(client, 1, 1)
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "MATCH"
        assert body["extractor"] == "deterministic_eval"
        assert "comparison_id" in body

    def test_exception_returns_200(self, client):
        r = _upload_files(client, 2, 2)
        assert r.status_code == 200
        assert r.json()["status"] == "EXCEPTION"

    def test_invalid_scanned_returns_200(self, client):
        r = _upload_files(client, 10, 10)
        assert r.status_code == 200
        assert r.json()["status"] == "INVALID DOCUMENT"

    def test_missing_files_returns_422(self, client):
        r = client.post("/api/analyze")
        assert r.status_code == 422

    def test_empty_file_returns_400(self, client):
        r = client.post(
            "/api/analyze",
            files={"po": ("empty.pdf", b"", "application/pdf"),
                   "invoice": ("inv.pdf", b"%PDF-valid", "application/pdf")},
        )
        assert r.status_code == 400


class TestComparisons:
    def test_list_empty(self, client):
        r = client.get("/api/comparisons")
        assert r.status_code == 200
        assert r.json()["count"] == 0

    def test_list_after_analyze(self, client):
        _upload_files(client, 1, 1)
        r = client.get("/api/comparisons")
        assert r.json()["count"] == 1

    def test_list_filter_match(self, client):
        _upload_files(client, 1, 1)
        r = client.get("/api/comparisons?result=MATCH")
        assert r.json()["count"] == 1

    def test_list_filter_exception(self, client):
        _upload_files(client, 2, 2)
        r = client.get("/api/comparisons?result=EXCEPTION")
        assert r.json()["count"] == 1

    def test_detail_not_found(self, client):
        r = client.get("/api/comparisons/9999")
        assert r.status_code == 404

    def test_detail_retrieved(self, client):
        res = _upload_files(client, 1, 1).json()
        cid = res["comparison_id"]
        r = client.get(f"/api/comparisons/{cid}")
        assert r.status_code == 200
        assert r.json()["result"] == "MATCH"

    def test_detail_extractor_labeled(self, client):
        _upload_files(client, 1, 1)
        cid = client.get("/api/comparisons").json()["comparisons"][0]["comparison_id"]
        detail = client.get(f"/api/comparisons/{cid}").json()
        assert detail["extractor"] == "deterministic_eval"

    def test_detail_has_document_filenames(self, client):
        _upload_files(client, 1, 1)
        cid = client.get("/api/comparisons").json()["comparisons"][0]["comparison_id"]
        detail = client.get(f"/api/comparisons/{cid}").json()
        assert detail["documents"]["po_filename"] == "po.pdf"
        assert detail["documents"]["invoice_filename"] == "inv.pdf"

    def test_detail_logs_include_start(self, client):
        _upload_files(client, 1, 1)
        cid = client.get("/api/comparisons").json()["comparisons"][0]["comparison_id"]
        detail = client.get(f"/api/comparisons/{cid}").json()
        stages = [entry["stage"] for entry in detail["logs"]]
        assert "START" in stages
        assert "COMPLETE" in stages


class TestReview:
    def test_review_pending_exception(self, client):
        res = _upload_files(client, 2, 2).json()
        cid = res["comparison_id"]
        assert res["status"] == "EXCEPTION"
        r = client.post(f"/api/comparisons/{cid}/review",
                        json={"decision": "approve", "note": "ok"})
        assert r.status_code == 200
        assert r.json()["status"] == "saved"

    def test_review_reject(self, client):
        res = _upload_files(client, 2, 2).json()
        cid = res["comparison_id"]
        r = client.post(f"/api/comparisons/{cid}/review",
                        json={"decision": "reject", "note": "wrong"})
        assert r.status_code == 200
        assert r.json()["status"] == "saved"

    def test_review_cannot_double_review(self, client):
        res = _upload_files(client, 2, 2).json()
        cid = res["comparison_id"]
        client.post(f"/api/comparisons/{cid}/review", json={"decision": "approve"})
        r2 = client.post(f"/api/comparisons/{cid}/review", json={"decision": "reject"})
        assert r2.status_code == 409

    def test_review_invalid_on_match(self, client):
        res = _upload_files(client, 1, 1).json()
        cid = res["comparison_id"]
        r = client.post(f"/api/comparisons/{cid}/review",
                        json={"decision": "approve"})
        assert r.status_code == 400

    def test_review_bad_decision(self, client):
        res = _upload_files(client, 2, 2).json()
        cid = res["comparison_id"]
        r = client.post(f"/api/comparisons/{cid}/review",
                        json={"decision": "invalid"})
        assert r.status_code == 400


class TestDashboardStats:
    def test_empty_stats(self, client):
        r = client.get("/api/dashboard/stats")
        assert r.status_code == 200
        body = r.json()["stats"]
        assert body["total"] == 0
        assert body["matches"] == 0

    def test_stats_after_comparisons(self, client):
        _upload_files(client, 1, 1)
        _upload_files(client, 2, 2)
        body = client.get("/api/dashboard/stats").json()["stats"]
        assert body["total"] == 2
        assert body["matches"] == 1
        assert body["exceptions"] == 1


class TestEvaluationEndpoint:
    def test_cases_list(self, client):
        r = client.get("/api/evaluation/cases")
        assert r.status_code == 200
        cases = r.json()
        assert len(cases) == 10

    def test_run_deterministic(self, client):
        r = client.post("/api/evaluation/run",
                        json={"mode": "deterministic_eval"})
        assert r.status_code == 200
        body = r.json()
        assert body["summary"]["accuracy"] == 1.0
        assert body["count"] == 10