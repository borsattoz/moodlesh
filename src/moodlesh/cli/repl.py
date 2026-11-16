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
from moodlesh.cli.navigation import ROOT
from moodlesh.core.errors import AuthenticationError, ConnectionLostError, MoodleshError, NotFoundError


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
    if isinstance(cause, AuthenticationError) or isinstance(cause.__cause__, AuthenticationError):
        console.print(Text("hint: run `moodlesh login` again", style="yellow"))


def connection_lost(exc: MoodleshError) -> bool:
    """Whether `exc` means the connection to Moodle cannot work again in this run."""
    cause = exc.cause if isinstance(exc, ScriptError) else exc
    return isinstance(cause, ConnectionLostError)


def run_repl(interpreter: Interpreter, console: Console, history_file: Path | None = None) -> int:
    """Read-eval-print loop. Returns the exit code.

    The shell ends with exit code 1 when the connection to Moodle is lost for
    good (`ConnectionLostError`): every later command would fail too.
    """
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

    last_problem: str | None = None
    while True:
        path, problem = _safe_prompt_path(interpreter)
        # Show a problem once, not again at every prompt while it lasts.
        if problem is not None and str(problem) != last_problem:
            report_error(console, problem)
        if problem is not None and connection_lost(problem):
            return _leave(console)
        last_problem = None if problem is None else str(problem)
        try:
            mode = "[dry-run]" if interpreter.dry_run else ""
            prompt = f"moodle{mode}:{path}> "
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
            if connection_lost(exc):
                return _leave(console)


def _leave(console: Console) -> int:
    console.print(Text("the connection to Moodle is lost: leaving the shell", style="yellow"))
    return 1


def _safe_prompt_path(interpreter: Interpreter) -> tuple[str, MoodleshError | None]:
    """The path for the prompt, and the problem met while building it, if any.

    Building the path may ask Moodle for the course, and that can fail. A
    course that no longer exists (or is no longer accessible) sends the shell
    back to the list of courses. Any other problem (session, network) keeps the
    current location, so that the next prompt simply tries again.
    """
    try:
        return interpreter.prompt_path(), None
    except NotFoundError as exc:
        interpreter.cwd = ROOT
        return interpreter.prompt_path(), exc  # the root needs nothing from Moodle
    except MoodleshError as exc:
        return "?", exc
