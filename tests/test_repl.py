"""Tests of the interactive shell."""

from __future__ import annotations

import io
from typing import ClassVar

import pytest
from rich.console import Console

from moodlesh.cli import repl
from moodlesh.cli.interpreter import Interpreter
from moodlesh.cli.navigation import ROOT
from moodlesh.cli.repl import _safe_prompt_path, run_repl
from moodlesh.core.errors import BackendError, ConnectionLostError, NotFoundError
from moodlesh.core.models import Course

from conftest import run


def _fail_next_course_load(interpreter: Interpreter, monkeypatch: pytest.MonkeyPatch, exc: Exception) -> None:
    """Make the next request for a course fail, as if Moodle answered with `exc`."""

    def fail(course_id: int) -> Course:
        raise exc

    interpreter.navigator.invalidate()
    monkeypatch.setattr(interpreter.navigator.backend, "get_course", fail)


def test_prompt_shows_the_current_location(interpreter: Interpreter) -> None:
    run(interpreter, "cd PHY1; cd 1")
    assert _safe_prompt_path(interpreter) == ("/PHY1/Kinematics", None)


def test_network_or_session_problem_keeps_the_location(
    interpreter: Interpreter, monkeypatch: pytest.MonkeyPatch
) -> None:
    run(interpreter, "cd PHY1; cd 1")
    where = interpreter.cwd
    problem = BackendError("connection refused")
    _fail_next_course_load(interpreter, monkeypatch, problem)

    assert _safe_prompt_path(interpreter) == ("?", problem)
    assert interpreter.cwd == where


def test_course_that_no_longer_exists_goes_back_to_the_root(
    interpreter: Interpreter, monkeypatch: pytest.MonkeyPatch
) -> None:
    run(interpreter, "cd PHY1; cd 1")
    problem = NotFoundError("course 101 does not exist or is not accessible")
    _fail_next_course_load(interpreter, monkeypatch, problem)

    assert _safe_prompt_path(interpreter) == ("/", problem)
    assert interpreter.cwd == ROOT


class _Session:
    """Stands for prompt_toolkit's `PromptSession`: answers the given lines, then end of input."""

    lines: ClassVar[list[str]] = []
    prompts: ClassVar[list[str]] = []

    def __init__(self, **_: object) -> None:
        pass

    def prompt(self, message: str) -> str:
        _Session.prompts.append(message)
        if not _Session.lines:
            raise EOFError
        return _Session.lines.pop(0)


def _shell(interpreter: Interpreter, monkeypatch: pytest.MonkeyPatch, *lines: str) -> tuple[int, str]:
    """Run the shell on `lines`; return its exit code and what it printed."""
    monkeypatch.setattr(repl, "PromptSession", _Session)
    monkeypatch.setattr(_Session, "lines", list(lines))
    monkeypatch.setattr(_Session, "prompts", [])
    out = io.StringIO()
    code = run_repl(interpreter, Console(file=out, width=200, color_system=None))
    return code, out.getvalue()


def test_shell_leaves_when_the_connection_is_lost(
    interpreter: Interpreter, monkeypatch: pytest.MonkeyPatch
) -> None:
    run(interpreter, "cd PHY1")
    _fail_next_course_load(interpreter, monkeypatch, ConnectionLostError("the browser closed"))

    code, out = _shell(interpreter, monkeypatch)  # the prompt itself meets the problem

    assert code == 1
    assert "the browser closed" in out
    assert "leaving the shell" in out
    assert _Session.prompts == []  # it never asked for a command that could not work


def test_shell_leaves_when_a_command_loses_the_connection(
    interpreter: Interpreter, monkeypatch: pytest.MonkeyPatch
) -> None:
    code, out = _shell(interpreter, monkeypatch, "cd PHY1; ls", "ls")
    assert code == 0  # end of input: the connection was fine

    run(interpreter, "cd /")
    _fail_next_course_load(interpreter, monkeypatch, ConnectionLostError("the browser closed"))
    code, out = _shell(interpreter, monkeypatch, "cd PHY1; ls", "ls")

    assert code == 1
    assert "line 1: the browser closed" in out
    assert len(_Session.prompts) == 1  # the second line was never asked for


def test_shell_stays_open_after_other_errors(
    interpreter: Interpreter, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fail_next_course_load(interpreter, monkeypatch, BackendError("connection refused"))
    code, out = _shell(interpreter, monkeypatch, "cd PHY1; ls", "exit")
    assert code == 0
    assert "connection refused" in out
    assert "leaving the shell" not in out
    assert len(_Session.prompts) == 2


def test_a_lasting_prompt_problem_is_reported_once(
    interpreter: Interpreter, monkeypatch: pytest.MonkeyPatch
) -> None:
    run(interpreter, "cd PHY1")
    _fail_next_course_load(interpreter, monkeypatch, BackendError("connection refused"))
    code, out = _shell(interpreter, monkeypatch, "", "")
    assert code == 0
    assert out.count("connection refused") == 1
    assert _Session.prompts == ["moodle:?> "] * 3
