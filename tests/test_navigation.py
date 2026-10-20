from __future__ import annotations

import pytest

from moodlesh.cli.navigation import ROOT, Location, Navigator
from moodlesh.core.errors import NotFoundError
from moodlesh.memory import InMemoryBackend


@pytest.fixture
def nav(backend: InMemoryBackend) -> Navigator:
    return Navigator(backend)


def test_resolve_by_number_and_by_name_case_insensitive(nav: Navigator) -> None:
    by_number = nav.resolve(ROOT, "/101/1/2")
    by_name = nav.resolve(ROOT, "/phy1/kinematics/EXERCISES")
    assert by_number == by_name
    assert nav.display(by_name) == "/PHY1/Kinematics/Exercises"


def test_relative_paths_and_dots(nav: Navigator) -> None:
    course = nav.resolve(ROOT, "PHY1")
    section = nav.resolve(course, "./1")
    assert nav.resolve(section, "..") == course
    assert nav.resolve(section, "../2") == nav.resolve(ROOT, "/PHY1/Dynamics")
    assert nav.resolve(section, "/") == ROOT
    assert nav.resolve(section, "../../..") == ROOT  # going above the root stays at the root


def test_course_by_full_name(nav: Navigator) -> None:
    assert nav.resolve(ROOT, "Physics I") == Location(101)


@pytest.mark.parametrize(
    ("path", "message"),
    [
        ("/NOPE", "no course"),
        ("/PHY1/9", "has no section 9"),
        ("/PHY1/Optics", "no section"),
        ("/PHY1/1/1", "is an activity"),
        ("/PHY1/1/99", "has no item 99"),
        ("/PHY1/1/Exercises/1", "is an activity"),
    ],
)
def test_resolution_errors(nav: Navigator, path: str, message: str) -> None:
    with pytest.raises(NotFoundError, match=message):
        nav.resolve(ROOT, path)


def test_ambiguous_names_require_the_number(backend: InMemoryBackend) -> None:
    backend.seed_section(101, "Kinematics")
    nav = Navigator(backend)
    with pytest.raises(NotFoundError, match="ambiguous"):
        nav.resolve(ROOT, "/PHY1/Kinematics")


def test_cache_is_used_until_invalidated(backend: InMemoryBackend) -> None:
    nav = Navigator(backend)
    before = nav.course(101)
    backend.seed_section(101, "Added from outside")
    assert nav.course(101) is before
    nav.invalidate(101)
    assert nav.course(101).sections[-1].name == "Added from outside"


def test_entries_mark_containers(nav: Navigator) -> None:
    entries = nav.entries(nav.resolve(ROOT, "/PHY1/1"))
    assert [(e.key, e.is_container) for e in entries] == [("1", False), ("2", True), ("3", False)]
