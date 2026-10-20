"""Tests of the entry point, tab completion and the REPL."""

from __future__ import annotations

from pathlib import Path

import pytest
from prompt_toolkit.completion import CompleteEvent
from prompt_toolkit.document import Document
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput
from rich.console import Console

from moodlesh.cli.completion import DslCompleter
from moodlesh.cli.interpreter import Interpreter
from moodlesh.cli.main import EXIT_ERROR, EXIT_OK, EXIT_USAGE, main
from moodlesh.cli.repl import run_repl


def test_exec_in_demo_mode(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--demo", "exec", "cd PHY1; tree -d"]) == EXIT_OK
    assert "Exercises/" in capsys.readouterr().out


def test_exit_codes(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--demo", "exec", "ls --bogus"]) == EXIT_USAGE
    assert main(["--demo", "exec", 'ls "open']) == EXIT_USAGE
    assert main(["--demo", "exec", "cd /NOPE"]) == EXIT_ERROR
    assert "no course" in capsys.readouterr().out


def test_missing_url_is_reported(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.delenv("MOODLESH_URL", raising=False)
    monkeypatch.delenv("MOODLESH_TOKEN", raising=False)
    assert main(["exec", "ls"]) == EXIT_ERROR
    assert "MOODLESH_URL" in capsys.readouterr().out


def test_run_check_validates_without_moodle(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    script = tmp_path / "structure.mdl"
    script.write_text("# standard structure\ncd 101\nmkdir Exercises --at 1 --into 1\n", encoding="utf-8")
    assert main(["run", str(script), "--check"]) == EXIT_OK
    assert "2 commands" in capsys.readouterr().out


def complete(interpreter: Interpreter, text: str) -> list[str]:
    completer = DslCompleter(interpreter)
    return [c.text for c in completer.get_completions(Document(text), CompleteEvent())]


def test_completion_of_commands_options_and_paths(interpreter: Interpreter) -> None:
    assert complete(interpreter, "mk") == ["mkdir"]
    assert complete(interpreter, "tree --d") == ["--depth", "--dirs"]
    assert complete(interpreter, "cd P") == ["PHY1"]
    assert complete(interpreter, "cd /PHY1/K") == ["/PHY1/Kinematics"]
    assert complete(interpreter, "cd /PHY1/Kinematics/") == ["/PHY1/Kinematics/Exercises"]  # containers only
    assert complete(interpreter, "help r") == ["refresh", "rename"]
    assert complete(interpreter, "mkdir X --into /PHY1/D") == ["/PHY1/Dynamics"]
    assert complete(interpreter, "mkdir X") == []  # the name is free text


def test_completion_quotes_names_with_spaces(interpreter: Interpreter) -> None:
    assert complete(interpreter, "cd Physics") == ['"Physics I"']


def test_repl_session(interpreter: Interpreter) -> None:
    console = Console(record=True, width=100, color_system=None)
    with create_pipe_input() as pipe:
        pipe.send_text('cd PHY1\nmkdir "Optics" --at 2\nls --bad\nexit\n')
        from prompt_toolkit.application import create_app_session

        with create_app_session(input=pipe, output=DummyOutput()):
            code = run_repl(interpreter, console)
    text = console.export_text()
    assert code == EXIT_OK
    assert "created section 2 'Optics'" in text
    assert "unknown option" in text
