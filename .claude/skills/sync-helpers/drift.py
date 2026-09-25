"""Find duplicated helpers that have drifted apart between the analyzers.

Every analyzers/<service>.py carries its own copy of the shared helpers (human_size, _require, the render
blocks, _render_html, _friendly_errors, View._progress, View.help, ...), because each file must work alone
in a notebook. This compares every top-level definition, and every private View / Analyzer method, that
two or more analyzers define under the same name.

Service names are normalized first (S3View / DynamoDBView -> <View>, S3_PRICES -> <PRICES>, the CSS root
class -> <css>), so only real differences are reported. A difference in docstrings or comments only is
reported separately from a difference in code.

    python .claude/skills/sync-helpers/drift.py              # summary + diffs of everything that differs
    python .claude/skills/sync-helpers/drift.py _esc help    # just these names (View.help matches "help")
    python .claude/skills/sync-helpers/drift.py --summary    # one line per differing name, no diffs
"""

from __future__ import annotations

import argparse
import ast
import difflib
import re
import sys
import textwrap
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
ANALYZERS = ROOT / "analyzers"

# The helpers CLAUDE.md lists as deliberately duplicated. Every analyzer should have all of them.
EXPECTED = ["human_size", "human_money", "_require", "_in_notebook", "_esc", "_Title", "_Cards", "_Table", "_Note",
            "_Text", "_render_html", "_render_text", "_friendly_errors", "View._progress", "View.help", "View._show"]


def _definitions(path: Path) -> tuple[dict[str, tuple[int, str]], dict[str, str]]:
    """name -> (line, source) for top-level defs and private View / Analyzer methods, plus the file's
    service-specific tokens (View / Analyzer class names, price table, CSS root class)."""
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    defs: dict[str, tuple[int, str]] = {}
    tokens: dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            defs[node.name] = (node.lineno, ast.get_source_segment(source, node) or "")
            if isinstance(node, ast.ClassDef) and node.name.endswith(("View", "Analyzer")):
                role = "View" if node.name.endswith("View") else "Analyzer"
                tokens[role] = node.name
                for member in node.body:
                    if isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef)) and (
                            member.name.startswith("_") or member.name == "help"):
                        segment = ast.get_source_segment(source, member) or ""
                        defs[f"{role}.{member.name}"] = (member.lineno, textwrap.dedent("    " + segment))
        elif isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            name = node.targets[0].id
            defs[name] = (node.lineno, ast.get_source_segment(source, node) or "")
            if name.endswith("_PRICES"):
                tokens["PRICES"] = name
    css = re.search(r"""<div class="([\w-]+)">""", defs.get("_render_html", (0, ""))[1])
    if css:
        tokens["css"] = css.group(1)
    return defs, tokens


def _normalize(text: str, tokens: dict[str, str]) -> str:
    for role in ("View", "Analyzer", "PRICES"):
        if role in tokens:
            text = re.sub(rf"\b{re.escape(tokens[role])}\b", f"<{role}>", text)
    if "css" in tokens:
        css = re.escape(tokens["css"])
        text = re.sub(rf"\.{css}\b", ".<css>", text)
        text = re.sub(rf"""class="{css}\"""", 'class="<css>"', text)
    return text


def _code_only(text: str) -> str:
    """AST dump without docstrings, so docstring and comment edits don't count as code changes."""
    placeholder = re.sub(r"<(View|Analyzer|PRICES|css)>", r"__\1__", text)
    try:
        tree = ast.parse(textwrap.dedent(placeholder))
    except SyntaxError:
        return text
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if (isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Module)) and body
                and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)):
            node.body = body[1:] or [ast.Pass()]
    return ast.dump(tree)


def main() -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    parser.add_argument("names", nargs="*", help="only these names (e.g. _esc, help, View._progress)")
    parser.add_argument("--summary", action="store_true", help="list differing names without diffs")
    args = parser.parse_args()

    files = sorted(ANALYZERS.glob("*.py"))
    parsed: dict[str, tuple[dict[str, tuple[int, str]], dict[str, str]]] = {}
    for path in files:
        try:
            parsed[path.name] = _definitions(path)
        except SyntaxError as exc:
            print(f"skipped {path.name}: it doesn't parse (line {exc.lineno}: {exc.msg})")
    if len(parsed) < 2:
        print("Fewer than two analyzers parse, so there is nothing to compare.")
        return 0

    wanted = set(args.names)

    def selected(name: str) -> bool:
        return not wanted or name in wanted or name.split(".")[-1] in wanted

    missing = {file: [n for n in EXPECTED if n not in defs] for file, (defs, _) in parsed.items()}
    missing = {file: names for file, names in missing.items() if names and any(selected(n) for n in names)}

    counts = Counter(name for defs, _ in parsed.values() for name in defs)
    shared = sorted((n for n, c in counts.items() if c >= 2 and selected(n)), key=lambda n: (n.count("."), n))
    same, docs_only, code = [], [], []
    for name in shared:
        variants = {file: _normalize(defs[name][1], tokens) for file, (defs, tokens) in parsed.items() if name in defs}
        if len(set(variants.values())) == 1:
            same.append(name)
        elif len({_code_only(v) for v in variants.values()}) == 1:
            docs_only.append((name, variants))
        else:
            code.append((name, variants))

    print(f"Compared {', '.join(parsed)}: {len(shared)} shared names, {len(same)} identical, "
          f"{len(docs_only)} differ in docstrings/comments only, {len(code)} differ in code.")
    for file, names in missing.items():
        print(f"\n{file} is missing shared helpers: {', '.join(names)}")

    for title, group in (("Differ in code", code), ("Differ in docstrings or comments only", docs_only)):
        if not group:
            continue
        print(f"\n== {title} ==")
        for name, variants in group:
            where = ", ".join(f"{file}:{parsed[file][0][name][0]}" for file in variants)
            print(f"  {name:<28} {where}")
        if args.summary:
            continue
        for name, variants in group:
            # Diff every other variant against the most common one (ties: first file alphabetically).
            reference = Counter(variants.values()).most_common(1)[0][0]
            ref_file = next(file for file, text in variants.items() if text == reference)
            for file, text in variants.items():
                if text == reference:
                    continue
                print(f"\n--- {name}: {ref_file} vs {file}")
                diff = difflib.unified_diff(reference.splitlines(), text.splitlines(), ref_file, file, lineterm="", n=1)
                print("\n".join(list(diff)[2:]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
