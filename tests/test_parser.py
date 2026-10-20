from __future__ import annotations

import pytest

from moodlesh.cli.dsl.ast import Option, Value
from moodlesh.cli.dsl.parser import parse
from moodlesh.cli.errors import DslSyntaxError


def test_empty_and_comment_only_scripts_have_no_commands() -> None:
    assert parse("") == []
    assert parse("  \n# just a comment\n\n") == []


def test_command_with_values_and_options() -> None:
    [cmd] = parse('mkdir "Worked examples" --at 2 -i /PHY1/1')
    assert cmd.name == "mkdir"
    kinds = [type(a) for a in cmd.args]
    assert kinds == [Value, Option, Value, Option, Value]
    first = cmd.args[0]
    assert isinstance(first, Value)
    assert first.text == "Worked examples"
    assert first.quoted


def test_inline_option_value() -> None:
    [cmd] = parse('ls --filter="linear motion"')
    [opt] = cmd.args
    assert isinstance(opt, Option)
    assert opt.name == "filter"
    assert opt.inline_value is not None
    assert opt.inline_value.text == "linear motion"


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        (r'"with \"quotes\""', 'with "quotes"'),
        ("'single \\n literal'", "single \\n literal"),
        (r'"a\nb"', "a\nb"),
    ],
)
def test_string_escapes(source: str, expected: str) -> None:
    [cmd] = parse(f"rename 1 {source}")
    value = cmd.args[1]
    assert isinstance(value, Value)
    assert value.text == expected


def test_separators_comments_and_positions() -> None:
    commands = parse("cd PHY1 ; ls # list\n\n  tree -L 2;;pwd")
    assert [c.name for c in commands] == ["cd", "ls", "tree", "pwd"]
    assert commands[2].pos.line == 3
    assert commands[2].pos.column == 3


def test_hash_inside_word_is_not_a_comment() -> None:
    [cmd] = parse("cd /C#/1")
    value = cmd.args[0]
    assert isinstance(value, Value)
    assert value.text == "/C#/1"


def test_unclosed_quote_reports_position_with_caret() -> None:
    with pytest.raises(DslSyntaxError) as info:
        parse('ls\nmkdir "open')
    error = info.value
    assert (error.line, error.column) == (2, 7)
    assert "unclosed quotes" in error.message
    assert error.caret() == 'mkdir "open\n      ^'
