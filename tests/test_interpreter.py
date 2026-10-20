from __future__ import annotations

import pytest

from moodlesh.cli.errors import DslSyntaxError, ExitRequested, UsageError
from moodlesh.cli.interpreter import Interpreter, ScriptError, compile_script
from moodlesh.core.errors import InvalidOperationError, NotFoundError
from moodlesh.memory import InMemoryBackend

from conftest import run


def test_ls_courses_with_filter(interpreter: Interpreter) -> None:
    out = run(interpreter, "ls -f calculus")
    assert "CALC1" in out
    assert "PHY1" not in out


def test_cd_and_pwd(interpreter: Interpreter) -> None:
    assert run(interpreter, "cd PHY1; cd 1; pwd") == "/PHY1/Kinematics"
    assert run(interpreter, "cd; pwd") == "/"


def test_ls_section_dirs_only(interpreter: Interpreter) -> None:
    out = run(interpreter, "cd /PHY1/1; ls -d")
    assert "Exercises/" in out
    assert "Linear motion notes" not in out


def test_mkdir_section_at_position(interpreter: Interpreter, backend: InMemoryBackend) -> None:
    out = run(interpreter, 'cd PHY1; mkdir "Optics" --at 2')
    assert "created section 2 'Optics'" in out
    assert [s.name for s in backend.get_course(101).sections] == [
        "General",
        "Kinematics",
        "Optics",
        "Dynamics",
    ]


def test_mkdir_section_appends_by_default(interpreter: Interpreter, backend: InMemoryBackend) -> None:
    run(interpreter, "mkdir Waves --into /PHY1")
    assert backend.get_course(101).sections[-1].name == "Waves"


def test_mkdir_subsection_at_position(interpreter: Interpreter, backend: InMemoryBackend) -> None:
    out = run(interpreter, "cd /PHY1/Kinematics; mkdir Theory --at 1")
    assert "created subsection 'Theory' in 'Kinematics'" in out
    names = [i.name for i in backend.get_course(101).sections[1].items]
    assert names == ["Theory", "Linear motion notes", "Exercises", "Lab report"]


def test_mkdir_refreshes_the_cache(interpreter: Interpreter) -> None:
    run(interpreter, "cd PHY1; ls")  # fills the cache
    assert "Optics" in run(interpreter, "mkdir Optics; ls")


@pytest.mark.parametrize(
    ("script", "error"),
    [
        ("mkdir Course", InvalidOperationError),  # at the root
        ("cd /PHY1/1/Exercises; mkdir Nested", InvalidOperationError),
        ("cd PHY1; mkdir X --at 9", InvalidOperationError),
        ("rename /PHY1 New", InvalidOperationError),
        ("cd /PHY1/7", NotFoundError),
    ],
)
def test_invalid_operations(interpreter: Interpreter, script: str, error: type[Exception]) -> None:
    with pytest.raises(ScriptError) as info:
        run(interpreter, script)
    assert isinstance(info.value.cause, error)


def test_rename_section(interpreter: Interpreter, backend: InMemoryBackend) -> None:
    run(interpreter, 'rename /PHY1/1/Exercises "Practice"')
    assert backend.get_course(101).sections[1].subsections[0].name == "Practice"


def test_script_is_validated_before_anything_runs(interpreter: Interpreter, backend: InMemoryBackend) -> None:
    before = backend.get_course(101)
    with pytest.raises(ScriptError) as info:
        run(interpreter, "mkdir First --into /PHY1\nls --bogus")
    assert info.value.line == 2
    assert isinstance(info.value.cause, UsageError)
    assert backend.get_course(101) == before  # the first line did not run


def test_execution_stops_at_first_runtime_error(interpreter: Interpreter, backend: InMemoryBackend) -> None:
    outputs: list[object] = []
    with pytest.raises(ScriptError) as info:
        outputs.extend(interpreter.run("mkdir First --into /PHY1\ncd /NOPE\nmkdir After --into /PHY1"))
    assert info.value.line == 2
    assert len(outputs) == 1  # the first line's output arrived before the error
    names = [s.name for s in backend.get_course(101).sections]
    assert "First" in names
    assert "After" not in names


def test_compile_reports_syntax_errors() -> None:
    with pytest.raises(DslSyntaxError):
        compile_script('ls "open')


def test_exit(interpreter: Interpreter) -> None:
    with pytest.raises(ExitRequested):
        run(interpreter, "exit")


def test_help(interpreter: Interpreter) -> None:
    assert "mkdir" in run(interpreter, "help")
    assert "--into PATH" in run(interpreter, "help mkdir")


def test_refresh_moves_up_if_location_disappeared(backend: InMemoryBackend) -> None:
    interpreter = Interpreter(backend)
    run(interpreter, "cd /PHY1/1")
    backend._courses[101].sections.pop(1)  # the section disappears "from outside"
    run(interpreter, "refresh")
    assert run(interpreter, "pwd") == "/PHY1"
