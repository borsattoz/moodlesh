"""Exception hierarchy of the library."""

from __future__ import annotations


class MoodleshError(Exception):
    """Root of all moodlesh exceptions."""


class NotFoundError(MoodleshError):
    """The course, section or item does not exist."""


class InvalidOperationError(MoodleshError):
    """Operation not allowed by the course structure (e.g. an invalid position)."""


class BackendError(MoodleshError):
    """Error returned by Moodle or by the transport."""

    def __init__(self, message: str, *, function: str | None = None, errorcode: str | None = None) -> None:
        prefix = f"{function}: " if function else ""
        suffix = f" [{errorcode}]" if errorcode else ""
        super().__init__(f"{prefix}{message}{suffix}")
        self.function = function
        self.errorcode = errorcode


class AuthenticationError(BackendError):
    """Expired session, or invalid sesskey or token."""
