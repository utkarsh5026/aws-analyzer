"""Check the analyzers against the hard constraints in CLAUDE.md that ruff and pytest don't catch.

    python .claude/skills/check/rules.py            # errors and warnings; exit 1 on any error
    python .claude/skills/check/rules.py --apis     # also list every AWS operation each analyzer calls
    python .claude/skills/check/rules.py path.py    # just this file

Errors (exit 1):
  - a top-level import that isn't the standard library, boto3 or botocore (optional packages load lazily)
  - an import of another analyzer (each file must work alone)
  - no `from __future__ import annotations`
  - the five numbered `# N. ...` section banners missing or out of order
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
ANALYZERS = ROOT / "analyzers"
ALLOWED_TOP_LEVEL = {"boto3", "botocore"}
NEWER_STDLIB = {"compression", "annotationlib", "tomllib", "zoneinfo", "graphlib"}  # stdlib on newer Pythons
READ_PREFIXES = ("Get", "List", "Describe", "Head", "Scan", "Query", "Select", "BatchGet", "Lookup", "Search",
                 "Retrieve")
PARTIQL = {"ExecuteStatement", "BatchExecuteStatement", "ExecuteTransaction"}
PRAGMA = "# read-only:"

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
    "sts:GetCallerIdentity": None,  # needs no permission
}
# boto3 service name -> IAM service prefix, where they differ (every Bedrock client is authorized as bedrock:).
IAM_PREFIX = {"bedrock-agent": "bedrock", "bedrock-agent-runtime": "bedrock", "bedrock-runtime": "bedrock"}


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


def check_file(path: Path, report: Report, readme: str, apis: dict[str, set[str]]) -> None:
    rel = path.relative_to(ROOT).as_posix() if path.is_relative_to(ROOT) else str(path)
    source = path.read_text(encoding="utf-8")
    lines = source.splitlines()
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as exc:
        report.error(f"{rel}:{exc.lineno}", f"doesn't parse: {exc.msg}")
        return
    siblings = {p.stem for p in ANALYZERS.glob("*.py")} - {path.stem}

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
        for name in _module_root(node):
            where = f"{rel}:{node.lineno}"
            if name in siblings:
                report.error(where, f"imports the {name} analyzer; each analyzer must work alone (copy the helper)")
            elif id(node) in top_level and not _is_stdlib(name) and name not in ALLOWED_TOP_LEVEL:
                report.error(where, f"imports {name} at import time; only boto3 + stdlib may load there "
                                    f"(import it inside the function via _require({name!r}, ...))")
            elif id(node) not in top_level and not _is_stdlib(name) and name not in ALLOWED_TOP_LEVEL | {"IPython"}:
                report.warn(where, f"imports {name} directly; use _require({name!r}, purpose) so a missing "
                                   "package becomes a note that says what to pip install")

    # Section banners 1..5 in order.
    banners = [int(m.group(1)) for m in re.finditer(r"^# (\d+)\. ", source, re.MULTILINE)]
    if banners != [1, 2, 3, 4, 5]:
        report.error(rel, f"section banners are {banners or 'missing'}; expected # 1. .. # 5. in order "
                          "(Helpers, Data models, Pure analysis, <Service>Analyzer, <Service>View)")

    # View methods: decorated, documented, render through blocks.
    view = next((n for n in tree.body if isinstance(n, ast.ClassDef) and n.name.endswith("View")), None)
    if view is None:
        report.error(rel, "no <Service>View class")
    else:
        for member in view.body:
            if not isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef)) or member.name.startswith("_"):
                continue
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
    services = {n.args[0].value for n in ast.walk(tree) if isinstance(n, ast.Call)
                and isinstance(n.func, ast.Attribute) and n.func.attr == "client" and n.args
                and isinstance(n.args[0], ast.Constant) and isinstance(n.args[0].value, str)}
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
            if "client" in target.lower() or node.func.attr not in defined:
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


def _documented(action: str, readme: str) -> bool:
    if action in readme:
        return True
    service, _, name = action.partition(":")
    return any(name.startswith(prefix) for prefix in re.findall(rf"{re.escape(service)}:(\w+)\*", readme))


def main() -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    parser.add_argument("files", nargs="*", type=Path, help="analyzer files (default: analyzers/*.py)")
    parser.add_argument("--apis", action="store_true", help="list the AWS operations each analyzer calls")
    args = parser.parse_args()
    readme_path = ROOT / "README.md"
    readme = readme_path.read_text(encoding="utf-8") if readme_path.exists() else ""
    report, apis = Report(), {}
    files = [f.resolve() for f in args.files] or sorted(ANALYZERS.glob("*.py"))
    for path in files:
        check_file(path, report, readme, apis)
    for label, items in (("error", report.errors), ("warning", report.warnings)):
        for item in items:
            print(f"{label}: {item}")
    if args.apis:
        for rel, names in sorted(apis.items()):
            print(f"\nAWS operations in {rel} ({len(names)}):")
            for name in sorted(names):
                actions = [iam_action(c) or "(none needed)" for c in name.split(" | ")]
                print(f"  {name:<52} IAM: {' | '.join(actions)}")
    print(f"\nrules: {len(files)} analyzers, {len(report.errors)} errors, {len(report.warnings)} warnings")
    return 1 if report.errors else 0


if __name__ == "__main__":
    sys.exit(main())
