"""Tests of dry runs and of the change journal."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from moodlesh.cli.interpreter import Interpreter
from moodlesh.core.backend import CourseBackend
from moodlesh.core.errors import BackendError, InvalidOperationError, PartialChangeError
from moodlesh.core.models import CourseSummary
from moodlesh.dryrun import DryRunBackend
from moodlesh.journal import Journal, JournalingBackend
from moodlesh.memory import InMemoryBackend
from moodlesh.moodle.client import MoodleBackend

from conftest import run
from fakes import FakeMoodle

WRITES = (
    "core_courseformat_update_course",
    "core_courseformat_new_module",
    "core_courseformat_create_module",
    "core_update_inplace_editable",
)


def test_dry_run_simulates_and_never_writes() -> None:
    moodle = FakeMoodle()
    interpreter = Interpreter(DryRunBackend(MoodleBackend(moodle)), dry_run=True)
    out = run(
        interpreter, "cd PHY1; mkdir Optics --at 2; cd Optics; mkdir Lenses; rename . 'Optics I'; tree -d"
    )

    assert "would create section 2 'Optics'" in out
    assert "would create subsection 'Lenses' in 'Optics'" in out
    assert "would rename 'Optics' to 'Optics I'" in out
    assert "Lenses/" in out  # later commands see earlier simulated changes
    assert not [name for name, _ in moodle.calls if name in WRITES]
    assert "Optics" not in [s.name for s in moodle.db.get_course(101).sections]


def test_dry_run_reports_permission_problems_like_a_real_run() -> None:
    moodle = FakeMoodle(not_editable={101})
    # --all-courses: the course is listed even though the user cannot edit it
    interpreter = Interpreter(DryRunBackend(MoodleBackend(moodle, editable_only=False)), dry_run=True)
    with pytest.raises(Exception, match="cannot edit"):
        run(interpreter, "cd PHY1; mkdir Optics")


def test_dry_run_keeps_moodle_rules(backend: InMemoryBackend) -> None:
    dry = DryRunBackend(backend)
    delegated = dry.get_course(101).sections[1].subsections[0].section
    with pytest.raises(InvalidOperationError, match="inside a subsection"):
        dry.add_subsection(101, delegated.id, name="Nested")


def test_journal_records_successes_and_failures(tmp_path: Path, backend: InMemoryBackend) -> None:
    path = tmp_path / "changes.jsonl"
    journaled = JournalingBackend(backend, Journal(path, "https://moodle.example.org"))
    created = journaled.add_section(101, 2, "Optics")
    with pytest.raises(InvalidOperationError):
        journaled.add_section(101, 99, "Nowhere")

    first, second = (json.loads(line) for line in path.read_text().splitlines())
    assert first["action"] == "add_section"
    assert first["site"] == "https://moodle.example.org"
    assert first["course"] == 101
    assert first["args"] == {"position": 2, "name": "Optics"}
    assert first["ok"] is True
    assert first["result"] == {"section_id": created.id, "number": 2}
    assert second["ok"] is False
    assert "invalid position" in second["error"]
    assert path.stat().st_mode & 0o777 == 0o600


def test_journal_is_not_written_for_reads(tmp_path: Path, backend: InMemoryBackend) -> None:
    path = tmp_path / "changes.jsonl"
    journaled = JournalingBackend(backend, Journal(path, "x"))
    journaled.list_courses()
    journaled.get_course(101)
    assert not path.exists()


def test_journaling_backend_forwards_preflight(tmp_path: Path) -> None:
    moodle = FakeMoodle(not_editable={101})
    journaled = JournalingBackend(MoodleBackend(moodle), Journal(tmp_path / "j.jsonl", "x"))
    with pytest.raises(BackendError, match="cannot edit"):
        journaled.check_writable(101)


def test_journal_records_a_partial_change_with_what_was_created(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    moodle = FakeMoodle()

    def refuse(**_: object) -> None:
        raise BackendError("connection lost")

    monkeypatch.setattr(moodle, "_core_update_inplace_editable", refuse)
    path = tmp_path / "changes.jsonl"
    journaled = JournalingBackend(MoodleBackend(moodle), Journal(path, "x"))
    with pytest.raises(PartialChangeError):
        journaled.add_section(101, 2, "Optics")

    [entry] = (json.loads(line) for line in path.read_text().splitlines())
    created = moodle.db.get_course(101).sections[2]
    assert entry["ok"] is False
    assert entry["partial"] is True
    assert entry["result"] == {"section_id": created.id, "number": 2}
    assert "could not be named 'Optics'" in entry["error"]


def test_journal_partial_change_without_identified_item(tmp_path: Path) -> None:
    moodle = FakeMoodle(concurrent_change=lambda db: db.add_section(101))
    path = tmp_path / "changes.jsonl"
    journaled = JournalingBackend(MoodleBackend(moodle), Journal(path, "x"))
    with pytest.raises(PartialChangeError):
        journaled.add_section(101, None, "Optics")
    [entry] = (json.loads(line) for line in path.read_text().splitlines())
    assert entry["partial"] is True
    assert entry["result"] is None


@pytest.mark.parametrize("backend_class", [InMemoryBackend, MoodleBackend, DryRunBackend, JournalingBackend])
def test_every_backend_implements_every_operation(backend_class: type) -> None:
    # Abstract methods left unimplemented would make the class impossible to create.
    assert not backend_class.__abstractmethods__


def test_a_safety_layer_missing_an_operation_cannot_be_created(backend: InMemoryBackend) -> None:
    # What would happen if a new write operation were added to CourseBackend and
    # forgotten in a safety layer: the layer fails as soon as it is created.
    class Incomplete(CourseBackend):
        def list_courses(self, name_filter: str | None = None) -> list[CourseSummary]:
            return backend.list_courses(name_filter)

    with pytest.raises(TypeError, match="abstract"):
        Incomplete()  # type: ignore[abstract]


def test_journaling_backend_has_no_catch_all_forwarding() -> None:
    assert "__getattr__" not in vars(JournalingBackend)
