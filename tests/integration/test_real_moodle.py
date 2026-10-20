"""End-to-end tests against a real, disposable Moodle site.

Skipped unless MOODLESH_IT_URL is set. They WRITE to the site: never point them
at a production instance. The site must be seeded with `integration/seed_site.php`
(a 'Science' category with PHY1 and CALC1, a 'Humanities' category with HIST, a
category manager of Science and an editing teacher of PHY1).

    MOODLESH_IT_URL=http://127.0.0.1:8045 \\
    MOODLESH_IT_USER=catmanager MOODLESH_IT_PASSWORD='Manager123!' \\
    MOODLESH_IT_TEACHER=teacher MOODLESH_IT_TEACHER_PASSWORD='Teacher123!' \\
    uv run pytest tests/integration
"""

from __future__ import annotations

import os
import uuid

import pytest

from moodlesh.core.errors import BackendError
from moodlesh.dryrun import DryRunBackend
from moodlesh.moodle import AjaxSessionTransport, MoodleBackend, login_with_password

URL = os.environ.get("MOODLESH_IT_URL", "")

pytestmark = pytest.mark.skipif(not URL, reason="set MOODLESH_IT_URL to run against a real Moodle")


def _backend(user_var: str, password_var: str, *, editable_only: bool = True) -> MoodleBackend:
    user, password = os.environ.get(user_var), os.environ.get(password_var)
    if not user or not password:
        pytest.skip(f"set {user_var} and {password_var}")
    cookies = login_with_password(URL, user, password)
    return MoodleBackend(AjaxSessionTransport(URL, cookies), editable_only=editable_only)


@pytest.fixture(scope="module")
def manager() -> MoodleBackend:
    return _backend("MOODLESH_IT_USER", "MOODLESH_IT_PASSWORD")


def _course_id(backend: MoodleBackend, shortname: str) -> int:
    [course] = [c for c in backend.list_courses() if c.shortname == shortname]
    return course.id


def _unique(prefix: str) -> str:
    return f"{prefix} {uuid.uuid4().hex[:6]}"


def test_lists_only_editable_courses(manager: MoodleBackend) -> None:
    assert {c.shortname for c in manager.list_courses()} >= {"PHY1", "CALC1"}
    assert "HIST" not in {c.shortname for c in manager.list_courses()}


def test_reads_subsections(manager: MoodleBackend) -> None:
    course = manager.get_course(_course_id(manager, "PHY1"))
    [dynamics] = [s for s in course.sections if s.name == "Dynamics"]
    assert "Extras" in [s.name for s in dynamics.subsections]


def test_section_subsection_and_rename(manager: MoodleBackend) -> None:
    course_id = _course_id(manager, "CALC1")
    name = _unique("IT section")
    section = manager.add_section(course_id, 1, name)
    first = manager.get_course(course_id).section_by_number(1)
    assert first is not None
    assert first.name == name

    sub = manager.add_subsection(course_id, section.id, name="IT sub")
    renamed = _unique("IT renamed")
    manager.rename_section(course_id, sub.id, renamed)
    parent = manager.get_course(course_id).section_by_number(1)
    assert parent is not None
    assert [s.name for s in parent.subsections] == [renamed]


def test_dry_run_writes_nothing(manager: MoodleBackend) -> None:
    course_id = _course_id(manager, "CALC1")
    before = manager.get_course(course_id)
    dry = DryRunBackend(manager)
    dry.add_section(course_id, 1, _unique("IT dry"))
    assert manager.get_course(course_id).sections == before.sections


def test_teacher_without_manager_rights_gets_no_partial_write() -> None:
    teacher = _backend("MOODLESH_IT_TEACHER", "MOODLESH_IT_TEACHER_PASSWORD", editable_only=False)
    [phy] = [c for c in teacher.list_courses() if c.shortname == "PHY1"]
    before = teacher.get_course(phy.id)
    with pytest.raises(BackendError):
        teacher.add_section(phy.id, 1, _unique("IT teacher"))
    assert teacher.get_course(phy.id).sections == before.sections
