"""Check the analyzers against the hard constraints in CLAUDE.md that ruff and pytest don't catch.

    python .claude/skills/check/rules.py            # errors and warnings; exit 1 on any error
    python .claude/skills/check/rules.py --apis     # also list every AWS operation each analyzer calls
    python .claude/skills/check/rules.py path.py    # just this file

Errors (exit 1):
  - a top-level import that isn't the standard library, boto3 or botocore (optional packages load lazily)
  - an import of another analyzer (shared code goes in _kit/), except a companion importing the analyzer it
    builds on (COMPANIONS: s3_explorer.py on s3.py); and _kit/ importing any analyzer
  - an analyzer defining a name that _kit/ has (a copy of a shared helper: import it from _kit instead)
  - no `from __future__ import annotations`
  - the five numbered `# N. ...` section banners missing or out of order (not in _kit/)
  - an AWS operation that isn't read-only (Put*, Delete*, Create*, ...) - nothing may write
  - a public View method without @_friendly_errors or a docstring (help() lists the docstring's first line),
    or one that prints / displays directly instead of building blocks for self._show()
  - print() outside the View class (the analyzer and the pure functions never print)
Warnings:
  - PartiQL (execute_statement and friends) can write; it needs a guard that only lets SELECT through, marked
    with a `# read-only: <why>` comment on the call's line
  - a function-level import of a third-party package other than IPython (use _require(module, purpose))
  - a public View method not annotated `-> None`
  - an AWS operation whose IAM permission isn't in README.md
"""

from __future__ import annotations

import argparse
import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
PACKAGE = "aws_analyzer"
ANALYZERS = ROOT / "src" / PACKAGE
KIT = ANALYZERS / "_kit"  # the code the analyzers share: checked like them, but it has no sections or View
ALLOWED_TOP_LEVEL = {"boto3", "botocore"}
# A companion builds on one analyzer: it imports that analyzer (lazily, so it still imports alone), its UI class
# is a <Service>View or <Service>Explorer, and its AWS calls are checked against that analyzer's services.
COMPANIONS = {"s3_explorer": "s3"}
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
    """'s3:ListObjectsV2' -> 's3:ListBucket', 'bedrock-agent:GetKnowledgeBase' -> 'bedrock:GetKnowledgeBase'."""
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


def _analyzers_imported(node: ast.Import | ast.ImportFrom) -> list[str]:
    """The modules an import loads, by their name in the package: `import s3`, `from .s3 import x`,
    `from aws_analyzer import s3` and `import aws_analyzer.s3` all give ["s3"]."""
    if isinstance(node, ast.Import):
        return [alias.name.split(".")[1] if alias.name.startswith(f"{PACKAGE}.") else alias.name.split(".")[0]
                for alias in node.names]
    module = node.module or ""
    if node.level and module:  # from .s3 import x
        return [module.split(".")[0]]
    if node.level or module == PACKAGE:  # from . import s3, from aws_analyzer import s3
        return [alias.name for alias in node.names]
    if module.startswith(f"{PACKAGE}."):  # from aws_analyzer.s3 import x
        return [module.split(".")[1]]
    return [module.split(".")[0]]


def _is_stdlib(name: str) -> bool:
    return name in sys.stdlib_module_names or name in NEWER_STDLIB or name == "__future__"


def _operations(services: set[str]) -> dict[str, list[tuple[str, str]]]:
    """snake_case operation name -> [(service, OperationName)] for the services the analyzer creates clients for.
    A name can belong to several (s3 and s3control both have get_public_access_block)."""
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
    """(name, line) of every function, class and variable a module defines at its top level."""
    names = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.append((node.name, node.lineno))
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            names += [(t.id, node.lineno) for t in targets if isinstance(t, ast.Name)]
    return names


def kit_names() -> dict[str, str]:
    """Every name _kit/ defines -> the module it's in (fmt, text, ...)."""
    names: dict[str, str] = {}
    for path in sorted(KIT.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        names.update((name, path.stem) for name, _ in _top_level_names(tree))
    return names


def check_file(path: Path, report: Report, readme: str, apis: dict[str, set[str]],
               kit: dict[str, str] | None = None) -> None:
    rel = path.relative_to(ROOT).as_posix() if path.is_relative_to(ROOT) else str(path)
    source = path.read_text(encoding="utf-8")
    lines = source.splitlines()
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as exc:
        report.error(f"{rel}:{exc.lineno}", f"doesn't parse: {exc.msg}")
        return
    in_kit = path.parent.name == KIT.name
    parent = None if in_kit else COMPANIONS.get(path.stem)
    siblings = {p.stem for p in ANALYZERS.glob("*.py")} - {path.stem, parent, "__init__"}
    kit = kit_names() if kit is None else kit

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
        for name in sorted(set(_analyzers_imported(node)) & siblings):
            if in_kit:
                report.error(where, f"_kit imports the {name} analyzer; _kit loads with every analyzer, so it "
                                    "never imports one")
            else:
                report.error(where, f"imports the {name} analyzer; analyzers don't import each other (shared "
                                    "code goes in _kit/)")
        for name in _module_root(node):
            if name in siblings or name == PACKAGE:
                continue  # reported above
            if id(node) in top_level and not _is_stdlib(name) and name not in ALLOWED_TOP_LEVEL:
                report.error(where, f"imports {name} at import time; only boto3 + stdlib may load there "
                                    f"(import it inside the function via _require({name!r}, ...))")
            elif (id(node) not in top_level and not _is_stdlib(name) and name != parent
                  and name not in ALLOWED_TOP_LEVEL | {"IPython"}):
                report.warn(where, f"imports {name} directly; use _require({name!r}, purpose) so a missing "
                                   "package becomes a note that says what to pip install")

    # A copy of a shared helper: _kit has it, so the analyzer imports it from there.
    if not in_kit:
        for name, line in _top_level_names(tree):
            if name in kit:
                report.error(f"{rel}:{line}", f"defines {name}, which _kit/{kit[name]}.py has; import it from there "
                                              "(from ._kit." + kit[name] + " import " + name + ") instead of a copy")

    # Section banners 1..5 in order.
    banners = [int(m.group(1)) for m in re.finditer(r"^# (\d+)\. ", source, re.MULTILINE)]
    if banners != [1, 2, 3, 4, 5] and not in_kit:
        report.error(rel, f"section banners are {banners or 'missing'}; expected # 1. .. # 5. in order "
                          "(Helpers, Data models, Pure analysis, <Service>Analyzer, <Service>View)")

    # View methods: decorated, documented, render through blocks.
    ui_suffixes = ("View", "Explorer") if parent else ("View",)
    view = next((n for n in tree.body if isinstance(n, ast.ClassDef) and n.name.endswith(ui_suffixes)), None)
    if view is None:
        if not in_kit:
            report.error(rel, "no <Service>View class")
    else:
        for member in view.body:
            if not isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef)) or member.name.startswith("_"):
                continue
            if any(isinstance(d, ast.Name) and d.id == "property"
                   or isinstance(d, ast.Attribute) and d.attr in ("setter", "deleter") for d in member.decorator_list):
                continue  # an attribute to read or set, not a command
            where = f"{rel}:{member.lineno} {view.name}.{member.name}"
            decorators = {d.id if isinstance(d, ast.Name) else getattr(d, "attr", "") for d in member.decorator_list}
            if member.name != "help" and "_friendly_errors" not in decorators:
                report.error(where, "no @_friendly_errors (an AWS or input error would show a traceback)")
            doc = ast.get_docstring(member)
            if not doc or not doc.strip().split("\n")[0].strip():
                report.error(where, "no docstring; help() shows its first line as the description")
            if not (isinstance(member.returns, ast.Constant) and member.returns.value is None):
                report.warn(where, "not annotated -> None (View methods render and return nothing)")
            for node in ast.walk(member):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in (
                        "print", "display", "HTML"):
                    report.error(f"{rel}:{node.lineno} {view.name}.{member.name}",
                                 f"calls {node.func.id}(); add blocks and pass them to self._show()")

    # print() outside the View.
    for stmt in tree.body:
        if stmt is view:
            continue
        for node in ast.walk(stmt):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "print":
                report.error(f"{rel}:{node.lineno}", "print() outside the View; the analyzer and pure functions "
                                                     "return data and never print")

    # AWS operations: read-only, and each one's permission documented in README.md.
    services = _client_services(tree)
    if parent and (ANALYZERS / f"{parent}.py").exists():  # it calls AWS through the analyzer's clients
        services |= _client_services(ast.parse((ANALYZERS / f"{parent}.py").read_text(encoding="utf-8")))
    ops = _operations(services)
    defined = {n.name for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
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


def _client_services(tree: ast.AST) -> set[str]:
    """The services a file creates clients for: session.client("s3", ...) -> {"s3"}."""
    return {n.args[0].value for n in ast.walk(tree) if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Attribute) and n.func.attr == "client" and n.args
            and isinstance(n.args[0], ast.Constant) and isinstance(n.args[0].value, str)}


def _documented(action: str, readme: str) -> bool:
    if action in readme:
        return True
    service, _, name = action.partition(":")
    return any(name.startswith(prefix) for prefix in re.findall(rf"{re.escape(service)}:(\w+)\*", readme))


def main() -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    parser.add_argument("files", nargs="*", type=Path,
                        help="files to check (default: src/aws_analyzer/*.py and src/aws_analyzer/_kit/*.py)")
    parser.add_argument("--apis", action="store_true", help="list the AWS operations each analyzer calls")
    args = parser.parse_args()
    readme_path = ROOT / "README.md"
    readme = readme_path.read_text(encoding="utf-8") if readme_path.exists() else ""
    report, apis = Report(), {}
    files = [f.resolve() for f in args.files] or sorted(
        p for p in [*ANALYZERS.glob("*.py"), *KIT.glob("*.py")] if p.name != "__init__.py")
    kit = kit_names()
    for path in files:
        check_file(path, report, readme, apis, kit)
    for label, items in (("error", report.errors), ("warning", report.warnings)):
        for item in items:
            print(f"{label}: {item}")
    if args.apis:
        for rel, names in sorted(apis.items()):
            print(f"\nAWS operations in {rel} ({len(names)}):")
            for name in sorted(names):
                actions = [iam_action(c) or "(none needed)" for c in name.split(" | ")]
                print(f"  {name:<52} IAM: {' | '.join(actions)}")
    shared = sum(path.parent.name == KIT.name for path in files)
    print(f"\nrules: {len(files) - shared} analyzers and {shared} _kit modules, {len(report.errors)} errors, "
          f"{len(report.warnings)} warnings")
    return 1 if report.errors else 0


if __name__ == "__main__":
    sys.exit(main())
