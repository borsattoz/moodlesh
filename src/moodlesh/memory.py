"""In-memory backend: for the tests and for trying the shell without Moodle (`--demo`).

It reproduces the Moodle rules that matter for the structure: contiguous section
numbering (0 = general), delegated sections numbered after the top-level ones,
no subsections inside subsections.
"""

from __future__ import annotations

import itertools
from collections.abc import Iterator
from dataclasses import dataclass, field

from moodlesh.core.backend import item_insertion_before, section_insertion_target
from moodlesh.core.errors import InvalidOperationError, NotFoundError
from moodlesh.core.models import Activity, Course, CourseSummary, Section, SectionItem, Subsection


@dataclass
class _Act:
    cmid: int
    name: str
    modname: str


@dataclass
class _Sec:
    id: int
    name: str
    items: list[_Act | _Sub] = field(default_factory=list)
    delegated: bool = False

    def walk(self) -> Iterator[_Sec]:
        yield self
        for item in self.items:
            if isinstance(item, _Sub):
                yield from item.section.walk()


@dataclass
class _Sub:
    cmid: int
    section: _Sec


@dataclass
class _Course:
    summary: CourseSummary
    sections: list[_Sec] = field(default_factory=list)

    def find(self, section_id: int) -> _Sec | None:
        return next((s for top in self.sections for s in top.walk() if s.id == section_id), None)


class InMemoryBackend:
    """A fully in-memory `CourseBackend` implementation."""

    def __init__(self, *, first_id: int = 1000, id_step: int = 1) -> None:
        self._courses: dict[int, _Course] = {}
        self._ids = itertools.count(first_id, id_step)

    # ------------------------------------------------------------- seeding data
    def create_course(
        self,
        course_id: int,
        shortname: str,
        fullname: str,
        category: str = "",
        category_id: int | None = None,
    ) -> int:
        summary = CourseSummary(course_id, shortname, fullname, category, "topics", category_id)
        self._courses[course_id] = _Course(summary, [_Sec(next(self._ids), "General")])
        return course_id

    def load(self, course: Course) -> None:
        """Copy an existing course (e.g. read from Moodle), keeping its ids."""
        self._courses[course.id] = _Course(course.summary, [_from_model(s) for s in course.sections])

    def seed_section(self, course_id: int, name: str) -> int:
        section = _Sec(next(self._ids), name)
        self._course(course_id).sections.append(section)
        return section.id

    def seed_activity(self, course_id: int, section_id: int, name: str, modname: str) -> int:
        cmid = next(self._ids)
        self._section(course_id, section_id).items.append(_Act(cmid, name, modname))
        return cmid

    def seed_subsection(self, course_id: int, section_id: int, name: str) -> int:
        delegated = _Sec(next(self._ids), name, delegated=True)
        self._section(course_id, section_id).items.append(_Sub(next(self._ids), delegated))
        return delegated.id

    # ------------------------------------------------------------ CourseBackend
    def list_courses(self, name_filter: str | None = None) -> list[CourseSummary]:
        summaries = [c.summary for c in self._courses.values()]
        return [s for s in summaries if not name_filter or s.matches(name_filter)]

    def get_course(self, course_id: int) -> Course:
        course = self._course(course_id)
        numbers = itertools.count(len(course.sections))  # delegated sections come after the regular ones
        sections = tuple(self._to_model(sec, i, numbers) for i, sec in enumerate(course.sections))
        return Course(course.summary, sections)

    def add_section(self, course_id: int, position: int | None = None, name: str | None = None) -> Section:
        target = section_insertion_target(self.get_course(course_id), position)
        sections = self._course(course_id).sections
        index = len(sections) if target is None else target.number + 1
        new = _Sec(next(self._ids), name or f"Topic {index}")
        sections.insert(index, new)
        return self._find_model(course_id, new.id)

    def add_subsection(
        self,
        course_id: int,
        parent_section_id: int,
        position: int | None = None,
        name: str | None = None,
    ) -> Section:
        parent_model = self._find_model(course_id, parent_section_id)
        before = item_insertion_before(parent_model, position)
        parent = self._section(course_id, parent_section_id)
        index = len(parent.items) if before is None else [i.cmid for i in parent.items].index(before)
        delegated = _Sec(next(self._ids), name or "New subsection", delegated=True)
        parent.items.insert(index, _Sub(next(self._ids), delegated))
        return self._find_model(course_id, delegated.id)

    def rename_section(self, course_id: int, section_id: int, name: str) -> None:
        if not name.strip():
            raise InvalidOperationError("the name cannot be empty")
        self._section(course_id, section_id).name = name

    # ------------------------------------------------------------------ helpers
    def _course(self, course_id: int) -> _Course:
        try:
            return self._courses[course_id]
        except KeyError:
            raise NotFoundError(f"course {course_id} does not exist") from None

    def _section(self, course_id: int, section_id: int) -> _Sec:
        section = self._course(course_id).find(section_id)
        if section is None:
            raise NotFoundError(f"section {section_id} does not exist in course {course_id}")
        return section

    def _find_model(self, course_id: int, section_id: int) -> Section:
        section = self.get_course(course_id).find_section(section_id)
        if section is None:  # pragma: no cover - internal consistency
            raise NotFoundError(f"section {section_id} does not exist in course {course_id}")
        return section

    def _to_model(self, sec: _Sec, number: int, delegated_numbers: Iterator[int]) -> Section:
        items: list[SectionItem] = []
        for item in sec.items:
            if isinstance(item, _Act):
                items.append(Activity(item.cmid, item.name, item.modname))
            else:
                sub = self._to_model(item.section, next(delegated_numbers), delegated_numbers)
                items.append(Subsection(item.cmid, sub))
        return Section(sec.id, number, sec.name, tuple(items), delegated=sec.delegated)


def _from_model(section: Section) -> _Sec:
    items: list[_Act | _Sub] = []
    for item in section.items:
        if isinstance(item, Subsection):
            items.append(_Sub(item.cmid, _from_model(item.section)))
        else:
            items.append(_Act(item.cmid, item.name, item.modname))
    return _Sec(section.id, section.name, items, delegated=section.delegated)


def demo_backend() -> InMemoryBackend:
    """Backend preloaded with a few sample courses."""
    backend = InMemoryBackend()

    phy = backend.create_course(101, "PHY1", "Physics I", "Science", 1)
    backend.seed_activity(phy, backend.get_course(phy).sections[0].id, "Announcements", "forum")
    kin = backend.seed_section(phy, "Kinematics")
    backend.seed_activity(phy, kin, "Linear motion notes", "resource")
    ex = backend.seed_subsection(phy, kin, "Exercises")
    backend.seed_activity(phy, ex, "Worked examples", "page")
    backend.seed_activity(phy, ex, "Self-assessment quiz", "quiz")
    backend.seed_activity(phy, kin, "Lab report", "assign")
    dyn = backend.seed_section(phy, "Dynamics")
    backend.seed_activity(phy, dyn, "Newton's laws", "resource")
    backend.seed_subsection(phy, dyn, "Extras")

    calc = backend.create_course(102, "CALC1", "Calculus I", "Science", 1)
    lim = backend.seed_section(calc, "Limits")
    backend.seed_activity(calc, lim, "Theory of limits", "book")
    backend.seed_section(calc, "Derivatives")

    backend.create_course(201, "HIST", "Modern History", "Humanities", 2)
    return backend
