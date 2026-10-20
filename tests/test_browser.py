"""Tests of the browser transport logic, with a fake page instead of Playwright."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from moodlesh.core.errors import AuthenticationError, BackendError
from moodlesh.moodle.auth import SessionStore
from moodlesh.moodle.browser import _Config, _PageSession

BASE = "https://moodle.example.org"
CHALLENGE = "<html><head><title>Security Check</title></head></html>"


def ok(data: object) -> tuple[int, str]:
    return 200, json.dumps([{"error": False, "data": data}])


@dataclass
class FakePage:
    """Simulates a tab: a sequence of URLs/states and the responses to fetches."""

    url: str = BASE + "/my/"
    states: list[dict[str, Any] | None] = field(default_factory=lambda: [{"sesskey": "k1", "userId": 5}])
    responses: list[tuple[int, str]] = field(default_factory=list)
    on_goto: Callable[[FakePage, str], None] | None = None
    visited: list[str] = field(default_factory=list)
    fetched: list[dict[str, str]] = field(default_factory=list)
    closed: bool = False

    def is_closed(self) -> bool:
        return self.closed

    def goto(self, url: str, wait_until: str = "load") -> None:
        self.visited.append(url)
        if self.on_goto:
            self.on_goto(self, url)
        else:
            self.url = url

    def wait_for_timeout(self, ms: float) -> None:
        pass

    def evaluate(self, script: str, arg: Any = None) -> Any:
        if arg is None:  # Moodle state
            return self.states.pop(0) if len(self.states) > 1 else self.states[0]
        self.fetched.append(arg)
        return list(self.responses.pop(0))


def session(page: FakePage, tmp_path: Path, messages: list[str] | None = None) -> _PageSession:
    sink = messages if messages is not None else []
    cfg = _Config(
        BASE, SessionStore(tmp_path), sink.append, ready_timeout_s=5, poll_interval_s=0, launch_options={}
    )
    return _PageSession(page, cfg)


def test_call_uses_sesskey_from_page(tmp_path: Path) -> None:
    page = FakePage(responses=[ok({"x": 1})])
    assert session(page, tmp_path).call("core_course_get_courses", {"options": {"ids": [3]}}) == {"x": 1}
    [fetch] = page.fetched
    assert "sesskey=k1&info=core_course_get_courses" in fetch["url"]
    assert json.loads(fetch["body"])[0]["args"] == {"options": {"ids": [3]}}


def test_waits_for_security_check_then_proceeds(tmp_path: Path) -> None:
    messages: list[str] = []
    page = FakePage(states=[None, None, {"sesskey": "k2", "userId": 5}], responses=[ok(1)])
    assert session(page, tmp_path, messages).call("f", {}) == 1
    assert any("security check" in m for m in messages)


def test_waits_for_login_on_external_idp(tmp_path: Path) -> None:
    messages: list[str] = []
    page = FakePage(url="https://idp.example.org/sso", responses=[ok(1)])
    page.on_goto = lambda p, url: None  # navigating to /my/ ends up on the IdP

    def login_after_two_polls() -> None:
        page.url = BASE + "/"

    calls = {"n": 0}

    def wait(ms: float) -> None:
        calls["n"] += 1
        if calls["n"] == 2:
            login_after_two_polls()

    page.wait_for_timeout = wait  # type: ignore[method-assign]
    assert session(page, tmp_path, messages).call("f", {}) == 1
    assert "complete the login in the browser window" in messages


def test_challenge_during_a_call_reloads_and_retries(tmp_path: Path) -> None:
    page = FakePage(responses=[(403, CHALLENGE), ok("dopo")])
    assert session(page, tmp_path).call("f", {}) == "dopo"
    assert page.visited == [BASE + "/my/"]  # reloads Moodle to show the check to the user


def test_expired_sesskey_is_refreshed(tmp_path: Path) -> None:
    expired = (200, json.dumps({"error": "Invalid sesskey", "errorcode": "invalidsesskey"}))
    page = FakePage(
        states=[{"sesskey": "old", "userId": 5}, {"sesskey": "new", "userId": 5}], responses=[expired, ok(2)]
    )
    assert session(page, tmp_path).call("f", {}) == 2
    assert "sesskey=new" in page.fetched[1]["url"]


def test_function_errors_are_not_retried(tmp_path: Path) -> None:
    denied = (
        ok(None)[0],
        json.dumps(
            [{"error": True, "exception": {"message": "access denied", "errorcode": "nopermissions"}}]
        ),
    )
    page = FakePage(responses=[denied])
    with pytest.raises(BackendError, match="access denied"):
        session(page, tmp_path).call("f", {})
    assert len(page.fetched) == 1


def test_persistent_challenge_gives_up_after_one_retry(tmp_path: Path) -> None:
    page = FakePage(responses=[(403, CHALLENGE), (403, CHALLENGE)])
    with pytest.raises(AuthenticationError, match="Security Check"):
        session(page, tmp_path).call("f", {})


def test_guest_page_triggers_login(tmp_path: Path) -> None:
    page = FakePage(states=[{"sesskey": "g", "userId": 0}, {"sesskey": "k", "userId": 5}], responses=[ok(1)])

    def login_completes(p: FakePage, url: str) -> None:
        p.url = BASE + "/"  # the SSO brings us back to Moodle, now authenticated

    page.on_goto = login_completes
    assert session(page, tmp_path).call("f", {}) == 1
    assert BASE + "/login/index.php" in page.visited


def test_closed_window_is_reported(tmp_path: Path) -> None:
    page = FakePage(states=[None])
    page.closed = True
    with pytest.raises(AuthenticationError, match="closed"):
        session(page, tmp_path).ensure_ready()


def test_ready_timeout(tmp_path: Path) -> None:
    page = FakePage(states=[None])
    sess = session(page, tmp_path)
    sess._cfg = _Config(
        BASE, SessionStore(tmp_path), lambda _: None, ready_timeout_s=0, poll_interval_s=0, launch_options={}
    )
    with pytest.raises(AuthenticationError, match="within 0 seconds"):
        sess.ensure_ready()


class FakePlaywrightError(Exception):
    pass


def test_only_page_errors_are_retried(tmp_path: Path) -> None:
    def failing(script: str, arg: Any = None) -> Any:
        if arg is None:
            return {"sesskey": "k1", "userId": 5}
        raise TypeError("a bug, not a page problem")

    page = FakePage()
    page.evaluate = failing  # type: ignore[method-assign]
    sess = _PageSession(
        page,
        _Config(BASE, SessionStore(tmp_path), lambda _: None, 5, 0, {}, page_error=FakePlaywrightError),
    )
    with pytest.raises(TypeError):
        sess.call("f", {})
    assert page.visited == []  # no reload, no retry


def test_page_errors_are_retried_once(tmp_path: Path) -> None:
    calls: list[Any] = []

    def flaky(script: str, arg: Any = None) -> Any:
        if arg is None:
            return {"sesskey": "k1", "userId": 5}
        calls.append(arg)
        if len(calls) == 1:
            raise FakePlaywrightError("Execution context was destroyed")
        return list(ok(7))

    page = FakePage()
    page.evaluate = flaky  # type: ignore[method-assign]
    sess = _PageSession(
        page,
        _Config(BASE, SessionStore(tmp_path), lambda _: None, 5, 0, {}, page_error=FakePlaywrightError),
    )
    assert sess.call("f", {}) == 7
    assert len(calls) == 2


def test_page_still_loading_is_not_reported_to_the_user(tmp_path: Path) -> None:
    messages: list[str] = []
    page = FakePage(states=[{"loading": True}, {"sesskey": "k3", "userId": 5}])
    assert session(page, tmp_path, messages).ensure_ready() == "k3"
    assert messages == []
