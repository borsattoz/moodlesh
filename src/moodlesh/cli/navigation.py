"""Filesystem-like navigation through courses, plus a cache of what was read.

Path hierarchy:

    /                          root: the list of courses
    /<course>                  a course (numeric id, or short/full name)
    /<course>/<section>        a section (section number or name)
    /<course>/<section>/<sub>  a subsection (position in the section, or name)

Numeric segments always mean what `ls` shows in the # column.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import TypeVar

from moodlesh.core.backend import CourseBackend
from moodlesh.core.errors import NotFoundError
from moodlesh.core.models import Activity, Course, CourseSummary, Section, Subsection

T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class Location:
    """A location in the hierarchy: no course = root."""

    course_id: int | None = None
    section_ids: tuple[int, ...] = ()

    @property
    def is_root(self) -> bool:
        return self.course_id is None

    @property
    def is_course(self) -> bool:
        return self.course_id is not None and not self.section_ids

    def parent(self) -> Location:
        if self.section_ids:
            return Location(self.course_id, self.section_ids[:-1])
        return ROOT

    def child(self, section_id: int) -> Location:
        return Location(self.course_id, (*self.section_ids, section_id))


ROOT = Location()


@dataclass(frozen=True, slots=True)
class Entry:
    """A child of a location, for listings and tab completion."""

    key: str  # the number to use in paths
    name: str
    is_container: bool
    aliases: tuple[str, ...] = ()  # other valid names (e.g. the course full name)


class Navigator:
    """Resolve paths and cache the courses and structures read from the backend."""

    def __init__(self, backend: CourseBackend) -> None:
        self.backend = backend
        self._courses: list[CourseSummary] | None = None
        self._trees: dict[int, Course] = {}

    # ------------------------------------------------------------------- cache
    def courses(self) -> list[CourseSummary]:
        if self._courses is None:
            self._courses = self.backend.list_courses()
        return self._courses

    def course(self, course_id: int) -> Course:
        if course_id not in self._trees:
            self._trees[course_id] = self.backend.get_course(course_id)
        return self._trees[course_id]

    def invalidate(self, course_id: int | None = None) -> None:
        """Discard the cache of one course, or everything if `course_id` is None."""
        if course_id is None:
            self._courses = None
            self._trees.clear()
        else:
            self._trees.pop(course_id, None)

    # ---------------------------------------------------------------- resolution
    def section(self, location: Location) -> Section | None:
        """The section `location` points to, or None for the root and for courses."""
        if location.course_id is None or not location.section_ids:
            return None
        section = self.course(location.course_id).find_section(location.section_ids[-1])
        if section is None:
            raise NotFoundError("the current section no longer exists: use cd .. or refresh")
        return section

    def resolve(self, base: Location, path: str | None) -> Location:
        """Resolve `path` (absolute or relative to `base`) into a location.

        Raises:
            NotFoundError: if a segment does not match any item.
        """
        if not path:
            return base
        location = ROOT if path.startswith("/") else base
        for segment in (s for s in path.split("/") if s and s != "."):
            location = location.parent() if segment == ".." else self._step(location, segment)
        return location

    def _step(self, location: Location, segment: str) -> Location:
        if location.course_id is None:
            return Location(self._resolve_course(segment).id)
        section = self.section(location)
        if section is None:
            target = self._resolve_top_section(self.course(location.course_id), segment)
        else:
            target = self._resolve_subsection(section, segment)
        return location.child(target.id)

    def _resolve_course(self, segment: str) -> CourseSummary:
        courses = self.courses()
        if segment.isdigit():
            course_id = int(segment)
            found = next((c for c in courses if c.id == course_id), None)
            # A course may be missing from the listing and still be accessible.
            return found or self.course(course_id).summary
        return _match(
            segment,
            courses,
            keys=(lambda c: c.shortname, lambda c: c.fullname),
            describe=lambda c: f"{c.shortname} ({c.id})",
            what="no course",
        )

    @staticmethod
    def _resolve_top_section(course: Course, segment: str) -> Section:
        if segment.isdigit():
            section = course.section_by_number(int(segment))
            if section is None:
                raise NotFoundError(f"the course has no section {segment}")
            return section
        return _match(
            segment,
            course.sections,
            keys=(lambda s: s.name,),
            describe=lambda s: f"{s.number} {s.name}",
            what="no section",
        )

    @staticmethod
    def _resolve_subsection(section: Section, segment: str) -> Section:
        if segment.isdigit():
            index = int(segment)
            if not 1 <= index <= len(section.items):
                raise NotFoundError(f"'{section.name}' has no item {segment}")
            item = section.items[index - 1]
        else:
            item = _match(
                segment,
                section.items,
                keys=(lambda i: i.name,),
                describe=lambda i: i.name,
                what="no item",
            )
        if isinstance(item, Activity):
            raise NotFoundError(f"'{item.name}' is an activity ({item.modname}), not a section")
        return item.section

    # -------------------------------------------------------------- presentation
    def display(self, location: Location) -> str:
        if location.course_id is None:
            return "/"
        course = self.course(location.course_id)
        parts = [course.summary.shortname or str(course.id)]
        parts += [self._section_name(course, sid) for sid in location.section_ids]
        return "/" + "/".join(parts)

    @staticmethod
    def _section_name(course: Course, section_id: int) -> str:
        section = course.find_section(section_id)
        return section.name if section else f"#{section_id}"

    def entries(self, location: Location) -> list[Entry]:
        """The children of a location (for tab completion)."""
        if location.course_id is None:
            return [Entry(str(c.id), c.shortname, True, aliases=(c.fullname,)) for c in self.courses()]
        section = self.section(location)
        if section is None:
            return [Entry(str(s.number), s.name, True) for s in self.course(location.course_id).sections]
        return [
            Entry(str(i), item.name, isinstance(item, Subsection))
            for i, item in enumerate(section.items, start=1)
        ]


def _match(
    segment: str,
    candidates: Sequence[T],
    *,
    keys: tuple[Callable[[T], str], ...],
    describe: Callable[[T], str],
    what: str,
) -> T:
    """Find the candidate whose name matches `segment` (case-insensitively)."""
    needle = segment.casefold()
    for key in keys:
        found = [c for c in candidates if key(c).casefold() == needle]
        if len(found) == 1:
            return found[0]
        if len(found) > 1:
            options = ", ".join(describe(c) for c in found)
            raise NotFoundError(f"'{segment}' is ambiguous ({options}): use the number")
    raise NotFoundError(f"{what} named '{segment}'")
