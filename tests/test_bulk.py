"""Tests of running one script on many courses."""

from __future__ import annotations

from pathlib import Path

import pytest
from rich.console import RenderableType

from moodlesh.cli.bulk import CourseSelection, run_on_courses, select_courses, summary_table
from moodlesh.cli.errors import UsageError
from moodlesh.cli.main import EXIT_ERROR, EXIT_OK, EXIT_USAGE, main
from moodlesh.memory import InMemoryBackend

from conftest import as_text

PRACTICE = 'mkdir "Practice" --at 1\nmkdir "Past exams" --into Practice\n'


def names(backend: InMemoryBackend, course_id: int) -> list[str]:
    return [s.name for s in backend.get_course(course_id).sections]


@pytest.mark.parametrize(
    ("selection", "expected"),
    [
        (CourseSelection(courses=("PHY1",)), ["PHY1"]),
        (CourseSelection(courses=("102", "physics i")), ["PHY1", "CALC1"]),
        (CourseSelection(categories=("Science",)), ["PHY1", "CALC1"]),
        (CourseSelection(categories=("2",)), ["HIST"]),
        (CourseSelection(match="calc"), ["CALC1"]),
        (CourseSelection(categories=("science",), match="phys"), ["PHY1"]),
        (CourseSelection(courses=("HIST",), match="calc"), ["CALC1", "HIST"]),
    ],
)
def test_select_courses(backend: InMemoryBackend, selection: CourseSelection, expected: list[str]) -> None:
    assert sorted(c.shortname for c in select_courses(backend, selection)) == sorted(expected)


@pytest.mark.parametrize(
    ("selection", "message"),
    [
        (CourseSelection(courses=("NOPE",)), "no editable course"),
        (CourseSelection(match="zzz"), "no course matches"),
    ],
)
def test_select_courses_errors(backend: InMemoryBackend, selection: CourseSelection, message: str) -> None:
    with pytest.raises(UsageError, match=message):
        select_courses(backend, selection)


def test_script_runs_on_each_course_from_its_root(backend: InMemoryBackend) -> None:
    courses = select_courses(backend, CourseSelection(categories=("Science",)))
    outputs: list[RenderableType] = []
    outcomes = run_on_courses(backend, PRACTICE, courses, emit=outputs.append)

    assert all(o.ok for o in outcomes)
    for course_id in (101, 102):
        assert names(backend, course_id)[1] == "Practice"
        assert backend.get_course(course_id).sections[1].subsections[0].name == "Past exams"
    assert names(backend, 201) == ["General"]  # not selected


def test_stops_at_first_failing_course_unless_keep_going(backend: InMemoryBackend) -> None:
    script = "cd Kinematics\nmkdir Theory\n"  # only PHY1 has a 'Kinematics' section
    courses = select_courses(backend, CourseSelection(courses=("CALC1", "PHY1")))

    stopped = run_on_courses(backend, script, courses, emit=lambda _: None)
    assert [(o.course.shortname, o.ok, o.skipped) for o in stopped] == [
        ("PHY1", True, False),
        ("CALC1", False, False),
    ]

    fresh = InMemoryBackend()
    fresh.create_course(1, "A", "A", "X", 1)
    fresh.create_course(2, "B", "B", "X", 1)
    fresh.seed_section(2, "Kinematics")
    courses = select_courses(fresh, CourseSelection(categories=("X",)))
    skipped = run_on_courses(fresh, script, courses, emit=lambda _: None)
    assert [(o.course.shortname, o.skipped) for o in skipped] == [("A", False), ("B", True)]
    kept = run_on_courses(fresh, script, courses, emit=lambda _: None, keep_going=True)
    assert [(o.course.shortname, o.ok) for o in kept] == [("A", False), ("B", True)]


def test_summary_table() -> None:
    backend = InMemoryBackend()
    backend.create_course(1, "A", "A", "X", 1)
    outcomes = run_on_courses(backend, "mkdir One", backend.list_courses(), emit=lambda _: None, dry_run=True)
    text = as_text(summary_table(outcomes, dry_run=True))
    assert "1 of 1 courses ok — nothing was changed" in text
    assert "ok (dry run)" in text


def test_cli_bulk_dry_run_changes_nothing(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    script = tmp_path / "practice.mdl"
    script.write_text(PRACTICE, encoding="utf-8")
    assert main(["--demo", "--dry-run", "run", str(script), "--category", "Science"]) == EXIT_OK
    out = capsys.readouterr().out
    assert "2 course(s): PHY1, CALC1" in out
    assert "would create section 1 'Practice'" in out
    assert "nothing was changed" in out


def test_cli_bulk_requires_confirmation_without_a_terminal(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    script = tmp_path / "practice.mdl"
    script.write_text(PRACTICE, encoding="utf-8")
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    assert main(["--demo", "run", str(script), "--match", "calc"]) == EXIT_USAGE
    assert "--yes" in capsys.readouterr().out
    assert main(["--demo", "run", str(script), "--match", "calc", "--yes"]) == EXIT_OK


def test_cli_bulk_reports_failures(capsys: pytest.CaptureFixture[str]) -> None:
    code = main(
        ["--demo", "exec", "cd Kinematics", "--course", "CALC1", "--course", "PHY1", "--keep-going", "-y"]
    )
    assert code == EXIT_ERROR
    assert "1 of 2 courses ok" in capsys.readouterr().out
