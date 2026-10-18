"""
The code reader: loads the handler's module the way Python imports it and follows the
handler, keeping what each name holds, into a CodeMap.
"""

from __future__ import annotations

import ast
import sys
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Generator, Iterable

from ...._kit.text import _clip

from ..package import _JUNK_RE
from .terms import (
    _ADDED_MODULES, _AWS_KEY_RE, _B64_DECODES, _CACHES, _COLLECTIONS, _COMMANDS, _DEFAULT_TIMEOUTS, _DICT_METHODS,
    _FILE_WRITERS, _GUESSES, _HTTP_METHODS, _HTTP_SESSIONS, _JSON_LOADS, _LOG_LEVELS, _MAP_DEPTH, _MAP_FILES,
    _MAP_RUNS, _MAP_STEPS, _REMOVED_MODULES, _RESOURCE_ACTIONS, _ROUTE_METHODS, _RUNTIME_MODULES, _TRANSFERS,
)
from .syntax import (
    _a, _const, _def_line, _doc_line, _is_main_guard, _names_in, _secret_spots, _swallows, _target, _unparse,
)
from .aws_model import _access, _aws_model, _event_label, _iam_action, _service_name
from .model import AwsCall, CodeFunction, CodeMap, CodeSpot, CodeStep, EnvRead, EventField, ImportUse


@dataclass(frozen=True)
class _Sym:
    """
    What a name holds, as far as how_it_works() can tell without running the code.
    """

    kind: str  # 'mod' (an outside module or name, dotted), 'own' (one of its own modules), 'func', 'class', 'obj' (an
    # instance of one of its classes), 'bound' (one's method), 'client', 'resource', 'res' (a resource object: a
    # Table), 'coll' (a resource collection), 'paginator', 'batch', 'method' (one of theirs), 'meta', 'event',
    # 'evmethod', 'context', 'env' (os.environ), 'envget', 'envval', 'logger', 'log', 'http', 'httpcall', 'builtin',
    # 'enumerate'
    name: str = ""
    sub: str = ""
    path: tuple[str, ...] = ()
    inner: _Sym | None = None


@dataclass
class _Frame:
    """
    A function (or a module's top level) being read: what its names hold, and where it is.
    """

    module: str
    file: str
    function: str  # 'etl.handler', or the module ('etl') at its top level
    scope: dict[str, _Sym | None]
    node: Any = None  # the function's ast node; None at a module's top level
    outers: list[dict[str, _Sym | None]] = field(default_factory=list)  # the functions around it (closures)
    loops: list[str] = field(default_factory=list)  # the loops it's in, its callers' first
    guards: list[str] = field(default_factory=list)  # the tests of the ifs it's in
    globals: set[str] = field(default_factory=set)
    optional: int = 0  # inside a try that catches ImportError
    cached: bool = False  # @lru_cache and the like: it runs once
    returns: list[_Sym | None] = field(default_factory=list)
    depth: int = 0
    words: set[str] | None = None  # every name and text in it, for spotting a next-page token

    @property
    def top(self) -> bool:
        return self.node is None


@dataclass
class _Module:
    name: str
    file: str | None
    tree: Any = None
    scope: dict[str, _Sym | None] = field(default_factory=dict)
    steps: list[CodeStep] = field(default_factory=list)
    loaded: bool = False


@dataclass
class _Collect:
    """
    What one statement does, gathered while its expressions are read.
    """

    notes: list[tuple[str, str]] = field(default_factory=list)
    calls: list[CodeStep] = field(default_factory=list)

    def note(self, kind: str, text: str) -> None:
        if (kind, text) not in self.notes:
            self.notes.append((kind, text))


class _CodeReader:
    """
    Follows a Python function's code from its handler with the ast module, never running it. It keeps track of what
    each name holds (a boto3 client, part of the event, one of its own functions, an environment variable...) as far
    as it can tell, so it knows what each line calls, and fills a CodeMap. One per map_code().
    """

    def __init__(self, texts: dict[str, str], names: list[str], root: str, cmap: CodeMap,
                 version: tuple[int, int] | None, libraries: set[str], layers: bool):
        self.texts, self.root, self.cmap, self.version, self.layers = texts, root, cmap, version, layers
        self.tops: set[str] = set()  # what's at the package's top: folders and modules
        for name in names:
            if name.startswith(root):
                first, slash, _ = name[len(root):].partition("/")
                self.tops.add(first if slash else first.split(".")[0])
        self.libraries = libraries
        self.sources: dict[str, str] = {}  # its own modules: name -> file
        for path in texts:
            if not path.startswith(root) or not path.endswith(".py"):
                continue
            rel = path[len(root):-3]
            module = rel.replace("/", ".")
            if module.split(".")[0] in libraries or "-" in module or "site-packages" in module:
                continue
            module = module[:-len(".__init__")] if module.endswith(".__init__") else module
            if module and module != "__init__":
                self.sources[module] = path
        self.modules: dict[str, _Module] = {}
        self.defs: dict[str, tuple[str, Any, str | None]] = {}  # key -> (module, node, class key)
        self.classes: dict[str, dict[str, Any]] = {}
        self.closures: dict[str, list[dict[str, _Sym | None]]] = {}
        self.cached: set[str] = set()
        self.cur = _Collect()
        self.stack: list[str] = []
        self.memo: dict[Any, _Sym | None] = {}
        self.shown: set[str] = set()
        self.reached: dict[str, CodeFunction] = {}
        self.count = 0
        self.routes: list[tuple[str, str, str, str]] = []  # (method, path, function key, router)
        self.event_reads: dict[tuple[str, ...], EventField] = {}
        self.seen: set[Any] = set()
        self.target: set[str] = set()  # the names the statement being read assigns to
        self.loading = 0  # modules being loaded: what runs now runs when Lambda starts the code
        self.runs = 0  # function bodies read

    # ---------------------------------------------------------------- reading

    @contextmanager
    def _collecting(self) -> Generator[_Collect, None, None]:
        saved, self.cur = self.cur, _Collect()
        try:
            yield self.cur
        finally:
            self.cur = saved

    def read(self, handler: str) -> None:
        cmap = self.cmap
        module, _, name = handler.rpartition(".")
        module = module.replace("/", ".")
        mod = self._load_chain(module)
        cmap.startup = [step for m in self._chain(module) if m.name != module for step in self._module_step(m)]
        cmap.startup += mod.steps
        value = mod.scope.get(name)
        file = mod.file or cmap.handler_file or ""
        if value is not None and value.kind in ("func", "bound"):
            key = value.name
            node = self.defs[key][1]
            cmap.entry, cmap.entry_line = key, node.lineno
            positional = node.args.posonlyargs + node.args.args
            plain = len(positional) - len(node.args.defaults)
            cmap.params = [a.arg + ("=…" if i >= plain else "") for i, a in enumerate(positional)]
            cmap.params += ["*" + node.args.vararg.arg] if node.args.vararg else []
            cmap.is_async = isinstance(node, ast.AsyncFunctionDef)
            with self._collecting() as got:
                self._run(key, [_Sym("event"), _Sym("context")], {}, None, None)
            cmap.steps = got.calls
        elif name in mod.scope or self.routes:
            assigned = next((n for n in (mod.tree.body if mod.tree else []) if isinstance(n, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == name for t in n.targets)), None)
            cmap.entry, cmap.entry_line = f"{mod.name}.{name}", assigned.lineno if assigned else 0
            step = CodeStep(file, cmap.entry_line, _unparse(assigned) if assigned else f"{name} = …", "def",
                            name=cmap.entry)
            frame = _Frame(mod.name, file, cmap.entry, {}, node=assigned)
            with self._collecting() as got:
                for method, path, key, _ in self.routes:
                    self._run(key, [], {}, frame, None, label=f"{method} {path}")
            step.steps = [CodeStep(c.file, c.line, c.code, "def", c.notes, c.steps, c.calls, c.name, c.seen)
                          for c in got.calls]
            if not self.routes:
                step.notes.append(("more", "an object this can't follow, not a function"))
            cmap.steps = [step]
        if cmap.entry is None and mod.tree is not None:
            cmap.handlers = [f"{n.name}()" for n in mod.tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                             and len(n.args.posonlyargs + n.args.args) == 2]
        cmap.functions = list(self.reached.values())
        cmap.event = list(self.event_reads.values())
        cmap.routes = [(method, path, key) for method, path, key, _ in self.routes]
        loaded = [m for m in self.modules.values() if m.tree is not None]
        cmap.other_files = sum(1 for name, path in self.sources.items()
                               if name not in self.modules and not _JUNK_RE.search(path))
        for m in loaded:
            for node in m.tree.body:
                members = node.body if isinstance(node, ast.ClassDef) else [node]
                prefix = f"{m.name}.{node.name}." if isinstance(node, ast.ClassDef) else f"{m.name}."
                cmap.unreached += sum(1 for f in members if isinstance(f, (ast.FunctionDef, ast.AsyncFunctionDef))
                                      and prefix + f.name not in self.reached)
            self._secrets(m)
        for name, path in self.sources.items():
            for match in _AWS_KEY_RE.finditer(self.texts[path]):
                line = self.texts[path].count("\n", 0, match.start()) + 1
                self._spot("secret", path, line, name, "", "an AWS access key", ok=False)

    def _module_step(self, mod: _Module) -> list[CodeStep]:
        if not mod.steps or mod.file is None:
            return []
        return [CodeStep(mod.file, 1, f"{mod.file}, run when it's imported", "module", steps=mod.steps, name=mod.name)]

    def _chain(self, module: str) -> list[_Module]:
        parts = module.split(".")
        return [self.modules[".".join(parts[:i])] for i in range(1, len(parts) + 1)
                if ".".join(parts[:i]) in self.modules]

    def _load_chain(self, module: str) -> _Module:
        """
        A module and the packages it's in, loaded the way Python imports them: each package's __init__ first.
        """
        parts = module.split(".")
        for i in range(1, len(parts)):
            if ".".join(parts[:i]) in self.sources:
                self._load(".".join(parts[:i]))
        return self._load(module)

    def _load(self, name: str) -> _Module:
        if name in self.modules:
            return self.modules[name]
        mod = self.modules[name] = _Module(name, self.sources.get(name))
        if mod.file is None:
            return mod
        text = self.texts[mod.file]
        if len(self.modules) > _MAP_FILES:
            self.cmap.limited = f"read the first {_MAP_FILES} of its modules"
            return mod
        try:
            mod.tree = ast.parse(text, filename=mod.file)
        except (SyntaxError, ValueError) as exc:
            where = f"line {exc.lineno}: " if isinstance(exc, SyntaxError) and exc.lineno else ""
            why = f"{where}{getattr(exc, 'msg', None) or exc}"
            self.cmap.skipped.append((mod.file, why))
            self._spot("syntax", mod.file, getattr(exc, "lineno", 0) or 0, name, "", why, ok=False)
            return mod
        self.cmap.files += 1
        self.cmap.lines += text.count("\n") + (0 if text.endswith("\n") else 1)
        frame = _Frame(name, mod.file, name, mod.scope)
        mod.scope.setdefault("__name__", None)
        self.loading += 1
        try:
            mod.steps = self._block(mod.tree.body, frame)
        finally:
            self.loading -= 1
        mod.loaded = True
        return mod

    # ---------------------------------------------------------------- statements

    def _block(self, stmts: list[Any], frame: _Frame) -> list[CodeStep]:
        out: list[CodeStep] = []
        for stmt in stmts:
            out.extend(self._stmt(stmt, frame))
        return out

    def _step(self, node: Any, frame: _Frame, code: str, kind: str, got: _Collect | None = None,
              steps: Iterable[CodeStep] = ()) -> CodeStep:
        self.count += 1
        notes = got.notes if got else []
        events = [text for k, text in notes if k == "event"]
        notes = [(k, text) for k, text in notes if k != "event" or not any(
            other.startswith((text + "[", text + ".", text + " (")) for other in events)]
        return CodeStep(frame.file, getattr(node, "lineno", 0), code, kind, notes, list(steps),
                        got.calls if got else [])

    def _stmt(self, stmt: Any, frame: _Frame) -> list[CodeStep]:
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
            frame.scope[stmt.name] = _Sym("func", self._define(stmt, frame))
            return []
        if isinstance(stmt, ast.ClassDef):
            return self._define_class(stmt, frame)
        if isinstance(stmt, (ast.Import, ast.ImportFrom)):
            return self._import(stmt, frame)
        if isinstance(stmt, (ast.Global, ast.Nonlocal)):
            frame.globals.update(stmt.names)
            return []
        if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant):
            return []  # a docstring
        if isinstance(stmt, (ast.For, ast.AsyncFor, ast.While)):
            return self._loop(stmt, frame)
        if isinstance(stmt, ast.If):
            return self._if(stmt, frame)
        if isinstance(stmt, (ast.With, ast.AsyncWith)):
            return self._with(stmt, frame)
        if isinstance(stmt, ast.Try) or type(stmt).__name__ == "TryStar":
            return self._try(stmt, frame)
        if isinstance(stmt, ast.Match):
            return self._match(stmt, frame)
        kind = "do"
        with self._collecting() as got:
            if isinstance(stmt, ast.Return):
                kind = "return"
                value = self._eval(stmt.value, frame)
                frame.returns.append(value)
                if frame.function == self.cmap.entry and stmt.value is not None:
                    self.cmap.returns.append((_unparse(stmt.value, 2000), frame.file, stmt.lineno))
            elif isinstance(stmt, ast.Raise):
                kind = "raise"
                self._eval(stmt.exc, frame)
                self._eval(stmt.cause, frame)
            elif isinstance(stmt, (ast.Assign, ast.AnnAssign)):
                targets = stmt.targets if isinstance(stmt, ast.Assign) else [stmt.target]
                self.target = {t.id for t in targets if isinstance(t, ast.Name)}
                value = self._eval(stmt.value, frame)
                self.target = set()
                if stmt.value is not None:
                    for target in targets:
                        self._bind(target, value, frame)
            else:
                for child in ast.iter_child_nodes(stmt):
                    if isinstance(child, ast.expr):
                        self._eval(child, frame)
        if (kind != "do" and not frame.top) or got.notes or got.calls:
            return [self._step(stmt, frame, _unparse(stmt), kind, got)]
        return []

    def _loop(self, stmt: Any, frame: _Frame) -> list[CodeStep]:
        with self._collecting() as head:
            if isinstance(stmt, ast.While):
                self._eval(stmt.test, frame)
                header = f"while {_unparse(stmt.test, 110)}:"
            else:
                items = self._eval(stmt.iter, frame)
                self._bind_loop(stmt.target, items, frame)
                prefix = "async for" if isinstance(stmt, ast.AsyncFor) else "for"
                header = f"{prefix} {_target(stmt.target)} in {_unparse(stmt.iter, 100)}:"
        frame.loops.append(header)
        body = self._block(stmt.body, frame)
        frame.loops.pop()
        orelse = self._block(stmt.orelse, frame)
        out = [self._step(stmt, frame, header, "loop", head, body)] if head.notes or head.calls or body else []
        if orelse:
            out.append(CodeStep(frame.file, stmt.orelse[0].lineno - 1, "else:", "else", steps=orelse))
        return out

    def _if(self, stmt: ast.If, frame: _Frame) -> list[CodeStep]:
        if frame.top and _is_main_guard(stmt.test):
            return []  # runs only when the file is run as a script, never in Lambda
        with self._collecting() as head:
            self._eval(stmt.test, frame)
        test = _unparse(stmt.test, 110)
        frame.guards.append(test)
        body = self._block(stmt.body, frame)
        frame.guards.pop()
        rest: list[CodeStep] = []
        if len(stmt.orelse) == 1 and isinstance(stmt.orelse[0], ast.If):
            rest = self._if(stmt.orelse[0], frame)
            if rest and rest[0].kind == "if" and rest[0].line == stmt.orelse[0].lineno:
                rest[0].code = "el" + rest[0].code
        elif stmt.orelse:
            frame.guards.append(f"not ({test})")
            orelse = self._block(stmt.orelse, frame)
            frame.guards.pop()
            if orelse:
                rest = [CodeStep(frame.file, stmt.orelse[0].lineno - 1, "else:", "else", steps=orelse)]
        if head.notes or head.calls or body or rest:
            return [self._step(stmt, frame, f"if {test}:", "if", head, body), *rest]
        return []

    def _with(self, stmt: Any, frame: _Frame) -> list[CodeStep]:
        with self._collecting() as head:
            parts = []
            for item in stmt.items:
                value = self._eval(item.context_expr, frame)
                if item.optional_vars is not None:
                    self._bind(item.optional_vars, value, frame)
                parts.append(_unparse(item.context_expr, 100) + (
                    f" as {_unparse(item.optional_vars, 30)}" if item.optional_vars is not None else ""))
        body = self._block(stmt.body, frame)
        header = f"{'async with' if isinstance(stmt, ast.AsyncWith) else 'with'} {', '.join(parts)}:"
        return [self._step(stmt, frame, header, "with", head, body)] if head.notes or head.calls or body else []

    def _try(self, stmt: Any, frame: _Frame) -> list[CodeStep]:
        optional = any(h.type is None or _names_in(h.type, ("ImportError", "ModuleNotFoundError", "Exception"))
                       for h in stmt.handlers)
        frame.optional += optional
        body = self._block(stmt.body, frame)
        frame.optional -= optional
        handlers = []
        for h in stmt.handlers:
            with self._collecting() as head:
                self._eval(h.type, frame)
                if h.name:
                    frame.scope[h.name] = None
                silent = _swallows(h) and not frame.top
                if silent:
                    head.note("warn", "drops the error")
            hbody = self._block(h.body, frame)
            header = "except" + (f" {_unparse(h.type, 60)}" if h.type is not None else "") + (
                f" as {h.name}" if h.name else "") + ":"
            if silent:
                code = " ".join([header] + [_unparse(s, 40) for s in h.body])
                self._spot("swallow", frame.file, h.lineno, frame.function, code)
            if hbody or head.notes:
                handlers.append(self._step(h, frame, header, "except", head, hbody))
        orelse = self._block(stmt.orelse, frame)
        final = self._block(stmt.finalbody, frame)
        if not (body or handlers or orelse or final):
            return []
        out = [CodeStep(frame.file, stmt.lineno, "try:", "try", steps=body), *handlers]
        if orelse:
            out.append(CodeStep(frame.file, stmt.orelse[0].lineno - 1, "else:", "else", steps=orelse))
        if final:
            out.append(CodeStep(frame.file, stmt.finalbody[0].lineno - 1, "finally:", "else", steps=final))
        return out

    def _match(self, stmt: Any, frame: _Frame) -> list[CodeStep]:
        with self._collecting() as head:
            self._eval(stmt.subject, frame)
        cases = []
        for case in stmt.cases:
            with self._collecting() as got:
                self._eval(case.guard, frame)
            body = self._block(case.body, frame)
            if body or got.notes:
                guard = f" if {_unparse(case.guard, 60)}" if case.guard is not None else ""
                cases.append(self._step(case.pattern, frame, f"case {_unparse(case.pattern, 80)}{guard}:", "if",
                                        got, body))
        if head.notes or cases:
            return [self._step(stmt, frame, f"match {_unparse(stmt.subject, 100)}:", "if", head, cases)]
        return []

    # ---------------------------------------------------------------- definitions and imports

    def _define(self, node: Any, frame: _Frame, cls: str | None = None) -> str:
        key = f"{cls or (frame.module if frame.top else frame.function)}.{node.name}"
        self.defs[key] = (frame.module, node, cls)
        if not frame.top:
            self.closures[key] = [frame.scope, *frame.outers]
        for d in node.decorator_list:
            target = d.func if isinstance(d, ast.Call) else d
            name = target.id if isinstance(target, ast.Name) else getattr(target, "attr", "")
            if name in _CACHES:
                self.cached.add(key)
            if (isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute) and d.func.attr in _ROUTE_METHODS
                    and d.args and isinstance(_const(d.args[0]), str) and str(_const(d.args[0])).startswith("/")):
                verb = d.func.attr.upper()
                if verb in ("ROUTE", "API_ROUTE"):
                    methods = next((kw.value for kw in d.keywords if kw.arg == "methods"), None)
                    found = [str(_const(e)) for e in getattr(methods, "elts", []) if isinstance(_const(e), str)]
                    verb = ", ".join(found) or "GET"
                self.routes.append((verb, str(_const(d.args[0])), key, _unparse(d.func.value, 60)))
        return key

    def _define_class(self, node: ast.ClassDef, frame: _Frame) -> list[CodeStep]:
        key = f"{frame.module if frame.top else frame.function}.{node.name}"
        info: dict[str, Any] = {"methods": {}, "attrs": {}, "bases": []}
        self.classes[key] = info
        frame.scope[node.name] = _Sym("class", key)
        for base in node.bases:
            found = self._eval(base, frame)
            if found is not None and found.kind == "class":
                info["bases"].append(found.name)
        steps = []
        for stmt in node.body:
            if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
                info["methods"][stmt.name] = self._define(stmt, frame, cls=key)
            elif isinstance(stmt, (ast.Assign, ast.AnnAssign)) and stmt.value is not None:
                with self._collecting() as got:
                    value = self._eval(stmt.value, frame)
                targets = stmt.targets if isinstance(stmt, ast.Assign) else [stmt.target]
                for target in targets:
                    if isinstance(target, ast.Name):
                        info["attrs"][target.id] = value
                if got.notes or got.calls:
                    steps.append(self._step(stmt, frame, _unparse(stmt), "do", got))
        return [CodeStep(frame.file, node.lineno, f"class {node.name}:", "do", steps=steps)] if steps else []

    def _member(self, cls: str, name: str, seen: set[str] | None = None) -> tuple[str, Any] | None:
        """
        A class's attribute or method, looked for in its own classes it's made from too.
        """
        seen = seen if seen is not None else set()
        info = self.classes.get(cls)
        if info is None or cls in seen:
            return None
        seen.add(cls)
        if name in info["attrs"]:
            return "attr", info["attrs"][name]
        if name in info["methods"]:
            return "method", info["methods"][name]
        for base in info["bases"]:
            found = self._member(base, name, seen)
            if found:
                return found
        return None

    def _own(self, dotted: str) -> str | None:
        return dotted if dotted in self.sources else None

    def _import(self, stmt: Any, frame: _Frame) -> list[CodeStep]:
        before = set(self.modules)
        if isinstance(stmt, ast.Import):
            for alias in stmt.names:
                own = self._own(alias.name)
                self._record_import(alias.name, bool(own), stmt, frame)
                bound = alias.asname or alias.name.split(".")[0]
                if own:
                    self._load_chain(own)
                    frame.scope[bound] = _Sym("own", own if alias.asname else own.split(".")[0])
                elif alias.name.split(".")[0] in self.sources:
                    frame.scope[bound] = _Sym("own", alias.name.split(".")[0])
                else:
                    frame.scope[bound] = _Sym("mod", alias.name if alias.asname else bound)
        else:
            base = stmt.module or ""
            if stmt.level:
                package = frame.module.split(".")
                is_package = (self.sources.get(frame.module) or "").endswith("__init__.py")
                keep = len(package) - (stmt.level - (1 if is_package else 0))
                base = ".".join(package[:max(keep, 0)] + ([stmt.module] if stmt.module else []))
            own = bool(stmt.level) or base in self.sources or base.split(".")[0] in self.sources
            self._record_import(base or ".", own, stmt, frame)
            if own and base in self.sources:
                self._load_chain(base)
            for alias in stmt.names:
                if alias.name == "*":
                    if own and base in self.modules:
                        frame.scope.update({k: v for k, v in self.modules[base].scope.items() if not k.startswith("_")})
                    continue
                sub = f"{base}.{alias.name}" if base else alias.name
                if own and sub in self.sources:
                    self._load_chain(sub)
                    value: _Sym | None = _Sym("own", sub)
                elif own:
                    value = self.modules[base].scope.get(alias.name) if base in self.modules else None
                else:
                    value = _Sym("mod", sub)
                frame.scope[alias.asname or alias.name] = value
        loaded = [self.modules[name] for name in self.modules if name not in before]
        calls = [step for mod in loaded for step in self._module_step(mod)]
        if calls:
            step = self._step(stmt, frame, _unparse(stmt), "do")
            step.calls = calls
            return [step]
        return []

    def _record_import(self, module: str, own: bool, stmt: Any, frame: _Frame) -> None:
        top = module.split(".")[0]
        version = self.version or (3, 99)
        if own or module == ".":
            source = "own"
        elif top in _REMOVED_MODULES:
            source = "removed" if version >= _REMOVED_MODULES[top] else "stdlib"
        elif top in _ADDED_MODULES:
            source = "stdlib" if version >= _ADDED_MODULES[top] else "missing"
        elif top in sys.stdlib_module_names:
            source = "stdlib"
        elif top in _RUNTIME_MODULES:
            source = "runtime"
        elif top in self.tops:
            source = "package"
        else:
            source = "layer" if self.layers else "missing"
        key = (module, frame.file, stmt.lineno)
        if key not in self.seen:
            self.seen.add(key)
            self.cmap.imports.append(ImportUse(module, source, frame.file, stmt.lineno, bool(frame.optional),
                                               frame.top))

    # ---------------------------------------------------------------- names

    def _lookup(self, name: str, frame: _Frame) -> _Sym | None:
        for scope in (frame.scope, *frame.outers):
            if name in scope:
                return scope[name]
        module = self.modules.get(frame.module)
        if module is not None and name in module.scope:
            return module.scope[name]
        if name in ("print", "open", "enumerate", "sorted", "reversed", "list", "tuple", "iter"):
            return _Sym("builtin", name)
        return None

    def _bind(self, target: Any, value: _Sym | None, frame: _Frame) -> None:
        if isinstance(target, ast.Name):
            frame.scope[target.id] = value
            if target.id in frame.globals and not frame.top and frame.module in self.modules:
                self.modules[frame.module].scope[target.id] = value
        elif isinstance(target, ast.Attribute):
            owner = self._eval(target.value, frame)
            if owner is not None and owner.kind in ("obj", "class") and owner.name in self.classes:
                self.classes[owner.name]["attrs"][target.attr] = value
            elif owner is not None and owner.kind == "own" and owner.name in self.modules:
                self.modules[owner.name].scope[target.attr] = value
        elif isinstance(target, (ast.Tuple, ast.List)):
            for elt in target.elts:
                self._bind(elt.value if isinstance(elt, ast.Starred) else elt, None, frame)

    def _item(self, items: _Sym | None) -> _Sym | None:
        """
        What a loop over `items` gets each time round.
        """
        if items is None:
            return None
        if items.kind == "event":
            return _Sym("event", path=items.path + ("[]",))
        if items.kind == "coll":
            return _Sym("res", items.name, "ObjectSummary" if items.name == "s3" else "")
        return None

    def _bind_loop(self, target: Any, items: _Sym | None, frame: _Frame) -> None:
        if items is not None and items.kind == "enumerate" and isinstance(target, ast.Tuple) and len(target.elts) == 2:
            self._bind(target.elts[0], None, frame)
            self._bind(target.elts[1], self._item(items.inner), frame)
        else:
            self._bind(target, self._item(items), frame)

    # ---------------------------------------------------------------- expressions

    def _eval(self, node: Any, frame: _Frame) -> _Sym | None:
        if node is None:
            return None
        if isinstance(node, ast.Name):
            return self._lookup(node.id, frame)
        if isinstance(node, ast.Attribute):
            return self._attribute(node, frame)
        if isinstance(node, ast.Call):
            return self._call(node, frame)
        if isinstance(node, ast.Subscript):
            return self._subscript(node, frame)
        if isinstance(node, (ast.Await, ast.Starred)):
            return self._eval(node.value, frame)
        if isinstance(node, ast.BoolOp):
            values = [self._eval(v, frame) for v in node.values]
            return next((v for v in values if v is not None), None)
        if isinstance(node, ast.IfExp):
            self._eval(node.test, frame)
            first, second = self._eval(node.body, frame), self._eval(node.orelse, frame)
            return first or second
        if isinstance(node, ast.NamedExpr):
            value = self._eval(node.value, frame)
            self._bind(node.target, value, frame)
            return value
        if isinstance(node, ast.Lambda):
            return None
        if isinstance(node, (ast.ListComp, ast.SetComp, ast.GeneratorExp, ast.DictComp)):
            saved = dict(frame.scope)
            depth = len(frame.loops)
            for gen in node.generators:
                self._bind_loop(gen.target, self._eval(gen.iter, frame), frame)
                frame.loops.append(f"for {_target(gen.target)} in {_unparse(gen.iter, 80)}")
                for cond in gen.ifs:
                    self._eval(cond, frame)
            if isinstance(node, ast.DictComp):
                self._eval(node.key, frame)
                self._eval(node.value, frame)
            else:
                self._eval(node.elt, frame)
            del frame.loops[depth:]
            frame.scope.clear()
            frame.scope.update(saved)
            return None
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.expr):
                self._eval(child, frame)
        return None

    def _attribute(self, node: ast.Attribute, frame: _Frame) -> _Sym | None:
        base = self._eval(node.value, frame)
        attr = node.attr
        if base is None:
            return None
        kind = base.kind
        if kind == "mod":
            dotted = f"{base.name}.{attr}"
            return _Sym("env") if dotted == "os.environ" else _Sym("mod", dotted)
        if kind == "own":
            sub = f"{base.name}.{attr}"
            if sub in self.sources:
                self._load_chain(sub)
                return _Sym("own", sub)
            return self._load(base.name).scope.get(attr)
        if kind in ("obj", "class"):
            found = self._member(base.name, attr)
            if found is None:
                return None
            what, value = found
            if what == "attr":
                return value
            node_ = self.defs[value][1]
            static = any(getattr(d, "id", "") == "staticmethod" for d in node_.decorator_list)
            classmethod_ = any(getattr(d, "id", "") == "classmethod" for d in node_.decorator_list)
            if static or (kind == "class" and not classmethod_):
                return _Sym("func", value)
            return _Sym("bound", value, inner=_Sym("class", base.name) if classmethod_ else base)
        if kind == "event":
            if attr in _DICT_METHODS:
                return _Sym("evmethod", attr, path=base.path)
            path = base.path + (attr,)
            self._event_read(path, node, frame, required=True)
            return _Sym("event", path=path)
        if kind == "env":
            return _Sym("envget", attr) if attr in ("get", "setdefault", "pop") else None
        if kind == "session":
            return _Sym("mod", f"boto3.{attr}")
        if kind in ("client", "resource", "res", "coll", "paginator", "batch"):
            if attr == "meta":
                return _Sym("meta", base.name)
            if kind in ("res", "resource") and (base.name, attr) in _COLLECTIONS:
                return _Sym("coll", base.name, _COLLECTIONS[(base.name, attr)])
            return _Sym("method", base.name, attr, inner=base)
        if kind == "meta":
            return _Sym("client", base.name) if attr == "client" else None
        if kind == "logger":
            return _Sym("log", attr)
        if kind == "http":
            return _Sym("httpcall", base.name, attr, inner=base)
        return None

    def _subscript(self, node: ast.Subscript, frame: _Frame) -> _Sym | None:
        base = self._eval(node.value, frame)
        key = _const(node.slice)
        if not isinstance(node.slice, ast.Constant):
            self._eval(node.slice, frame)
        if base is None:
            return None
        if base.kind == "env" and isinstance(key, str):
            self._env_read(key, node, frame, required=True)
            return _Sym("envval", key)
        if base.kind == "event":
            if isinstance(key, str):
                path = base.path + (key,)
                self._event_read(path, node, frame, required=True)
                return _Sym("event", path=path)
            if isinstance(key, int):
                return _Sym("event", path=base.path + ("[]",))
            if isinstance(node.slice, ast.Slice):
                return base
        return None

    def _call(self, node: ast.Call, frame: _Frame) -> _Sym | None:
        func = self._eval(node.func, frame)
        args = [self._eval(a, frame) for a in node.args]
        kwargs = {}
        for kw in node.keywords:
            value = self._eval(kw.value, frame)
            if kw.arg:
                kwargs[kw.arg] = value
        if func is None:
            return self._unknown_call(node, frame)
        kind = func.kind
        if kind == "func":
            return self._run(func.name, args, kwargs, frame, node)
        if kind == "bound":
            return self._run(func.name, args, kwargs, frame, node, bound=func.inner)
        if kind == "class":
            init = self._member(func.name, "__init__")
            obj = _Sym("obj", func.name)
            if init and init[0] == "method":
                self._run(init[1], args, kwargs, frame, node, bound=obj)
            return obj
        if kind == "mod":
            return self._outside(func.name, node, args, frame)
        if kind == "builtin":
            return self._builtin(func.name, node, args, frame)
        if kind == "method":
            return self._aws_method(func, node, frame)
        if kind == "envget":
            name = _const(node.args[0]) if node.args else None
            if isinstance(name, str):
                default = node.args[1] if len(node.args) > 1 else next(
                    (kw.value for kw in node.keywords if kw.arg == "default"), None)
                self._env_read(name, node, frame, required=False,
                               default=_unparse(default, 60) if default is not None else "")
                return _Sym("envval", name)
            return None
        if kind == "evmethod":
            key = _const(node.args[0]) if node.args else None
            if func.name in ("get", "pop", "setdefault") and isinstance(key, str):
                path = func.path + (key,)
                self._event_read(path, node, frame, required=False)
                return _Sym("event", path=path)
            return None
        if kind == "log":
            if func.name in _LOG_LEVELS:
                self.cur.note("log", _LOG_LEVELS[func.name])
            return None
        if kind == "httpcall":
            if func.sub in _HTTP_METHODS:
                self._http(func.name, func.sub, node, frame, given="timeout" in (func.inner.path if func.inner else ()))
            return None
        return None

    def _unknown_call(self, node: ast.Call, frame: _Frame) -> _Sym | None:
        """
        A call to something it couldn't follow: a method name only AWS has, a file written with pandas, a log
        line, or a web framework's router.
        """
        if not isinstance(node.func, ast.Attribute):
            return None
        attr = node.func.attr
        receiver = _unparse(node.func.value, 60)
        if attr in _GUESSES:
            service, op = _GUESSES[attr]
            self._aws(service, op, node, frame, guessed=True)
        elif attr in _FILE_WRITERS and node.args:
            self._write(node.args[0], node, frame)
        elif attr in _LOG_LEVELS and "log" in receiver.lower():
            self.cur.note("log", _LOG_LEVELS[attr])
        elif attr == "resolve" and any(router == receiver for *_, router in self.routes):
            for method, path, key, router in self.routes:
                if router == receiver:
                    self._run(key, [], {}, frame, node, label=f"{method} {path}")
        return None

    def _outside(self, name: str, node: ast.Call, args: list[_Sym | None], frame: _Frame) -> _Sym | None:
        """
        A call to something from outside the package: boto3, os, json, requests, logging, time...
        """
        first = args[0] if args else None
        last = name.rsplit(".", 1)[-1]
        if name.startswith("boto3") and last in ("client", "resource"):
            return self._make_client(last, node, frame)
        if name in ("boto3.Session", "boto3.session.Session"):
            return _Sym("session")
        if name == "os.getenv":
            key = _const(node.args[0]) if node.args else None
            if isinstance(key, str):
                default = node.args[1] if len(node.args) > 1 else None
                self._env_read(key, node, frame, required=False,
                               default=_unparse(default, 60) if default is not None else "")
                return _Sym("envval", key)
            return None
        if name in _JSON_LOADS or name in _B64_DECODES:
            if first is not None and first.kind == "event":
                return _Sym("event", path=first.path + ("{json}" if name in _JSON_LOADS else "{base64}",))
            return None
        if name in ("urllib.parse.unquote_plus", "urllib.parse.unquote", "str.strip"):
            return first
        if name == "logging.getLogger" or last == "Logger":
            return _Sym("logger")
        if name.startswith("logging.") and last in _LOG_LEVELS:
            self.cur.note("log", _LOG_LEVELS[last])
            return None
        top = name.split(".")[0]
        if name in _HTTP_SESSIONS:
            given = any(kw.arg == "timeout" for kw in node.keywords) or (top == "http" and len(node.args) >= 3)
            return _Sym("http", _HTTP_SESSIONS[name], path=("timeout",) if given else ())
        if top in ("requests", "httpx") and last in _HTTP_METHODS and name.count(".") == 1:
            self._http(top, last, node, frame)
            return None
        if name == "urllib.request.urlopen":
            self._http("urllib.request", "get", node, frame, given=len(node.args) >= 3)
            return None
        if name in ("time.sleep", "asyncio.sleep"):
            wait = _unparse(node.args[0], 30) if node.args else "?"
            self._spot("sleep", frame.file, node.lineno, frame.function, _unparse(node, 80), wait, loop=self._loop_of(frame))
            self.cur.note("sleep", f"waits {wait} s" if isinstance(_const(node.args[0] if node.args else None),
                                                                  (int, float)) else f"waits ({wait})")
            return None
        if name in _COMMANDS:
            self._spot("command", frame.file, node.lineno, frame.function, _unparse(node, 80))
            self.cur.note("command", "runs a command")
            return None
        if top == "pandas" and last.startswith("read_") and isinstance(_const(node.args[0] if node.args else None), str):
            if str(_const(node.args[0])).startswith("s3://"):
                self._aws("s3", "GetObject", node, frame)
        return None

    def _builtin(self, name: str, node: ast.Call, args: list[_Sym | None], frame: _Frame) -> _Sym | None:
        if name == "print":
            self.cur.note("log", "prints")
        elif name == "open" and node.args:
            mode = _const(node.args[1]) if len(node.args) > 1 else next(
                (_const(kw.value) for kw in node.keywords if kw.arg == "mode"), "r")
            if isinstance(mode, str) and any(c in mode for c in "wax+"):
                self._write(node.args[0], node, frame)
            else:
                path = _const(node.args[0])
                self.cur.note("file", f"reads {_clip(path, 40)}" if isinstance(path, str) else "reads a file")
        elif name == "enumerate" and args and args[0] is not None:
            return _Sym("enumerate", inner=args[0])
        elif name in ("sorted", "reversed", "list", "tuple", "iter") and args:
            return args[0]
        return None

    def _write(self, path_node: Any, node: ast.Call, frame: _Frame) -> None:
        """
        A file the code writes: on Lambda's disk only /tmp (and a file system mounted under /mnt) can be.
        """
        prefix = None
        if isinstance(_const(path_node), str):
            prefix = str(_const(path_node))
        elif isinstance(path_node, ast.JoinedStr) and path_node.values and isinstance(_const(path_node.values[0]),
                                                                                       str):
            prefix = str(_const(path_node.values[0]))
        if prefix is None:
            self.cur.note("file", "writes a file")
            return
        if prefix.startswith("s3://"):
            self._aws("s3", "PutObject", node, frame)
            return
        ok = prefix.startswith(("/tmp", "/mnt/"))
        self._spot("write", frame.file, node.lineno, frame.function, _unparse(node, 80), prefix, ok=ok)
        self.cur.note("file", f"writes {_clip(prefix, 40)}")

    def _make_client(self, kind: str, node: ast.Call, frame: _Frame) -> _Sym:
        service = _const(node.args[0]) if node.args else next(
            (_const(kw.value) for kw in node.keywords if kw.arg == "service_name"), None)
        service = service if isinstance(service, str) else "?"
        once = (self.loading > 0 or frame.cached or bool(self.target & frame.globals)
                or any("None" in g or g.startswith("not ") for g in frame.guards))
        name = _service_name(service) if service != "?" else "an AWS"
        self._spot("client", frame.file, node.lineno, frame.function, _unparse(node, 80), service, ok=once,
                   loop=self._loop_of(frame))
        self.cur.note("client", f"makes {_a(name)} {name} {'client' if kind == 'client' else 'resource'}")
        return _Sym(kind if kind == "client" else "resource", service)

    def _aws_method(self, func: _Sym, node: ast.Call, frame: _Frame) -> _Sym | None:
        owner, service, method = func.inner, func.name, func.sub
        if owner is None:
            return None
        if owner.kind == "client":
            if method == "get_paginator":
                op = self._op(service, _const(node.args[0]) if node.args else None)
                return _Sym("paginator", service, op) if op else None
            if method == "get_waiter":
                self.cur.note("aws-reads", f"{_service_name(service)} waiter {_const(node.args[0]) if node.args else ''}"
                              .strip())
                return None
            if method in ("generate_presigned_url", "generate_presigned_post"):
                what = _const(node.args[0]) if node.args and method.endswith("url") else next(
                    (_const(kw.value) for kw in node.keywords if kw.arg == "ClientMethod"), None)
                op = self._op(service, what) if isinstance(what, str) else ("PutObject" if method.endswith("post")
                                                                              else None)
                if op:
                    self._aws(service, op, node, frame, access="signs")
                return None
            if service == "s3" and method in _TRANSFERS:
                self._aws(service, _TRANSFERS[method], node, frame)
                return None
            if service == "?" and method in _GUESSES:
                self._aws(*_GUESSES[method], node, frame, guessed=True)
                return None
            op = self._op(service, method)
            if op:
                self._aws(service, op, node, frame)
            return None
        if owner.kind == "resource":
            if method[:1].isupper():
                return _Sym("res", service, method)
            if method == "get_queue_by_name":
                self._aws("sqs", "GetQueueUrl", node, frame)
                return _Sym("res", "sqs", "Queue")
            op = self._op(service, method)
            if op:
                self._aws(service, op, node, frame)
            return None
        if owner.kind == "res":
            if method[:1].isupper():
                return _Sym("res", service, method)
            if method == "batch_writer":
                return _Sym("batch", service, "BatchWriteItem")
            op = _RESOURCE_ACTIONS.get((service, owner.sub), {}).get(method) or self._op(service, method)
            if op:
                self._aws(service, op, node, frame)
            return None
        if owner.kind == "coll":
            if method in ("all", "filter", "limit", "page_size", "pages"):
                self._aws(service, owner.sub, node, frame, pages="all")
                return owner
            if method == "delete":
                self._aws(service, "DeleteObjects", node, frame)
            return None
        if owner.kind == "paginator" and method == "paginate":
            self._aws(service, owner.sub, node, frame, pages="all")
        elif owner.kind == "batch" and method in ("put_item", "delete_item"):
            self._aws(service, owner.sub, node, frame)
        return None

    # ---------------------------------------------------------------- calls into its own code

    def _run(self, key: str, args: list[_Sym | None], kwargs: dict[str, _Sym | None], caller: _Frame | None,
             call: Any, bound: _Sym | None = None, label: str = "") -> _Sym | None:
        """
        One of its own functions, called: its steps (shown the first time), and what it returns.
        """
        module, node, cls = self.defs[key]
        file = self.modules[module].file or ""
        depth = caller.depth + 1 if caller is not None and caller.function != caller.module else 0
        info = self.reached.get(key)
        if info is None:
            end = getattr(node, "end_lineno", None) or node.lineno
            info = self.reached[key] = CodeFunction(key, file, node.lineno, end - node.lineno + 1,
                                                    _unparse(node.args, 100), _doc_line(node), depth=depth)
        info.depth = min(info.depth, depth)
        if caller is not None and call is not None:
            where = f"{caller.function} line {call.lineno}"
            if where not in info.called_from:
                info.called_from.append(where)
        step = CodeStep(file, node.lineno, _def_line(node), "def", name=key)
        if label:
            step.notes.append(("route", label))
        self.cur.calls.append(step)
        if key in self.stack:
            step.notes.append(("more", "calls itself: not followed again"))
            step.seen = True
            return None
        if depth > _MAP_DEPTH or self.count > _MAP_STEPS or self.runs > _MAP_RUNS:
            step.notes.append(("more", "not opened: " + ("too many calls deep" if depth > _MAP_DEPTH
                                                         else "the outline is long enough")))
            step.seen = True
            self.cmap.limited = (f"followed calls {_MAP_DEPTH} deep" if depth > _MAP_DEPTH
                                 else f"stopped opening functions after {_MAP_STEPS:,} lines" if self.count > _MAP_STEPS
                                 else f"stopped after reading {_MAP_RUNS:,} function bodies")
            return None
        a = node.args
        params = [p.arg for p in a.posonlyargs + a.args]
        values = ([bound] if bound is not None else []) + list(args)
        scope: dict[str, _Sym | None] = dict(zip(params, values))
        for name in params[len(values):] + [p.arg for p in a.kwonlyargs]:
            scope.setdefault(name, kwargs.get(name))
        for extra in (a.vararg, a.kwarg):
            if extra is not None:
                scope[extra.arg] = None
        loops = list(caller.loops) if caller is not None else []
        memo = (key, bool(loops), tuple(sorted(((k, v) for k, v in scope.items() if v is not None),
                                               key=lambda kv: kv[0])))
        if memo in self.memo:
            step.seen = key in self.shown
            if not step.seen:
                self.shown.add(key)
            return self.memo[memo]
        self.memo[memo] = None
        self.runs += 1
        frame = _Frame(module, file, key, scope, node=node, outers=self.closures.get(key, []), loops=loops,
                       cached=key in self.cached, depth=depth)
        self.stack.append(key)
        try:
            steps = self._block(node.body, frame)
        finally:
            self.stack.pop()
        value = next((v for v in frame.returns if v is not None), None)
        self.memo[memo] = value
        if key in self.shown:
            step.seen = True
        else:
            self.shown.add(key)
            step.steps = steps
        return value

    # ---------------------------------------------------------------- what it reaches

    def _loop_of(self, frame: _Frame) -> str:
        return frame.loops[-1] if frame.loops else ""

    def _op(self, service: str, method: Any) -> str | None:
        """
        A client method's operation: ('s3', 'get_object') -> 'GetObject'; None when it isn't one.
        """
        if not isinstance(method, str):
            return None
        model = _aws_model(service)
        if model is None:
            return _GUESSES[method][1] if method in _GUESSES else None
        return model["ops"].get(method)

    def _aws(self, service: str, op: str, node: ast.Call, frame: _Frame, *, access: str | None = None,
             pages: str | None = None, guessed: bool = False) -> None:
        access = access or _access(op)
        if pages is None:
            pages = self._pages(service, op, node, frame)
        key = (service, op, frame.file, node.lineno)
        if key not in self.seen:
            self.seen.add(key)
            self.cmap.aws.append(AwsCall(service, op, access, _iam_action(service, op), frame.file, node.lineno,
                                         frame.function, _unparse(node, 120), self._loop_of(frame), pages, guessed,
                                         self.loading > 0))
        self.cur.note(f"aws-{access}", f"{_service_name(service)} {op}")

    def _pages(self, service: str, op: str, node: ast.Call, frame: _Frame) -> str:
        """
        'one' when a list call reads only its first page, 'all' when the code follows the next-page token, ''
        when the call doesn't come in pages, or asks for a set number.
        """
        model = _aws_model(service)
        if model is None or op not in model["pages"]:
            return ""
        inputs, outputs, limit = model["pages"][op]
        given = {kw.arg for kw in node.keywords if kw.arg}
        if limit and limit in given:
            return ""
        if given & inputs or any(kw.arg is None for kw in node.keywords):
            return "all"
        if frame.words is None:
            tree = frame.node if frame.node is not None else (self.modules[frame.module].tree
                                                              if frame.module in self.modules else None)
            frame.words = set()
            for n in ast.walk(tree) if tree is not None else []:
                if isinstance(n, ast.Name):
                    frame.words.add(n.id)
                elif isinstance(n, ast.Attribute):
                    frame.words.add(n.attr)
                elif isinstance(n, ast.Constant) and isinstance(n.value, str):
                    frame.words.add(n.value)
        return "all" if any(word in frame.words for word in outputs) else "one"

    def _env_read(self, name: str, node: Any, frame: _Frame, *, required: bool, default: str = "") -> None:
        key = ("env", name, frame.file, node.lineno)
        if key not in self.seen:
            self.seen.add(key)
            self.cmap.env.append(EnvRead(name, frame.file, node.lineno, frame.function, required, default,
                                         self.loading > 0))
        self.cur.note("env", name)

    def _event_read(self, path: tuple[str, ...], node: Any, frame: _Frame, *, required: bool) -> None:
        if "*" in path:
            return
        found = self.event_reads.get(path)
        if found is None:
            self.event_reads[path] = EventField(path, frame.file, node.lineno, required)
        elif required and not found.required:
            found.required = True
        self.cur.note("event", _event_label(path))

    def _http(self, library: str, method: str, node: ast.Call, frame: _Frame, given: bool = False) -> None:
        args = list(node.args)
        if method == "request" and args:  # requests.request('POST', url), pool.request('GET', url)
            verb = _const(args[0])
            method, args = (str(verb).lower() if isinstance(verb, str) else "request"), args[1:]
        url = args[0] if args else next((kw.value for kw in node.keywords if kw.arg == "url"), None)
        has_timeout = (given or library in _DEFAULT_TIMEOUTS or any(kw.arg == "timeout" for kw in node.keywords))
        verb = method.upper() if method in _HTTP_METHODS and method != "request" else "request"
        target = _unparse(url, 60) if url is not None else ""
        self._spot("http", frame.file, node.lineno, frame.function, _unparse(node, 100), f"{verb} {target}".strip(),
                   ok=has_timeout, loop=self._loop_of(frame))
        shown = _const(url)
        self.cur.note("http", f"HTTP {verb}" + (f" {_clip(str(shown), 40)}" if isinstance(shown, str) else ""))

    def _spot(self, kind: str, file: str, line: int, function: str, code: str, detail: str = "", *, ok: bool = True,
              loop: str = "") -> None:
        key = (kind, file, line, detail)
        if key not in self.seen:
            self.seen.add(key)
            self.cmap.spots.append(CodeSpot(kind, file, line, function, code, detail, ok, loop, self.loading > 0))

    def _secrets(self, mod: _Module) -> None:
        """
        Secret-looking values written in the code: a password, token or key given as a literal. Only their names
        are kept, never the values (an AWS access key is found in every file, by read()).
        """
        for name, value in _secret_spots(mod.tree):
            if name != "an AWS access key":
                self._spot("secret", mod.file or "", value.lineno, mod.name, "", name, ok=False)
