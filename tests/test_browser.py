"""Tests of the browser transport logic, with a fake page instead of Playwright.

`_PageSession` is async (it runs on the browser thread's loop): each test runs
it with `asyncio.run`.
"""

from __future__ import annotations

import asyncio
import inspect
import json
from collections.abc import Callable, Generator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Self

import pytest

from moodlesh.core.errors import AuthenticationError, BackendError
from moodlesh.moodle.auth import SessionStore
from moodlesh.moodle.browser import _Config, _TabSession

BASE = "https://moodle.example.org"
CHALLENGE = "<html><head><title>Security Check</title></head></html>"


def ok(data: object) -> tuple[int, str]:
    return 200, json.dumps([{"error": False, "data": data}])


@dataclass
class FakeTab:
    """Simulates a tab: a sequence of URLs/states and the responses to fetches."""

    url: str = BASE + "/my/"
    states: list[dict[str, Any] | None] = field(default_factory=lambda: [{"sesskey": "k1", "userId": 5}])
    responses: list[tuple[int, str]] = field(default_factory=list)
    on_goto: Callable[[FakeTab, str], None] | None = None
    visited: list[str] = field(default_factory=list)
    fetched: list[dict[str, str]] = field(default_factory=list)

    async def get(
        self, url: str = "chrome://welcome", new_tab: bool = False, new_window: bool = False
    ) -> Self:
        self.visited.append(url)
        if self.on_goto:
            self.on_goto(self, url)
        else:
            self.url = url

        return self

    async def wait(self, t: int | float = 0.5) -> None:
        pass

    async def send(
        self, cdp_obj: Generator[dict[str, Any], dict[str, Any], Any], _attach: bool = False, **kwargs
    ) -> Any:
        caller = inspect.stack()[1].function
        if caller == "_inspect":  # Moodle state
            value = self.states.pop(0) if len(self.states) > 1 else self.states[0]
            return type("", (object,), {"value": value})(), None
        self.fetched.append({"url": self.url, "body": ""})
        return type("", (object,), {"value": self.responses.pop(0)})(), None


class SyncSession:
    """Runs the async `_PageSession` methods to completion, for readable tests."""

    def __init__(self, inner: _TabSession) -> None:
        self.inner = inner

    def call(self, function: str, args: dict[str, Any]) -> Any:
        return asyncio.run(self.inner.call(function, args))

    def ensure_ready(self) -> str:
        return asyncio.run(self.inner.ensure_ready())


def session(page: FakeTab, tmp_path: Path, messages: list[str] | None = None) -> SyncSession:
    sink = messages if messages is not None else []
    cfg = _Config(
        BASE, SessionStore(tmp_path), sink.append, ready_timeout_s=5, poll_interval_s=0, launch_options={}
    )
    return SyncSession(_TabSession(page, cfg))


def test_waits_for_security_check_then_proceeds(tmp_path: Path) -> None:
    messages: list[str] = []
    tab = FakeTab(states=[None, None, {"sesskey": "k2", "userId": 5}], responses=[ok(1)])
    assert session(tab, tmp_path, messages).call("f", {}) == 1
    assert any("security check" in m for m in messages)


def test_function_errors_are_not_retried(tmp_path: Path) -> None:
    denied = (
        ok(None)[0],
        json.dumps(
            [{"error": True, "exception": {"message": "access denied", "errorcode": "nopermissions"}}]
        ),
    )
    tab = FakeTab(responses=[denied])
    with pytest.raises(BackendError, match="access denied"):
        session(tab, tmp_path).call("f", {})
    assert len(tab.fetched) == 1


def test_persistent_challenge_gives_up_after_one_retry(tmp_path: Path) -> None:
    tab = FakeTab(responses=[(403, CHALLENGE), (403, CHALLENGE)])
    with pytest.raises(AuthenticationError, match="Security Check"):
        session(tab, tmp_path).call("f", {})


def test_guest_page_triggers_login(tmp_path: Path) -> None:
    tab = FakeTab(states=[{"sesskey": "g", "userId": 0}, {"sesskey": "k", "userId": 5}], responses=[ok(1)])

    def login_completes(p: FakeTab, url: str) -> None:
        p.url = BASE + "/"  # the SSO brings us back to Moodle, now authenticated

    tab.on_goto = login_completes
    assert session(tab, tmp_path).call("f", {}) == 1
    assert BASE + "/login/index.php" in tab.visited


def test_ready_timeout(tmp_path: Path) -> None:
    tab = FakeTab(states=[None])
    sess = session(tab, tmp_path)
    sess.inner._cfg = _Config(
        BASE, SessionStore(tmp_path), lambda _: None, ready_timeout_s=0, poll_interval_s=0, launch_options={}
    )
    with pytest.raises(AuthenticationError, match="within 0 seconds"):
        sess.ensure_ready()


def test_page_still_loading_is_not_reported_to_the_user(tmp_path: Path) -> None:
    messages: list[str] = []
    tab = FakeTab(states=[{"loading": True}, {"sesskey": "k3", "userId": 5}])
    assert session(tab, tmp_path, messages).ensure_ready() == "k3"
    assert messages == []
