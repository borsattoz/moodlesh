"""Command-line entry point.

    moodlesh [options] [shell]               interactive shell (default)
    moodlesh [options] run SCRIPT [targets]  run a script (- = stdin)
    moodlesh [options] exec "COMMANDS"       run commands given as an argument
    moodlesh [options] login [--password]    save a session for the given URL
    moodlesh [options] logout                end and delete the saved session

The URL is given with --url or with the MOODLESH_URL environment variable.

Safety: --dry-run simulates every change and writes nothing; real changes are
recorded in a journal (`changes.jsonl` in the state directory). With targets
(--course, --category, --match) a script runs on many courses, after asking
for confirmation.

How to talk to Moodle (--transport or MOODLESH_TRANSPORT):

* browser (default): calls are made from a Chromium window, like the ones the
  user interface makes. Works with SSO and with anti-bot checks: you do the
  login and the checks yourself, in the window. MOODLESH_BROWSER_CHANNEL=chrome
  uses the installed Chrome; MOODLESH_BROWSER_PATH points to an executable.
* http: direct HTTP requests with the saved session, no windows.

With a token (--token or MOODLESH_TOKEN) the REST web services are used instead.
--demo uses sample data held in memory.
"""

from __future__ import annotations

import argparse
import getpass
import logging
import os
import sys
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from rich.console import Console

from moodlesh import __version__
from moodlesh.cli.bulk import CourseSelection, run_on_courses, select_courses, summary_table
from moodlesh.cli.errors import CliError, DslSyntaxError, ExitRequested, UsageError
from moodlesh.cli.interpreter import Interpreter, ScriptError, compile_script
from moodlesh.cli.repl import report_error, run_repl
from moodlesh.core.backend import CourseBackend
from moodlesh.core.errors import MoodleshError
from moodlesh.dryrun import DryRunBackend
from moodlesh.journal import Journal, JournalingBackend
from moodlesh.memory import demo_backend
from moodlesh.moodle import (
    BrowserTransport,
    SessionStore,
    connect_with_browser,
    connect_with_session,
    connect_with_token,
    end_server_session,
    login_with_password,
    verify_session,
)
from moodlesh.moodle.auth import default_state_dir

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_USAGE = 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="moodlesh", description="The command line Moodle never had.")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("--url", default=os.environ.get("MOODLESH_URL"), help="Moodle URL (or MOODLESH_URL)")
    parser.add_argument(
        "--token",
        default=os.environ.get("MOODLESH_TOKEN"),
        help="web service token: use REST instead of the session (prefer MOODLESH_TOKEN: "
        "arguments are visible to other users of the computer)",
    )
    parser.add_argument(
        "--transport",
        choices=("browser", "http"),
        default=os.environ.get("MOODLESH_TRANSPORT", "browser"),
        help="browser: calls from a Chromium window (default); http: direct requests",
    )
    parser.add_argument("-n", "--dry-run", action="store_true", help="show what would change, change nothing")
    parser.add_argument(
        "--all-courses",
        action="store_true",
        help="list every course you can see, not only the ones you can edit",
    )
    parser.add_argument("--demo", action="store_true", help="use sample data held in memory")
    parser.add_argument("-v", "--verbose", action="count", default=0, help="detailed logs (-vv for debug)")

    targets = argparse.ArgumentParser(add_help=False)
    group = targets.add_argument_group("targets (run the commands on many courses)")
    group.add_argument(
        "-c",
        "--course",
        action="append",
        default=[],
        metavar="COURSE",
        help="a course by id, short name or full name (repeatable)",
    )
    group.add_argument(
        "--category",
        action="append",
        default=[],
        metavar="CATEGORY",
        help="every editable course in this category, by id or name (repeatable)",
    )
    group.add_argument("--match", metavar="TEXT", help="every editable course whose name contains TEXT")
    group.add_argument(
        "--keep-going", action="store_true", help="continue with the next course after an error"
    )
    group.add_argument("-y", "--yes", action="store_true", help="do not ask for confirmation")

    sub = parser.add_subparsers(dest="action")
    sub.add_parser("shell", help="interactive shell (default)")

    run = sub.add_parser("run", parents=[targets], help="run a script of commands")
    run.add_argument("script", help="script file, or - for standard input")
    run.add_argument("--check", action="store_true", help="validate the script without connecting to Moodle")

    execute = sub.add_parser("exec", parents=[targets], help="run the commands given as an argument")
    execute.add_argument("commands", help='commands, separated by ";"')

    login = sub.add_parser("login", help="log in to Moodle and save the session")
    login.add_argument(
        "--password",
        action="store_true",
        help="use username and password instead of the browser (manual authentication only)",
    )
    login.add_argument("--username", help="username (with --password)")

    sub.add_parser("logout", help="end the session and delete it from this computer")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    console = Console()
    _configure_logging(args.verbose)
    handler = _ACTIONS.get(args.action, _shell)
    try:
        return handler(args, console)
    except DslSyntaxError as exc:
        report_error(console, exc)
        return EXIT_USAGE
    except ScriptError as exc:
        report_error(console, exc)
        return EXIT_USAGE if isinstance(exc.cause, CliError) else EXIT_ERROR
    except CliError as exc:
        report_error(console, exc)
        return EXIT_USAGE
    except MoodleshError as exc:
        report_error(console, exc)
        return EXIT_ERROR
    except KeyboardInterrupt:
        return EXIT_ERROR


# --------------------------------------------------------------------------- #
# Actions
# --------------------------------------------------------------------------- #
def _shell(args: argparse.Namespace, console: Console) -> int:
    with _open_backend(args, console) as backend:
        interpreter = Interpreter(backend, dry_run=args.dry_run)
        return run_repl(interpreter, console, default_state_dir() / "history")


def _exec(args: argparse.Namespace, console: Console) -> int:
    return _run_text(args.commands, args, console)


def _run_script(args: argparse.Namespace, console: Console) -> int:
    text = sys.stdin.read() if args.script == "-" else Path(args.script).read_text(encoding="utf-8")
    if args.check:
        commands = compile_script(text)  # validation does not need Moodle
        console.print(f"valid script: {len(commands)} commands")
        return EXIT_OK
    return _run_text(text, args, console)


def _run_text(text: str, args: argparse.Namespace, console: Console) -> int:
    compile_script(text)  # syntax and usage errors surface before connecting to Moodle
    selection = CourseSelection(tuple(args.course), tuple(args.category), args.match)
    with _open_backend(args, console) as backend:
        if selection.active:
            return _run_bulk(text, selection, backend, args, console)
        try:
            for output in Interpreter(backend, dry_run=args.dry_run).run(text):
                console.print(output)
        except ExitRequested:
            pass
    if args.dry_run:
        console.print("[yellow]dry run: nothing was changed[/yellow]")
    return EXIT_OK


def _run_bulk(
    text: str,
    selection: CourseSelection,
    backend: CourseBackend,
    args: argparse.Namespace,
    console: Console,
) -> int:
    courses = select_courses(backend, selection)
    console.print(f"{len(courses)} course(s): " + ", ".join(c.shortname for c in courses))
    if not args.dry_run and not args.yes and not _confirm(f"Apply the changes to {len(courses)} course(s)?"):
        console.print("cancelled: nothing was changed")
        return EXIT_ERROR
    outcomes = run_on_courses(
        backend, text, courses, emit=console.print, dry_run=args.dry_run, keep_going=args.keep_going
    )
    console.print(summary_table(outcomes, dry_run=args.dry_run))
    return EXIT_OK if all(o.ok for o in outcomes) else EXIT_ERROR


def _confirm(question: str) -> bool:
    if not sys.stdin.isatty():
        raise UsageError("confirmation needed: add --yes to run on many courses without a terminal")
    return input(f"{question} [y/N] ").strip().casefold() in {"y", "yes"}


def _login(args: argparse.Namespace, console: Console) -> int:
    url = _require_url(args)
    store = SessionStore()
    if args.password:
        username = args.username or input("username: ")
        store.save(url, login_with_password(url, username, getpass.getpass("password: ")))
    else:
        console.print("opening the browser: complete the login, the window will close by itself")
        # Opening waits for an authenticated Moodle page; closing saves the session.
        BrowserTransport(
            url, store=store, notify=_notifier(console), launch_options=_launch_options()
        ).close()
    if args.transport == "http":
        cookies = store.load(url)
        assert cookies is not None
        # With the http transport the session must also work outside the browser.
        verify_session(url, cookies)
    console.print(f"[green]session saved[/green] to {store.path_for(url)}")
    return EXIT_OK


def _logout(args: argparse.Namespace, console: Console) -> int:
    url = _require_url(args)
    store = SessionStore()
    cookies = store.load(url)
    if cookies is None:
        console.print("no saved session")
        return EXIT_OK
    ended = end_server_session(url, cookies)
    store.delete(url)
    console.print("session ended on Moodle and deleted" if ended else "session deleted from this computer")
    return EXIT_OK


_ACTIONS: dict[str | None, Callable[[argparse.Namespace, Console], int]] = {
    "shell": _shell,
    "run": _run_script,
    "exec": _exec,
    "login": _login,
    "logout": _logout,
}


# --------------------------------------------------------------------------- #
# Backend
# --------------------------------------------------------------------------- #
@contextmanager
def _open_backend(args: argparse.Namespace, console: Console) -> Iterator[CourseBackend]:
    """Create the chosen backend, add dry-run or journaling, and close it at the end."""
    if args.demo:
        demo = demo_backend()
        yield DryRunBackend(demo) if args.dry_run else demo
        return
    url = _require_url(args)
    editable_only = not args.all_courses
    if args.token:
        yield _safety(connect_with_token(url, args.token, editable_only=editable_only), url, args, console)
    elif args.transport == "http":
        yield _safety(connect_with_session(url, editable_only=editable_only), url, args, console)
    else:
        console.print("[dim]opening the browser (keep it open while you use moodlesh)…[/dim]")
        backend, transport = connect_with_browser(
            url, notify=_notifier(console), launch_options=_launch_options(), editable_only=editable_only
        )
        with transport:
            yield _safety(backend, url, args, console)


def _safety(backend: CourseBackend, url: str, args: argparse.Namespace, console: Console) -> CourseBackend:
    if args.dry_run:
        console.print("[yellow]dry run: changes are simulated, Moodle is not modified[/yellow]")
        return DryRunBackend(backend)
    return JournalingBackend(backend, Journal(journal_path(), url))


def journal_path() -> Path:
    custom = os.environ.get("MOODLESH_JOURNAL")
    return Path(custom) if custom else default_state_dir() / "changes.jsonl"


def _notifier(console: Console) -> Callable[[str], None]:
    return lambda message: console.print(f"[yellow]→ {message}[/yellow]")


def _launch_options() -> dict[str, Any]:
    options: dict[str, Any] = {}
    if channel := os.environ.get("MOODLESH_BROWSER_CHANNEL"):
        options["channel"] = channel
    if path := os.environ.get("MOODLESH_BROWSER_PATH"):
        options["executable_path"] = path
    return options


def _require_url(args: argparse.Namespace) -> str:
    if not args.url:
        raise MoodleshError("give the Moodle URL with --url or the MOODLESH_URL variable (or use --demo)")
    return str(args.url)


def _configure_logging(verbosity: int) -> None:
    level = {0: logging.WARNING, 1: logging.INFO}.get(verbosity, logging.DEBUG)
    logging.basicConfig(level=level, format="%(levelname)s %(name)s: %(message)s")
    # httpx logs full request URLs, which contain the session's sesskey: keep them
    # out of logs that users may paste into bug reports.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
