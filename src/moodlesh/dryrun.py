"""Dry-run backend: reads from a real backend, simulates every write in memory.

Each course is read from the real backend the first time it is needed and copied
into an `InMemoryBackend`; from then on reads and writes for that course go to
the copy. Later commands therefore see earlier simulated changes (you can `cd`
into a section a script has just "created"), while Moodle is never modified.

Permission problems that can be detected with reads only (e.g. a course the
user cannot edit) are still reported, through the real backend's
`check_writable`, so a dry run fails where the real run would.
"""

from __future__ import annotations

from moodlesh.core.backend import CourseBackend
from moodlesh.core.models import Course, CourseSummary, Section
from moodlesh.memory import InMemoryBackend


class DryRunBackend(CourseBackend):
    """`CourseBackend` that never writes to the backend it wraps."""

    def __init__(self, real: CourseBackend) -> None:
        self._real = real
        # Negative ids can never collide with the ids of real Moodle objects.
        self._copy = InMemoryBackend(first_id=-1, id_step=-1)
        self._loaded: set[int] = set()

    def list_courses(self, name_filter: str | None = None) -> list[CourseSummary]:
        return self._real.list_courses(name_filter)

    def get_course(self, course_id: int) -> Course:
        if course_id not in self._loaded:
            self._copy.load(self._real.get_course(course_id))
            self._loaded.add(course_id)
        return self._copy.get_course(course_id)

    def add_section(self, course_id: int, position: int | None = None, name: str | None = None) -> Section:
        self._preflight(course_id, needs_rename=name is not None)
        return self._copy.add_section(course_id, position, name)

    def add_subsection(
        self,
        course_id: int,
        parent_section_id: int,
        position: int | None = None,
        name: str | None = None,
    ) -> Section:
        self._preflight(course_id, needs_rename=name is not None)
        return self._copy.add_subsection(course_id, parent_section_id, position, name)

    def rename_section(self, course_id: int, section_id: int, name: str) -> None:
        self._preflight(course_id, needs_rename=True)
        self._copy.rename_section(course_id, section_id, name)

    def _preflight(self, course_id: int, *, needs_rename: bool) -> None:
        self.get_course(course_id)
        check = getattr(self._real, "check_writable", None)
        if check is not None:
            check(course_id, needs_rename=needs_rename)
