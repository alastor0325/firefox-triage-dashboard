"""The app refuses DNS-rebound and cross-site state-changing requests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.conftest import write_draft
from triage_dashboard import app as app_module, request_guard
from triage_dashboard.app import app

ORIGIN = "http://127.0.0.1:9001"
client = TestClient(app, base_url=ORIGIN)


@pytest.fixture(autouse=True)
def _loopback_only(monkeypatch: pytest.MonkeyPatch) -> None:
    """Undo conftest's testserver allowance: these tests use real hosts."""
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


@pytest.mark.parametrize("path", ["/", "/healthz", "/static/style.css", "/static/app.js"])
def test_security_headers_sent(triage_dir: Path, path: str) -> None:
    response = client.get(path)
    assert response.status_code == 200
    for name, value in request_guard.SECURITY_HEADERS.items():
        assert response.headers[name] == value


def test_csp_forbids_inline_script_and_framing() -> None:
    directives = dict(d.split(" ", 1) for d in request_guard.CSP.split("; "))
    assert directives["script-src"] == "'self'"
    assert directives["connect-src"] == "'self'"
    assert directives["frame-ancestors"] == "'none'"
    assert directives["object-src"] == "'none'"
    assert directives["base-uri"] == "'none'"


def test_security_headers_on_refusal(triage_dir: Path) -> None:
    response = client.get("/", headers={"Host": "evil.example:9001"})
    assert response.status_code == 403
    assert response.headers["content-type"].startswith("text/plain")
    for name, value in request_guard.SECURITY_HEADERS.items():
        assert response.headers[name] == value


def test_event_stream_passes_through(triage_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """/events streams through the middleware with the headers intact."""

    async def one_keepalive(queue, is_disconnected):
        yield ": keepalive\n\n"

    monkeypatch.setattr(app_module, "sse_event_stream", one_keepalive)
    with client.stream("GET", "/events") as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        assert response.headers["content-security-policy"] == request_guard.CSP
        assert next(response.iter_text()).startswith(": keepalive")

