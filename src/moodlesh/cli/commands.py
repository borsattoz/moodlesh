"""DSL commands: typed data, specs and validation (binding).

Each command is an immutable dataclass. Its `CommandSpec` describes positional
arguments and options: validation, help and tab completion are all derived from
it. Adding a command only takes the dataclass, its spec and the matching branch
in the interpreter.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, ClassVar

from moodlesh.cli.dsl.ast import Argument, Option, RawCommand, Value
from moodlesh.cli.errors import UsageError


class Completion(Enum):
    """What to suggest for an argument during tab completion."""

    NONE = "none"
    PATH = "path"
    COMMAND = "command"


@dataclass(frozen=True, slots=True)
class Param:
    """Positional argument."""

    name: str
    help: str
    required: bool = True
    completion: Completion = Completion.NONE
    metavar: str | None = None

    @property
    def display(self) -> str:
        return self.metavar or self.name.upper()


@dataclass(frozen=True, slots=True)
class Opt:
    """Option. If `type` is None it is a boolean flag."""

    name: str
    help: str
    short: str | None = None
    type: type[int] | type[str] | None = str
    metavar: str = "VALUE"
    min_value: int | None = None
    completion: Completion = Completion.NONE

    @property
    def dest(self) -> str:
        return self.name.replace("-", "_")

    @property
    def is_flag(self) -> bool:
        return self.type is None


@dataclass(frozen=True, slots=True)
class CommandSpec:
    name: str
    summary: str
    params: tuple[Param, ...] = ()
    options: tuple[Opt, ...] = ()
    aliases: tuple[str, ...] = ()
    examples: tuple[str, ...] = ()

    def usage(self) -> str:
        parts = [self.name]
        for p in self.params:
            parts.append(p.display if p.required else f"[{p.display}]")
        for o in self.options:
            flag = f"-{o.short}" if o.short else f"--{o.name}"
            parts.append(f"[{flag}]" if o.is_flag else f"[{flag} {o.metavar}]")
        return " ".join(parts)

    def find_option(self, name: str) -> Opt | None:
        return next((o for o in self.options if name in (o.name, o.short)), None)


class Command:
    """Base class of the commands. Each subclass defines `spec`."""

    spec: ClassVar[CommandSpec]


# --------------------------------------------------------------------------- #
# Commands
# --------------------------------------------------------------------------- #
_PATH = Param(
    "path", "path (absolute or relative)", required=False, completion=Completion.PATH, metavar="PATH"
)
_FILTER = Opt("filter", "show only the items whose name contains the text", short="f", metavar="TEXT")


@dataclass(frozen=True, slots=True)
class Ls(Command):
    path: str | None = None
    filter: str | None = None
    dirs: bool = False

    spec = CommandSpec(
        "ls",
        "List courses, sections or the contents of a section",
        params=(_PATH,),
        options=(_FILTER, Opt("dirs", "show only sections and subsections", short="d", type=None)),
        examples=("ls", "ls -f physics", "ls /PHY1/1 --dirs"),
    )


@dataclass(frozen=True, slots=True)
class Cd(Command):
    path: str | None = None

    spec = CommandSpec(
        "cd",
        "Change location (with no argument, go back to the root)",
        params=(_PATH,),
        examples=("cd PHY1", "cd 1", "cd Exercises", "cd ..", "cd /101/2"),
    )


@dataclass(frozen=True, slots=True)
class Pwd(Command):
    spec = CommandSpec("pwd", "Show the current location")


@dataclass(frozen=True, slots=True)
class Tree(Command):
    path: str | None = None
    depth: int | None = None
    dirs: bool = False

    spec = CommandSpec(
        "tree",
        "Show the tree structure of a course or section",
        params=(_PATH,),
        options=(
            Opt("depth", "maximum depth", short="L", type=int, metavar="N", min_value=1),
            Opt("dirs", "show only sections and subsections", short="d", type=None),
        ),
        examples=("tree", "tree -L 2", "tree /PHY1 -d"),
    )


@dataclass(frozen=True, slots=True)
class Mkdir(Command):
    name: str
    at: int | None = None
    into: str | None = None

    spec = CommandSpec(
        "mkdir",
        "Create a section (inside a course) or a subsection (inside a section)",
        params=(Param("name", "name of the new section", metavar="NAME"),),
        options=(
            Opt("at", "position (as shown by ls); default: at the end", type=int, metavar="N", min_value=1),
            Opt(
                "into",
                "where to create it; default: the current location",
                short="i",
                metavar="PATH",
                completion=Completion.PATH,
            ),
        ),
        examples=('mkdir "Thermodynamics" --at 3', "mkdir Exercises --into /PHY1/2 --at 1"),
    )


@dataclass(frozen=True, slots=True)
class Rename(Command):
    path: str
    name: str

    spec = CommandSpec(
        "rename",
        "Rename a section or subsection",
        params=(
            Param("path", "section to rename", completion=Completion.PATH, metavar="PATH"),
            Param("name", "new name", metavar="NAME"),
        ),
        aliases=("mv",),
        examples=('rename 2 "Point dynamics"', 'rename Exercises "Practice"'),
    )


@dataclass(frozen=True, slots=True)
class Refresh(Command):
    spec = CommandSpec("refresh", "Reload the data from Moodle (discard the cache)")


@dataclass(frozen=True, slots=True)
class Help(Command):
    command: str | None = None

    spec = CommandSpec(
        "help",
        "Show the list of commands, or the help for one command",
        params=(
            Param(
                "command",
                "command name",
                required=False,
                completion=Completion.COMMAND,
                metavar="COMMAND",
            ),
        ),
    )


@dataclass(frozen=True, slots=True)
class Exit(Command):
    spec = CommandSpec("exit", "Leave the shell", aliases=("quit",))


COMMANDS: tuple[type[Command], ...] = (Ls, Cd, Pwd, Tree, Mkdir, Rename, Refresh, Help, Exit)
_BY_NAME: dict[str, type[Command]] = {
    name: cls for cls in COMMANDS for name in (cls.spec.name, *cls.spec.aliases)
}


def command_names() -> list[str]:
    return sorted(_BY_NAME)


def lookup(name: str) -> type[Command] | None:
    return _BY_NAME.get(name)


# --------------------------------------------------------------------------- #
# Binding
# --------------------------------------------------------------------------- #
def bind(raw: RawCommand) -> Command:
    """Validate a raw command against its spec and return the typed command.

    Raises:
        UsageError: unknown command, missing or extra arguments, invalid values.
    """
    cls = lookup(raw.name)
    if cls is None:
        raise UsageError(f"unknown command '{raw.name}': type help for the list")
    spec = cls.spec
    kwargs: dict[str, Any] = {}
    positionals: list[str] = []

    pending = list(raw.args)
    while pending:
        arg = pending.pop(0)
        if isinstance(arg, Value):
            positionals.append(arg.text)
            continue
        opt = spec.find_option(arg.name)
        if opt is None:
            raise UsageError(f"{spec.name}: unknown option '{_display(arg)}'\nusage: {spec.usage()}")
        if opt.dest in kwargs:
            raise UsageError(f"{spec.name}: option '{_display(arg)}' given twice")
        kwargs[opt.dest] = _option_value(spec, opt, arg, pending)

    kwargs.update(_bind_positionals(spec, positionals))
    return cls(**kwargs)


def _option_value(spec: CommandSpec, opt: Opt, arg: Option, pending: list[Argument]) -> bool | int | str:
    """The value of an option: True for flags, otherwise the inline value or the next argument."""
    if opt.is_flag:
        if arg.inline_value is not None:
            raise UsageError(f"{spec.name}: option '{_display(arg)}' takes no value")
        return True
    if arg.inline_value is not None:
        return _convert(spec, opt, arg.inline_value.text)
    if pending and isinstance(following := pending[0], Value):
        pending.pop(0)
        return _convert(spec, opt, following.text)
    raise UsageError(f"{spec.name}: option '{_display(arg)}' requires {opt.metavar}")


def _bind_positionals(spec: CommandSpec, positionals: list[str]) -> dict[str, str]:
    if len(positionals) > len(spec.params):
        extra = positionals[len(spec.params)]
        raise UsageError(f"{spec.name}: unexpected argument '{extra}'\nusage: {spec.usage()}")
    missing = [p.display for p in spec.params[len(positionals) :] if p.required]
    if missing:
        raise UsageError(f"{spec.name}: missing {', '.join(missing)}\nusage: {spec.usage()}")
    return {param.name: text for param, text in zip(spec.params, positionals, strict=False)}


def _convert(spec: CommandSpec, opt: Opt, text: str) -> int | str:
    if opt.type is int:
        try:
            number = int(text)
        except ValueError:
            raise UsageError(f"{spec.name}: --{opt.name} requires an integer, not '{text}'") from None
        if opt.min_value is not None and number < opt.min_value:
            raise UsageError(f"{spec.name}: --{opt.name} must be at least {opt.min_value}")
        return number
    return text


def _display(opt: Option) -> str:
    return f"-{opt.name}" if len(opt.name) == 1 else f"--{opt.name}"
