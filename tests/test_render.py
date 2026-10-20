from __future__ import annotations

from moodlesh.cli import render
from moodlesh.memory import InMemoryBackend

from conftest import as_text

EXPECTED_TREE = """\
Physics I  PHY1 · id 101
├── 0 General
│   └── 1 Announcements  forum
├── 1 Kinematics
│   ├── 1 Linear motion notes  resource
│   ├── 2 Exercises/
│   │   ├── 1 Worked examples  page
│   │   └── 2 Self-assessment quiz  quiz
│   └── 3 Lab report  assign
└── 2 Dynamics
    ├── 1 Newton's laws  resource
    └── 2 Extras/

3 sections, 2 subsections, 6 activities"""


def test_course_tree(backend: InMemoryBackend) -> None:
    assert as_text(render.course_tree(backend.get_course(101))) == EXPECTED_TREE


def test_tree_depth_and_dirs_only(backend: InMemoryBackend) -> None:
    course = backend.get_course(101)
    shallow = as_text(render.course_tree(course, depth=1))
    assert "Announcements" not in shallow
    assert shallow.endswith("3 sections")

    dirs = as_text(render.course_tree(course, dirs_only=True))
    assert "Exercises/" in dirs
    assert "Announcements" not in dirs
    assert dirs.endswith("3 sections, 2 subsections")


def test_section_tree_and_empty_section(backend: InMemoryBackend) -> None:
    course = backend.get_course(101)
    extras = course.sections[2].subsections[0].section
    assert as_text(render.section_tree(extras)) == "Extras/\n\nempty"


def test_tables_show_positions(backend: InMemoryBackend) -> None:
    section = backend.get_course(101).sections[1]
    text = as_text(render.items_table(list(enumerate(section.items, start=1))))
    assert "2  subsection  Exercises/" in text
    assert "1 subsection, 2 activities" in as_text(render.sections_table([section]))
