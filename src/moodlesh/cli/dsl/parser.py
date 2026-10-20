"""Lark-based DSL parser: text -> list of `RawCommand`."""

from __future__ import annotations

import re
from functools import cache
from importlib import resources

from lark import Lark, Token, Transformer, UnexpectedCharacters, UnexpectedInput, UnexpectedToken, v_args

from moodlesh.cli.dsl.ast import Argument, Option, Position, RawCommand, Value
from moodlesh.cli.errors import DslSyntaxError

_ESCAPES = {"n": "\n", "t": "\t", '"': '"', "\\": "\\"}
_ESCAPE_RE = re.compile(r"\\(.)")


@cache
def _lark() -> Lark:
    grammar = resources.files("moodlesh.cli.dsl").joinpath("grammar.lark").read_text(encoding="utf-8")
    return Lark(grammar, parser="lalr", propagate_positions=True, maybe_placeholders=False)


def _pos(token: Token) -> Position:
    return Position(token.line or 1, token.column or 1)


def _unquote(token: Token) -> str:
    raw = str(token)
    if raw.startswith("'"):
        return raw[1:-1]
    return _ESCAPE_RE.sub(lambda m: _ESCAPES.get(m.group(1), m.group(0)), raw[1:-1])


class _ToAst(Transformer[Token, list[RawCommand]]):
    @v_args(inline=True)
    def value(self, token: Token) -> Value:
        if token.type == "STRING":
            return Value(_unquote(token), quoted=True, pos=_pos(token))
        return Value(str(token), quoted=False, pos=_pos(token))

    def option(self, children: list[Token | Value]) -> Option:
        token = children[0]
        assert isinstance(token, Token)
        name = str(token).lstrip("-").rstrip("=")
        inline = children[1] if len(children) > 1 else None
        assert inline is None or isinstance(inline, Value)
        return Option(name, _pos(token), inline)

    def command(self, children: list[Token | Argument]) -> RawCommand:
        name_token = children[0]
        assert isinstance(name_token, Token)
        args = tuple(c for c in children[1:] if isinstance(c, Value | Option))
        return RawCommand(str(name_token), args, _pos(name_token))

    def start(self, children: list[RawCommand | Token]) -> list[RawCommand]:
        return [c for c in children if isinstance(c, RawCommand)]


def parse(text: str) -> list[RawCommand]:
    """Parse a script (one or more lines) and return its commands.

    Raises:
        DslSyntaxError: with the line and column of the error.
    """
    try:
        tree = _lark().parse(text)
    except UnexpectedInput as exc:
        raise _syntax_error(text, exc) from None
    return _ToAst().transform(tree)


def _char_at(text: str, pos: int | None) -> str:
    return text[pos : pos + 1] if pos is not None else ""


def _syntax_error(text: str, exc: UnexpectedInput) -> DslSyntaxError:
    line, column = exc.line, exc.column
    if isinstance(exc, UnexpectedToken) and exc.token.type == "$END":
        message = "unexpected end of command"
    elif isinstance(exc, UnexpectedCharacters) and _char_at(text, exc.pos_in_stream) in {'"', "'"}:
        message = "unclosed quotes"
    else:
        message = "invalid syntax"
    return DslSyntaxError(message, line=max(line, 1), column=max(column, 1), source=text)
