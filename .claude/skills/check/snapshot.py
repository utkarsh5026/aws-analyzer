"""Snapshot what every View shows, to prove that a refactor changed nothing a user sees.

    python .claude/skills/check/snapshot.py run OUT                     # this checkout, into OUT
    python .claude/skills/check/snapshot.py run OUT --root ../main      # another checkout (e.g. a git worktree of main)
    python .claude/skills/check/snapshot.py compare BASE NEW            # diff two snapshots; exit 1 if they differ

`run` sends every case below, and every figure in the checkout's shots.py, to a View on the demo data (demo.py's
and shots.py's scenes, taken from the checkout being snapshotted, so a baseline can come from main). Each case
runs twice, with a new View each time: once with mode="text", keeping what it prints, and once with mode="html",
keeping what IPython's display() receives. The wall clock is stopped at NOW (time-machine), so the demo data, its
ages and dates, and signed links are the same on every run, and what still changes (durations measured on the
monotonic clock, moto's random IDs, the checkout's own path) is normalized: two runs of the same code are identical.

OUT also gets names.json, every name each module defines and the attributes of each class it defines, and
apis.txt, the AWS operations rules.py finds. `compare` reports any case whose output changed, a name or class
attribute that no longer exists (`--allow-lost s3._band` for one removed on purpose), and any change in the AWS
operations.

Needs the dev requirements, plus time-machine (pip install time-machine).
"""

from __future__ import annotations

import argparse
import ast
import contextlib
import difflib
import importlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
REGION = "us-east-1"
NOW = datetime(2026, 10, 10, 12, 0, tzinfo=timezone.utc)  # the frozen wall clock every snapshot runs at
SERVICES =["s3", "dynamodb", "bedrock_kb", "bedrock_chat", "sagemaker_env", "opensearch", "lambda_functions"]

# (name, setup, code) per service, run on demo.py's data (the /demo skill describes it). The setup's output isn't
# kept. Left out: commands that write files (download, save_runs), open a window (explore, app) or show a signed
# link that expires (link).
_CHAT_QUESTIONS = '["How long do refunds take? | refund-policy.pdf", "What does error E1234 mean?"]'
DEMO_CASES: dict[str, list[tuple[str, str, str]]] = {
    "s3": [
        ("overview", "", "ui.overview()"),
        ("buckets", "", "ui.buckets()"),
        ("bucket_info", "", 'ui.bucket_info("demo-lake")'),
        ("policy", "", 'ui.policy("demo-lake")'),
        ("summary", "", 'ui.summary("s3://demo-lake/")'),
        ("ls", "", 'ui.ls("s3://demo-lake/")'),
        ("ls_details", "", 'ui.ls("s3://demo-lake/curated/", details=True)'),
        ("tree", "", 'ui.tree("s3://demo-lake/", depth=2)'),
        ("find", "", 'ui.find("s3://demo-lake/", pattern="*.parquet")'),
        ("largest", "", 'ui.largest("s3://demo-lake/")'),
        ("newest", "", 'ui.newest("s3://demo-lake/", n=10)'),
        ("oldest", "", 'ui.oldest("s3://demo-lake/", n=10)'),
        ("what_if", "", 'ui.what_if("s3://demo-lake/logs/", move_after={30: "STANDARD_IA", 180: "GLACIER"})'),
        ("duplicates", "", 'ui.duplicates("s3://demo-lake/raw/")'),
        ("compare", "", 'ui.compare("s3://demo-lake/curated/orders/", "s3://demo-lake/exports/")'),
        ("versions", "", 'ui.versions("s3://demo-lake/reports/")'),
        ("history", "", 'ui.history("s3://demo-lake/reports/daily.csv")'),
        ("deleted", "", 'ui.deleted("s3://demo-lake/reports/")'),
        ("uploads", "", 'ui.uploads("s3://demo-lake/")'),
        ("head", "", 'ui.head("s3://demo-lake/curated/customers.csv")'),
        ("preview_csv", "", 'ui.preview("s3://demo-lake/curated/customers.csv")'),
        ("preview_parquet", "", 'ui.preview("s3://demo-lake/curated/orders/part-00000.parquet")'),
        ("preview_tar", "", 'ui.preview("s3://demo-models/xgboost/2024-06-01/output/model.tar.gz")'),
        ("file_details", "", 'ui.file_details("s3://demo-lake/curated/")'),
        ("download_zip_dry", "", 'ui.download_zip("s3://demo-lake/curated/", dry_run=True)'),
        ("missing", "", 'ui.head("s3://demo-lake/no/such/key.csv")'),
        ("help_one", "", 'ui.help("summary")'),
    ],
    "dynamodb": [
        ("tables", "", "ui.tables()"),
        ("table_info", "", 'ui.table_info("orders")'),
        ("table_info_sessions", "", 'ui.table_info("sessions")'),
        ("schema", "", 'ui.schema("orders")'),
        ("scan", "", 'ui.scan("orders", 5)'),
        ("scan_where_more", "", 'ui.scan("orders", 5, where={"status": "failed"}); ui.more()'),
        ("query", "", 'ui.query("orders", "USER#0")'),
        ("get", "", 'ui.get("orders", "USER#0", "ORDER#0000")'),
        ("sql", "", 'ui.sql(\'SELECT * FROM "orders" WHERE pk = ?\', "USER#0")'),
        ("largest", "", 'ui.largest("orders", 5)'),
        ("count", "", 'ui.count("orders")'),
        ("value_counts", "", 'ui.value_counts("orders", "status")'),
        ("counters", "", 'ui.get("counters", "42")'),
        ("missing", "", 'ui.table_info("no-such-table")'),
        ("help_one", "", 'ui.help("query")'),
    ],
    "bedrock_kb": [
        ("kbs", "", "ui.kbs()"),
        ("kb_info", 'ui.use("support-docs")', "ui.kb_info()"),
        ("syncs", "", 'ui.syncs("support-docs")'),
        ("documents", "", 'ui.documents("support-docs")'),
        ("unsynced", "", 'ui.unsynced("support-docs")'),
        ("files", 'ui.use("support-docs")', "ui.files()"),
        ("file", 'ui.use("support-docs")', 'ui.file("warranty.pdf")'),
        ("search", 'ui.use("support-docs")', 'ui.search("How long do refunds take?")'),
        ("chunk", 'ui.use("support-docs"); ui.search("How long do refunds take?")', "ui.chunk(1)"),
        ("search_file", 'ui.use("support-docs")', 'ui.search_file("warranty.pdf", "Can I return a faulty laptop?")'),
        ("ask", 'ui.use("support-docs")', 'ui.ask("How long do refunds take?")'),
        ("follow_up", 'ui.use("support-docs"); ui.ask("How long do refunds take?")',
         'ui.follow_up("What about EU customers?")'),
        ("models", "", "ui.models()"),
        ("other_kbs", "", 'ui.kb_info("sales-playbooks"); ui.kb_info("hr-policies"); ui.kb_info("legacy-faq")'),
        ("missing", "", 'ui.kb_info("no-such-kb")'),
        ("help_one", "", 'ui.help("search")'),
    ],
    "bedrock_chat": [
        ("ask", 'ui.use("support-docs")', 'ui.ask("How long do refunds take?")'),
        ("retrieve", 'ui.use("support-docs")', 'ui.retrieve("How long do refunds take?")'),
        ("last_transcript", 'ui.use("support-docs"); ui.ask("How long do refunds take?")',
         "ui.last(); ui.transcript()"),
        ("settings", 'ui.use("support-docs")', 'ui.settings(); ui.set("n", 8); ui.settings(); ui.unset("n")'),
        ("fields", "", 'ui.fields("rerank")'),
        ("request", 'ui.use("support-docs")', "ui.request()"),
        ("code", 'ui.use("support-docs")', "ui.code()"),
        ("ask_all", 'ui.use("support-docs")', f"ui.ask_all({_CHAT_QUESTIONS}); ui.results(); ui.runs()"),
        ("compare_runs", f'ui.use("support-docs"); ui.ask_all({_CHAT_QUESTIONS}); ui.set("n", 3); '
                         f"ui.ask_all({_CHAT_QUESTIONS})", "ui.compare_runs(1, 2)"),
        ("sweep", 'ui.use("support-docs")', f'ui.sweep({_CHAT_QUESTIONS}, setups=[{{"n": 3}}, {{"n": 5}}])'),
        ("new_chat", 'ui.use("support-docs"); ui.ask("How long do refunds take?")', "ui.new_chat(); ui.transcript()"),
        ("help_one", "", 'ui.help("ask")'),
    ],
    "sagemaker_env": [
        ("instance", "", "ui.instance()"),
        ("disk", "", "ui.disk()"),
        ("running", "", "ui.running()"),
        ("help_one", "", 'ui.help("disk")'),
    ],
    "opensearch": [
        ("overview", "", "ui.overview()"),
        ("indexes", "", 'ui.indexes("vectors-prod")'),
        ("index_info", "", 'ui.index_info("vectors-prod/support-docs")'),
        ("sample", "", 'ui.sample("vectors-prod/support-docs", n=5)'),
        ("search", "", 'ui.search("how do I get my money back?", index="vectors-prod/support-docs")'),
        ("help_one", "", 'ui.help("search")'),
    ],
    "lambda_functions": [
        ("functions", "", "ui.functions()"),
        ("functions_all", "", 'ui.functions(regions="all")'),
        ("function_info", "", 'ui.function_info("orders-etl")'),
        ("errors", "", 'ui.errors("orders-etl")'),
        ("performance", "", 'ui.performance("orders-etl")'),
        ("logs", "", 'ui.logs("orders-etl", n=20)'),
        ("logs_search", "", 'ui.logs("orders-etl", search="KeyError", n=10)'),
        ("code", "", 'ui.code("orders-etl")'),
        ("missing", "", 'ui.function_info("no-such-function")'),
        ("help_one", "", 'ui.help("errors")'),
    ],
}

# What _hold_still can't fix, replaced before a snapshot is written. Found by snapshotting main twice.
NORMALIZE: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b"), "<uuid>"),  # uuid4() in demo data
    (re.compile(r"\b\d[\d,.]* ?(B|KB|MB|GB|TB) free\b"), "<size> free"),  # this machine's free memory and disk
]


# ----------------------------------------------------------------------------- running a checkout


def _layout(root: Path) -> tuple[Path, str]:
    """(the folder the analyzers are in, the prefix their modules import under) for this checkout: the package in
    src/aws_analyzer/, or the analyzers/ folder of the files before 0.14 (a stale analyzers/__pycache__ doesn't count)."""
    if (root / "src" / "aws_analyzer" / "s3.py").exists():
        return root / "src" / "aws_analyzer", "aws_analyzer."
    return root / "analyzers", ""


def _module(root: Path, name: str):
    folder, prefix = _layout(root)
    path = str(folder if not prefix else folder.parent)
    if path not in sys.path:
        sys.path.insert(0, path)
    return importlib.import_module(prefix + name)


def _normalize(text: str, root: Path, work: Path) -> str:
    text = text.replace(sys.executable, "<python>")  # sagemaker_env shows it, and it may be inside the checkout
    text = text.replace(str(root), "<ROOT>").replace(str(work), "<WORK>")
    for pattern, replacement in NORMALIZE:
        text = pattern.sub(replacement, text)
    return text


def _capture(view_cls, core, mode: str, setup: str, code: str) -> str:
    """What one case shows: the text it prints in text mode, or the HTML it hands display() in html mode."""
    import IPython.display

    shown: list[str] = []
    real = IPython.display.display

    def display(*objects, **_):
        shown.extend(getattr(obj, "data", repr(obj)) for obj in objects)

    ui = view_cls(core, mode=mode, progress="off")
    scope = {"ui": ui, "core": core}
    printed = io.StringIO()
    IPython.display.display = display
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            exec(setup, scope)
        shown.clear()
        with contextlib.redirect_stdout(printed):
            exec(code, scope)
    except Exception:  # the Views turn errors into notes, so this is itself a finding
        shown.append("EXCEPTION\n" + traceback.format_exc(limit=-3))
    finally:
        IPython.display.display = real
    return printed.getvalue() + "".join(f"{html}\n" for html in shown)


def _hold_still() -> None:
    """Take out what changes from run to run, before the demo data, moto or an analyzer is imported: stop the wall
    clock at NOW (every age, date and signed link), stop the monotonic clock (every duration reads 0), and fix the
    process ID (sagemaker_env's "this kernel") and the random bytes and numbers that IDs are made from (moto's
    upload IDs, the PDF pages' zoom buttons, uuid4()). The worker also runs with PYTHONHASHSEED=0."""
    try:
        import time_machine
    except ImportError:
        sys.exit("snapshot.py needs time-machine to stop the clock: pip install time-machine")
    import random
    import time

    time_machine.travel(NOW, tick=False).start()
    time.monotonic = time.perf_counter = lambda: 0.0  # threading and queue keep the real ones they imported
    os.getpid = lambda: 4242
    random.seed(7)
    os.urandom = random.Random(7).randbytes


def worker(root: Path, source: str, service: str, out: Path, work: Path) -> int:
    """Run one service's cases in this process, against one scene; write <out>/<source>/<service>/<case>.<mode>."""
    _hold_still()
    for name, value in {"AWS_ACCESS_KEY_ID": "testing", "AWS_SECRET_ACCESS_KEY": "testing",
                        "AWS_SESSION_TOKEN": "testing", "AWS_DEFAULT_REGION": REGION}.items():
        os.environ[name] = value
    os.environ.pop("AWS_PROFILE", None)
    sys.path.insert(0, str(root / ".claude" / "skills" / "demo"))
    from moto import mock_aws
    from moto.moto_api._internal import mock_random

    mock_random.seed(7)

    if source == "shots":
        scenes = importlib.import_module("shots")
        seed = scenes.SCENES[service]
        cases = [(f.name, f.setup, f.code) for f in scenes.FIGURES if f.service == service]
    else:
        scenes = importlib.import_module("demo")
        seed = scenes.SEEDERS[service]
        cases = list(DEMO_CASES[service])
    cases.append(("help", "", "ui.help()"))
    folder = out / source / service
    folder.mkdir(parents=True, exist_ok=True)
    with mock_aws():
        kwargs = seed() or {}
        mod = _module(root, service)
        view_cls = next(v for k, v in vars(mod).items() if k.endswith("View") and isinstance(v, type))
        core_cls = next(v for k, v in vars(mod).items() if k.endswith("Analyzer") and isinstance(v, type))
        core = core_cls(**kwargs) if "session" in kwargs else core_cls(region=REGION, **kwargs)
        for name, setup, code in cases:
            for mode, suffix in (("text", "txt"), ("html", "html")):
                shown = _capture(view_cls, core, mode, setup, code)
                (folder / f"{name}.{suffix}").write_text(_normalize(shown, root, work), encoding="utf-8")
    return 0


def _defined(path: Path) -> list[str]:
    """The names a file defines at its top level (in if / try blocks too): what a user can import from it."""
    names: list[str] = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.col_offset == 0:
            names.append(node.name)
        elif isinstance(node, (ast.Assign, ast.AnnAssign)) and node.col_offset == 0:
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            names += [t.id for t in targets if isinstance(t, ast.Name)]
    return sorted(set(names))


def names_worker(root: Path, out: Path) -> int:
    """names.json, per module: the names its file defines (what a user may import from it), every name the imported
    module has, and the attributes of each class of ours it has."""
    folder, prefix = _layout(root)
    modules = [path for path in sorted(folder.glob("*.py")) if not path.stem.startswith("_")]
    ours = {prefix + path.stem for path in modules}

    def is_ours(obj) -> bool:
        module = getattr(obj, "__module__", "") or ""
        return module in ours or (bool(prefix) and module.startswith(prefix))

    report: dict[str, dict] = {}
    for path in modules:
        mod = _module(root, path.stem)
        present = sorted(n for n in dir(mod) if not (n.startswith("__") and n.endswith("__")))
        classes = {n: sorted(a for a in dir(getattr(mod, n)) if not (a.startswith("__") and a.endswith("__")))
                   for n in present if isinstance(getattr(mod, n), type) and is_ours(getattr(mod, n))}
        report[path.stem] = {"defined": [n for n in _defined(path) if n in present], "present": present,
                             "classes": classes}
    (out / "names.json").write_text(json.dumps(report, indent=1, sort_keys=True), encoding="utf-8")
    return 0


def run(root: Path, out: Path, sources: list[str], services: list[str]) -> int:
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    work = out.parent / ".snapshot-work"  # the same path for every snapshot, so file paths in reports match
    failed = 0
    for source in sources:
        for service in services:
            if source == "shots" and service == "bedrock_chat":
                continue  # its figures come from chat_shots.py, in a browser
            if work.exists():
                shutil.rmtree(work)
            work.mkdir()
            command = [sys.executable, str(Path(__file__).resolve()), "_worker", str(root), source, service,
                       str(out.resolve()), str(work.resolve())]
            env = {**os.environ, "PYTHONHASHSEED": "0"}  # the same order for sets of strings in every run
            try:  # with the clock stopped, code that waits for the time to pass would wait forever
                proc = subprocess.run(command, cwd=work, env=env, capture_output=True, text=True, timeout=900)
            except subprocess.TimeoutExpired as exc:
                proc = subprocess.CompletedProcess(command, 1, "", f"timed out after {exc.timeout}s")
            status = "ok" if proc.returncode == 0 else "FAILED"
            print(f"{status:6} {source}/{service}", flush=True)
            if proc.returncode:
                failed += 1
                print(proc.stderr[-3000:], file=sys.stderr)
    proc = subprocess.run([sys.executable, str(Path(__file__).resolve()), "_names", str(root), str(out.resolve())],
                          capture_output=True, text=True)
    print(f"{'ok' if proc.returncode == 0 else 'FAILED':6} names", flush=True)
    failed += proc.returncode != 0
    if proc.returncode:
        print(proc.stderr[-3000:], file=sys.stderr)
    rules = root / ".claude" / "skills" / "check" / "rules.py"
    proc = subprocess.run([sys.executable, str(rules), "--apis"], cwd=root, capture_output=True, text=True)
    output = re.sub(r"(analyzers|src/aws_analyzer)/", "<pkg>/", proc.stdout + proc.stderr)
    # Only the operations each file calls: the rest of the output (warnings with line numbers, how many files were
    # checked) changes with refactors that change nothing.
    blocks = re.findall(r"^AWS operations in .*\n(?:  .*\n)*", output, re.MULTILINE)
    (out / "apis.txt").write_text("\n".join(blocks) or output, encoding="utf-8")
    print(f"{'ok':6} apis", flush=True)
    if work.exists():
        shutil.rmtree(work)
    return 1 if failed else 0


# ----------------------------------------------------------------------------- comparing two snapshots


def compare(base: Path, new: Path, context: int, max_lines: int, allow_lost: set[str]) -> int:
    def files(root: Path) -> set[str]:
        return {str(p.relative_to(root)) for p in root.rglob("*") if p.is_file() and p.name != "names.json"}

    old_files, new_files = files(base), files(new)
    problems = 0
    for name in sorted(old_files - new_files):
        print(f"MISSING  {name}")
        problems += 1
    for name in sorted(new_files - old_files):
        print(f"NEW      {name}")
    same = 0
    for name in sorted(old_files & new_files):
        before = (base / name).read_text(encoding="utf-8").splitlines()
        after = (new / name).read_text(encoding="utf-8").splitlines()
        if before == after:
            same += 1
            continue
        problems += 1
        print(f"CHANGED  {name}")
        diff = list(difflib.unified_diff(before, after, f"base/{name}", f"new/{name}", lineterm="", n=context))
        for line in diff[:max_lines]:
            print("    " + line[:400])
        if len(diff) > max_lines:
            print(f"    ... {len(diff) - max_lines} more diff lines")

    old_names = json.loads((base / "names.json").read_text(encoding="utf-8"))
    new_names = json.loads((new / "names.json").read_text(encoding="utf-8"))
    lost = 0
    for module, before in old_names.items():
        after = new_names.get(module)
        if after is None:
            print(f"LOST     module {module}")
            lost += 1
            continue
        present = set(after["present"])
        for name in before["defined"]:
            if name in present:
                continue
            if f"{module}.{name}" in allow_lost:
                print(f"removed  {module}.{name} (--allow-lost)")
                continue
            print(f"LOST     {module}.{name}")
            lost += 1
        for cls, attrs in before["classes"].items():
            if cls not in present:
                continue  # reported above, if the file defined it
            if cls not in after["classes"]:
                print(f"LOST     {module}.{cls} is no longer a class")
                lost += 1
                continue
            missing = sorted(set(attrs) - set(after["classes"][cls]))
            if missing:
                print(f"LOST     {module}.{cls}: {', '.join(missing)}")
                lost += 1
    problems += lost
    print(f"\n{same} of {len(old_files & new_files)} outputs identical; {problems} difference{'s' * (problems != 1)}"
          f"{f', including {lost} lost names' if lost else ''}.")
    return 1 if problems else 0


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] == "_worker":
        root, source, service, out, work = sys.argv[2:7]
        return worker(Path(root), source, service, Path(out), Path(work))
    if len(sys.argv) > 1 and sys.argv[1] == "_names":
        return names_worker(Path(sys.argv[2]), Path(sys.argv[3]))
    intro, _, rest = (__doc__ or "").partition("\n\n")
    parser = argparse.ArgumentParser(description=intro, epilog=rest, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    run_cmd = commands.add_parser("run", help="snapshot a checkout")
    run_cmd.add_argument("out", type=Path)
    run_cmd.add_argument("--root", type=Path, default=ROOT, help="the checkout to snapshot (default: this one)")
    run_cmd.add_argument("--source", choices=["demo", "shots"], action="append", help="only these scenes")
    run_cmd.add_argument("--service", choices=SERVICES, action="append", help="only these services")
    compare_cmd = commands.add_parser("compare", help="diff two snapshots")
    compare_cmd.add_argument("base", type=Path)
    compare_cmd.add_argument("new", type=Path)
    compare_cmd.add_argument("--context", type=int, default=2, help="lines of context in each diff")
    compare_cmd.add_argument("--max-lines", type=int, default=60, help="diff lines shown per output")
    compare_cmd.add_argument("--allow-lost", nargs="*", default=[], metavar="MODULE.NAME",
                             help="names removed on purpose (a dead private copy), reported but not counted")
    args = parser.parse_args()
    if args.command == "run":
        return run(args.root.resolve(), args.out.resolve(), args.source or ["demo", "shots"], args.service or SERVICES)
    return compare(args.base, args.new, args.context, args.max_lines, set(args.allow_lost))


if __name__ == "__main__":
    sys.exit(main())
