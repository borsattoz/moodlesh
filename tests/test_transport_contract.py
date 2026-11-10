"""The `Transport` contract, checked for every transport with the same situations.

Each situation (a result, an error returned by Moodle, an invalid session, a
page that is not from Moodle, a network failure) is turned into the wire
format of each transport, and every transport must react in the same way. See
the docstring of `moodlesh.moodle.transport.Transport`.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import pytest

from moodlesh.core.errors import AuthenticationError, BackendError
from moodlesh.moodle.auth import SessionStore
from moodlesh.moodle.browser import _Config, _PageSession
from moodlesh.moodle.transport import AjaxSessionTransport, NotMoodleResponseError, RestTransport, Transport

BASE = "https://moodle.example.org"
DASHBOARD = '<script>M.cfg = {"sesskey":"k","userId":5}</script>'
PAGE = "<html><head><title>Security Check</title></head><body>Checking your browser</body></html>"

# The situations, in the wire format of the AJAX endpoint and of REST.
AJAX = {
    "result": json.dumps([{"error": False, "data": {"x": 1}}]),
    "moodle error": json.dumps(
        [{"error": True, "exception": {"message": "No access", "errorcode": "nopermissions"}}]
    ),
    "invalid session": json.dumps(
        {"error": "Your session has timed out", "errorcode": "servicerequireslogin"}
    ),
    "not Moodle": PAGE,
}
REST = {
    "result": json.dumps({"x": 1}),
    "moodle error": json.dumps(
        {"exception": "required_capability_exception", "errorcode": "nopermissions", "message": "No access"}
    ),
    "invalid session": json.dumps(
        {"exception": "moodle_exception", "errorcode": "invalidtoken", "message": "x"}
    ),
    "not Moodle": PAGE,
}


@dataclass
class Setup:
    """A transport wired to one situation, and how many calls reached Moodle."""

    transport: Transport
    calls: Callable[[], int]


class _PageError(Exception):
    """Stands for Playwright's `Error` in the browser transport."""


def _ajax(situation: str) -> Setup:
    count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal count
        if situation == "network failure":
            raise httpx.ConnectError("connection refused", request=request)
        if request.url.path == "/my/":
            return httpx.Response(200, text=DASHBOARD)
        count += 1
        return httpx.Response(200, text=AJAX[situation])

    client = httpx.Client(transport=httpx.MockTransport(handler))
    return Setup(AjaxSessionTransport(BASE, httpx.Cookies(), client=client), lambda: count)


def _rest(situation: str) -> Setup:
    count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal count
        count += 1
        if situation == "network failure":
            raise httpx.ConnectError("connection refused", request=request)
        return httpx.Response(200, text=REST[situation])

    client = httpx.Client(transport=httpx.MockTransport(handler))
    return Setup(RestTransport(BASE, "tok", client=client), lambda: count)


class _Page:
    """A browser tab whose fetches all meet the same situation."""

    def __init__(self, situation: str) -> None:
        self.situation = situation
        self.url = BASE + "/my/"
        self.fetches = 0

    def is_closed(self) -> bool:
        return False

    async def goto(self, url: str, wait_until: str = "load") -> None:
        self.url = url

    async def wait_for_timeout(self, ms: float) -> None:
        pass

    async def evaluate(self, script: str, arg: Any = None) -> Any:
        if arg is None:  # the state of the Moodle page
            return {"sesskey": "k", "userId": 5}
        self.fetches += 1
        if self.situation == "network failure":
            raise _PageError("net::ERR_CONNECTION_REFUSED")
        return [200, AJAX[self.situation]]


def _browser(situation: str, tmp_path: Path) -> Setup:
    page = _Page(situation)
    config = _Config(BASE, SessionStore(tmp_path), lambda _: None, 5, 0, {}, page_error=_PageError)
    # `_PageSession` is what `BrowserTransport` runs on its browser thread's loop.
    return Setup(_SyncPageSession(_PageSession(page, config)), lambda: page.fetches)


class _SyncPageSession:
    """The synchronous face of `_PageSession`, as `BrowserTransport.call` gives it."""

    def __init__(self, session: _PageSession) -> None:
        self._session = session

    def call(self, function: str, args: Any) -> Any:
        return asyncio.run(self._session.call(function, args))


TRANSPORTS: dict[str, Callable[[str, Path], Setup]] = {
    "ajax": lambda situation, _: _ajax(situation),
    "rest": lambda situation, _: _rest(situation),
    "browser": _browser,
}


@pytest.fixture(params=sorted(TRANSPORTS))
def make(request: pytest.FixtureRequest, tmp_path: Path) -> Callable[[str], Setup]:
    build = TRANSPORTS[request.param]
    return lambda situation: build(situation, tmp_path)


def test_result_is_returned(make: Callable[[str], Setup]) -> None:
    setup = make("result")
    assert setup.transport.call("f", {}) == {"x": 1}


def test_moodle_error_is_a_backend_error_and_is_never_retried(make: Callable[[str], Setup]) -> None:
    setup = make("moodle error")
    with pytest.raises(BackendError) as caught:
        setup.transport.call("f", {})
    assert caught.value.errorcode == "nopermissions"
    assert not isinstance(caught.value, AuthenticationError)
    assert setup.calls() == 1  # the function may have written something: never call it twice


def test_invalid_session_or_token_is_an_authentication_error(make: Callable[[str], Setup]) -> None:
    with pytest.raises(AuthenticationError):
        make("invalid session").transport.call("f", {})


def test_response_not_from_moodle_is_reported_as_such(make: Callable[[str], Setup]) -> None:
    with pytest.raises(NotMoodleResponseError, match="Security Check"):
        make("not Moodle").transport.call("f", {})


def test_network_failure_is_a_backend_error(make: Callable[[str], Setup]) -> None:
    with pytest.raises(BackendError) as caught:
        make("network failure").transport.call("f", {})
    assert not isinstance(caught.value, AuthenticationError)
    assert isinstance(caught.value.__cause__, httpx.HTTPError | _PageError)
