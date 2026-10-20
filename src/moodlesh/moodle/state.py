"""Conversion of the state returned by `core_courseformat_get_state` into the model.

The state looks like `{"course": {...}, "section": [...], "cm": [...]}`.
Delegated sections (subsections) have `component == "mod_subsection"`, and the
module owning them has `delegatesectionid` set.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from moodlesh.core.errors import BackendError
from moodlesh.core.models import Activity, Course, CourseSummary, Section, SectionItem, Subsection

JsonObject = Mapping[str, Any]


def parse_state(raw: str | JsonObject) -> JsonObject:
    """Decode the state (the web service returns it as a JSON string)."""
    state = json.loads(raw) if isinstance(raw, str) else raw
    if not isinstance(state, Mapping) or not {"course", "section", "cm"} <= state.keys():
        raise BackendError("unexpected course state format", function="core_courseformat_get_state")
    return state


def build_course(summary: CourseSummary, raw_state: str | JsonObject) -> Course:
    """Build the course tree from the Moodle state."""
    state = parse_state(raw_state)
    sections = {int(s["id"]): s for s in state["section"]}
    cms = {int(cm["id"]): cm for cm in state["cm"]}

    order = [int(sid) for sid in state["course"].get("sectionlist", [])] or sorted(
        sections, key=lambda sid: int(sections[sid]["number"])
    )
    top_level = [sid for sid in order if sid in sections and not sections[sid].get("component")]
    return Course(
        summary=summary,
        sections=tuple(_build_section(sections[sid], sections, cms) for sid in top_level),
    )


def _build_section(
    data: JsonObject,
    sections: Mapping[int, JsonObject],
    cms: Mapping[int, JsonObject],
) -> Section:
    items: list[SectionItem] = []
    for raw_cmid in data.get("cmlist", []):
        cm = cms.get(int(raw_cmid))
        if cm is None:  # not visible to the current user
            continue
        items.append(_build_item(cm, sections, cms))
    return Section(
        id=int(data["id"]),
        number=int(data.get("number", data.get("section", 0))),
        name=str(data.get("title") or data.get("rawtitle") or ""),
        items=tuple(items),
        visible=bool(data.get("visible", True)),
        delegated=bool(data.get("component")),
    )


def _build_item(
    cm: JsonObject, sections: Mapping[int, JsonObject], cms: Mapping[int, JsonObject]
) -> SectionItem:
    delegated_id = cm.get("delegatesectionid")
    if delegated_id and int(delegated_id) in sections:
        return Subsection(
            cmid=int(cm["id"]),
            section=_build_section(sections[int(delegated_id)], sections, cms),
            visible=bool(cm.get("visible", True)),
        )
    return Activity(
        cmid=int(cm["id"]),
        name=str(cm.get("name", "")),
        modname=str(cm.get("module", "")),
        visible=bool(cm.get("visible", True)),
    )
