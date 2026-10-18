"""
Split a service module into a package by a plan, moving each top-level statement verbatim, with the comments above
it, into the module the plan names, and writing each module's imports from the names it uses.

    python .claude/skills/modularize/split.py SOURCE.py PLAN.py OUT_DIR

PLAN.py (written in the scratchpad, one per service) defines:

    MODULES = {"prices": ["INSTANCE_TYPES", ...], "helpers": ["KB, MB, GB, TB", "_int", ...],
               "analysis/parsing": [...], ...}     # in layer order; a tuple assignment is named "KB, MB, GB, TB"
    DOCS = {"prices": "the module's docstring", "analysis": "the analysis/ package's", ...}
    REEXPORT_KIT = ["human_size", ...]             # public _kit names the old module exposed, re-exported

Every top-level statement must be named exactly once; imports and the module docstring are handled here. A module
that uses a name from a later module stops the split (reorder the plan), unless it only uses it in type hints,
which go under `if TYPE_CHECKING:`. OUT_DIR gets the package: each module, an __init__.py with the old module's
docstring that re-exports every public name in __all__, and an __init__.py with a docstring for each sub-package.

Imports are written generously (a name a function shadows locally still gets imported), so run
`ruff check OUT_DIR --select F401 --fix` after placing the package. Then `python split.py --report OUT_DIR RULES`
prints what each module needs in ruff.toml's per-file-ignores (RULES: the old file's, comma-separated) and in
tests/test_style.py's COMMENTS_LEFT.
"""

from __future__ import annotations

import ast
import importlib.util
import io
import re
import subprocess
import sys
import tokenize
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType

BANNER = re.compile(r"^# (=+|\d+\. .*)$")
ALLOWED_COMMENT = re.compile(r"#(!| =+$| \d+\. | read-only:| noqa\b| type: ignore| pragma: )")
MAX_LINE = 120
BLANK_LINES_BETWEEN_MOVED = 2


@dataclass
class Statement:
    """
    One top-level statement of the source: its name in the plan, its node, and the lines it owns (from just after
    the statement before it, so the comments above it come along).
    """

    name: str
    node: ast.stmt
    owned_from: int

    @property
    def first_line(self) -> int:
        decorators = getattr(self.node, "decorator_list", [])
        return min([self.node.lineno] + [d.lineno for d in decorators])

    @property
    def last_line(self) -> int:
        return self.node.end_lineno or self.node.lineno


@dataclass
class Source:
    """
    The module being split: its lines, docstring, the imports at its top (name -> how it was imported) and its
    other top-level statements in order.
    """

    lines: list[str]
    docstring: str
    imports: dict[str, tuple] = field(default_factory=dict)
    statements: list[Statement] = field(default_factory=list)


@dataclass
class Needs:
    """
    What one new module imports: names from the old module's imports, and names from other new modules (at run
    time, or only for type hints).
    """

    imported: set[str] = field(default_factory=set)
    local: dict[str, set[str]] = field(default_factory=dict)
    typing_only: dict[str, set[str]] = field(default_factory=dict)

    def add_local(self, name: str, target: str, *, is_later: bool) -> None:
        """
        A name another new module defines: imported at run time from an earlier module, or for type hints only
        from a later one.
        """
        bucket = self.typing_only if is_later else self.local
        bucket.setdefault(target, set()).add(name)


def statement_name(node: ast.stmt) -> str:
    """
    How a plan names a statement: a function's or class's name, an assignment's targets ("KB, MB, GB, TB").
    """
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return node.name
    if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
        return node.target.id
    if isinstance(node, ast.Assign):
        return " = ".join(", ".join(_target_names(target)) for target in node.targets)
    raise SystemExit(f"line {node.lineno}: a top-level {type(node).__name__} has no name a plan can use")


def _target_names(target: ast.expr) -> list[str]:
    """
    The names an assignment target binds: `x` -> ["x"], `a, b` -> ["a", "b"].
    """
    elements = target.elts if isinstance(target, ast.Tuple) else [target]
    return [element.id for element in elements if isinstance(element, ast.Name)]


def defined_names(node: ast.stmt) -> list[str]:
    """
    Every name a top-level statement binds.
    """
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return [node.name]
    if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
        return [node.target.id]
    if isinstance(node, ast.Assign):
        return [name for target in node.targets for name in _target_names(target)]
    return []


def _annotations(node: ast.AST) -> list[ast.expr]:
    """
    The type hints written in a statement: its functions' parameters and returns, and annotated assignments.
    """
    hints: list[ast.expr] = []
    for sub in ast.walk(node):
        if isinstance(sub, ast.AnnAssign):
            hints.append(sub.annotation)
        if not isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        arguments = sub.args
        for argument in [*arguments.posonlyargs, *arguments.args, *arguments.kwonlyargs, arguments.vararg,
                         arguments.kwarg]:
            if argument is not None and argument.annotation is not None:
                hints.append(argument.annotation)
        if sub.returns is not None:
            hints.append(sub.returns)
    return hints


def used_names(node: ast.stmt) -> tuple[set[str], set[str]]:
    """
    (the names a statement reads at run time, the names it reads only in type hints).
    """
    in_hints = {id(sub) for hint in _annotations(node) for sub in ast.walk(hint)}
    at_run_time: set[str] = set()
    in_hints_only: set[str] = set()
    for sub in ast.walk(node):
        if not (isinstance(sub, ast.Name) and isinstance(sub.ctx, ast.Load)):
            continue
        (in_hints_only if id(sub) in in_hints else at_run_time).add(sub.id)
    return at_run_time, in_hints_only - at_run_time


def read_source(path: Path) -> Source:
    """
    The module to split, its top-level statements in order, each owning the lines after the one before it.
    """
    text = path.read_text(encoding="utf-8")
    tree = ast.parse(text)
    source = Source(lines=text.splitlines(), docstring=ast.get_docstring(tree, clean=False) or "")
    previous_end = tree.body[0].end_lineno or 1
    for node in tree.body[1:]:
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                source.imports[alias.asname or alias.name] = ("from", node.module, node.level, alias.name,
                                                              alias.asname)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                source.imports[alias.asname or alias.name.split(".")[0]] = ("import", alias.name, alias.asname)
        else:
            source.statements.append(Statement(statement_name(node), node, previous_end + 1))
        previous_end = node.end_lineno or node.lineno
    return source


def load_plan(path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location("plan", path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"can't load the plan {path}")
    plan = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(plan)
    return plan


def place_statements(source: Source, plan: ModuleType) -> dict[str, str]:
    """
    Each statement's name -> the module the plan puts it in, after checking every statement is placed once.
    """
    known = {statement.name for statement in source.statements}
    placed: dict[str, str] = {}
    for module, names in plan.MODULES.items():
        for name in names:
            if name not in known:
                raise SystemExit(f"the plan names {name!r}, which isn't a top-level statement")
            if name in placed:
                raise SystemExit(f"{name!r} is in both {placed[name]} and {module}")
            placed[name] = module
    unplaced = [statement.name for statement in source.statements if statement.name not in placed]
    if unplaced:
        raise SystemExit(f"the plan doesn't place {unplaced}")
    return placed


def statement_text(source: Source, statement: Statement) -> list[str]:
    """
    A statement's lines with the comments above it, without the section banners or leading blank lines.
    """
    above = source.lines[statement.owned_from - 1:statement.first_line - 1]
    kept = [line for line in above if not BANNER.match(line)]
    while kept and not kept[0].strip():
        kept.pop(0)
    return kept + source.lines[statement.first_line - 1:statement.last_line]


def blank_lines_before(source: Source, statement: Statement, follows_in_source: bool) -> int:
    """
    The blank lines between this statement and the one before it in its new module: the source's own spacing when
    the two were neighbours there, else two.
    """
    if not follows_in_source:
        return BLANK_LINES_BETWEEN_MOVED
    between = source.lines[statement.owned_from - 1:statement.first_line - 1]
    if any(BANNER.match(line) for line in between):
        return BLANK_LINES_BETWEEN_MOVED
    blanks = sum(1 for line in between if not line.strip())
    return min(max(blanks, 1), BLANK_LINES_BETWEEN_MOVED)


def module_body(source: Source, statements: list[Statement]) -> str:
    """
    The moved statements of one module, in their order in the source.
    """
    position = {id(statement): i for i, statement in enumerate(source.statements)}
    pieces: list[str] = []
    previous: int | None = None
    for statement in sorted(statements, key=lambda s: position[id(s)]):
        here = position[id(statement)]
        if pieces:
            follows = previous is not None and here == previous + 1
            pieces.append("\n" * blank_lines_before(source, statement, follows))
        pieces.append("\n".join(statement_text(source, statement)) + "\n")
        previous = here
    return "".join(pieces)


def module_needs(module: str, statements: list[Statement], source: Source, home: dict[str, str],
                 order: list[str]) -> Needs:
    """
    What a module must import for the names its statements read.
    """
    own = {name for statement in statements for name in defined_names(statement.node)}
    at_run_time: set[str] = set()
    in_hints_only: set[str] = set()
    for statement in statements:
        run_time, hints = used_names(statement.node)
        at_run_time |= run_time
        in_hints_only |= hints
    in_hints_only -= at_run_time

    needs = Needs()
    for name in sorted(at_run_time | in_hints_only):
        if name in own:
            continue
        if name in home:
            is_later = order.index(home[name]) > order.index(module)
            if is_later and name not in in_hints_only:
                raise SystemExit(f"{module} reads {name} from {home[name]}, a later module: reorder the plan")
            needs.add_local(name, home[name], is_later=is_later)
        elif name in source.imports:
            needs.imported.add(name)
    return needs


def isort_key(name: str) -> tuple[int, str]:
    """
    ruff's isort order within one import: constants, then classes, then the rest, each case-sensitively.
    """
    bare = name.lstrip("_")
    if len(bare) > 1 and bare.isupper():
        return (0, name)
    if bare[:1].isupper():
        return (1, name)
    return (2, name)


def wrapped(prefix: str, names: list[str]) -> list[str]:
    """
    `from x import a, b` on one line, or in brackets over several when it's too long.
    """
    line = prefix + ", ".join(names)
    if len(line) <= MAX_LINE:
        return [line]
    lines, current = [prefix + "("], "    "
    for name in names:
        if len(current) + len(name) + 2 > MAX_LINE - 2:
            lines.append(current.rstrip())
            current = "    "
        current += f"{name}, "
    return lines + [current.rstrip(), ")"]


def relative(target: str, module: str) -> str:
    """
    How `module` names `target` in a relative import: "analysis/findings" names "models" as "..models" and
    "analysis/parsing" as ".parsing".
    """
    own_folders = module.split("/")[:-1]
    target_parts = target.split("/")
    shared = 0
    while shared < len(own_folders) and shared < len(target_parts) - 1 and own_folders[shared] == target_parts[shared]:
        shared += 1
    return "." * (len(own_folders) - shared + 1) + ".".join(target_parts[shared:])


def imported_lines(names: set[str], source: Source, depth: int) -> list[str]:
    """
    The old module's imports a new module needs, grouped like the old one's: stdlib, third party, the package.
    """
    plain: list[str] = []
    groups: tuple[dict[str, list[str]], ...] = ({}, {}, {})
    for name in names:
        how = source.imports[name]
        if how[0] == "import":
            plain.append(f"import {how[1]}" + (f" as {how[2]}" if how[2] else ""))
            continue
        _, module, level, original, asname = how
        shown = original + (f" as {asname}" if asname else "")
        if level:
            groups[2].setdefault("." * (level + depth) + module, []).append(shown)
        elif module.split(".")[0] in sys.stdlib_module_names:
            groups[0].setdefault(module, []).append(shown)
        else:
            groups[1].setdefault(module, []).append(shown)

    lines = sorted(plain)
    for i, group in enumerate(groups):
        if group and (i > 0 and lines):
            lines.append("")
        for module in sorted(group):
            lines += wrapped(f"from {module} import ", sorted(group[module], key=isort_key))
    return lines


def local_lines(needed: dict[str, set[str]], module: str, order: list[str]) -> list[str]:
    lines: list[str] = []
    for target in sorted(needed, key=order.index):
        lines += wrapped(f"from {relative(target, module)} import ", sorted(needed[target], key=isort_key))
    return lines


def module_header(module: str, docstring: str, needs: Needs, source: Source, order: list[str]) -> list[str]:
    """
    A new module's docstring and imports.
    """
    depth = module.count("/") + 1
    lines = ['"""', docstring, '"""', "", "from __future__ import annotations", ""]
    imports = imported_lines(needs.imported, source, depth)
    if needs.typing_only:
        imports = _with_type_checking(imports)
    lines += imports
    local = local_lines(needs.local, module, order)
    if local:
        lines += ([""] if imports else []) + local
    if needs.typing_only:
        lines += ["", "if TYPE_CHECKING:"]
        lines += ["    " + line for line in local_lines(needs.typing_only, module, order)]
    return lines


def _with_type_checking(imports: list[str]) -> list[str]:
    for i, line in enumerate(imports):
        if line.startswith("from typing import ") and not line.endswith("("):
            names = [name.strip() for name in line[len("from typing import "):].split(",")]
            imports[i] = "from typing import " + ", ".join(sorted({*names, "TYPE_CHECKING"}, key=isort_key))
            return imports
    return ["from typing import TYPE_CHECKING", *imports]


def package_init(source: Source, plan: ModuleType, home: dict[str, str], order: list[str]) -> str:
    """
    The package's __init__.py: the old module's docstring, its public names re-exported, and __all__.
    """
    lines = [f'"""{source.docstring}"""', "", "from __future__ import annotations", ""]
    kit: dict[str, list[str]] = {}
    for name in plan.REEXPORT_KIT:
        _, module, level, _original, _asname = source.imports[name]
        kit.setdefault("." * (level + 1) + module, []).append(name)
    for module in sorted(kit):
        lines += wrapped(f"from {module} import ", sorted(kit[module], key=isort_key))
    public = {name: target for name, target in home.items() if not name.startswith("_")}
    for target in order:
        names = sorted((name for name, where in public.items() if where == target), key=isort_key)
        if names:
            lines += wrapped(f"from .{target.replace('/', '.')} import ", names)
    exported = sorted([*public, *plan.REEXPORT_KIT], key=isort_key)
    lines += ["", "__all__ = ["] + [f'    "{name}",' for name in exported] + ["]"]
    return "\n".join(lines) + "\n"


def write_package(source_path: Path, plan_path: Path, out_dir: Path) -> None:
    source = read_source(source_path)
    plan = load_plan(plan_path)
    placed = place_statements(source, plan)
    order = list(plan.MODULES)
    home = {name: placed[statement.name] for statement in source.statements for name in defined_names(statement.node)}

    out_dir.mkdir(parents=True, exist_ok=True)
    for module in order:
        statements = [statement for statement in source.statements if placed[statement.name] == module]
        needs = module_needs(module, statements, source, home, order)
        header = module_header(module, plan.DOCS[module], needs, source, order)
        path = out_dir / f"{module}.py"
        path.parent.mkdir(parents=True, exist_ok=True)
        _write_subpackage_init(path.parent, out_dir, module, plan)
        text = "\n".join(header).rstrip("\n") + "\n\n\n" + module_body(source, statements)
        path.write_text(text, encoding="utf-8")
    (out_dir / "__init__.py").write_text(package_init(source, plan, home, order), encoding="utf-8")


def _write_subpackage_init(folder: Path, out_dir: Path, module: str, plan: ModuleType) -> None:
    init = folder / "__init__.py"
    if folder == out_dir or init.exists():
        return
    docstring = plan.DOCS[module.rsplit("/", 1)[0]]
    init.write_text(f'"""\n{docstring}\n"""\n\nfrom __future__ import annotations\n', encoding="utf-8")


def comment_count(path: Path) -> int:
    """
    The `#` comments tests/test_style.py counts in a file.
    """
    tokens = tokenize.generate_tokens(io.StringIO(path.read_text(encoding="utf-8")).readline)
    return sum(1 for token in tokens if token.type == tokenize.COMMENT and not ALLOWED_COMMENT.match(token.string))


def report(package: Path, old_rules: str) -> None:
    """
    Print each module's entry for ruff.toml's per-file-ignores (only the old file's rules it still breaks) and for
    COMMENTS_LEFT, with paths from the repository's root.
    """
    root = Path(__file__).resolve().parents[3]
    beside_python = Path(sys.executable).parent / "ruff"
    ruff = str(beside_python) if beside_python.exists() else "ruff"
    result = subprocess.run([ruff, "check", str(package), "--isolated", "--select", old_rules,
                             "--output-format", "concise"], capture_output=True, text=True, check=False)
    broken: dict[str, set[str]] = {}
    for line in result.stdout.splitlines():
        found = re.match(r"(.+?\.py):\d+:\d+: ([A-Z]+\d+)", line)
        if found:
            path = Path(found.group(1)).resolve().relative_to(root).as_posix()
            broken.setdefault(path, set()).add(found.group(2))
    print("ruff.toml [lint.per-file-ignores]:")
    for path in sorted(broken):
        print(f'"{path}" = [{", ".join(chr(34) + rule + chr(34) for rule in sorted(broken[path]))}]')
    print("\ntests/test_style.py COMMENTS_LEFT:")
    for path in sorted(package.rglob("*.py")):
        count = comment_count(path)
        if count:
            print(f'    "{path.resolve().relative_to(root).as_posix()}": {count},')


def main() -> int:
    if sys.argv[1] == "--report":
        report(Path(sys.argv[2]), sys.argv[3])
        return 0
    write_package(Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
