"""Refuse requests from other sites.

The dashboard listens on loopback with no auth, so any page open in the
user's browser could otherwise POST to it (form posts need no CORS
preflight) or, after DNS rebinding, read it. Two checks:

- Host must be `localhost` or an IP literal, never a DNS name an attacker
  could point at this machine, which defeats DNS rebinding.
- A state-changing request from a browser must come from the dashboard
  itself: `Sec-Fetch-Site` same-origin or none, else an `Origin` matching
  the Host. Requests with neither header (curl, scripts) aren't from a
  browser page and pass.
"""

from __future__ import annotations

import ipaddress
from collections.abc import Mapping, Set
from urllib.parse import urlsplit

# Defense in depth for every response. Only the dashboard's own script files
# run (no inline script or eval, so injected markup can't run code), fetches
# stay on this origin, no other site may frame the dashboard (a framed page's
# clicks would be same-origin and pass the checks below), and cross-origin
# navigations carry no Referer. Inline styles stay allowed for htmx's
# indicator styles and the templates' style attributes.
CSP = "; ".join((
    "default-src 'self'",
    "script-src 'self'",
    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com",
    "font-src https://fonts.gstatic.com",
    "img-src 'self' data: http: https:",
    "connect-src 'self'",
    "object-src 'none'",
    "base-uri 'none'",
    "form-action 'self'",
    "frame-ancestors 'none'",
))
SECURITY_HEADERS = {
    "Content-Security-Policy": CSP,
    "X-Frame-Options": "DENY",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "same-origin",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cross-Origin-Resource-Policy": "same-origin",
}

HOST_NAMES = frozenset({"localhost"})
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
SAME_SITE_FETCH = frozenset({"same-origin", "none"})


def host_name(host: str) -> str:
    """The name part of a Host header, lowercased: `[::1]:9001` -> `::1`."""
    try:
        return (urlsplit(f"//{host}").hostname or "").lower()
    except ValueError:
        return ""


def host_allowed(host: str, names: Set[str] = HOST_NAMES) -> bool:
    """An IP literal or one of `names`. A rebound page's Host is the
    attacker's DNS name; an IP literal origin can only be this server."""
    name = host_name(host)
    if name in names:
        return True
    try:
        ipaddress.ip_address(name)
    except ValueError:
        return False
    return True


def request_problem(
    method: str,
    headers: Mapping[str, str],
    allowed_hosts: Set[str] = HOST_NAMES,
) -> str | None:
    """Why this request is refused, or None to let it through.

    `headers` must look names up case-insensitively (Starlette's do).
    """
    host = headers.get("host", "")
    if not host_allowed(host, allowed_hosts):
        return "unexpected Host header"
    if method.upper() in SAFE_METHODS:
        return None
    fetch_site = headers.get("sec-fetch-site")
    if fetch_site is not None:
        return None if fetch_site in SAME_SITE_FETCH else "cross-site request"
    origin = headers.get("origin")
    if origin is None:
        return None
    if urlsplit(origin).netloc.lower() != host.lower():
        return "cross-origin request"
    return None

