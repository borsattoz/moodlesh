"""Tab completion for the interactive shell (prompt_toolkit)."""

from __future__ import annotations

import re
from collections.abc import Iterable

from prompt_toolkit.completion import CompleteEvent, Completer, Completion
from prompt_toolkit.document import Document

from moodlesh.cli.commands import CommandSpec, command_names, lookup
from moodlesh.cli.commands import Completion as Kind
from moodlesh.cli.interpreter import Interpreter
from moodlesh.core.errors import MoodleshError

# Rough tokens of the current line: strings (possibly unclosed) or words.
_TOKEN_RE = re.compile(r'"(?:\\.|[^"\\])*"?|\'[^\']*\'?|[^\s;]+')
_NEEDS_QUOTES = re.compile(r"[\s;\"'#]")


class DslCompleter(Completer):
    """Complete command names, options and paths at the current location."""

    def __init__(self, interpreter: Interpreter) -> None:
        self._interpreter = interpreter

    def get_completions(self, document: Document, complete_event: CompleteEvent) -> Iterable[Completion]:
        text = document.text_before_cursor.split(";")[-1].split("\n")[-1]
        tokens = _TOKEN_RE.findall(text)
        current = "" if not tokens or text[-1:].isspace() else tokens.pop()

        if not tokens:
            yield from _complete_words(current, command_names())
            return
        cls = lookup(tokens[0])
        if cls is None:
            return
        spec = cls.spec
        if current.startswith("-"):
            options = [f"--{o.name}" for o in spec.options]
            yield from _complete_words(current, options)
            return

        kind = _expected_kind(spec, tokens[1:])
        if kind is Kind.COMMAND:
            yield from _complete_words(current, command_names())
        elif kind is Kind.PATH:
            yield from self._complete_path(current)

    def _complete_path(self, current: str) -> Iterable[Completion]:
        raw = current.strip("\"'")
        base, _, partial = raw.rpartition("/")
        prefix = base + "/" if raw.count("/") else ""
        try:
            location = self._interpreter.navigator.resolve(
                self._interpreter.cwd, base or ("/" if prefix else None)
            )
            entries = self._interpreter.navigator.entries(location)
        except MoodleshError:
            return
        needle = partial.casefold()
        for entry in entries:
            if not entry.is_container:
                continue
            for candidate in (entry.name, *entry.aliases, entry.key):
                if candidate.casefold().startswith(needle):
                    value = prefix + candidate
                    if _NEEDS_QUOTES.search(value):
                        value = '"' + value.replace('"', '\\"') + '"'
                    yield Completion(
                        value,
                        start_position=-len(current),
                        display=candidate,
                        display_meta=entry.key if candidate == entry.name else entry.name,
                    )
                    break


def _expected_kind(spec: CommandSpec, args: list[str]) -> Kind:
    """Kind of completion for the next argument, given the ones already typed."""
    positional = 0
    expecting: Kind | None = None
    for token in args:
        if expecting is not None:
            expecting = None
            continue
        if token.startswith("-"):
            opt = spec.find_option(token.lstrip("-").split("=")[0])
            if opt is not None and not opt.is_flag and "=" not in token:
                expecting = opt.completion
            continue
        positional += 1
    if expecting is not None:
        return expecting
    if positional < len(spec.params):
        return spec.params[positional].completion
    return Kind.NONE


def _complete_words(current: str, words: Iterable[str]) -> Iterable[Completion]:
    for word in words:
        if word.startswith(current):
            yield Completion(word, start_position=-len(current))
