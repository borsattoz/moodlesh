"""Immutable domain model of a Moodle course structure.

A course contains top-level sections. Each section holds an ordered list of
items: activities (`Activity`) or subsections (`Subsection`), which in turn
wrap a "delegated" section with its own items.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class CourseSummary:
    """Essential course data, as returned by a course search."""

    id: int
    shortname: str
    fullname: str
    category: str = ""
    format: str | None = None
    category_id: int | None = None

    def matches(self, text: str) -> bool:
        """True if `text` occurs (case-insensitively) in the short or full name."""
        needle = text.casefold()
        return needle in self.shortname.casefold() or needle in self.fullname.casefold()


@dataclass(frozen=True, slots=True)
class Activity:
    """A course activity or resource (course module)."""

    cmid: int
    name: str
    modname: str
    visible: bool = True

    @property
    def is_container(self) -> bool:
        return False


@dataclass(frozen=True, slots=True)
class Subsection:
    """A subsection: the `mod_subsection` module plus the section it delegates to."""

    cmid: int
    section: Section
    visible: bool = True

    @property
    def name(self) -> str:
        return self.section.name

    @property
    def is_container(self) -> bool:
        return True


SectionItem = Activity | Subsection


@dataclass(frozen=True, slots=True)
class Section:
    """A course section, either top-level or delegated to a subsection."""

    id: int
    number: int
    name: str
    items: tuple[SectionItem, ...] = ()
    visible: bool = True
    delegated: bool = False

    @property
    def is_container(self) -> bool:
        return True

    @property
    def subsections(self) -> tuple[Subsection, ...]:
        return tuple(item for item in self.items if isinstance(item, Subsection))

    def walk(self) -> Iterator[Section]:
        """Iterate over this section and all nested sections (pre-order)."""
        yield self
        for sub in self.subsections:
            yield from sub.section.walk()


@dataclass(frozen=True, slots=True)
class Course:
    """A course together with its full structure."""

    summary: CourseSummary
    sections: tuple[Section, ...] = field(default=())

    @property
    def id(self) -> int:
        return self.summary.id

    @property
    def name(self) -> str:
        return self.summary.fullname

    def all_sections(self) -> Iterator[Section]:
        for section in self.sections:
            yield from section.walk()

    def find_section(self, section_id: int) -> Section | None:
        return next((s for s in self.all_sections() if s.id == section_id), None)

    def section_by_number(self, number: int) -> Section | None:
        return next((s for s in self.sections if s.number == number), None)
