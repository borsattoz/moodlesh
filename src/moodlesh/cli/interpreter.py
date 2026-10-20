"""DSL interpreter: runs the typed commands against the backend.

The interpreter does not print: each command returns a `rich` renderable (or
None) that the caller (REPL or script runner) displays as it sees fit.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass

from rich.console import RenderableType
from rich.text import Text

from moodlesh.cli import render
from moodlesh.cli.commands import Cd, Command, Exit, Help, Ls, Mkdir, Pwd, Refresh, Rename, Tree, bind, lookup
from moodlesh.cli.dsl.ast import Position
from moodlesh.cli.dsl.parser import parse
from moodlesh.cli.errors import ExitRequested, UsageError
from moodlesh.cli.navigation import ROOT, Location, Navigator
from moodlesh.core.backend import CourseBackend
from moodlesh.core.errors import InvalidOperationError, MoodleshError, NotFoundError


@dataclass(frozen=True, slots=True)
class BoundCommand:
    command: Command
    pos: Position


class ScriptError(MoodleshError):
    """Error while running a script, with the line that caused it."""

    def __init__(self, line: int, cause: MoodleshError) -> None:
        super().__init__(f"line {line}: {cause}")
        self.line = line
        self.cause = cause


def compile_script(text: str) -> list[BoundCommand]:
    """Parse and validate the whole text, without running anything or touching Moodle.

    Raises:
        DslSyntaxError: syntax error.
        ScriptError: a command used incorrectly, with its line.
    """
    bound: list[BoundCommand] = []
    for raw in parse(text):
        try:
            bound.append(BoundCommand(bind(raw), raw.pos))
        except UsageError as exc:
            raise ScriptError(raw.pos.line, exc) from exc
    return bound


class Interpreter:
    """Session state (current location, cache) and command execution."""

    def __init__(self, backend: CourseBackend, *, dry_run: bool = False, start: Location = ROOT) -> None:
        self.navigator = Navigator(backend)
        self.dry_run = dry_run
        self.cwd: Location = start

    @property
    def backend(self) -> CourseBackend:
        return self.navigator.backend

    def prompt_path(self) -> str:
        return self.navigator.display(self.cwd)

    # ------------------------------------------------------------------- input
    def run(self, text: str) -> Iterator[RenderableType]:
        """Run a line or a script, yielding each command's output as it goes.

        The whole text is validated before the first command runs; execution
        stops at the first error.

        Raises:
            DslSyntaxError: syntax error (no command was run).
            ScriptError: validation or execution error, with its line.
            ExitRequested: `exit` was run.
        """
        for item in compile_script(text):
            try:
                result = self.execute(item.command)
            except MoodleshError as exc:
                raise ScriptError(item.pos.line, exc) from exc
            if result is not None:
                yield result

    # --------------------------------------------------------------- execution
    def execute(self, command: Command) -> RenderableType | None:  # noqa: PLR0911 - one branch per command
        match command:
            case Ls():
                return self._ls(command)
            case Cd(path=path):
                self.cwd = self.navigator.resolve(self.cwd, path or "/")
                return None
            case Pwd():
                return Text(self.prompt_path())
            case Tree():
                return self._tree(command)
            case Mkdir():
                return self._mkdir(command)
            case Rename():
                return self._rename(command)
            case Refresh():
                self.navigator.invalidate()
                self.cwd = self._still_valid(self.cwd)
                return Text("data reloaded from Moodle", style="dim")
            case Help(command=name):
                return self._help(name)
            case Exit():
                raise ExitRequested
        raise UsageError(f"unhandled command: {type(command).__name__}")  # pragma: no cover

    def _ls(self, cmd: Ls) -> RenderableType:
        loc = self.navigator.resolve(self.cwd, cmd.path)
        needle = (cmd.filter or "").casefold()
        if loc.course_id is None:
            courses = [c for c in self.navigator.courses() if not needle or c.matches(needle)]
            return render.courses_table(courses)
        section = self.navigator.section(loc)
        if section is None:
            sections = self.navigator.course(loc.course_id).sections
            return render.sections_table([s for s in sections if needle in s.name.casefold()])
        items = [
            (i, item)
            for i, item in enumerate(section.items, start=1)
            if needle in item.name.casefold() and (not cmd.dirs or item.is_container)
        ]
        return render.items_table(items)

    def _tree(self, cmd: Tree) -> RenderableType:
        loc = self.navigator.resolve(self.cwd, cmd.path)
        if loc.course_id is None:
            return render.courses_tree(self.navigator.courses())
        section = self.navigator.section(loc)
        if section is None:
            return render.course_tree(
                self.navigator.course(loc.course_id), depth=cmd.depth, dirs_only=cmd.dirs
            )
        return render.section_tree(section, depth=cmd.depth, dirs_only=cmd.dirs)

    def _mkdir(self, cmd: Mkdir) -> RenderableType:
        loc = self.navigator.resolve(self.cwd, cmd.into)
        if loc.course_id is None:
            raise InvalidOperationError("creating courses is not supported: enter a course first")
        parent = self.navigator.section(loc)
        try:
            if parent is None:
                created = self.backend.add_section(loc.course_id, cmd.at, cmd.name)
                message = f"{self._verb('created')} section {created.number} '{created.name}'"
            else:
                created = self.backend.add_subsection(loc.course_id, parent.id, cmd.at, cmd.name)
                message = f"{self._verb('created')} subsection '{created.name}' in '{parent.name}'"
        finally:
            self.navigator.invalidate(loc.course_id)
        return Text(message, style=self._style)

    def _rename(self, cmd: Rename) -> RenderableType:
        loc = self.navigator.resolve(self.cwd, cmd.path)
        section = self.navigator.section(loc)
        if loc.course_id is None or section is None:
            raise InvalidOperationError("only sections and subsections can be renamed")
        try:
            self.backend.rename_section(loc.course_id, section.id, cmd.name)
        finally:
            self.navigator.invalidate(loc.course_id)
        return Text(f"{self._verb('renamed')} '{section.name}' to '{cmd.name}'", style=self._style)

    def _verb(self, past: str) -> str:
        """'created' for real changes, 'would create' in a dry run."""
        return f"would {past.removesuffix('d')}" if self.dry_run else past

    @property
    def _style(self) -> str:
        return "yellow" if self.dry_run else "green"

    @staticmethod
    def _help(name: str | None) -> RenderableType:
        if name is None:
            return render.help_overview()
        cls = lookup(name)
        if cls is None:
            raise UsageError(f"unknown command '{name}'")
        return render.help_command(cls.spec)

    def _still_valid(self, location: Location) -> Location:
        """Move up until the location exists again (after a refresh or external changes)."""
        while not location.is_root:
            try:
                self.navigator.display(location)
                self.navigator.section(location)
                return location
            except NotFoundError:
                location = location.parent()
        return location
