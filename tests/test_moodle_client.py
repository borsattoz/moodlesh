"""Tests of the Moodle backend against a fake Moodle with the same functions and formats."""

from __future__ import annotations

import pytest

from moodlesh.core.errors import BackendError, InvalidOperationError, PartialChangeError
from moodlesh.memory import demo_backend
from moodlesh.moodle.client import MoodleBackend
from moodlesh.moodle.state import build_course

from fakes import FakeMoodle, course_to_state


@pytest.fixture
def moodle() -> FakeMoodle:
    return FakeMoodle()


@pytest.fixture
def client(moodle: FakeMoodle) -> MoodleBackend:
    return MoodleBackend(moodle)


def test_state_roundtrip_matches_the_model() -> None:
    db = demo_backend()
    course = db.get_course(101)
    assert build_course(course.summary, course_to_state(course)) == course


def test_list_courses_with_filter(client: MoodleBackend, moodle: FakeMoodle) -> None:
    courses = client.list_courses("physics")
    assert [c.shortname for c in courses] == ["PHY1"]
    assert moodle.called("core_course_search_courses")[0]["criteriavalue"] == "physics"


def test_get_course_reads_state(client: MoodleBackend, moodle: FakeMoodle) -> None:
    course = client.get_course(101)
    assert [s.name for s in course.sections] == ["General", "Kinematics", "Dynamics"]
    assert course.sections[1].subsections[0].section.items[1].name == "Self-assessment quiz"
    assert moodle.called("core_course_get_courses")  # summary not cached yet


def test_add_section_at_position_uses_previous_section_as_target(
    client: MoodleBackend, moodle: FakeMoodle
) -> None:
    course = client.get_course(101)
    created = client.add_section(101, position=2, name="Optics")

    [args] = moodle.called("core_courseformat_update_course")
    assert args["targetsectionid"] == course.sections[1].id  # Moodle inserts after the target
    assert created.number == 2
    assert created.name == "Optics"
    assert [s.name for s in client.get_course(101).sections] == [
        "General",
        "Kinematics",
        "Optics",
        "Dynamics",
    ]


def test_add_section_at_end_has_no_target(client: MoodleBackend, moodle: FakeMoodle) -> None:
    client.add_section(101)
    [args] = moodle.called("core_courseformat_update_course")
    assert "targetsectionid" not in args
    assert not moodle.called("core_update_inplace_editable")  # no name: no rename


def test_add_subsection_before_an_item(client: MoodleBackend, moodle: FakeMoodle) -> None:
    parent = client.get_course(101).sections[1]
    created = client.add_subsection(101, parent.id, position=1, name="Theory")

    [args] = moodle.called("core_courseformat_new_module")
    assert args["targetsectionid"] == parent.id
    assert args["targetcmid"] == parent.items[0].cmid
    assert created.delegated
    [rename] = moodle.called("core_update_inplace_editable")
    assert rename == {
        "component": "format_topics",
        "itemtype": "sectionname",
        "itemid": created.id,
        "value": "Theory",
    }
    assert client.get_course(101).sections[1].items[0].name == "Theory"


def test_add_subsection_falls_back_on_older_45(client: MoodleBackend, moodle: FakeMoodle) -> None:
    moodle.legacy_45 = True
    parent = client.get_course(101).sections[2]
    client.add_subsection(101, parent.id, name="Extra")
    [args] = moodle.called("core_courseformat_create_module")
    assert args["targetsectionnum"] == parent.number
    assert "targetcmid" not in args


def test_subsection_inside_subsection_is_rejected_without_calling_moodle(
    client: MoodleBackend, moodle: FakeMoodle
) -> None:
    delegated = client.get_course(101).sections[1].subsections[0].section
    with pytest.raises(InvalidOperationError):
        client.add_subsection(101, delegated.id)
    assert not moodle.called("core_courseformat_new_module")


def test_permission_errors_are_not_retried_elsewhere(client: MoodleBackend, moodle: FakeMoodle) -> None:
    def deny(**_: object) -> None:
        raise BackendError("Sorry, but you do not currently have permissions", errorcode="nopermissions")

    moodle._core_courseformat_new_module = deny  # type: ignore[method-assign]
    parent = client.get_course(101).sections[1]
    with pytest.raises(BackendError, match="permissions"):
        client.add_subsection(101, parent.id)
    assert not moodle.called("core_courseformat_create_module")


def test_format_from_search_avoids_extra_call(moodle: FakeMoodle) -> None:
    moodle.include_format_in_search = True
    client = MoodleBackend(moodle)
    client.list_courses()
    client.rename_section(101, client.get_course(101).sections[1].id, "Point kinematics")
    assert not moodle.called("core_course_get_courses")


WRITE_FUNCTIONS = (
    "core_courseformat_update_course",
    "core_courseformat_new_module",
    "core_courseformat_create_module",
    "core_update_inplace_editable",
)


def test_nothing_is_written_when_the_course_is_not_editable(moodle: FakeMoodle) -> None:
    # Regression found on a real Moodle 4.5: a teacher could create a section that
    # then could not be named, leaving a half-done change.
    moodle.not_editable = {101}
    client = MoodleBackend(moodle)
    with pytest.raises(BackendError, match="cannot edit"):
        client.add_section(101, name="Optics")
    parent = client.get_course(101).sections[1]
    with pytest.raises(BackendError, match="cannot edit"):
        client.add_subsection(101, parent.id, name="Theory")
    assert not [name for name, _ in moodle.calls if name in WRITE_FUNCTIONS]


def test_fallback_only_when_new_module_is_missing(client: MoodleBackend, moodle: FakeMoodle) -> None:
    def no_section(**_: object) -> None:
        raise BackendError("This section does not exist", errorcode="sectionnotexist")

    moodle._core_courseformat_new_module = no_section  # type: ignore[method-assign]
    parent = client.get_course(101).sections[1]
    with pytest.raises(BackendError, match="does not exist"):
        client.add_subsection(101, parent.id, name="X")
    assert not moodle.called("core_courseformat_create_module")


def test_lists_only_editable_courses_by_default(moodle: FakeMoodle) -> None:
    moodle.not_editable = {201}
    assert [c.shortname for c in MoodleBackend(moodle).list_courses()] == ["PHY1", "CALC1"]
    everything = MoodleBackend(moodle, editable_only=False).list_courses()
    assert [c.shortname for c in everything] == ["PHY1", "CALC1", "HIST"]
    assert everything[0].category_id == 1


def test_empty_names_are_rejected_before_any_call(client: MoodleBackend, moodle: FakeMoodle) -> None:
    with pytest.raises(InvalidOperationError):
        client.add_section(101, name="   ")
    assert not moodle.calls


# --------------------------------------------------------------------------- #
# Someone else changes the course while moodlesh creates something (issue: the
# new item must still be identified correctly, or not at all).
# --------------------------------------------------------------------------- #
def _names(moodle: FakeMoodle, course_id: int = 101) -> list[str]:
    return [s.name for s in moodle.db.get_course(course_id).sections]


def test_new_section_is_found_when_another_is_added_elsewhere(
    client: MoodleBackend, moodle: FakeMoodle
) -> None:
    moodle.concurrent_change = lambda db: db.add_section(101, None, "Someone else's")
    created = client.add_section(101, 2, "Optics")
    assert created.name == "Optics"
    assert _names(moodle) == ["General", "Kinematics", "Optics", "Dynamics", "Someone else's"]


@pytest.mark.parametrize(
    ("position", "theirs"),
    [
        (None, None),  # both appended at the end
        (2, 2),  # both inserted at the same place
    ],
)
def test_ambiguous_new_section_is_not_renamed(
    client: MoodleBackend, moodle: FakeMoodle, position: int | None, theirs: int | None
) -> None:
    moodle.concurrent_change = lambda db: db.add_section(101, theirs)
    with pytest.raises(BackendError, match=r"was created in course 101.*the name 'Optics' was not applied"):
        client.add_section(101, position, "Optics")
    assert not moodle.called("core_update_inplace_editable")
    assert "Optics" not in _names(moodle)


def test_new_subsection_is_found_when_another_is_added_elsewhere(
    client: MoodleBackend, moodle: FakeMoodle
) -> None:
    dynamics = moodle.db.get_course(101).sections[2]
    moodle.concurrent_change = lambda db: db.add_subsection(101, dynamics.id, 1, "Someone else's")
    created = client.add_subsection(101, dynamics.id, name="Problems")
    assert created.name == "Problems"
    after = moodle.db.get_course(101).sections[2]
    assert [s.name for s in after.subsections] == ["Someone else's", "Extras", "Problems"]


def test_ambiguous_new_subsection_is_not_renamed(client: MoodleBackend, moodle: FakeMoodle) -> None:
    dynamics = moodle.db.get_course(101).sections[2]
    moodle.concurrent_change = lambda db: db.add_subsection(101, dynamics.id, 2)
    with pytest.raises(BackendError, match=r"subsection was created.*'Problems' was not applied"):
        client.add_subsection(101, dynamics.id, 2, name="Problems")
    assert not moodle.called("core_update_inplace_editable")


def _fail_rename(monkeypatch: pytest.MonkeyPatch, moodle: FakeMoodle) -> None:
    def refuse(**_: object) -> None:
        raise BackendError("connection lost", function="core_update_inplace_editable")

    monkeypatch.setattr(moodle, "_core_update_inplace_editable", refuse)


def test_section_created_but_not_named_is_reported_as_partial(
    client: MoodleBackend, moodle: FakeMoodle, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fail_rename(monkeypatch, moodle)
    with pytest.raises(PartialChangeError, match="could not be named 'Optics'") as caught:
        client.add_section(101, 2, "Optics")
    created = caught.value.created
    assert created is not None
    assert moodle.db.get_course(101).sections[2].id == created.id  # it exists in Moodle


def test_subsection_created_but_not_named_is_reported_as_partial(
    client: MoodleBackend, moodle: FakeMoodle, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fail_rename(monkeypatch, moodle)
    dynamics = moodle.db.get_course(101).sections[2]
    with pytest.raises(PartialChangeError, match="subsection was created") as caught:
        client.add_subsection(101, dynamics.id, name="Problems")
    assert caught.value.created is not None


def test_unidentified_new_section_is_a_partial_change(client: MoodleBackend, moodle: FakeMoodle) -> None:
    moodle.concurrent_change = lambda db: db.add_section(101)
    with pytest.raises(PartialChangeError) as caught:
        client.add_section(101, None, "Optics")
    assert caught.value.created is None
