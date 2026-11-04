"""The app refuses DNS-rebound and cross-site state-changing requests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.conftest import write_draft
from triage_dashboard import app as app_module
from triage_dashboard.app import app

ORIGIN = "http://127.0.0.1:9001"
client = TestClient(app, base_url=ORIGIN)


@pytest.fixture(autouse=True)
def _loopback_only(monkeypatch: pytest.MonkeyPatch) -> None:
    """Undo conftest's testserver allowance: these tests use real hosts."""
    from triage_dashboard import request_guard

    monkeypatch.setattr(app_module, "ALLOWED_HOSTS", request_guard.HOST_NAMES)


def _refine(headers: dict[str, str]):
    return client.post("/draft/2039425/refine", data={"feedback": "x"}, headers=headers)


def _queued(triage_dir: Path) -> list[dict]:
    path = triage_dir / "claude-queue.jsonl"
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line]


def test_loopback_get_ok(triage_dir: Path) -> None:
    assert client.get("/healthz").status_code == 200


def test_rebound_host_refused(triage_dir: Path) -> None:
    response = client.get("/", headers={"Host": "evil.example:9001"})
    assert response.status_code == 403
    assert "Host" in response.text


def test_static_also_checked(triage_dir: Path) -> None:
    response = client.get("/static/does-not-matter.css", headers={"Host": "evil.example:9001"})
    assert response.status_code == 403


def test_same_origin_post_ok(triage_dir: Path) -> None:
    write_draft(triage_dir, 2039425)
    response = _refine({"Origin": ORIGIN, "Sec-Fetch-Site": "same-origin"})
    assert response.status_code == 200
    assert len(_queued(triage_dir)) == 1


@pytest.mark.parametrize(
    "headers",
    [
        {"Origin": "https://evil.example", "Sec-Fetch-Site": "cross-site"},
        {"Origin": "http://127.0.0.1:8000", "Sec-Fetch-Site": "same-site"},
        {"Origin": "https://evil.example"},
        {"Origin": "null"},
    ],
)
def test_cross_site_post_refused(triage_dir: Path, headers: dict[str, str]) -> None:
    write_draft(triage_dir, 2039425)
    response = _refine(headers)
    assert response.status_code == 403
    assert _queued(triage_dir) == []


def test_cross_site_apply_refused(triage_dir: Path) -> None:
    write_draft(triage_dir, 2039425)
    response = client.post(
        "/draft/2039425/apply", headers={"Origin": "https://evil.example", "Sec-Fetch-Site": "cross-site"}
    )
    assert response.status_code == 403
    assert _queued(triage_dir) == []
    assert (triage_dir / "pending" / "bug-2039425.json").is_file()
