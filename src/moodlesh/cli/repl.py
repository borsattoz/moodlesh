"""Interactive shell and error reporting."""

from __future__ import annotations

from pathlib import Path

from prompt_toolkit import PromptSession
from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
from prompt_toolkit.history import FileHistory, History, InMemoryHistory
from rich.console import Console
from rich.text import Text

from moodlesh.cli.completion import DslCompleter
from moodlesh.cli.errors import DslSyntaxError, ExitRequested
from moodlesh.cli.interpreter import Interpreter, ScriptError
from moodlesh.core.errors import AuthenticationError, MoodleshError


def report_error(console: Console, exc: MoodleshError) -> None:
    """Show an error readably, pointing at the exact spot for syntax errors."""
    if isinstance(exc, DslSyntaxError):
        caret = exc.caret()
        if caret:
            console.print(Text(caret, style="yellow"))
        console.print(Text(f"syntax error: {exc}", style="bold red"))
        return
    cause = exc.cause if isinstance(exc, ScriptError) else exc
    console.print(Text(f"error: {exc}", style="bold red"))
    if isinstance(cause, AuthenticationError):
        console.print(Text("hint: run `moodlesh login` again", style="yellow"))


def run_repl(interpreter: Interpreter, console: Console, history_file: Path | None = None) -> int:
    """Read-eval-print loop. Returns the exit code."""
    history: History = InMemoryHistory()
    if history_file is not None:
        history_file.parent.mkdir(parents=True, exist_ok=True)
        history = FileHistory(str(history_file))
    session: PromptSession[str] = PromptSession(
        history=history,
        completer=DslCompleter(interpreter),
        auto_suggest=AutoSuggestFromHistory(),
        complete_while_typing=False,
    )
    console.print(Text("moodlesh — type help for the list of commands, exit to quit", style="dim"))

    while True:
        try:
            mode = "[dry-run]" if interpreter.dry_run else ""
            prompt = f"moodle{mode}:{_safe_prompt_path(interpreter)}> "
            line = session.prompt(prompt)
        except KeyboardInterrupt:
            continue
        except EOFError:
            return 0
        try:
            for output in interpreter.run(line):
                console.print(output)
        except ExitRequested:
            return 0
        except MoodleshError as exc:
            report_error(console, exc)


def _safe_prompt_path(interpreter: Interpreter) -> str:
    try:
        return interpreter.prompt_path()
    except MoodleshError:
        interpreter.cwd = interpreter.cwd.parent()
        return "?"
