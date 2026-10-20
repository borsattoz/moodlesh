from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from moodlesh.core.errors import AuthenticationError
from moodlesh.moodle.auth import SessionStore, end_server_session, is_moodle_page, verify_session

BASE = "https://moodle.example.org"


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        (f"{BASE}/my/", True),
        (f"{BASE}/course/view.php?id=3", True),
        (f"{BASE}/login/index.php", False),
        (f"{BASE}/auth/saml2/login.php?wants=x", False),  # redirect towards the SSO
        (f"{BASE}/auth/oidc/", False),
        (f"{BASE}/Shibboleth.sso/Login?target=x", False),  # Shibboleth SP on the same host
        ("https://idp.example.org/sso/login", False),  # external authentication server
        ("about:blank", False),
    ],
)
def test_is_moodle_page(url: str, expected: bool) -> None:
    assert is_moodle_page(url, BASE) is expected


def test_is_moodle_page_with_moodle_in_a_subdirectory() -> None:
    base = "https://example.org/moodle"
    assert is_moodle_page("https://example.org/moodle/my/", base)
    assert not is_moodle_page("https://example.org/moodle/auth/cas/", base)
    assert not is_moodle_page("https://example.org/other/", base)


def test_session_store_roundtrip_with_private_permissions(tmp_path: Path) -> None:
    store = SessionStore(tmp_path)
    cookies = httpx.Cookies()
    cookies.set("MoodleSession", "abc", domain="moodle.example.org", path="/")

    path = store.save(BASE, cookies)
    assert path.stat().st_mode & 0o777 == 0o600
    loaded = store.load(BASE)
    assert loaded is not None
    assert loaded.get("MoodleSession") == "abc"
    assert store.delete(BASE)
    assert store.load(BASE) is None


def _client(
    final_path: str, body: str = '<script>M.cfg = {"sesskey":"k","userId":5}</script>'
) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/my/" and final_path != "/my/":
            return httpx.Response(303, headers={"Location": f"{BASE}{final_path}"})
        if request.url.path == "/my/":
            return httpx.Response(200, text=body)
        return httpx.Response(200, text="<title>Login IdP</title>")

    return httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)


def test_verify_session_accepts_authenticated_session() -> None:
    verify_session(BASE, httpx.Cookies(), client=_client("/my/"))


@pytest.mark.parametrize("final_path", ["/login/index.php", "/Shibboleth.sso/Login?target=x"])
def test_verify_session_rejects_unauthenticated_and_says_where(final_path: str) -> None:
    with pytest.raises(AuthenticationError, match="Login IdP"):
        verify_session(BASE, httpx.Cookies(), client=_client(final_path))


def test_verify_session_rejects_guest_session() -> None:
    guest = '<script>M.cfg = {"sesskey":"k","userId":0}</script>'
    with pytest.raises(AuthenticationError, match="guest session"):
        verify_session(BASE, httpx.Cookies(), client=_client("/my/", guest))


class _FakeLogoutSite:
    """A Moodle that logs the session out when `logout.php` gets the right sesskey."""

    def __init__(self, *, blocked: bool = False) -> None:
        self.logged_in = True
        self.blocked = blocked
        self.logout_sesskeys: list[str | None] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        if self.blocked:
            return httpx.Response(403, text="<title>Security Check</title>")
        if request.url.path == "/login/logout.php":
            self.logout_sesskeys.append(request.url.params.get("sesskey"))
            self.logged_in = request.url.params.get("sesskey") != "k"
            return httpx.Response(303, headers={"Location": f"{BASE}/"})
        if request.url.path == "/my/" and not self.logged_in:
            return httpx.Response(303, headers={"Location": f"{BASE}/login/index.php"})
        return httpx.Response(200, text='<script>M.cfg = {"sesskey":"k","userId":5}</script>')

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self.handler), follow_redirects=True)


def test_end_server_session_logs_out_with_the_sesskey() -> None:
    site = _FakeLogoutSite()
    assert end_server_session(BASE, httpx.Cookies(), client=site.client()) is True
    assert site.logout_sesskeys == ["k"]


def test_end_server_session_is_best_effort_behind_bot_protection() -> None:
    site = _FakeLogoutSite(blocked=True)
    assert end_server_session(BASE, httpx.Cookies(), client=site.client()) is False


def test_session_store_tightens_existing_file(tmp_path: Path) -> None:
    store = SessionStore(tmp_path)
    path = store.path_for(BASE)
    path.write_text("[]")
    path.chmod(0o644)
    store.save_raw(BASE, [])
    assert path.stat().st_mode & 0o777 == 0o600
