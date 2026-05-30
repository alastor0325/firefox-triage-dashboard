"""Integration tests for splitting bug_context.see_also into a regression
tag block (entries with `regress` in the label) vs the rest (Similar)."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from tests.conftest import write_draft
from triage_dashboard.app import app


client = TestClient(app)


# ─── regression tag block ───────────────────────────────────────────

def test_regression_block_separates_regressor_from_similar(triage_dir: Path) -> None:
    write_draft(
        triage_dir, 1,
        bug_context={
            "see_also": [
                {"bug_id": 1981503, "label": "regressor"},
                {"bug_id": 99, "label": "same root cause"},
            ],
        },
    )
    body = client.get("/").text
    # The regression block is present and references the regressor bug.
    assert "regression-tag-block" in body
    assert "1981503" in body
    # The block uses a tag called "regression" (single source of truth
    # for the label — the section header IS the label, individual
    # entries don't repeat "regressor").
    assert "regression" in body.lower()
    # The non-regressor bug must NOT appear inside the regression block.
    block_start = body.index("regression-tag-block")
    block_end = body.index("</div>", block_start)
    assert "99" not in body[block_start:block_end]


def test_regression_block_absent_when_no_regressors(triage_dir: Path) -> None:
    write_draft(
        triage_dir, 1,
        bug_context={
            "see_also": [{"bug_id": 99, "label": "same root cause"}],
        },
    )
    body = client.get("/").text
    assert "regression-tag-block" not in body


def test_regression_block_case_insensitive_label_match(triage_dir: Path) -> None:
    """`Regressed by`, `REGRESSION`, etc. all count as regressors."""
    write_draft(
        triage_dir, 1,
        bug_context={
            "see_also": [{"bug_id": 1981503, "label": "Regressed by"}],
        },
    )
    body = client.get("/").text
    assert "regression-tag-block" in body
    assert "1981503" in body


def test_regression_block_renders_each_regressor(triage_dir: Path) -> None:
    write_draft(
        triage_dir, 1,
        bug_context={
            "see_also": [
                {"bug_id": 111, "label": "regressor"},
                {"bug_id": 222, "label": "regressed by"},
            ],
        },
    )
    body = client.get("/").text
    assert "regression-tag-block" in body
    assert "111" in body
    assert "222" in body
