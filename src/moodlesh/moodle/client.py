"""Backend that talks to a real Moodle instance (4.5+) through a `Transport`.

Moodle functions used (all available both via AJAX and via REST):

* `core_course_search_courses`      list/search courses (only editable ones by default)
* `core_course_get_courses`         data of a single course (including its format)
* `core_courseformat_get_state`     full course structure
* `core_courseformat_update_course` `section_add` action
* `core_courseformat_new_module`    subsection creation (falls back to
                                    `core_courseformat_create_module` on early
                                    4.5 releases that lack it)
* `core_update_inplace_editable`    section rename

Writes follow one rule: everything needed to *complete* an operation is
checked before the first change is sent (`check_writable`), so a refused
permission never leaves a half-done change behind (e.g. a section created but
not named).
"""

from __future__ import annotations

import dataclasses
import logging
from collections.abc import Mapping, Sequence
from typing import Any

from moodlesh.core.backend import item_insertion_before, section_insertion_target
from moodlesh.core.errors import (
    BackendError,
    InvalidOperationError,
    MoodleshError,
    NotFoundError,
    PartialChangeError,
)
from moodlesh.core.models import Course, CourseSummary, Section, Subsection
from moodlesh.moodle.state import build_course
from moodlesh.moodle.transport import Transport

logger = logging.getLogger(__name__)

SUBSECTION_MODNAME = "subsection"
EDIT_CAPABILITY = "moodle/course:update"


class MoodleBackend:
    """`CourseBackend` implementation for Moodle.

    Args:
        transport: how to reach Moodle.
        editable_only: list only the courses the user can edit (default). Set to
            False to list the whole course catalogue the user can see.
    """

    def __init__(self, transport: Transport, *, editable_only: bool = True) -> None:
        self._transport = transport
        self._editable_only = editable_only
        self._summaries: dict[int, CourseSummary] = {}
        self._editable_ids: set[int] | None = None

    # -------------------------------------------------------------------- reading
    def list_courses(self, name_filter: str | None = None) -> list[CourseSummary]:
        courses = self._search(name_filter or "", editable=self._editable_only)
        self._summaries.update({c.id: c for c in courses})
        return courses

    def get_course(self, course_id: int) -> Course:
        summary = self._get_summary(course_id)
        state = self._transport.call("core_courseformat_get_state", {"courseid": course_id})
        return build_course(summary, state)

    def _search(self, text: str, *, editable: bool) -> list[CourseSummary]:
        args: dict[str, Any] = {"criterianame": "search", "criteriavalue": text, "page": 0, "perpage": 0}
        if editable:
            args["requiredcapabilities"] = [EDIT_CAPABILITY]
        data = self._transport.call("core_course_search_courses", args)
        return [_summary_from(raw) for raw in data.get("courses", [])]

    def _get_summary(self, course_id: int, *, need_format: bool = False) -> CourseSummary:
        cached = self._summaries.get(course_id)
        # The search does not include the format: when it is needed, ask for it explicitly.
        if cached is None or (need_format and not cached.format):
            data = self._transport.call("core_course_get_courses", {"options": {"ids": [course_id]}})
            if not data:
                raise NotFoundError(f"course {course_id} does not exist or is not accessible")
            fresh = _summary_from(data[0])
            if cached is not None:  # keep what the search knew (e.g. the category name)
                fresh = dataclasses.replace(cached, format=fresh.format or cached.format)
            self._summaries[course_id] = fresh
        return self._summaries[course_id]

    # ------------------------------------------------------------------ preflight
    def check_writable(self, course_id: int, *, needs_rename: bool = True) -> None:
        """Check, with reads only, that a write on this course can be completed.

        Raises:
            BackendError: the user cannot edit the course, or the course format
                (needed to name sections) cannot be read.
        """
        if self._editable_ids is None:
            self._editable_ids = {c.id for c in self._search("", editable=True)}
        if course_id not in self._editable_ids:
            raise BackendError(
                f"you cannot edit course {course_id} ({EDIT_CAPABILITY} is missing)",
                errorcode="nopermissions",
            )
        if needs_rename and not self._get_summary(course_id, need_format=True).format:
            raise BackendError(f"unknown format for course {course_id}: cannot name sections")

    # -------------------------------------------------------------------- writing
    def add_section(self, course_id: int, position: int | None = None, name: str | None = None) -> Section:
        _check_name(name)
        self.check_writable(course_id, needs_rename=name is not None)
        course = self.get_course(course_id)
        target = section_insertion_target(course, position)
        existing = {s.id for s in course.sections}
        # Moodle inserts the new section right after `anchor`: the target, or the
        # last section when appending.
        anchor = target.id if target is not None else course.sections[-1].id if course.sections else None

        args: dict[str, Any] = {"action": "section_add", "courseid": course_id}
        if target is not None:
            args["targetsectionid"] = target.id
        self._transport.call("core_courseformat_update_course", args)

        sections = self.get_course(course_id).sections
        new_id = _identify_new([s.id for s in sections], existing, after=anchor)
        if new_id is None:
            raise _unidentified("section", course_id, [s for s in sections if s.id not in existing], name)
        created = next(s for s in sections if s.id == new_id)
        return self._apply_name(course_id, created, name, "section")

    def add_subsection(
        self,
        course_id: int,
        parent_section_id: int,
        position: int | None = None,
        name: str | None = None,
    ) -> Section:
        _check_name(name)
        course = self.get_course(course_id)
        parent = course.find_section(parent_section_id)
        if parent is None:
            raise NotFoundError(f"section {parent_section_id} does not exist in course {course_id}")
        before_cmid = item_insertion_before(parent, position)
        self.check_writable(course_id, needs_rename=name is not None)
        existing = {item.cmid for item in parent.items}
        # Moodle puts the new module right before `before_cmid`, or after the
        # last item when appending.
        last = parent.items[-1].cmid if parent.items else None

        self._create_subsection_module(course_id, parent, before_cmid)

        refreshed = self.get_course(course_id).find_section(parent_section_id)
        if refreshed is None:  # pragma: no cover - the section does not vanish while creating
            raise NotFoundError(f"section {parent_section_id} no longer exists")
        keys = [item.cmid for item in refreshed.items]
        if before_cmid is not None:
            new_cmid = _identify_new(keys, existing, before=before_cmid)
        else:
            new_cmid = _identify_new(keys, existing, after=last)
        created = next((i for i in refreshed.items if i.cmid == new_cmid), None)
        if not isinstance(created, Subsection):
            new_items = [i.section for i in refreshed.subsections if i.cmid not in existing]
            raise _unidentified("subsection", course_id, new_items, name)
        return self._apply_name(course_id, created.section, name, "subsection")

    def _create_subsection_module(self, course_id: int, parent: Section, before_cmid: int | None) -> None:
        args: dict[str, Any] = {"courseid": course_id, "modname": SUBSECTION_MODNAME}
        if before_cmid is not None:
            args["targetcmid"] = before_cmid
        try:
            self._transport.call("core_courseformat_new_module", {**args, "targetsectionid": parent.id})
        except BackendError as exc:
            # Fall back only when the function itself is missing (early 4.5 releases):
            # for any other error, retrying elsewhere could create the subsection twice.
            if not _is_missing_function(exc):
                raise
            logger.info("core_courseformat_new_module not available, using create_module")
            self._transport.call(
                "core_courseformat_create_module", {**args, "targetsectionnum": parent.number}
            )

    def rename_section(self, course_id: int, section_id: int, name: str) -> None:
        _check_name(name)
        self.check_writable(course_id)
        course_format = self._get_summary(course_id, need_format=True).format
        self._transport.call(
            "core_update_inplace_editable",
            {
                "component": f"format_{course_format}",
                "itemtype": "sectionname",
                "itemid": section_id,
                "value": name,
            },
        )

    def _apply_name(self, course_id: int, section: Section, name: str | None, what: str) -> Section:
        """Name a section just created; if that fails, say that the section exists anyway."""
        if name is None:
            return section
        try:
            self.rename_section(course_id, section.id, name)
        except MoodleshError as exc:
            raise PartialChangeError(
                f"a {what} was created in course {course_id} (id {section.id}), but it could not be "
                f"named '{name}': {exc}. Rename it in Moodle or with moodlesh.",
                created=section,
            ) from exc
        return dataclasses.replace(section, name=name)


def _check_name(name: str | None) -> None:
    if name is not None and not name.strip():
        raise InvalidOperationError("the name cannot be empty")


def _is_missing_function(exc: BackendError) -> bool:
    """True if Moodle says the external function does not exist on this site."""
    return exc.errorcode == "invalidrecord" and "external_functions" in str(exc)


def _summary_from(raw: Mapping[str, Any]) -> CourseSummary:
    category_id = raw.get("categoryid")
    return CourseSummary(
        id=int(raw["id"]),
        shortname=str(raw.get("shortname", "")),
        fullname=str(raw.get("fullname") or raw.get("displayname") or ""),
        category=str(raw.get("categoryname", "")),
        format=raw.get("format"),
        category_id=int(category_id) if category_id is not None else None,
    )


def _identify_new(
    keys: Sequence[int], existing: set[int], *, after: int | None = None, before: int | None = None
) -> int | None:
    """Find the item just created among `keys` (ids in course order), or `None` if unsure.

    Moodle does not say which item it created, so it is found by comparing with
    the ids that `existing` had before. If the course changed at the same time
    (someone else added an item), several ids are new: the one created by
    moodlesh is then the only new item next to where it was inserted (right
    after `after`, or right before `before`). If several new items are there,
    there is no way to tell them apart and `None` is returned.
    """
    new = [k for k in keys if k not in existing]
    if len(new) == 1:
        return new[0]
    anchor = before if before is not None else after
    if anchor is None or anchor not in keys:
        return None
    step = -1 if before is not None else 1
    run: list[int] = []
    index = keys.index(anchor) + step
    while 0 <= index < len(keys) and keys[index] not in existing:
        run.append(keys[index])
        index += step
    return run[0] if len(run) == 1 else None


def _unidentified(
    what: str, course_id: int, candidates: Sequence[Section], name: str | None
) -> PartialChangeError:
    """The error for a creation that succeeded but cannot be told apart from other changes."""
    if not candidates:
        return PartialChangeError(
            f"Moodle reported success, but no new {what} was found in course {course_id}"
        )
    listed = ", ".join(f"'{s.name}' (id {s.id})" for s in candidates)
    unnamed = f"; the name '{name}' was not applied" if name is not None else ""
    return PartialChangeError(
        f"a {what} was created in course {course_id}, but the course changed at the same time and it "
        f"cannot be told apart from the other new ones: {listed}{unnamed}. Check the course in Moodle."
    )
