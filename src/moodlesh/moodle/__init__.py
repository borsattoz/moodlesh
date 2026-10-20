"""Integration with a real Moodle instance."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from moodlesh.core.errors import AuthenticationError
from moodlesh.moodle.auth import SessionStore, end_server_session, login_with_password, verify_session
from moodlesh.moodle.browser import BrowserTransport, Notify
from moodlesh.moodle.client import MoodleBackend
from moodlesh.moodle.transport import AjaxSessionTransport, RestTransport, Transport

__all__ = [
    "AjaxSessionTransport",
    "BrowserTransport",
    "MoodleBackend",
    "RestTransport",
    "SessionStore",
    "Transport",
    "connect_with_browser",
    "connect_with_session",
    "connect_with_token",
    "end_server_session",
    "login_with_password",
    "verify_session",
]


def connect_with_browser(
    base_url: str,
    *,
    store: SessionStore | None = None,
    notify: Notify | None = None,
    launch_options: Mapping[str, Any] | None = None,
    editable_only: bool = True,
) -> tuple[MoodleBackend, BrowserTransport]:
    """Backend that works from a Chromium window (sites with anti-bot checks, SSO).

    Also returns the transport, which the caller must close.
    """
    transport = BrowserTransport(base_url, store=store, notify=notify, launch_options=launch_options)
    return MoodleBackend(transport, editable_only=editable_only), transport


def connect_with_session(
    base_url: str, store: SessionStore | None = None, *, editable_only: bool = True
) -> MoodleBackend:
    """HTTP backend using the saved session (sites without anti-bot checks)."""
    store = store or SessionStore()
    cookies = store.load(base_url)
    if cookies is None:
        raise AuthenticationError(f"no saved session for {base_url}: run `moodlesh login` first")
    return MoodleBackend(AjaxSessionTransport(base_url, cookies), editable_only=editable_only)


def connect_with_token(base_url: str, token: str, *, editable_only: bool = True) -> MoodleBackend:
    """REST backend using a web service token."""
    return MoodleBackend(RestTransport(base_url, token), editable_only=editable_only)
