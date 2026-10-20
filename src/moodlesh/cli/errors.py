"""CLI-specific errors (syntax and command usage)."""

from __future__ import annotations

from moodlesh.core.errors import MoodleshError


class CliError(MoodleshError):
    """Error caused by what the user typed."""


class DslSyntaxError(CliError):
    """The text does not follow the DSL grammar."""

    def __init__(self, message: str, *, line: int, column: int, source: str = "") -> None:
        super().__init__(message)
        self.message = message
        self.line = line
        self.column = column
        self.source = source

    def __str__(self) -> str:
        return f"line {self.line}, column {self.column}: {self.message}"

    def caret(self) -> str:
        """The offending line with a ^ marker under the error column."""
        lines = self.source.splitlines()
        if not 1 <= self.line <= len(lines):
            return ""
        return f"{lines[self.line - 1]}\n{' ' * (self.column - 1)}^"


class UsageError(CliError):
    """Syntactically valid command used incorrectly (arguments, options)."""


class ExitRequested(Exception):  # noqa: N818 - a control signal, not an error
    """Raised by the `exit` command to end the shell."""
