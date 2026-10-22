"""
A deployment package's source, searched like grep and read like a language server: which of its files are its own
(source_files), the lines that contain a text or match a pattern across them (search_package), and where a Python
name is defined and used (find_symbol_in). They read the .zip as code() downloads it, make no AWS calls, and run
nothing in it: Python is read with the ast module.
"""

from __future__ import annotations

import ast
import fnmatch
import io
import posixpath
import re
import zipfile
from typing import Callable

from ..._kit.text import _clip

from ..helpers import MB, runtime_family
from ..models import CodeMatch, CodePackage, CodeSearch, CodeSymbol, Function, SymbolReport, SymbolUse
from .package import _SECRET_FILE_RE, read_package

_SOURCE_EXTENSIONS = frozenset({".py", ".js", ".mjs", ".cjs", ".ts", ".rb", ".json", ".yaml", ".yml", ".toml",
                                ".cfg", ".ini", ".txt", ".sql", ".sh", ".html"})

_LIBRARY_PATH_RE = re.compile(r"(^|/)(node_modules|site-packages|__pycache__|[^/]+\.(?:dist|egg)-info)/")

_FILE_BYTES = 2 * MB

_READ_BYTES = 20 * MB

_BINARY_SNIFF = 8192

_PATTERN_CHARACTERS = 200

_REGEX_LINE_CHARACTERS = 1000

_NESTED_REPEAT_RE = re.compile(r"\([^()]*[*+}][^()]*\)[*+{]")

_CONTEXT_LINES = 2

_KEPT_MATCHES = 500

_TOP_LEVEL = "(module)"


def _library_names(paths: list[str]) -> set[str]:
    """
    The top-level names of the libraries packed in a package, from their .dist-info / .egg-info folders:
    'requests-2.32.3.dist-info' -> 'requests'.
    """
    names = set()
    for path in paths:
        first = path.split("/")[0]
        if first.endswith((".dist-info", ".egg-info")):
            names.add(re.split(r"-\d", first.rsplit(".", 1)[0])[0].lower().replace("-", "_"))
    return names


def source_files(pkg: CodePackage) -> list[str]:
    """
    A deployment package's own source files, the handler's first, then by folder depth and name: its text files
    (.py, .js, .json, .yaml, ...) outside the libraries packed with it (their .dist-info folders, node_modules,
    site-packages). Secrets files (.env, keys) aren't source and aren't listed.
    """
    paths = [f.path for f in pkg.files]
    libraries = _library_names(paths)
    own = []
    for path in paths:
        top = path.split("/")[0].removesuffix(".py").lower()
        is_source = posixpath.splitext(path)[1].lower() in _SOURCE_EXTENSIONS
        if is_source and top not in libraries and not _LIBRARY_PATH_RE.search(path):
            own.append(path)
    return sorted(own, key=lambda path: (path != pkg.handler_file, path.count("/"), path))


def _path_matches(path: str, wanted: str) -> bool:
    """
    Whether a path in the package is what files= names: the path itself, a glob over the path or the file's name
    ('*.py', 'lib/*.js'), or a folder ('lib', 'lib/') holding it.
    """
    wanted = wanted.strip().lstrip("/")
    if path == wanted or path.startswith(wanted.rstrip("/") + "/"):
        return True
    return fnmatch.fnmatchcase(path, wanted) or fnmatch.fnmatchcase(posixpath.basename(path), wanted)


def _chosen_files(pkg: CodePackage, files: str | None) -> list[str]:
    """
    The files a search reads: the package's own source files, or with files= (globs, folders or paths separated by
    commas) every file that matches, the libraries packed with it too, in source_files' order. Secrets files are
    never read.
    """
    if not files:
        return source_files(pkg)
    wanted = [part for part in files.split(",") if part.strip()]
    own = source_files(pkg)
    rest = sorted(f.path for f in pkg.files if f.path not in own)
    chosen = []
    for path in own + rest:
        if not _SECRET_FILE_RE.search(path) and any(_path_matches(path, w) for w in wanted):
            chosen.append(path)
    return chosen


def _read_texts(data: bytes, paths: list[str]) -> tuple[dict[str, str], list[str]]:
    """
    The text of each path in the package, in order, and the files left out with why: a file over _FILE_BYTES, a
    binary file, or what's past _READ_BYTES in all.
    """
    texts: dict[str, str] = {}
    skipped: list[str] = []
    total = 0
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        for path in paths:
            size = archive.getinfo(path).file_size
            if size > _FILE_BYTES:
                skipped.append(f"{path}: {size / MB:.1f} MB, too big to read")
                continue
            if total + size > _READ_BYTES:
                skipped.append(f"{path} and what's after it: past {_READ_BYTES // MB} MB read")
                break
            raw = archive.read(path)
            if b"\x00" in raw[:_BINARY_SNIFF]:
                skipped.append(f"{path}: a binary file")
                continue
            texts[path] = raw.decode("utf-8", errors="replace")
            total += size
    return texts, skipped


def _line_test(pattern: str, regex: bool) -> Callable[[str], bool]:
    """
    Whether a line matches: it contains `pattern` (case-sensitive), or with regex=True `pattern` as a Python
    regular expression matches in its first _REGEX_LINE_CHARACTERS. A pattern that repeats a repeated group
    ('(a+)+') is refused: on a long line it can take longer than the kernel will wait.
    """
    if not pattern:
        raise ValueError("Give a text to look for")
    if len(pattern) > _PATTERN_CHARACTERS:
        raise ValueError(f"The text to look for is {len(pattern):,} characters; keep it under {_PATTERN_CHARACTERS}")
    if not regex:
        return lambda line: pattern in line
    if _NESTED_REPEAT_RE.search(pattern):
        raise ValueError(f"{pattern!r} repeats a group that repeats itself, which can run for hours on a long line: "
                         "write it without the nested repeat")
    try:
        compiled = re.compile(pattern)
    except re.error as exc:
        raise ValueError(f"{pattern!r} isn't a regular expression Python reads: {exc}") from None
    return lambda line: compiled.search(line[:_REGEX_LINE_CHARACTERS]) is not None


def _file_matches(path: str, text: str, matches: Callable[[str], bool]) -> list[CodeMatch]:
    """
    The lines of one file that match, each with _CONTEXT_LINES lines around it.
    """
    lines = text.splitlines()
    found = []
    for number, line in enumerate(lines, 1):
        if matches(line):
            first, last = max(1, number - _CONTEXT_LINES), min(len(lines), number + _CONTEXT_LINES)
            context = [(n, lines[n - 1]) for n in range(first, last + 1)]
            found.append(CodeMatch(path, number, line, context))
    return found


def search_package(fn: Function, data: bytes | None, pattern: str, *, regex: bool = False,
                   files: str | None = None) -> CodeSearch:
    """
    The lines of a deployment package that contain `pattern` (case-sensitive), or match it as a Python regular
    expression with regex=True ('(?i)' first ignores case), like grep -rn: each with the lines around it, in file
    order, the handler's file first. It searches the package's own source files, or with files= (a glob such as
    '*.py', a folder or a path, several separated by commas) every file that matches, the libraries packed with it
    too. Secrets files (.env, keys) are never read, and nothing is run. It keeps the first _KEPT_MATCHES matches and
    counts the rest.
    """
    matches = _line_test(pattern, regex)
    pkg = read_package(fn, data)
    search = CodeSearch(fn, pattern, regex=regex, files=files)
    if data is None or not pkg.files:
        search.note = pkg.note or "The package holds no files."
        return search
    texts, search.skipped = _read_texts(data, _chosen_files(pkg, files))
    search.files_searched = len(texts)
    for path, text in texts.items():
        found = _file_matches(path, text, matches)
        if found:
            search.files_matched += 1
            search.total += len(found)
            search.matches.extend(found[: max(0, _KEPT_MATCHES - len(search.matches))])
    return search


def _module_name(path: str) -> str:
    """
    'app/store.py' -> 'app.store', 'app/__init__.py' -> 'app': the module a file is to an import.
    """
    module = path.removesuffix(".py").replace("/", ".")
    return module.removesuffix(".__init__")


def _definition_line(node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef) -> str:
    """
    A definition's first line as written, without its body: 'def load(self, day, *, limit=100) -> list',
    'class Orders(Base)'.
    """
    if isinstance(node, ast.ClassDef):
        bases = [ast.unparse(base) for base in node.bases] + [ast.unparse(k) for k in node.keywords]
        return f"class {node.name}({', '.join(bases)})" if bases else f"class {node.name}"
    keyword = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
    returns = f" -> {ast.unparse(node.returns)}" if node.returns else ""
    return f"{keyword} {node.name}({ast.unparse(node.args)}){returns}"


class _NameFinder(ast.NodeVisitor):
    """
    Walks one file's syntax tree for a name: the definitions whose qualified name ends with `wanted` and the lines
    that call, import or refer to its last part, each with the function or class it's in.
    """

    def __init__(self, path: str, lines: list[str], wanted: str):
        self.path, self.lines, self.wanted = path, lines, wanted
        self.last = wanted.rsplit(".", 1)[-1]
        self.module = _module_name(path)
        self.scopes: list[tuple[str, str]] = []
        self.definitions: list[CodeSymbol] = []
        self.uses: list[SymbolUse] = []
        self._called: set[int] = set()

    def _qualname(self, name: str) -> str:
        return ".".join([scope for scope, _ in self.scopes] + [name])

    def _is_wanted(self, qualname: str) -> bool:
        """
        Whether a definition is the one asked for: its last part is, and so is what's before it when the name has
        one ('Orders.load' is load in class Orders; 'store.load_orders' is load_orders in store.py).
        """
        if "." not in self.wanted:
            return qualname.rsplit(".", 1)[-1] == self.wanted
        full = f"{self.module}.{qualname}"
        return qualname == self.wanted or full == self.wanted or full.endswith("." + self.wanted)

    def _text(self, line: int) -> str:
        return self.lines[line - 1].strip() if 0 < line <= len(self.lines) else ""

    def _within(self) -> str:
        return ".".join(scope for scope, _ in self.scopes) or _TOP_LEVEL

    def _use(self, node: ast.AST, kind: str) -> None:
        self.uses.append(SymbolUse(self.path, node.lineno, self._text(node.lineno), kind, self._within()))

    def _define(self, node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef, kind: str) -> None:
        qualname = self._qualname(node.name)
        if self._is_wanted(qualname):
            doc = (ast.get_docstring(node) or "").strip().splitlines()
            self.definitions.append(CodeSymbol(
                qualname, kind, self.path, node.lineno, node.end_lineno or node.lineno, _definition_line(node),
                [f"@{ast.unparse(d)}" for d in node.decorator_list], doc[0] if doc else ""))
        self.scopes.append((node.name, kind))
        self.generic_visit(node)
        self.scopes.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._define(node, "method" if self.scopes and self.scopes[-1][1] == "class" else "function")

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._define(node, "method" if self.scopes and self.scopes[-1][1] == "class" else "function")

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._define(node, "class")

    def _assigned(self, targets: list[ast.expr], node: ast.stmt) -> None:
        """
        A module- or class-level assignment to the name is a definition of it (a constant, a client, a table).
        """
        in_function = any(kind != "class" for _, kind in self.scopes)
        for target in targets:
            if isinstance(target, ast.Name) and not in_function and self._is_wanted(self._qualname(target.id)):
                self.definitions.append(CodeSymbol(
                    self._qualname(target.id), "variable", self.path, node.lineno, node.end_lineno or node.lineno,
                    _clip(self._text(node.lineno), 120)))

    def visit_Assign(self, node: ast.Assign) -> None:
        self._assigned(node.targets, node)
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        self._assigned([node.target], node)
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        func = node.func
        name = func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else None
        if name == self.last:
            self._use(node, "call")
            self._called.add(id(func))
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:
        if node.id == self.last and isinstance(node.ctx, ast.Load) and id(node) not in self._called:
            self._use(node, "reference")

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if node.attr == self.last and isinstance(node.ctx, ast.Load) and id(node) not in self._called:
            self._use(node, "reference")
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if any(alias.name == self.last for alias in node.names):
            self._use(node, "import")

    def visit_Import(self, node: ast.Import) -> None:
        if any(alias.name.rsplit(".", 1)[-1] == self.last for alias in node.names):
            self._use(node, "import")


def _python_files(pkg: CodePackage) -> list[str]:
    return [path for path in source_files(pkg) if path.endswith(".py")]


def find_symbol_in(fn: Function, data: bytes | None, name: str, *, limit: int = 40) -> SymbolReport:
    """
    Where a Python name is defined in a deployment package and where it's used, like a language server's go to
    definition and find references: its definitions (functions, methods, classes and module-level names, each with
    its signature, decorators and the first line of its docstring) and the lines that call it, import it or refer
    to it, each with the function it's in. `name` is a name ('load_orders') or a dotted one ('Orders.load',
    'store.load_orders'), which a definition matches with its class or module; uses are matched by the last part
    alone, so a common name finds other things of that name too. Reads the package's own .py files with Python's
    ast module and runs nothing. A runtime that isn't Python, or a container image, gives a note.
    """
    report = SymbolReport(fn, name.strip())
    if not all(part.isidentifier() for part in report.name.split(".")):
        raise ValueError(f"{name!r} isn't a Python name: give one like 'load_orders' or 'Orders.load'")
    if runtime_family(fn.runtime) != "python" and fn.package_type != "Image":
        report.note = (f"It runs {fn.runtime or 'a custom runtime'}, and finding where a name is defined reads Python "
                       "code: search the code for the name instead.")
        return report
    pkg = read_package(fn, data)
    if data is None or not pkg.files:
        report.note = pkg.note or "The package holds no files."
        return report
    texts, report.skipped = _read_texts(data, _python_files(pkg))
    for path, text in texts.items():
        try:
            tree = ast.parse(text, filename=path)
        except SyntaxError as exc:
            report.skipped.append(f"{path}: Python can't read it (SyntaxError at line {exc.lineno})")
            continue
        report.files_read += 1
        finder = _NameFinder(path, text.splitlines(), report.name)
        finder.visit(tree)
        report.definitions.extend(finder.definitions)
        report.total_uses += len(finder.uses)
        report.uses.extend(finder.uses[: max(0, limit - len(report.uses))])
    return report
