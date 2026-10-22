"""Exception hierarchy of the library."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from moodlesh.core.models import Section


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


class PartialChangeError(BackendError):
    """An operation stopped after it had already changed Moodle.

    For example a section was created but could not be named. `created` is the
    item that now exists in Moodle, or `None` if it cannot be identified.
    """

    def __init__(self, message: str, *, created: Section | None = None) -> None:
        super().__init__(message)
        self.created = created
