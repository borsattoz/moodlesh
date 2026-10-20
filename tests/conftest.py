from __future__ import annotations

import io
import sys
from pathlib import Path

import pytest
from rich.console import Console, RenderableType

sys.path.insert(0, str(Path(__file__).parent))

from moodlesh.cli.interpreter import Interpreter
from moodlesh.memory import InMemoryBackend, demo_backend


@pytest.fixture
def backend() -> InMemoryBackend:
    return demo_backend()


@pytest.fixture
def interpreter(backend: InMemoryBackend) -> Interpreter:
    return Interpreter(backend)


def as_text(renderable: RenderableType, width: int = 120) -> str:
    """Render a rich object as plain text, without colours."""
    buffer = io.StringIO()
    Console(file=buffer, width=width, color_system=None).print(renderable)
    return "\n".join(line.rstrip() for line in buffer.getvalue().splitlines())


def run(interpreter: Interpreter, text: str) -> str:
    return "\n".join(as_text(o) for o in interpreter.run(text))
