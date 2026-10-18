"""
Split a class that's too big for one module into mixins, moving its methods verbatim, before split.py places them.

    python .claude/skills/modularize/mixins.py SOURCE.py PLAN.py OUT.py

PLAN.py defines MIXINS = {"LambdaExplorer": [("_LogsTab", "the mixin's docstring", ["_set_range", ...]), ...]}:
for each class, its mixins in the order they go in its bases, each with the members it takes. Each member moves with
the comments above it into a new class before the original, which lists the mixins first in its bases and keeps every
member the plan doesn't name (its docstring, attributes, __init__ and anything that calls super()). Methods keep their
names, so the class has the same attributes and its MRO finds each method where it did.
"""

from __future__ import annotations

import ast
import importlib.util
import sys
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType


@dataclass
class Member:
    """
    One member of a class body: its name and the lines it owns (the comments above it, its decorators, its body).
    """

    name: str
    owned_from: int
    first_line: int
    last_line: int


def load_plan(path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location("plan", path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"can't load the plan {path}")
    plan = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(plan)
    return plan


def member_name(node: ast.stmt) -> str | None:
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return node.name
    return None


def class_members(cls: ast.ClassDef) -> list[Member]:
    """
    The class's methods with the lines each owns, from just after the member before it.
    """
    members: list[Member] = []
    previous_end = cls.body[0].end_lineno or cls.body[0].lineno
    for node in cls.body[1:]:
        name = member_name(node)
        first = min([node.lineno] + [d.lineno for d in getattr(node, "decorator_list", [])])
        last = node.end_lineno or node.lineno
        if name is not None:
            members.append(Member(name, previous_end + 1, first, last))
        previous_end = last
    return members


def member_text(lines: list[str], member: Member) -> list[str]:
    """
    A member's lines with the comments above it, without the blank lines before them.
    """
    owned = lines[member.owned_from - 1:member.last_line]
    while owned and not owned[0].strip():
        owned.pop(0)
    return owned


def mixin_text(name: str, docstring: str, members: list[Member], lines: list[str]) -> list[str]:
    """
    A mixin class holding the members, each separated from the next by one blank line.
    """
    text = [f"class {name}:", '    """', *[f"    {line}" if line else "" for line in docstring.splitlines()], '    """']
    for member in members:
        text += [""] + member_text(lines, member)
    return text


def split_class(lines: list[str], cls: ast.ClassDef, mixins: list[tuple[str, str, list[str]]]) -> list[str]:
    """
    The source with the class's planned members moved into mixins placed before it, and the mixins in its bases.
    """
    members = {member.name: member for member in class_members(cls)}
    moved: set[int] = set()
    blocks: list[list[str]] = []
    for name, docstring, wanted in mixins:
        missing = [member for member in wanted if member not in members]
        if missing:
            raise SystemExit(f"{cls.name} has no {missing}")
        chosen = sorted((members[member] for member in wanted), key=lambda m: m.first_line)
        for member in chosen:
            moved.update(range(member.owned_from, member.last_line + 1))
        blocks.append(mixin_text(name, docstring, chosen, lines))

    header = lines[cls.lineno - 1]
    bases = ", ".join([name for name, _, _ in mixins] + [ast.unparse(base) for base in cls.bases])
    new_header = f"class {cls.name}({bases}):"
    if not header.startswith(f"class {cls.name}"):
        raise SystemExit(f"line {cls.lineno}: the class statement isn't on one line")

    before = lines[:cls.lineno - 1]
    kept = [line for number, line in enumerate(lines[cls.lineno:cls.end_lineno], cls.lineno + 1) if number not in moved]
    after = lines[cls.end_lineno:]
    mixin_lines: list[str] = []
    for block in blocks:
        mixin_lines += block + ["", ""]
    return before + mixin_lines + [new_header] + kept + after


def main() -> int:
    source_path, plan_path, out_path = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
    plan = load_plan(plan_path)
    text = source_path.read_text(encoding="utf-8")
    for class_name, mixins in plan.MIXINS.items():
        tree = ast.parse(text)
        cls = next((node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == class_name), None)
        if cls is None:
            raise SystemExit(f"no top-level class {class_name}")
        text = "\n".join(split_class(text.splitlines(), cls, mixins)) + "\n"
    out_path.write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
