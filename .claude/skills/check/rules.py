"""
Check the analyzers against the hard constraints in CLAUDE.md that ruff and pytest don't catch.

    python .claude/skills/check/rules.py            # errors and warnings; exit 1 on any error
    python .claude/skills/check/rules.py --apis     # also list every AWS operation each analyzer calls
    python .claude/skills/check/rules.py path.py    # just this file

Errors (exit 1):
  - a top-level import that isn't the standard library, boto3 or botocore (optional packages load lazily)
  - an import of another analyzer (shared code goes in _kit/), except a companion importing the analyzers it
    builds on (COMPANIONS: the agent/ package on lambda_functions/ and s3/); an import of a
    module a few analyzers share by any other analyzer (SHARED: _bedrock.py, for bedrock_kb.py, bedrock_chat.py and
    agent/); a shared module importing an analyzer;
    and _kit/ importing any of them
  - an analyzer defining a name that _kit/ has, or the shared module it imports (a copy of a shared helper: import
    it from there instead); and a shared module defining a name that _kit/ has
  - no `from __future__ import annotations`
  - the five numbered `# N. ...` section banners missing or out of order (not in _kit/, a shared module or an
    analyzer that is a package, like agent/: one concern per module there, and one of them has the View)
  - an AWS operation that isn't read-only (Put*, Delete*, Create*, ...) - nothing may write
  - a public View method without @_friendly_errors (a companion's window: @_window_errors) or a docstring
    (help() lists the docstring's first line), or one that prints / displays directly instead of building blocks
    for self._show()
  - print() outside the View class (the analyzer and the pure functions never print)
  - in an analyzer that is a package (a service split into one, see .claude/skills/modularize): a core module
    importing the UI (its package's style / blocks / view / explorer / window, or _kit's blocks, render, view,
    window, progress, highlight or jsontree; render's _call and _signature are allowed), its analysis importing its
    analyzer, a function or class defined in two of its modules, and the command checks above on its *Explorer and
    on the mixins a View or window is made of (_<Topic>Commands, _<Topic>Tab). Its AWS calls are checked against
    the clients every module of the package makes.
Warnings:
  - PartiQL (execute_statement and friends) can write; it needs a guard that only lets SELECT through, marked
    with a `# read-only: <why>` comment on the call's line
  - a function-level import of a third-party package other than IPython (use _require(module, purpose))
  - a public View method not annotated `-> None`
  - an AWS operation whose IAM permission isn't in README.md
  - a companion package (agent/) reaching into a private name of an analyzer it builds on (core._window)
  - an alias module (ALIASES: s3_explorer.py) holding anything but its docstring, imports from the analyzer it stands
    for and __all__ (that's an error)
"""

from __future__ import annotations

import argparse
import ast
import functools
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
PACKAGE = "aws_analyzer"
ANALYZERS = ROOT / "src" / PACKAGE
KIT = ANALYZERS / "_kit"  # the code the analyzers share: checked like them, but it has no sections or View
ALLOWED_TOP_LEVEL = {"boto3", "botocore"}
# A companion builds on analyzers: it imports them (lazily, so it still imports alone), its UI class is a
# <Service>View or <Service>Explorer, and its AWS calls are checked against those analyzers' services. The agent
# builds on every analyzer one of its toolkits reads through (agent/toolkits/: LambdaToolkit on LambdaAnalyzer,
# S3Toolkit on S3Analyzer).
COMPANIONS = {"agent": ("lambda_functions", "s3")}
ALIASES = {"s3_explorer": "s3"}
# Code a few analyzers share that the others must not load: a module of the package beside them -> the analyzers
# that import it. It's checked like _kit (no sections or View), imports _kit and never an analyzer, and the
# analyzers that import it don't define its names again.
SHARED = {"_bedrock": {"bedrock_kb", "bedrock_chat", "agent"}}  # the agent shows its answers with _bedrock's _Answer
# An analyzer can be a package (agent/): its modules are checked like an analyzer's file, as one analyzer.
PACKAGES = {p.parent.name for p in ANALYZERS.glob("*/__init__.py") if not p.parent.name.startswith("_")}
UI_LAYERS = {"style", "blocks", "view", "explorer", "window"}
KIT_UI = {"blocks", "render", "view", "window", "progress", "highlight", "jsontree"}
CORE_FROM_RENDER = {"_call", "_signature"}
MIXIN_RE = re.compile(r"_\w+(Commands|Tab)$")
REACHING_RECEIVER_RE = re.compile(r"_\w+_module$")
NEWER_STDLIB = {"compression", "annotationlib", "tomllib", "zoneinfo", "graphlib"}  # stdlib on newer Pythons
READ_PREFIXES = ("Get", "List", "Describe", "Head", "Scan", "Query", "Select", "BatchGet", "Lookup", "Search",
                 "Retrieve")
PARTIQL = {"ExecuteStatement", "BatchExecuteStatement", "ExecuteTransaction"}
PRAGMA = "# read-only:"
REGEX_METHODS = {"search", "match", "fullmatch", "findall", "finditer", "sub", "subn", "split"}

# IAM action for operations whose permission isn't simply <service>:<OperationName>.
IAM_ACTION = {
    "s3:ListObjectsV2": "s3:ListBucket", "s3:ListObjects": "s3:ListBucket", "s3:HeadBucket": "s3:ListBucket",
    "s3:ListObjectVersions": "s3:ListBucketVersions", "s3:ListMultipartUploads": "s3:ListBucketMultipartUploads",
    "s3:ListParts": "s3:ListMultipartUploadParts", "s3:HeadObject": "s3:GetObject",
    "s3:SelectObjectContent": "s3:GetObject", "s3:ListBuckets": "s3:ListAllMyBuckets",
    "s3:GetBucketLifecycleConfiguration": "s3:GetLifecycleConfiguration",
    "s3:GetBucketReplication": "s3:GetReplicationConfiguration",
    "s3:GetBucketEncryption": "s3:GetEncryptionConfiguration",
    "s3:GetBucketInventoryConfiguration": "s3:GetInventoryConfiguration",
    "s3:ListBucketInventoryConfigurations": "s3:GetInventoryConfiguration",
    "s3:GetBucketIntelligentTieringConfiguration": "s3:GetIntelligentTieringConfiguration",
    "s3:ListBucketIntelligentTieringConfigurations": "s3:GetIntelligentTieringConfiguration",
    "s3:GetBucketAnalyticsConfiguration": "s3:GetAnalyticsConfiguration",
    "s3:ListBucketAnalyticsConfigurations": "s3:GetAnalyticsConfiguration",
    "s3:GetBucketMetricsConfiguration": "s3:GetMetricsConfiguration",
    "s3:ListBucketMetricsConfigurations": "s3:GetMetricsConfiguration",
    "s3:GetObjectLockConfiguration": "s3:GetBucketObjectLockConfiguration",
    "s3:GetPublicAccessBlock": "s3:GetBucketPublicAccessBlock",
    "s3:GetBucketNotificationConfiguration": "s3:GetBucketNotification",
    "s3control:GetPublicAccessBlock": "s3:GetAccountPublicAccessBlock",
    "dynamodb:ExecuteStatement": "dynamodb:PartiQLSelect",
    "bedrock-runtime:Converse": "bedrock:InvokeModel",
    "bedrock-agent-runtime:RetrieveAndGenerateStream": "bedrock:RetrieveAndGenerate",  # one permission for both
    "sts:GetCallerIdentity": None,  # needs no permission
}
# boto3 service name -> IAM service prefix, where they differ (every Bedrock client is authorized as bedrock:,
# OpenSearch Service as es: and OpenSearch Serverless as aoss:).
IAM_PREFIX = {"bedrock-agent": "bedrock", "bedrock-agent-runtime": "bedrock", "bedrock-runtime": "bedrock",
              "opensearch": "es", "opensearchserverless": "aoss"}


def iam_action(candidate: str) -> str | None:
    """
    's3:ListObjectsV2' -> 's3:ListBucket', 'bedrock-agent:GetKnowledgeBase' -> 'bedrock:GetKnowledgeBase'.
    """
    if candidate in IAM_ACTION:
        return IAM_ACTION[candidate]
    service, _, name = candidate.partition(":")
    return f"{IAM_PREFIX.get(service, service)}:{name}"


class Report:
    def __init__(self) -> None:
        self.errors: list[str] = []
        self.warnings: list[str] = []

    def error(self, where: str, text: str) -> None:
        self.errors.append(f"{where}: {text}")

    def warn(self, where: str, text: str) -> None:
        self.warnings.append(f"{where}: {text}")


def _module_root(node: ast.Import | ast.ImportFrom) -> list[str]:
    if isinstance(node, ast.ImportFrom):
        return [] if node.level else [(node.module or "").split(".")[0]]
    return [alias.name.split(".")[0] for alias in node.names]


def _analyzers_imported(node: ast.Import | ast.ImportFrom, inside: tuple[str, ...] = ()) -> list[str]:
    """
    The modules an import loads, by their name in the package: `import s3`, `from .s3 import x`,
    `from aws_analyzer import s3` and `import aws_analyzer.s3` all give ["s3"]. `inside` is the package the
    importing file is in, below aws_analyzer (("agent", "toolkits") for agent/toolkits/x.py): a relative import is
    read from there, so `from ...lambda_functions import x` gives ["lambda_functions"] and `from ..budget import x`
    gives ["agent"].
    """
    if isinstance(node, ast.Import):
        return [alias.name.split(".")[1] if alias.name.startswith(f"{PACKAGE}.") else alias.name.split(".")[0]
                for alias in node.names]
    module = node.module or ""
    if node.level:  # from .s3 import x, from . import s3, from ..budget import x
        base = list(inside[:max(len(inside) - (node.level - 1), 0)])
        if module:
            return [(base + module.split("."))[0]]
        return [(base + [alias.name])[0] for alias in node.names]
    if module == PACKAGE:  # from aws_analyzer import s3
        return [alias.name for alias in node.names]
    if module.startswith(f"{PACKAGE}."):  # from aws_analyzer.s3 import x
        return [module.split(".")[1]]
    return [module.split(".")[0]]


def _is_stdlib(name: str) -> bool:
    return name in sys.stdlib_module_names or name in NEWER_STDLIB or name == "__future__"


def _operations(services: set[str]) -> dict[str, list[tuple[str, str]]]:
    """
    snake_case operation name -> [(service, OperationName)] for the services the analyzer creates clients for.
    A name can belong to several (s3 and s3control both have get_public_access_block).
    """
    try:
        import botocore.session
        from botocore import xform_name
    except ImportError:
        return {}
    session = botocore.session.get_session()
    ops: dict[str, list[tuple[str, str]]] = {}
    for service in sorted(services):
        try:
            model = session.get_service_model(service)
        except Exception:
            continue
        for op in getattr(model, "operation_names", []):
            ops.setdefault(xform_name(op), []).append((service, op))
    return ops


def _top_level_names(tree: ast.Module) -> list[tuple[str, int]]:
    """
    (name, line) of every function, class and variable a module defines at its top level.
    """
    names = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.append((node.name, node.lineno))
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            names += [(t.id, node.lineno) for t in targets if isinstance(t, ast.Name)]
    return names


def kit_names() -> dict[str, str]:
    """
    Every name _kit/ defines -> the module it's in (fmt, text, ...).
    """
    names: dict[str, str] = {}
    for path in sorted(KIT.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        names.update((name, path.stem) for name, _ in _top_level_names(tree))
    return names


def module_files(name: str) -> list[Path]:
    """
    The files of a module of the package, by its name there: [s3.py] for a file, every module of a package (agent/,
    _bedrock/ once it's one), its __init__.py included. Empty for a name that isn't there.
    """
    path = ANALYZERS / f"{name}.py"
    if path.exists():
        return [path]
    if (ANALYZERS / name / "__init__.py").exists():
        return sorted(p for p in (ANALYZERS / name).rglob("*.py") if "__pycache__" not in p.parts)
    return []


def top_modules() -> set[str]:
    """
    The name of every module directly in the package, a file or a package, except _kit, which every analyzer imports.
    """
    files = {p.stem for p in ANALYZERS.glob("*.py")} - {"__init__"}
    packages = {p.parent.name for p in ANALYZERS.glob("*/__init__.py")} - {KIT.name}
    return files | packages


@functools.lru_cache(maxsize=None)
def owner_functions(owner: str) -> frozenset[str]:
    """
    Every function and method an analyzer defines, across all its modules: a call like `self.core.list_functions()`
    in its view is its own method, not the ListFunctions operation, though the view's module doesn't define it.
    """
    names: set[str] = set()
    for path in module_files(owner):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        names |= {n.name for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    return frozenset(names)


@functools.lru_cache(maxsize=None)
def owner_services(owner: str) -> frozenset[str]:
    """
    The services every module of an analyzer creates clients for, together: a module of a package that calls a
    client another of its modules made is checked against that client's service too.
    """
    services: set[str] = set()
    for path in module_files(owner):
        services |= _client_services(ast.parse(path.read_text(encoding="utf-8"), filename=str(path)))
    return frozenset(services)


def shared_names() -> dict[str, set[str]]:
    """
    Every name each shared module defines: {"_bedrock": {"MODEL_PRICES", ...}} (none for one not written yet).
    """
    names: dict[str, set[str]] = {}
    for module in SHARED:
        for path in module_files(module):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            names.setdefault(module, set()).update(name for name, _ in _top_level_names(tree))
    return names


def _ui_classes(tree: ast.Module, *, in_package: bool, is_companion: bool) -> list[ast.ClassDef]:
    """
    The classes whose public methods are commands a user runs: the <Service>View, a companion's <Service>Explorer, and
    in a package also its <Service>Explorer and the mixins they're made of (_<Topic>Commands, _<Topic>Tab).
    A file analyzer's own explorer window isn't checked here (rules for it came with the packages).
    """
    suffixes = ("View", "Explorer") if in_package or is_companion else ("View",)
    classes = []
    for node in tree.body:
        if not isinstance(node, ast.ClassDef):
            continue
        is_mixin = in_package and MIXIN_RE.fullmatch(node.name) is not None
        if node.name.endswith(suffixes) or is_mixin:
            classes.append(node)
    return classes


def _guards(class_name: str, *, is_companion: bool) -> set[str]:
    """
    The decorators that guard a command of this class: @_friendly_errors on a View's, _kit's @_window_errors on a
    window's (an explorer or one of its tabs). A companion's classes may use either.
    """
    if is_companion:
        return {"_friendly_errors", "_window_errors"}
    if class_name.endswith(("Explorer", "Tab")):
        return {"_window_errors"}
    return {"_friendly_errors"}


def _is_attribute(member: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """
    Whether a method is a property (or its setter or deleter): an attribute to read or set, not a command.
    """
    return any(isinstance(d, ast.Name) and d.id == "property"
               or isinstance(d, ast.Attribute) and d.attr in ("setter", "deleter") for d in member.decorator_list)


def check_commands(ui_class: ast.ClassDef, rel: str, report: Report, *, guards: set[str]) -> None:
    """
    Each public command of a View, an explorer or one of their mixins is guarded, documented, returns None, and draws
    through blocks rather than printing or displaying.
    """
    for member in ui_class.body:
        if not isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef)) or member.name.startswith("_"):
            continue
        if _is_attribute(member):
            continue
        where = f"{rel}:{member.lineno} {ui_class.name}.{member.name}"
        decorators = {d.id if isinstance(d, ast.Name) else getattr(d, "attr", "") for d in member.decorator_list}
        if member.name != "help" and not decorators & guards:
            report.error(where, f"no @{' or @'.join(sorted(guards))} (an AWS or input error would show a traceback)")
        doc = ast.get_docstring(member)
        if not doc or not doc.strip().split("\n")[0].strip():
            report.error(where, "no docstring; help() shows its first line as the description")
        if not (isinstance(member.returns, ast.Constant) and member.returns.value is None):
            report.warn(where, "not annotated -> None (View methods render and return nothing)")
        for node in ast.walk(member):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)):
                continue
            if node.func.id in ("print", "display", "HTML"):
                report.error(f"{rel}:{node.lineno} {ui_class.name}.{member.name}",
                             f"calls {node.func.id}(); add blocks and pass them to self._show()")


def _path_in_package(path: Path, inside: tuple[str, ...]) -> tuple[str, ...]:
    """
    Where a module sits below its analyzer's package: opensearch/analysis/findings.py -> ("analysis", "findings"),
    opensearch/view.py -> ("view",), and () for the package's own __init__.py.
    """
    below = inside[1:]
    return below if path.stem == "__init__" else (*below, path.stem)


def _import_targets(node: ast.Import | ast.ImportFrom, inside: tuple[str, ...]) -> list[tuple[tuple[str, ...], set[str]]]:
    """
    What an import loads from aws_analyzer, as (its path below the package, the names taken from it):
    `from .._kit.render import _call` in opensearch/analysis.py -> [(("_kit", "render"), {"_call"})], and
    `from . import view` in opensearch/explorer.py -> [(("opensearch", "view"), set())]. Imports from outside the
    package give nothing.
    """
    if isinstance(node, ast.Import):
        prefix = f"{PACKAGE}."
        return [(tuple(alias.name[len(prefix):].split(".")), set()) for alias in node.names
                if alias.name.startswith(prefix)]
    module = node.module or ""
    if node.level:
        base = tuple(inside[:max(len(inside) - (node.level - 1), 0)])
    elif module == PACKAGE or module.startswith(f"{PACKAGE}."):
        base, module = (), module[len(PACKAGE) + 1:]
    else:
        return []
    names = {alias.name for alias in node.names}
    if module:
        return [((*base, *module.split(".")), names)]
    return [((*base, alias.name), set()) for alias in node.names]


def check_layers(tree: ast.Module, rel: str, report: Report, *, owner: str, own_path: tuple[str, ...]) -> None:
    """
    A package's core (helpers, models, analysis, analyzer and the service's other core modules) never imports its
    UI (style, blocks, view, explorer, window) or _kit's (blocks, render, view, window, progress, highlight,
    jsontree; render's _call and _signature, which write a call into a finding's text, are the exception), and its
    pure analysis never imports the analyzer, which talks to AWS. The UI may import anything, and the package's
    __init__.py re-exports both.
    """
    if not own_path or own_path[0] in UI_LAYERS:
        return
    is_analysis = own_path[0].startswith("analysis")
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Import, ast.ImportFrom)):
            continue
        where = f"{rel}:{node.lineno}"
        for target, names in _import_targets(node, (owner, *own_path[:-1])):
            if len(target) < 2:
                continue
            if target[0] == owner and target[1] in UI_LAYERS:
                report.error(where, f"the core module {'.'.join(own_path)} imports {target[1]}, which is UI; the "
                                    "core never knows a front-end exists (move what both need into the core)")
            elif target[0] == KIT.name and target[1] in KIT_UI and not (
                    target[1] == "render" and names and names <= CORE_FROM_RENDER):
                report.error(where, f"the core module {'.'.join(own_path)} imports _kit.{target[1]}, which is UI")
            elif is_analysis and target[0] == owner and target[1].startswith("analyzer"):
                report.error(where, f"{'.'.join(own_path)} is pure analysis but imports {target[1]}, which talks "
                                    "to AWS")


def _reaching_receivers(tree: ast.Module) -> set[str]:
    """
    The names a companion binds to an analyzer it builds on or to that analyzer's module: `core = self.analyzer`,
    `lf = _lambda_module()`.
    """
    receivers: set[str] = set()
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name)):
            continue
        value = node.value
        is_analyzer = isinstance(value, ast.Attribute) and value.attr == "analyzer"
        is_module = (isinstance(value, ast.Call) and isinstance(value.func, ast.Name)
                     and REACHING_RECEIVER_RE.fullmatch(value.func.id) is not None)
        if is_analyzer or is_module:
            receivers.add(node.targets[0].id)
    return receivers


def check_reach(tree: ast.Module, rel: str, report: Report) -> None:
    """
    A companion package (agent/) reads the analyzers it builds on through their public names only: their private
    ones may move or change in any refactor (`core._window`, `s3._exposure`). A warning until every service is a
    package (see .claude/skills/modularize).
    """
    receivers = _reaching_receivers(tree)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Attribute) or not node.attr.startswith("_") or node.attr.startswith("__"):
            continue
        value = node.value
        is_receiver = isinstance(value, ast.Name) and value.id in receivers
        is_analyzer = isinstance(value, ast.Attribute) and value.attr == "analyzer"
        if is_receiver or is_analyzer:
            report.warn(f"{rel}:{node.lineno}", f"reaches into the private name {node.attr} of an analyzer it builds "
                                                "on; make it public there and use that")


def check_file(path: Path, report: Report, readme: str, apis: dict[str, set[str]],
               kit: dict[str, str] | None = None, shared: dict[str, set[str]] | None = None) -> None:
    rel = path.relative_to(ROOT).as_posix() if path.is_relative_to(ROOT) else str(path)
    source = path.read_text(encoding="utf-8")
    lines = source.splitlines()
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as exc:
        report.error(f"{rel}:{exc.lineno}", f"doesn't parse: {exc.msg}")
        return
    owner, inside = _owner(path)
    in_kit = owner == KIT.name
    in_package = owner in PACKAGES
    is_shared = not in_kit and not in_package and owner in SHARED
    parents = () if in_kit else COMPANIONS.get(owner, ())
    uses = set() if in_kit else {module for module, users in SHARED.items() if owner in users}
    siblings = top_modules() - {owner, *parents} - uses
    kit = kit_names() if kit is None else kit
    shared = shared_names() if shared is None else shared

    # Imports: boto3 + stdlib at import time, optional packages lazily, never another analyzer.
    future = any(isinstance(n, ast.ImportFrom) and n.module == "__future__"
                 and any(a.name == "annotations" for a in n.names) for n in tree.body)
    if not future:
        report.error(rel, "no `from __future__ import annotations`")
    top_level = {id(n) for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom))}
    for stmt in tree.body:  # imports inside module-level try/if still run at import time
        if not isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            top_level |= {id(n) for n in ast.walk(stmt) if isinstance(n, (ast.Import, ast.ImportFrom))}
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Import, ast.ImportFrom)):
            continue
        where = f"{rel}:{node.lineno}"
        for name in sorted(set(_analyzers_imported(node, inside)) & siblings):
            if in_kit:
                report.error(where, f"_kit imports {name}; _kit loads with every analyzer, so it never imports an "
                                    "analyzer or a module some of them share")
            elif name in SHARED:
                report.error(where, f"imports {name}, which only {' and '.join(sorted(SHARED[name]))} share; what "
                                    "every analyzer needs goes in _kit/")
            elif is_shared:
                report.error(where, f"imports the {name} analyzer; {path.stem} loads with "
                                    f"{' and '.join(sorted(SHARED[path.stem]))}, so it never imports an analyzer")
            else:
                report.error(where, f"imports the {name} analyzer; analyzers don't import each other (shared "
                                    "code goes in _kit/)")
        for name in _module_root(node):
            if name in siblings or name == PACKAGE:
                continue  # reported above
            if id(node) in top_level and not _is_stdlib(name) and name not in ALLOWED_TOP_LEVEL:
                report.error(where, f"imports {name} at import time; only boto3 + stdlib may load there "
                                    f"(import it inside the function via _require({name!r}, ...))")
            elif (id(node) not in top_level and not _is_stdlib(name) and name not in parents
                  and name not in ALLOWED_TOP_LEVEL | {"IPython"}):
                report.warn(where, f"imports {name} directly; use _require({name!r}, purpose) so a missing "
                                   "package becomes a note that says what to pip install")

    # A copy of a shared helper: _kit (or the shared module the analyzer imports) has it, so it's imported from there.
    if not in_kit:
        for name, line in _top_level_names(tree):
            if name in kit:
                report.error(f"{rel}:{line}", f"defines {name}, which _kit/{kit[name]}.py has; import it from there "
                                              "(from ._kit." + kit[name] + " import " + name + ") instead of a copy")
            for module in sorted(uses):
                if name in shared.get(module, set()):
                    report.error(f"{rel}:{line}", f"defines {name}, which {module}.py has; import it from there "
                                                  f"(from .{module} import {name}) instead of a copy")

    # Section banners 1..5 in order.
    banners = [int(m.group(1)) for m in re.finditer(r"^# (\d+)\. ", source, re.MULTILINE)]
    if banners != [1, 2, 3, 4, 5] and not in_kit and not is_shared and not in_package:
        report.error(rel, f"section banners are {banners or 'missing'}; expected # 1. .. # 5. in order "
                          "(Helpers, Data models, Pure analysis, <Service>Analyzer, <Service>View)")

    # View methods: decorated, documented, render through blocks.
    ui_classes = _ui_classes(tree, in_package=in_package, is_companion=bool(parents))
    has_view = any(isinstance(n, ast.ClassDef) and n.name.endswith("View") for n in tree.body)
    if not has_view and not ui_classes and not in_kit and not is_shared and not in_package:
        report.error(rel, "no <Service>View class")
    for ui_class in ui_classes:
        check_commands(ui_class, rel, report, guards=_guards(ui_class.name, is_companion=bool(parents)))

    if in_package:
        check_layers(tree, rel, report, owner=owner, own_path=_path_in_package(path, inside))
    if in_package and parents:
        check_reach(tree, rel, report)

    # print() outside the View.
    for stmt in tree.body:
        if stmt in ui_classes:
            continue
        for node in ast.walk(stmt):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "print":
                report.error(f"{rel}:{node.lineno}", "print() outside the View; the analyzer and pure functions "
                                                     "return data and never print")

    # AWS operations: read-only, and each one's permission documented in README.md.
    services = _client_services(tree) | owner_services(owner)
    for parent in parents:  # it calls AWS through the analyzers' clients
        services |= owner_services(parent)
    ops = _operations(services)
    defined = {n.name for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    defined |= owner_functions(owner)
    # Strings that are keyword values or compared with something (engine="converse", kind == "scan") are labels,
    # not operation names handed to a client.
    labels = {id(n.value) for n in ast.walk(tree) if isinstance(n, ast.keyword)}
    labels |= {id(c) for n in ast.walk(tree) if isinstance(n, ast.Compare) for c in (n.left, *n.comparators)}
    found: dict[str, int] = {}
    for node in ast.walk(tree):
        name = None
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in ops:
            target = ast.get_source_segment(source, node.func.value) or ""
            # pattern.search(...) is a regex, not SageMaker's Search, unless it's called on a client
            if "client" in target.lower() or (node.func.attr not in defined and node.func.attr not in REGEX_METHODS):
                name = node.func.attr
        elif (isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value in ops
              and id(node) not in labels):
            name = node.value  # getattr(client, "get_bucket_policy"), get_paginator("list_objects_v2"), ...
        if name and name not in found:
            found[name] = getattr(node, "lineno", 0)
    for name, lineno in sorted(found.items(), key=lambda kv: kv[1]):
        candidates = [f"{service}:{op}" for service, op in ops[name]]
        actions = [iam_action(c) for c in candidates]
        documented = [c for c, a in zip(candidates, actions) if a is None or _documented(a, readme)]
        apis.setdefault(rel, set()).add(" | ".join(documented or candidates))
        op = ops[name][0][1]
        where = f"{rel}:{lineno}"
        acknowledged = PRAGMA in lines[lineno - 1]
        if op in PARTIQL:
            if not acknowledged:
                report.warn(where, f"{name} runs any PartiQL, including INSERT / UPDATE / DELETE; only let SELECT "
                                   f"through, then mark the line `{PRAGMA} <how>`")
        elif not op.startswith(READ_PREFIXES) and not acknowledged:
            report.error(where, f"{name} ({candidates[0]}) is not a read-only operation; nothing may write to AWS")
        if not documented:
            report.warn(where, f"{name} needs `{' or '.join(a for a in actions if a)}`, which README.md's IAM permissions don't list")


def _owner(path: Path) -> tuple[str, tuple[str, ...]]:
    """
    (the analyzer a file belongs to, the package it's in below aws_analyzer): s3.py -> ("s3", ()),
    _kit/fmt.py -> ("_kit", ("_kit",)), agent/toolkits/lambda_tools.py -> ("agent", ("agent", "toolkits")).
    """
    try:
        parts = path.resolve().relative_to(ANALYZERS.resolve()).parts
    except ValueError:  # a file outside the package: checked as an analyzer of its own
        return path.stem, ()
    return (parts[0], parts[:-1]) if len(parts) > 1 else (path.stem, ())


def package_files(package: str) -> list[Path]:
    """
    Every module of an analyzer that is a package (agent/), its __init__.py included.
    """
    return module_files(package)


def check_package(package: str, report: Report) -> None:
    """
    What a package must have as a whole: a <Service>View class in one of its modules, and each function and class
    defined in one module only (two modules defining the same helper are a copy; one imports it from the other).
    Constants may share a name, since a module's own limit (_ERROR_CHARACTERS) can differ from another's.
    """
    has_view = False
    defined_in: dict[str, list[str]] = {}
    for path in package_files(package):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        has_view = has_view or any(isinstance(n, ast.ClassDef) and n.name.endswith("View") for n in tree.body)
        rel = path.relative_to(ANALYZERS).as_posix()
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                defined_in.setdefault(node.name, []).append(f"{rel}:{node.lineno}")
    if not has_view:
        report.error(f"src/{PACKAGE}/{package}/", "no <Service>View class in any of its modules")
    for name, places in sorted(defined_in.items()):
        if len(places) > 1:
            report.error(f"src/{PACKAGE}/{places[1]}", f"defines {name}, which {places[0]} defines too; keep one and "
                                                     "import it")


def _client_services(tree: ast.AST) -> set[str]:
    """
    The services a file creates clients for: session.client("s3", ...) -> {"s3"}.
    """
    return {n.args[0].value for n in ast.walk(tree) if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Attribute) and n.func.attr == "client" and n.args
            and isinstance(n.args[0], ast.Constant) and isinstance(n.args[0].value, str)}


def check_alias(path: Path, report: Report) -> None:
    """
    An alias module (ALIASES) is kept where an analyzer used to be, so old imports still work (s3_explorer.py, since
    the explorer became s3/explorer/). It holds its docstring, `from __future__ import annotations`, imports from the
    analyzer that took its place and __all__, and nothing else: the code lives in that analyzer.
    """
    rel = path.relative_to(ROOT).as_posix()
    target = ALIASES[path.stem]
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for index, node in enumerate(tree.body):
        is_docstring = index == 0 and isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant)
        is_future = isinstance(node, ast.ImportFrom) and node.module == "__future__"
        is_target = isinstance(node, ast.ImportFrom) and _analyzers_imported(node) == [target]
        is_all = (isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name)
                  and node.targets[0].id == "__all__")
        if not (is_docstring or is_future or is_target or is_all):
            report.error(f"{rel}:{node.lineno}", f"is an alias of {target} (ALIASES), so it only re-exports names "
                                                 f"from there; put this in {target}")


def _documented(action: str, readme: str) -> bool:
    if action in readme:
        return True
    service, _, name = action.partition(":")
    return any(name.startswith(prefix) for prefix in re.findall(rf"{re.escape(service)}:(\w+)\*", readme))


def main() -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").strip().split("\n\n")[0])
    parser.add_argument("files", nargs="*", type=Path,
                        help="files to check (default: src/aws_analyzer/*.py, its packages and _kit/*.py)")
    parser.add_argument("--apis", action="store_true", help="list the AWS operations each analyzer calls")
    args = parser.parse_args()
    readme_path = ROOT / "README.md"
    readme = readme_path.read_text(encoding="utf-8") if readme_path.exists() else ""
    report, apis = Report(), {}
    files = [f.resolve() for f in args.files] or sorted(
        p for p in [*ANALYZERS.glob("*.py"), *KIT.glob("*.py")] if p.name != "__init__.py")
    if not args.files:
        files += [path for package in sorted(PACKAGES) for path in package_files(package)]
        files += [path for module in sorted(SHARED) if (ANALYZERS / module).is_dir() for path in module_files(module)]
    kit, shared = kit_names(), shared_names()
    for path in files:
        if path.parent == ANALYZERS and path.stem in ALIASES:
            check_alias(path, report)
        else:
            check_file(path, report, readme, apis, kit, shared)
    for package in sorted(PACKAGES) if not args.files else ():
        check_package(package, report)
    for label, items in (("error", report.errors), ("warning", report.warnings)):
        for item in items:
            print(f"{label}: {item}")
    if args.apis:
        for rel, names in sorted(apis.items()):
            print(f"\nAWS operations in {rel} ({len(names)}):")
            for name in sorted(names):
                actions = [iam_action(c) or "(none needed)" for c in name.split(" | ")]
                print(f"  {name:<52} IAM: {' | '.join(actions)}")
    owners = [_owner(path)[0] for path in files if not (path.parent == ANALYZERS and path.stem in ALIASES)]
    in_kit = owners.count(KIT.name)
    in_shared = sum(owner in SHARED for owner in owners)
    packages = sorted({owner for owner in owners if owner in PACKAGES})
    in_packages = sum(owner in PACKAGES for owner in owners)
    modules = f"{len(owners) - in_kit - in_shared - in_packages + len(packages)} analyzers"
    if packages:
        modules += f" ({', '.join(f'{p}/' for p in packages)} as a package of {in_packages} modules)"
    if in_shared:
        modules += f", {in_shared} shared module"
    print(f"\nrules: {modules} and {in_kit} _kit modules, {len(report.errors)} errors, "
          f"{len(report.warnings)} warnings")
    return 1 if report.errors else 0


if __name__ == "__main__":
    sys.exit(main())
