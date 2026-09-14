"""End-to-end evaluation reproducibility tests.

The full 10-case suite is expensive (~20s), so it is executed ONCE per
module via ``run`` (module-scoped), and every test below asserts a facet
of that single run.
"""

import json
import os
import tempfile
from pathlib import Path

import pytest


@pytest.fixture(scope="module", autouse=True)
def _neutralise_db_isolation():
    """Shadow conftest's per-function DB isolation:
    evaluation owns its own isolated database for the whole module."""


@pytest.fixture(scope="module")
def run():
    """Run the full evaluation suite exactly once, in an isolated DB."""
    from app.config import reload_settings
    from app.db.database import Database, use_database, reset_db

    tmp = tempfile.mkdtemp(prefix="imatch_eval_test_")
    use_database(Database(Path(tmp) / "eval_test.db"))

    old_mode = os.environ.get("EXTRACTOR_MODE")
    os.environ["EXTRACTOR_MODE"] = "deterministic_eval"
    reload_settings()
    try:
        from evaluation.run_evaluation import run_evaluation
        yield run_evaluation(mode="deterministic_eval", commit=False)
    finally:
        if old_mode is not None:
            os.environ["EXTRACTOR_MODE"] = old_mode
        else:
            os.environ.pop("EXTRACTOR_MODE", None)
        reload_settings()
        reset_db()


class TestEvaluationDeterministic:

    def test_accuracy_perfect(self, run):
        assert run["summary"]["accuracy"] == 1.0

    def test_no_false_approvals(self, run):
        assert run["summary"]["false_approvals"] == 0

    def test_no_false_exceptions(self, run):
        assert run["summary"]["false_exceptions"] == 0

    def test_no_system_failures(self, run):
        assert run["summary"]["system_failures"] == 0

    def test_all_ten_cases_executed(self, run):
        assert run["count"] == 10

    def test_extractor_labeled(self, run):
        assert run["extractor"] == "deterministic_eval"
        for c in run["cases"]:
            assert c["extractor"] == "deterministic_eval"

    def test_extraction_accuracy_perfect(self, run):
        assert run["summary"]["extraction_accuracy"] == 1.0

    def test_expected_statuses_match(self, run):
        expected = {1: "MATCH", 2: "EXCEPTION", 3: "EXCEPTION", 4: "EXCEPTION",
                    5: "EXCEPTION", 6: "EXCEPTION", 7: "EXCEPTION", 8: "MATCH",
                    9: "MATCH", 10: "INVALID DOCUMENT"}
        for c in run["cases"]:
            assert c["expected_status"] == expected[c["case_id"]]

    def test_results_serialisable(self, run):
        dump = json.dumps(run, default=str)
        loaded = json.loads(dump)
        assert loaded["count"] == 10
        assert "baseline" in loaded


class TestBaselineTable:
    """The synthetic baseline must correctly mirror exception cases."""

    def test_exceptions_marked_for_touch(self, run):
        b = run["baseline"]
        assert b["label"] == "Synthetic / Proxy Baseline"
        assert b["disclaimer"]
        for row in b["rows"]:
            case = next(c for c in run["cases"] if c["case_id"] == row["case_id"])
            is_exception = case["actual_status"] == "EXCEPTION"
            assert (row["system_touch"] == "yes") == is_exception

    def test_manual_seconds_account_for_exceptions(self, run):
        for row in run["baseline"]["rows"]:
            case = next(c for c in run["cases"] if c["case_id"] == row["case_id"])
            expected = 120 + (90 if case["actual_status"] == "EXCEPTION" else 0)
            assert row["manual_baseline_s"] == expected

    def test_totals_consistent(self, run):
        totals = run["baseline"]["totals"]
        assert totals["total_manual_seconds"] == sum(r["manual_baseline_s"]
                                                    for r in run["baseline"]["rows"])
        assert totals["system_human_intervention_percent"] == 60.0