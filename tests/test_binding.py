from __future__ import annotations

import pytest

from moodlesh.cli.commands import COMMANDS, Cd, Help, Ls, Mkdir, Rename, Tree, bind
from moodlesh.cli.dsl.parser import parse
from moodlesh.cli.errors import UsageError


def bind_one(text: str) -> object:
    [raw] = parse(text)
    return bind(raw)


def test_binds_positionals_options_and_flags() -> None:
    assert bind_one("ls /PHY1 -f motion -d") == Ls(path="/PHY1", filter="motion", dirs=True)
    assert bind_one("tree --depth=2") == Tree(depth=2)
    assert bind_one('mkdir "New" --at 3 --into ..') == Mkdir(name="New", at=3, into="..")
    assert bind_one("cd") == Cd()


def test_aliases() -> None:
    assert bind_one('mv 2 "Other"') == Rename(path="2", name="Other")
    assert bind_one("help mkdir") == Help(command="mkdir")


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("frobnicate", "unknown command"),
        ("ls --nope", "unknown option"),
        ("ls -d -d", "given twice"),
        ("ls --dirs=1", "takes no value"),
        ("mkdir X --at", "requires N"),
        ("mkdir X --at three", "requires an integer"),
        ("mkdir X --at 0", "at least 1"),
        ("mkdir", "missing NAME"),
        ("rename 2", "missing NAME"),
        ("pwd extra", "unexpected argument"),
    ],
)
def test_usage_errors(text: str, message: str) -> None:
    with pytest.raises(UsageError, match=message):
        bind_one(text)


def test_every_spec_is_coherent_with_its_dataclass() -> None:
    for cls in COMMANDS:
        fields = set(cls.__dataclass_fields__)  # type: ignore[attr-defined]
        declared = {p.name for p in cls.spec.params} | {o.dest for o in cls.spec.options}
        assert declared == fields, cls.__name__
