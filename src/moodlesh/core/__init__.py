"""Domain model, backend contract and exceptions."""

from moodlesh.core.backend import CourseBackend, item_insertion_before, section_insertion_target
from moodlesh.core.errors import (
    AuthenticationError,
    BackendError,
    ConnectionLostError,
    InvalidOperationError,
    MoodleshError,
    NotFoundError,
    PartialChangeError,
    SessionExpiredError,
)
from moodlesh.core.models import Activity, Course, CourseSummary, Section, SectionItem, Subsection

__all__ = [
    "Activity",
    "AuthenticationError",
    "BackendError",
    "ConnectionLostError",
    "Course",
    "CourseBackend",
    "CourseSummary",
    "InvalidOperationError",
    "MoodleshError",
    "NotFoundError",
    "PartialChangeError",
    "Section",
    "SectionItem",
    "SessionExpiredError",
    "Subsection",
    "item_insertion_before",
    "section_insertion_target",
]
