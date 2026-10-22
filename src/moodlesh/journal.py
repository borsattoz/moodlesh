"""A journal of the changes moodlesh makes, one JSON object per line.

`JournalingBackend` wraps any backend and appends one entry per write, whether
it succeeded or failed, so there is always a record of what was changed, where
and when. Reads are not recorded.

Example entry::

    {"time": "2026-10-20T15:42:07+02:00", "site": "https://moodle.example.org",
     "action": "add_section", "course": 2, "args": {"position": 2, "name": "Optics"},
     "ok": true, "result": {"section_id": 57, "number": 2}}

When an operation stops after it has already changed Moodle (a section created
but not named), the entry has `"ok": false, "partial": true`, and `result`
describes what now exists in Moodle (or is `null` if it cannot be identified).
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any, TypeVar

from moodlesh.core.backend import CourseBackend
from moodlesh.core.errors import PartialChangeError
from moodlesh.core.models import Course, CourseSummary, Section

T = TypeVar("T")


class Journal:
    """Append-only JSON-lines file, readable only by its owner."""

    def __init__(self, path: Path, site: str) -> None:
        self.path = path
        self.site = site

    def record(self, action: str, course_id: int, args: dict[str, Any], **outcome: Any) -> None:
        entry = {
            "time": datetime.now().astimezone().isoformat(timespec="seconds"),
            "site": self.site,
            "action": action,
            "course": course_id,
            "args": args,
            **outcome,
        }
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")


class JournalingBackend:
    """`CourseBackend` decorator that records every write in a `Journal`."""

    def __init__(self, inner: CourseBackend, journal: Journal) -> None:
        self._inner = inner
        self._journal = journal

    def __getattr__(self, name: str) -> Any:  # e.g. check_writable
        return getattr(self._inner, name)

    def list_courses(self, name_filter: str | None = None) -> list[CourseSummary]:
        return self._inner.list_courses(name_filter)

    def get_course(self, course_id: int) -> Course:
        return self._inner.get_course(course_id)

    def add_section(self, course_id: int, position: int | None = None, name: str | None = None) -> Section:
        return self._recorded(
            "add_section",
            course_id,
            {"position": position, "name": name},
            lambda: self._inner.add_section(course_id, position, name),
        )

    def add_subsection(
        self,
        course_id: int,
        parent_section_id: int,
        position: int | None = None,
        name: str | None = None,
    ) -> Section:
        return self._recorded(
            "add_subsection",
            course_id,
            {"parent_section_id": parent_section_id, "position": position, "name": name},
            lambda: self._inner.add_subsection(course_id, parent_section_id, position, name),
        )

    def rename_section(self, course_id: int, section_id: int, name: str) -> None:
        self._recorded(
            "rename_section",
            course_id,
            {"section_id": section_id, "name": name},
            lambda: self._inner.rename_section(course_id, section_id, name),
        )

    def _recorded(self, action: str, course_id: int, args: dict[str, Any], write: Callable[[], T]) -> T:
        try:
            result = write()
        except PartialChangeError as exc:
            # Stopped half-way: Moodle *was* changed, so say what now exists.
            self._journal.record(
                action, course_id, args, ok=False, partial=True, result=_details(exc.created), error=str(exc)
            )
            raise
        except Exception as exc:
            self._journal.record(action, course_id, args, ok=False, error=str(exc))
            raise
        self._journal.record(action, course_id, args, ok=True, result=_details(result))
        return result


def _details(result: object) -> dict[str, int] | None:
    return {"section_id": result.id, "number": result.number} if isinstance(result, Section) else None
