"""DSL syntax tree: generic commands, before validation."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Position:
    """Position in the source text (1-based), for error messages."""

    line: int
    column: int


@dataclass(frozen=True, slots=True)
class Value:
    """A value: a bare word or a quoted string (quotes already removed)."""

    text: str
    quoted: bool
    pos: Position


@dataclass(frozen=True, slots=True)
class Option:
    """An option, with its value if written as `--name=value`."""

    name: str
    pos: Position
    inline_value: Value | None = None


Argument = Value | Option


@dataclass(frozen=True, slots=True)
class RawCommand:
    """A command as written: name and arguments, in order."""

    name: str
    args: tuple[Argument, ...]
    pos: Position
