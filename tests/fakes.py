"""A fake Moodle that answers the external functions like the real one.

It uses `InMemoryBackend` as its "database" and produces responses in the
Moodle 4.5 format (in particular the `core_courseformat_get_state` state), so
the client tests also cover the state conversion.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from moodlesh.core.errors import BackendError
from moodlesh.core.models import Activity, Course, Section, Subsection
from moodlesh.memory import InMemoryBackend, demo_backend


@dataclass
class FakeMoodle:
    """Implements the `Transport` protocol, recording the calls it receives."""

    db: InMemoryBackend = field(default_factory=demo_backend)
    legacy_45: bool = False  # simulates a 4.5 without core_courseformat_new_module
    include_format_in_search: bool = False
    not_editable: set[int] = field(default_factory=set)  # courses the user can see but not edit
    calls: list[tuple[str, dict[str, Any]]] = field(default_factory=list)

    def call(self, function: str, args: Mapping[str, Any]) -> Any:
        self.calls.append((function, dict(args)))
        handler = getattr(self, "_" + function, None)
        if handler is None:
            raise BackendError("no such function", function=function, errorcode="invalidrecord")
        return handler(**args)

    def called(self, function: str) -> list[dict[str, Any]]:
        return [args for name, args in self.calls if name == function]

    # --------------------------------------------------------------- functions
    def _core_course_search_courses(
        self, criterianame: str, criteriavalue: str, requiredcapabilities: list[str] | None = None, **_: Any
    ) -> Any:
        assert criterianame == "search"
        courses = []
        for c in self.db.list_courses(criteriavalue or None):
            if requiredcapabilities and c.id in self.not_editable:
                continue
            raw = {
                "id": c.id,
                "shortname": c.shortname,
                "fullname": c.fullname,
                "categoryname": c.category,
                "categoryid": c.category_id,
            }
            if self.include_format_in_search:
                raw["format"] = c.format
            courses.append(raw)
        return {"total": len(courses), "courses": courses, "warnings": []}

    def _core_course_get_courses(self, options: Mapping[str, Any]) -> Any:
        found = {c.id: c for c in self.db.list_courses()}
        return [
            {"id": c.id, "shortname": c.shortname, "fullname": c.fullname, "format": c.format}
            for cid in options["ids"]
            if (c := found.get(cid))
        ]

    def _core_courseformat_get_state(self, courseid: int) -> str:
        return json.dumps(course_to_state(self.db.get_course(courseid)))

    def _core_courseformat_update_course(
        self, action: str, courseid: int, targetsectionid: int | None = None, **_: Any
    ) -> str:
        assert action == "section_add"
        if targetsectionid is None:
            self.db.add_section(courseid)
        else:
            target = self._section(courseid, targetsectionid)
            self.db.add_section(courseid, target.number + 1)  # Moodle inserts *after* the given section
        return "[]"

    def _core_courseformat_new_module(
        self, courseid: int, modname: str, targetsectionid: int, targetcmid: int | None = None
    ) -> str:
        if self.legacy_45:
            # The exact error Moodle 4.5 returns for a function that does not exist.
            raise BackendError(
                "Can't find data record in database table external_functions.", errorcode="invalidrecord"
            )
        return self._add_module(courseid, modname, self._section(courseid, targetsectionid), targetcmid)

    def _core_courseformat_create_module(
        self, courseid: int, modname: str, targetsectionnum: int, targetcmid: int | None = None
    ) -> str:
        section = self.db.get_course(courseid).section_by_number(targetsectionnum)
        assert section is not None
        return self._add_module(courseid, modname, section, targetcmid)

    def _core_update_inplace_editable(self, component: str, itemtype: str, itemid: int, value: str) -> Any:
        assert component == "format_topics"
        assert itemtype == "sectionname"
        course_id = next(
            c.id for c in self.db.list_courses() if self.db.get_course(c.id).find_section(itemid)
        )
        self.db.rename_section(course_id, itemid, value)
        return {"value": value}

    # ----------------------------------------------------------------- helpers
    def _add_module(self, courseid: int, modname: str, section: Section, targetcmid: int | None) -> str:
        assert modname == "subsection"
        position = None
        if targetcmid is not None:
            position = [i.cmid for i in section.items].index(targetcmid) + 1
        self.db.add_subsection(courseid, section.id, position)
        return "[]"

    def _section(self, course_id: int, section_id: int) -> Section:
        section = self.db.get_course(course_id).find_section(section_id)
        if section is None:
            raise BackendError("no such section", errorcode="invalidrecord")
        return section


def course_to_state(course: Course) -> dict[str, Any]:
    """Serialise a course in the `core_courseformat_get_state` format."""
    sections: list[dict[str, Any]] = []
    cms: list[dict[str, Any]] = []
    for section in course.all_sections():
        sections.append(
            {
                "id": section.id,
                "section": section.number,
                "number": section.number,
                "title": section.name,
                "rawtitle": section.name,
                "cmlist": [str(item.cmid) for item in section.items],
                "visible": section.visible,
                "component": "mod_subsection" if section.delegated else None,
                "itemid": section.id if section.delegated else None,
            }
        )
        for item in section.items:
            if isinstance(item, Subsection):
                cms.append(
                    {
                        "id": item.cmid,
                        "name": item.name,
                        "module": "subsection",
                        "visible": item.visible,
                        "sectionid": section.id,
                        "hasdelegatedsection": True,
                        "delegatesectionid": item.section.id,
                    }
                )
            else:
                assert isinstance(item, Activity)
                cms.append(
                    {
                        "id": item.cmid,
                        "name": item.name,
                        "module": item.modname,
                        "visible": item.visible,
                        "sectionid": section.id,
                        "hasdelegatedsection": False,
                    }
                )
    sections.sort(key=lambda s: s["number"])
    return {
        "course": {"id": course.id, "sectionlist": [s["id"] for s in sections]},
        "section": sections,
        "cm": cms,
    }
