"""Presentation: tables for `ls`, the tree for `tree`, help texts.

The functions are pure: they take the model and return `rich` renderables
without printing anything. That keeps them easy to test, and the shell decides
where to write them.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from rich.console import Group, RenderableType
from rich.table import Table
from rich.text import Text

from moodlesh.cli.commands import COMMANDS, CommandSpec
from moodlesh.core.models import Activity, Course, CourseSummary, Section, SectionItem, Subsection

STYLE_SECTION = "bold cyan"
STYLE_SUBSECTION = "bold blue"
STYLE_DIM = "dim"
STYLE_HIDDEN = "italic dim"


# --------------------------------------------------------------------------- #
# Tables for ls
# --------------------------------------------------------------------------- #
def courses_table(courses: Sequence[CourseSummary]) -> RenderableType:
    if not courses:
        return Text("no courses found", style=STYLE_DIM)
    table = Table(box=None, header_style="bold", pad_edge=False)
    table.add_column("#", justify="right", style=STYLE_DIM)
    table.add_column("short name", style=STYLE_SECTION)
    table.add_column("name")
    table.add_column("category", style=STYLE_DIM)
    for c in courses:
        table.add_row(str(c.id), c.shortname, c.fullname, c.category)
    return table


def sections_table(sections: Sequence[Section]) -> RenderableType:
    if not sections:
        return Text("no sections", style=STYLE_DIM)
    table = Table(box=None, header_style="bold", pad_edge=False)
    table.add_column("#", justify="right", style=STYLE_DIM)
    table.add_column("section")
    table.add_column("contents", style=STYLE_DIM)
    for s in sections:
        table.add_row(str(s.number), _section_label(s), _content_summary(s))
    return table


def items_table(items: Sequence[tuple[int, SectionItem]]) -> RenderableType:
    """Items of a section, with their (1-based) position."""
    if not items:
        return Text("empty section", style=STYLE_DIM)
    table = Table(box=None, header_style="bold", pad_edge=False)
    table.add_column("#", justify="right", style=STYLE_DIM)
    table.add_column("type", style=STYLE_DIM)
    table.add_column("name")
    table.add_column("contents", style=STYLE_DIM)
    for position, item in items:
        if isinstance(item, Subsection):
            table.add_row(
                str(position),
                "subsection",
                _section_label(item.section, sub=True),
                _content_summary(item.section),
            )
        else:
            name = Text(item.name, style="" if item.visible else STYLE_HIDDEN)
            table.add_row(str(position), item.modname, name, "")
    return table


def _section_label(section: Section, *, sub: bool = False) -> Text:
    style = STYLE_SUBSECTION if sub else STYLE_SECTION
    label = Text(section.name + ("/" if sub else ""), style=style)
    if not section.visible:
        label.append(" (hidden)", style=STYLE_HIDDEN)
    return label


def _content_summary(section: Section) -> str:
    subs = len(section.subsections)
    acts = len(section.items) - subs
    parts = []
    if subs:
        parts.append(_plural(subs, "subsection", "subsections"))
    if acts:
        parts.append(_plural(acts, "activity", "activities"))
    return ", ".join(parts) or "empty"


def _plural(count: int, one: str, many: str) -> str:
    return f"{count} {one if count == 1 else many}"


# --------------------------------------------------------------------------- #
# Tree
# --------------------------------------------------------------------------- #
@dataclass
class _Node:
    label: Text
    kind: str  # "section" | "subsection" | "activity"
    children: list[_Node] = field(default_factory=list)


@dataclass
class _Counts:
    sections: int = 0
    subsections: int = 0
    activities: int = 0

    def add(self, kind: str) -> None:
        if kind == "section":
            self.sections += 1
        elif kind == "subsection":
            self.subsections += 1
        else:
            self.activities += 1

    def __str__(self) -> str:
        parts = [
            (self.sections, "section", "sections"),
            (self.subsections, "subsection", "subsections"),
            (self.activities, "activity", "activities"),
        ]
        text = ", ".join(_plural(n, one, many) for n, one, many in parts if n)
        return text or "empty"


def course_tree(course: Course, *, depth: int | None = None, dirs_only: bool = False) -> RenderableType:
    header = Text(course.name, style="bold")
    header.append(f"  {course.summary.shortname} · id {course.id}", style=STYLE_DIM)
    nodes = [_section_node(s) for s in course.sections]
    return _render_tree(header, nodes, depth, dirs_only)


def section_tree(section: Section, *, depth: int | None = None, dirs_only: bool = False) -> RenderableType:
    header = _section_label(section, sub=section.delegated)
    nodes = [_item_node(i, item) for i, item in enumerate(section.items, start=1)]
    return _render_tree(header, nodes, depth, dirs_only)


def courses_tree(courses: Sequence[CourseSummary]) -> RenderableType:
    header = Text("/", style="bold")
    nodes = [
        _Node(Text(f"{c.shortname}  ", style=STYLE_SECTION).append(c.fullname, style=""), "section")
        for c in courses
    ]
    tree = _render_tree(header, nodes, depth=1, dirs_only=False, footer=False)
    hint = Text("enter a course (cd) to see its structure", style=STYLE_DIM)
    return Group(tree, hint)


def _section_node(section: Section) -> _Node:
    label = Text(f"{section.number} ", style=STYLE_DIM).append_text(_section_label(section))
    children = [_item_node(i, item) for i, item in enumerate(section.items, start=1)]
    return _Node(label, "section", children)


def _item_node(position: int, item: SectionItem) -> _Node:
    prefix = Text(f"{position} ", style=STYLE_DIM)
    if isinstance(item, Subsection):
        node = _section_node(item.section)
        return _Node(prefix.append_text(_section_label(item.section, sub=True)), "subsection", node.children)
    assert isinstance(item, Activity)
    label = prefix.append(item.name, style="" if item.visible else STYLE_HIDDEN)
    label.append(f"  {item.modname}", style=STYLE_DIM)
    return _Node(label, "activity")


def _render_tree(
    header: Text,
    nodes: list[_Node],
    depth: int | None,
    dirs_only: bool,
    *,
    footer: bool = True,
) -> RenderableType:
    out = Text()
    out.append_text(header)
    counts = _Counts()
    _draw(out, nodes, prefix="", level=1, depth=depth, dirs_only=dirs_only, counts=counts)
    if footer:
        out.append("\n\n")
        out.append(str(counts), style=STYLE_DIM)
    return out


def _draw(
    out: Text,
    nodes: list[_Node],
    *,
    prefix: str,
    level: int,
    depth: int | None,
    dirs_only: bool,
    counts: _Counts,
) -> None:
    visible = [n for n in nodes if not (dirs_only and n.kind == "activity")]
    for index, node in enumerate(visible):
        last = index == len(visible) - 1
        out.append("\n" + prefix + ("└── " if last else "├── "), style=STYLE_DIM)
        out.append_text(node.label)
        counts.add(node.kind)
        if node.children and (depth is None or level < depth):
            _draw(
                out,
                node.children,
                prefix=prefix + ("    " if last else "│   "),
                level=level + 1,
                depth=depth,
                dirs_only=dirs_only,
                counts=counts,
            )


# --------------------------------------------------------------------------- #
# Help
# --------------------------------------------------------------------------- #
def help_overview() -> RenderableType:
    table = Table(box=None, show_header=False, pad_edge=False)
    table.add_column(style="bold")
    table.add_column()
    for cls in COMMANDS:
        spec = cls.spec
        aliases = f" ({', '.join(spec.aliases)})" if spec.aliases else ""
        table.add_row(spec.name + aliases, spec.summary)
    footer = Text.assemble(
        ("\nPaths: ", "bold"),
        "/ is the root (courses). Each segment can be the number ls shows in the # column, "
        "or the name (quoted if it contains spaces). .. and relative paths work too.\n",
        ("Scripts: ", "bold"),
        "several commands separated by ; or newlines; # starts a comment.\n",
        ("help COMMAND", "bold"),
        " shows the details of a command.",
    )
    return Group(table, footer)


def help_command(spec: CommandSpec) -> RenderableType:
    out = Text()
    out.append(spec.usage(), style="bold")
    out.append(f"\n\n{spec.summary}\n")
    if spec.aliases:
        out.append(f"\nalias: {', '.join(spec.aliases)}\n", style=STYLE_DIM)
    rows: list[tuple[str, str]] = [(p.display, p.help) for p in spec.params]
    for o in spec.options:
        flags = ", ".join(f for f in (f"-{o.short}" if o.short else "", f"--{o.name}") if f)
        rows.append((flags if o.is_flag else f"{flags} {o.metavar}", o.help))
    if rows:
        width = max(len(r[0]) for r in rows)
        out.append("\n")
        for name, text in rows:
            out.append(f"  {name.ljust(width)}  ", style="bold")
            out.append(f"{text}\n")
    if spec.examples:
        out.append("\nexamples:\n", style=STYLE_DIM)
        for example in spec.examples:
            out.append(f"  {example}\n")
    out.rstrip()
    return out
