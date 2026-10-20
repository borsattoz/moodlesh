"""Run one script on many courses.

The script runs once per selected course, starting from that course's root, so
relative paths (`cd 1`, `mkdir "Labs" --at 2`, `--into Practice`) apply to
every course in the same way.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass

from rich.console import Group, RenderableType
from rich.rule import Rule
from rich.table import Table
from rich.text import Text

from moodlesh.cli.errors import ExitRequested, UsageError
from moodlesh.cli.interpreter import Interpreter, ScriptError
from moodlesh.cli.navigation import Location
from moodlesh.core.backend import CourseBackend
from moodlesh.core.models import CourseSummary


@dataclass(frozen=True, slots=True)
class CourseSelection:
    """Which courses a bulk run targets.

    `courses` adds courses explicitly (by id, short name or full name). `categories`
    (id or name) and `match` (text in the name) select among the courses the
    user can edit; when both are given a course must satisfy both.
    """

    courses: tuple[str, ...] = ()
    categories: tuple[str, ...] = ()
    match: str | None = None

    @property
    def active(self) -> bool:
        return bool(self.courses or self.categories or self.match)


@dataclass(frozen=True, slots=True)
class CourseOutcome:
    course: CourseSummary
    error: ScriptError | None = None
    skipped: bool = False

    @property
    def ok(self) -> bool:
        return self.error is None and not self.skipped


def select_courses(backend: CourseBackend, selection: CourseSelection) -> list[CourseSummary]:
    """Resolve a selection into a list of courses, in listing order and without duplicates.

    Raises:
        UsageError: a `--course` matches nothing or several courses, or nothing is selected.
    """
    available = backend.list_courses()
    chosen: dict[int, CourseSummary] = {}
    for spec in selection.courses:
        course = _resolve_course(spec, available)
        chosen[course.id] = course
    if selection.categories or selection.match:
        for course in available:
            if _in_categories(course, selection.categories) and (
                selection.match is None or course.matches(selection.match)
            ):
                chosen.setdefault(course.id, course)
    if not chosen:
        raise UsageError("no course matches the selection")
    order = {c.id: i for i, c in enumerate(available)}
    return sorted(chosen.values(), key=lambda c: order.get(c.id, len(order)))


def run_on_courses(
    backend: CourseBackend,
    text: str,
    courses: Sequence[CourseSummary],
    *,
    emit: Callable[[RenderableType], None],
    dry_run: bool = False,
    keep_going: bool = False,
) -> list[CourseOutcome]:
    """Run `text` on each course; stop at the first failing course unless `keep_going`."""
    outcomes: list[CourseOutcome] = []
    for index, course in enumerate(courses):
        emit(Rule(f"{course.shortname} · {course.fullname}", align="left", style="dim"))
        interpreter = Interpreter(backend, dry_run=dry_run, start=Location(course.id))
        try:
            for output in interpreter.run(text):
                emit(output)
        except ExitRequested:
            pass
        except ScriptError as exc:
            emit(Text(f"error: {exc}", style="bold red"))
            outcomes.append(CourseOutcome(course, error=exc))
            if not keep_going:
                outcomes.extend(CourseOutcome(c, skipped=True) for c in courses[index + 1 :])
                break
            continue
        outcomes.append(CourseOutcome(course))
    return outcomes


def summary_table(outcomes: Iterable[CourseOutcome], *, dry_run: bool = False) -> RenderableType:
    outcomes = list(outcomes)
    table = Table(box=None, header_style="bold", pad_edge=False)
    table.add_column("course", style="bold")
    table.add_column("result")
    for outcome in outcomes:
        if outcome.skipped:
            result = Text("skipped", style="dim")
        elif outcome.error is not None:
            result = Text(f"failed: {outcome.error}", style="red")
        else:
            result = Text("ok (dry run)" if dry_run else "ok", style="green")
        table.add_row(outcome.course.shortname, result)
    done = sum(o.ok for o in outcomes)
    title = Text(f"\n{done} of {len(outcomes)} courses ok" + (" — nothing was changed" if dry_run else ""))
    return Group(title, table)


def _resolve_course(spec: str, available: Sequence[CourseSummary]) -> CourseSummary:
    if spec.isdigit():
        found = [c for c in available if c.id == int(spec)]
    else:
        needle = spec.casefold()
        found = [c for c in available if needle in (c.shortname.casefold(), c.fullname.casefold())]
    if not found:
        raise UsageError(f"--course '{spec}': no editable course with that id or name")
    if len(found) > 1:
        names = ", ".join(f"{c.shortname} ({c.id})" for c in found)
        raise UsageError(f"--course '{spec}' is ambiguous ({names}): use the id")
    return found[0]


def _in_categories(course: CourseSummary, categories: Sequence[str]) -> bool:
    if not categories:
        return True
    for spec in categories:
        if spec.isdigit() and course.category_id == int(spec):
            return True
        if course.category.casefold() == spec.casefold():
            return True
    return False
