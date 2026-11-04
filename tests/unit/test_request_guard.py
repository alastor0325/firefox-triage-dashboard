"""Unit tests for request_guard.request_problem."""

from __future__ import annotations

import pytest

from triage_dashboard.request_guard import host_allowed, host_name, request_problem


class Headers(dict):
    """Case-insensitive lookups, like Starlette's Headers."""

    def __init__(self, **kw: str) -> None:
        super().__init__({k.replace("_", "-").lower(): v for k, v in kw.items()})

    def get(self, key, default=None):
        return super().get(key.lower(), default)


@pytest.mark.parametrize(
    "host,name",
    [
        ("127.0.0.1:9001", "127.0.0.1"),
        ("LOCALHOST:9001", "localhost"),
        ("[::1]:9001", "::1"),
        ("evil.example:9001", "evil.example"),
        ("", ""),
        ("[bad", ""),
    ],
)
def test_host_name(host: str, name: str) -> None:
    assert host_name(host) == name


@pytest.mark.parametrize(
    "host", ["127.0.0.1:9001", "localhost:9001", "[::1]:9001", "localhost", "0.0.0.0:9001", "192.168.1.5:9001"]
)
def test_ip_literal_or_localhost_passes(host: str) -> None:
    assert host_allowed(host)
    assert request_problem("GET", Headers(host=host)) is None


@pytest.mark.parametrize("method", ["GET", "POST"])
@pytest.mark.parametrize("host", ["evil.example:9001", "127.0.0.1.evil.example", "localhost.evil.example", "", "[bad"])
def test_other_host_refused(method: str, host: str) -> None:
    """DNS rebinding: the page's own origin, so Host names the attacker."""
    assert request_problem(method, Headers(host=host)) == "unexpected Host header"


def test_allowed_hosts_override() -> None:
    assert request_problem("GET", Headers(host="testserver"), {"testserver"}) is None


@pytest.mark.parametrize("site", ["same-origin", "none"])
def test_post_same_site_fetch_passes(site: str) -> None:
    h = Headers(host="127.0.0.1:9001", sec_fetch_site=site, origin="http://127.0.0.1:9001")
    assert request_problem("POST", h) is None


@pytest.mark.parametrize("site", ["cross-site", "same-site"])
def test_post_cross_site_fetch_refused(site: str) -> None:
    """same-site covers another loopback port, e.g. some other local server."""
    h = Headers(host="127.0.0.1:9001", sec_fetch_site=site)
    assert request_problem("POST", h) == "cross-site request"


def test_sec_fetch_site_wins_over_matching_origin() -> None:
    h = Headers(host="127.0.0.1:9001", sec_fetch_site="cross-site", origin="http://127.0.0.1:9001")
    assert request_problem("POST", h) == "cross-site request"


def test_safe_method_ignores_fetch_metadata() -> None:
    h = Headers(host="127.0.0.1:9001", sec_fetch_site="cross-site", origin="https://evil.example")
    assert request_problem("GET", h) is None


def test_post_matching_origin_passes() -> None:
    h = Headers(host="127.0.0.1:9001", origin="http://127.0.0.1:9001")
    assert request_problem("post", h) is None


@pytest.mark.parametrize(
    "origin", ["https://evil.example", "http://127.0.0.1:9002", "null", "http://localhost:9001"]
)
def test_post_other_origin_refused(origin: str) -> None:
    h = Headers(host="127.0.0.1:9001", origin=origin)
    assert request_problem("POST", h) == "cross-origin request"


def test_post_without_browser_headers_passes() -> None:
    """curl and scripts send neither header."""
    assert request_problem("POST", Headers(host="127.0.0.1:9001")) is None
