"""The contract every backend must honour, plus shared positioning rules.

Position conventions (always 1-based, as shown by `ls`):

* top-level sections: the position is the number the new section will have in
  the course. Section 0 (general) cannot move, so valid positions go from 1 to
  `last_section_number + 1`;
* items of a section (activities and subsections): the position is the index
  the new item will have in the section, from 1 to `item_count + 1`.

`None` always means "at the end".
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from moodlesh.core.errors import InvalidOperationError
from moodlesh.core.models import Course, CourseSummary, Section


@runtime_checkable
class CourseBackend(Protocol):
    """Course operations, independent of how we talk to Moodle."""

    def list_courses(self, name_filter: str | None = None) -> list[CourseSummary]:
        """List the visible courses, filtered by name if `name_filter` is given."""
        ...

    def get_course(self, course_id: int) -> Course:
        """Return the full structure of a course."""
        ...

    def add_section(self, course_id: int, position: int | None = None, name: str | None = None) -> Section:
        """Add a top-level section and return it."""
        ...

    def add_subsection(
        self,
        course_id: int,
        parent_section_id: int,
        position: int | None = None,
        name: str | None = None,
    ) -> Section:
        """Add a subsection to a section and return the delegated section created."""
        ...

    def rename_section(self, course_id: int, section_id: int, name: str) -> None:
        """Rename a section (top-level or delegated)."""
        ...


def section_insertion_target(course: Course, position: int | None) -> Section | None:
    """The section after which to insert a new section so that it gets number `position`.

    Returns `None` if the section should be appended at the end.

    Raises:
        InvalidOperationError: if the position is not valid.
    """
    if position is None:
        return None
    last = max((s.number for s in course.sections), default=0)
    if not 1 <= position <= last + 1:
        raise InvalidOperationError(f"invalid position {position}: use a value between 1 and {last + 1}")
    if position == last + 1:
        return None
    target = course.section_by_number(position - 1)
    if target is None:  # pragma: no cover - Moodle numbering is always contiguous
        raise InvalidOperationError(f"section {position - 1} does not exist")
    return target


def item_insertion_before(section: Section, position: int | None) -> int | None:
    """The cmid of the item to insert before, or `None` to append.

    Raises:
        InvalidOperationError: if the position is not valid or the section is delegated.
    """
    if section.delegated:
        raise InvalidOperationError("Moodle does not allow subsections inside a subsection")
    count = len(section.items)
    if position is None or position == count + 1:
        return None
    if not 1 <= position <= count + 1:
        raise InvalidOperationError(f"invalid position {position}: use a value between 1 and {count + 1}")
    return section.items[position - 1].cmid
