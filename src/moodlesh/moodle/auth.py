"""Session persistence and browser-less login.

The session (its cookies) is saved by `SessionStore` in a file readable only by
the user: while it is valid it is as good as a password. The format is
Playwright's cookie format, so the same file serves both the browser transport
(`moodlesh.moodle.browser`) and the HTTP one.

Interactive login (SSO included) happens in the browser: see `BrowserTransport`.
This module keeps the username/password login for manual authentication.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx

from moodlesh.core.errors import AuthenticationError, BackendError

_LOGINTOKEN_RE = re.compile(r'name="logintoken"\s+value="([^"]*)"')

# Paths of Moodle (and of SSO service providers installed on the same host) that
# authentication redirects go through: being there does not mean being logged in.
_AUTH_PATH_PREFIXES = ("/login/", "/auth/", "/Shibboleth.sso/", "/shibboleth-ds/", "/simplesaml/", "/saml/")

# Hosts where a password may be sent over plain HTTP: the traffic never leaves
# the computer (local test sites).
_LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})

RawCookie = dict[str, Any]


def default_state_dir() -> Path:
    base = os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state"
    return Path(base) / "moodlesh"


@dataclass(frozen=True, slots=True)
class SessionStore:
    """Save and reload session cookies, one file per Moodle instance."""

    directory: Path = field(default_factory=default_state_dir)

    def path_for(self, base_url: str) -> Path:
        parts = urlsplit(base_url)
        slug = re.sub(r"[^A-Za-z0-9.-]+", "_", f"{parts.netloc}{parts.path}".rstrip("/"))
        return self.directory / f"session-{slug}.json"

    def load_raw(self, base_url: str) -> list[RawCookie] | None:
        """The saved cookies, in Playwright's format (name, value, domain, path, ...)."""
        path = self.path_for(base_url)
        if not path.exists():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        return [c for c in data if isinstance(c, dict) and "name" in c and "value" in c]

    def save_raw(self, base_url: str, cookies: list[RawCookie]) -> Path:
        path = self.path_for(base_url)
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        # Created with mode 600 right away, so it is never exposed, not even briefly.
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        os.fchmod(fd, 0o600)  # also tighten a file that already existed with looser permissions
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(cookies, fh)
        return path

    def load(self, base_url: str) -> httpx.Cookies | None:
        raw = self.load_raw(base_url)
        return None if raw is None else to_httpx_cookies(raw)

    def save(self, base_url: str, cookies: httpx.Cookies) -> Path:
        raw = [
            {"name": c.name, "value": c.value, "domain": c.domain, "path": c.path or "/"}
            for c in cookies.jar
            if c.value is not None
        ]
        return self.save_raw(base_url, raw)

    def delete(self, base_url: str) -> bool:
        path = self.path_for(base_url)
        if path.exists():
            path.unlink()
            return True
        return False


def to_httpx_cookies(raw: list[RawCookie]) -> httpx.Cookies:
    cookies = httpx.Cookies()
    for c in raw:
        cookies.set(c["name"], c["value"], domain=c.get("domain") or "", path=c.get("path") or "/")
    return cookies


def login_with_password(
    base_url: str,
    username: str,
    password: str,
    *,
    client: httpx.Client | None = None,
) -> httpx.Cookies:
    """Log in through the standard form and return the cookies of an authenticated session.

    The outcome is not guessed from where Moodle redirects: after posting the
    form, the session is checked with `verify_session`, the same check the HTTP
    transport relies on.

    Raises:
        AuthenticationError: the address is not HTTPS (except for local test
            sites), the site cannot be reached or is not answering as Moodle
            (for example a bot check), or the login did not produce an
            authenticated session. The message says which.
    """
    base_url = base_url.rstrip("/")
    require_secure_for_password(base_url)
    http = client or httpx.Client(follow_redirects=True, timeout=30)
    try:
        return _login(http, base_url, username, password)
    except httpx.HTTPError as exc:
        raise AuthenticationError(f"login failed: cannot reach {base_url}: {exc}") from exc
    finally:
        if client is None:
            http.close()


def _login(http: httpx.Client, base_url: str, username: str, password: str) -> httpx.Cookies:
    page = http.get(f"{base_url}/login/index.php")
    if _looks_blocked(page):
        raise AuthenticationError(_BLOCKED)
    match = _LOGINTOKEN_RE.search(page.text)
    if match is None:
        raise AuthenticationError(
            f"login failed: {page.url} (HTTP {page.status_code}) does not show Moodle's login form"
        )

    form = {"username": username, "password": password, "logintoken": match.group(1)}
    response = http.post(f"{base_url}/login/index.php", data=form)
    path = urlsplit(str(response.url)).path
    if _looks_blocked(response):
        raise AuthenticationError(_BLOCKED)
    if "/admin/tool/mfa/" in path:
        raise AuthenticationError(
            "login failed: this account uses multi-factor authentication, which moodlesh cannot "
            "complete without a browser. Use `moodlesh login` (browser) instead."
        )
    if path.endswith("/login/change_password.php"):
        raise AuthenticationError(
            "login failed: Moodle requires a password change for this account. "
            "Change it in the browser, then try again."
        )
    if "/login/" in path:
        raise AuthenticationError(
            "login failed: wrong credentials, or the account does not use manual authentication"
        )

    cookies = httpx.Cookies(http.cookies)
    try:
        verify_session(base_url, cookies, client=http)
    except AuthenticationError as exc:
        raise AuthenticationError(f"login failed: the password was accepted, but {exc}") from exc
    return cookies


_BLOCKED = (
    "login failed: the site is blocking automated access (for example a bot check). "
    "Use `moodlesh login` (browser) instead."
)


def _looks_blocked(response: httpx.Response) -> bool:
    """True for the answer of a bot check (such as Cloudflare's) rather than of Moodle."""
    return "cf-mitigated" in response.headers or (
        response.status_code in {403, 429} and "logintoken" not in response.text
    )


def require_secure_for_password(base_url: str) -> None:
    """Refuse to send a password to `base_url` unless the connection is encrypted.

    Plain HTTP is accepted only for local test sites (`localhost`, `127.0.0.1`,
    `::1`), whose traffic never leaves the computer.

    Raises:
        AuthenticationError: the password would travel in clear text.
    """
    parts = urlsplit(base_url)
    if parts.scheme == "https":
        return
    host = parts.hostname or ""
    if parts.scheme == "http" and (host in _LOCAL_HOSTS or host.endswith(".localhost")):
        return
    raise AuthenticationError(
        f"refusing to send a password to {base_url}: the connection is not encrypted. "
        "Use the https:// address of the site (plain http:// is accepted only for local test sites)."
    )


def verify_session(base_url: str, cookies: httpx.Cookies, *, client: httpx.Client | None = None) -> None:
    """Check, with the same requests the HTTP transport makes, that the session is authenticated.

    Raises:
        AuthenticationError: if Moodle does not recognise the session (says which page was reached).
    """
    from moodlesh.moodle.transport import AjaxSessionTransport  # noqa: PLC0415 - avoids a circular import

    _ = AjaxSessionTransport(base_url, cookies, client=client).sesskey


def is_moodle_page(url: str, base_url: str) -> bool:
    """True if `url` is a "regular" Moodle page, outside the login and SSO paths."""
    base = urlsplit(base_url.rstrip("/"))
    parts = urlsplit(url)
    if parts.scheme not in {"http", "https"} or parts.netloc != base.netloc:
        return False
    if not parts.path.startswith(base.path):
        return False
    relative = parts.path[len(base.path) :]
    return not relative.startswith(_AUTH_PATH_PREFIXES)


def end_server_session(base_url: str, cookies: httpx.Cookies, *, client: httpx.Client | None = None) -> bool:
    """Best effort: log the session out on the Moodle server too.

    Returns True if Moodle confirmed the logout. Sites behind bot protection
    refuse plain HTTP clients: then this returns False and the session simply
    expires on the server as usual.
    """
    from moodlesh.moodle.transport import AjaxSessionTransport  # noqa: PLC0415 - avoids a circular import

    http = client or httpx.Client(follow_redirects=True, timeout=15)
    base_url = base_url.rstrip("/")
    try:
        sesskey = AjaxSessionTransport(base_url, cookies, client=http).sesskey
        http.get(f"{base_url}/login/logout.php", params={"sesskey": sesskey})
        after = http.get(f"{base_url}/my/")
    except (BackendError, httpx.HTTPError):
        return False
    finally:
        if client is None:
            http.close()
    return not is_moodle_page(str(after.url), base_url)
