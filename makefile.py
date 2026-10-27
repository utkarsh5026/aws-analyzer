"""
This project's commands in one place: python makefile.py <task> [options]. `make <task>` runs the same tasks on
machines that have make, and this file runs them on machines that don't (Windows).

    python makefile.py setup                 # install the pinned dev requirements (requirements-dev.txt)
    python makefile.py test -k policy        # pytest, with any pytest arguments
    python makefile.py lint                  # ruff check .
    python makefile.py check                 # what CI runs, plus the project's own rules (.claude/skills/check)
    python makefile.py build                 # the PyPI sdist and wheel in dist/, checked by twine
    python makefile.py docs                  # the guide site, built the way the Docs workflow builds it
    python makefile.py docs-serve            # preview the guide at http://127.0.0.1:8000
    python makefile.py demo s3               # a service's reports on demo data in moto (no AWS account needed)
    python makefile.py local                 # try the package as pip would install it, without publishing it
    python makefile.py local --live --profile dev   # ... and against your own AWS account (read-only)
    python makefile.py local-agent           # the same with the AI agent and everything it needs (Python 3.11+)
    python makefile.py notebook              # JupyterLab on that locally installed package
    python makefile.py envs                  # the Python environments on this machine (conda, venv) and their kernels
    python makefile.py install-env           # install this checkout into ones you pick (asks which, and what with)
    python makefile.py install-env --env pytorch_p310 --extras all --kernel   # ... the same without questions
    python makefile.py clean                 # remove what these tasks built in this checkout
    python makefile.py clean --all           # ... and the agent sandbox's files in ~/.cache/aws-analyzer
    python makefile.py clean --all --dry-run # say what it would remove, and how much, without removing it

`local` is the check before a release: it builds the wheel from this checkout, installs it in a fresh virtual
environment in .local-check/ (only boto3 comes with it, as from PyPI), imports every module there, then installs the
optional packages and runs every service's reports on demo data against that installed copy, not src/. Each
service's reports are saved in .local-check/reports/ as text and as the notebook's HTML, to open in a browser.

`local-agent` installs the AI agent (AgentView) the same way, in one step: it picks a Python 3.11 or newer (the
agent's packages need one) when this one is older, installs the wheel with every extra (the agent's LangChain
packages, Deno for its Python sandbox, and the data, file and notebook packages), downloads the sandbox's Pyodide
files, and asks the agent a question on demo data with a scripted model. `make notebook` then opens JupyterLab on it.

`install-env` is for a machine that already has its Python environments, like a SageMaker notebook's conda ones: it
finds them the way SageMakerView().envs() does (conda installations and their envs/, ~/.conda/envs, virtual
environments, and the Jupyter kernel that starts each), lets you pick some, builds the wheel from this checkout and
installs it into each with pip, with the extras you choose, and can add a Jupyter kernel for one that has none.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent
LOCAL = ROOT / ".local-check"
LOCAL_VENV = LOCAL / "venv"
LOCAL_DIST = LOCAL / "dist"
LOCAL_REPORTS = LOCAL / "reports"
LOCAL_NOTEBOOKS = LOCAL / "notebooks"
DEMO = ROOT / ".claude" / "skills" / "demo" / "demo.py"
CHECK = ROOT / ".claude" / "skills" / "check" / "run.py"
DEV_REQUIREMENTS = ROOT / "requirements-dev.txt"
DOCS_REQUIREMENTS = ROOT / "requirements-docs.txt"

DEFAULT_EXTRAS = "data,files,notebook"
AGENT_EXTRAS = {"agent", "all"}
AGENT_INSTALL_EXTRAS = "all"
AGENT_MIN_PYTHON = (3, 11)
AGENT_PYTHONS = ["python3.14", "python3.13", "python3.12", "python3.11"]
FAILED_LINES_SHOWN = 40
SRC = ROOT / "src"
CONDA_PYTHONS = ["/opt/conda/bin/python", "~/anaconda3/bin/python", "~/anaconda3/envs/JupyterSystemEnv/bin/python",
                 "~/miniconda3/bin/python"]
NOTEBOOK_INSTANCE_CONDA_ENVS = Path("/home/ec2-user/anaconda3/envs")
KERNEL_NAME_UNSAFE = re.compile(r"[^a-z0-9._-]+")
BUILT_FOLDERS = [LOCAL, ROOT / "dist", ROOT / "build", ROOT / "site", ROOT / ".pytest_cache", ROOT / ".ruff_cache",
                 ROOT / "htmlcov"]
BUILT_PATTERNS = ["src/*.egg-info", "**/__pycache__"]
SANDBOX_CACHE_NAME = "aws-analyzer"
TEST_PYODIDE_ENV = "AWS_ANALYZER_TEST_PYODIDE"
SIZE_UNITS = ["B", "KB", "MB", "GB", "TB"]
BYTES_PER_UNIT = 1024
PIP_QUIET = ["--quiet", "--disable-pip-version-check"]
COMMAND_ERROR = re.compile(r"^\[!\] (?:.+  \[\w+\]|Couldn't .+)$", re.MULTILINE)

IMPORT_EVERYTHING = """
import importlib
import sys
from pathlib import Path

import aws_analyzer

location = Path(aws_analyzer.__file__).resolve().parent
if not location.is_relative_to(Path(sys.prefix).resolve()):
    sys.exit(f"aws_analyzer was imported from {location}, not from the installed wheel")
for module in aws_analyzer._MODULES:
    importlib.import_module(f"aws_analyzer.{module}")
for name in aws_analyzer.__all__:
    getattr(aws_analyzer, name)
print(f"aws-analyzer {aws_analyzer.__version__} from {location}: {len(aws_analyzer._MODULES)} modules and "
      f"{len(aws_analyzer.__all__)} names import with only boto3")
"""

PREPARE_SANDBOX = """
from aws_analyzer.agent import LocalSandbox

print("      " + LocalSandbox().prepare(lambda name: print(f"      downloading {name}", flush=True)))
"""

FIND_ENVIRONMENTS = """
import dataclasses
import json
import sys

sys.path.insert(0, sys.argv[1])
from aws_analyzer.sagemaker_env import SageMakerAnalyzer

print(json.dumps(dataclasses.asdict(SageMakerAnalyzer().environments()), default=str))
"""

INSTALLED_VERSION = """
import aws_analyzer
print(aws_analyzer.__version__)
"""

DEMO_ON_INSTALLED = """
import runpy
import sys
from pathlib import Path

import aws_analyzer

location = Path(aws_analyzer.__file__).resolve()
if not location.is_relative_to(Path(sys.prefix).resolve()):
    sys.exit(f"aws_analyzer was imported from {location}, not from the installed wheel")
sys.argv = sys.argv[1:]
runpy.run_path(sys.argv[0], run_name="__main__")
"""

STARTER_NOTEBOOK = """{
 "cells": [
  {"cell_type": "markdown", "metadata": {}, "source": [
   "# aws-analyzer, installed from this checkout\\n",
   "\\n",
   "This kernel runs the wheel `makefile.py local` built, not a release from PyPI. ",
   "It uses your AWS credentials (`AWS_PROFILE`, `~/.aws`), and every command only reads."
  ]},
  {"cell_type": "code", "execution_count": null, "metadata": {}, "outputs": [], "source": [
   "import aws_analyzer\\n",
   "aws_analyzer.__version__, aws_analyzer.__file__"
  ]},
  {"cell_type": "code", "execution_count": null, "metadata": {}, "outputs": [], "source": [
   "from aws_analyzer import S3View\\n",
   "ui = S3View()\\n",
   "ui.help()"
  ]},
  {"cell_type": "code", "execution_count": null, "metadata": {}, "outputs": [], "source": [
   "ui.overview()"
  ]}
 ],
 "metadata": {"kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"}},
 "nbformat": 4,
 "nbformat_minor": 5
}
"""


@dataclass(frozen=True)
class Tour:
    """
    What `local` runs for one service: its commands on demo data, and with --live, on the user's own account.
    """

    service: str
    demo_code: str
    live_code: str = ""
    needs_agent: bool = False


TOURS = [
    Tour("s3", 'ui.overview(); ui.bucket_info("demo-lake"); ui.summary("s3://demo-lake/")', "ui.overview()"),
    Tour("dynamodb", 'ui.tables(); ui.table_info("orders"); ui.schema("orders")', "ui.tables()"),
    Tour("bedrock_kb", 'ui.kbs(); ui.use("support-docs"); ui.kb_info(); ui.search("How long do refunds take?"); '
         'ui.files()', "ui.kbs()"),
    Tour("bedrock_chat", 'ui.use("support-docs"); ui.ask("How long do refunds take?"); ui.settings(); ui.code()',
         "ui.models()"),
    Tour("sagemaker_env", "ui.instance(); ui.disk(); ui.running(); ui.envs()", "ui.running()"),
    Tour("opensearch", 'ui.overview(); ui.indexes("vectors-prod"); ui.index_info("vectors-prod/support-docs")',
         "ui.overview()"),
    Tour("lambda_functions", 'ui.functions(regions="all"); ui.function_info("orders-etl"); ui.errors("orders-etl"); '
         'ui.performance("orders-etl")', "ui.functions()"),
    Tour("agent", 'ui.ask("Which Lambda failed in the last 24 hours?"); ui.tools()', needs_agent=True),
]
DEMO_SERVICES = [tour.service for tour in TOURS]


@dataclass(frozen=True)
class TourResult:
    """
    How one service's reports went: the line `local` prints for it, and where its output was saved. `errors` are
    the notes a command showed instead of its report (an AWS error, a section it couldn't read).
    """

    name: str
    exit_code: int
    errors: list[str]
    seconds: float
    output: str
    saved: Path
    live: bool = False

    @property
    def status(self) -> str:
        """
        FAIL when the run broke, or a command failed on demo data, where every one should work. Against a real
        account a failed command is WARN: it's what the account allows (credentials, permissions), not the code.
        """
        if self.exit_code:
            return "FAIL"
        if not self.errors:
            return "PASS"
        return "WARN" if self.live else "FAIL"



def run(command: list[str | Path], *, cwd: Path = ROOT) -> int:
    """
    Runs a command with its output shown as it comes, and returns its exit code.
    """
    print(f"$ {' '.join(str(part) for part in command)}", flush=True)
    return subprocess.run([str(part) for part in command], cwd=cwd).returncode


def run_quietly(command: list[str | Path], *, cwd: Path = ROOT) -> subprocess.CompletedProcess[str]:
    """
    Runs a command and keeps what it printed, for a step whose output is shown only when it fails.
    """
    return subprocess.run([str(part) for part in command], cwd=cwd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")


def has_module(python: str | Path, module: str) -> bool:
    """
    Whether `python` can import `module`, so a task can say what to install instead of failing on it.
    """
    return run_quietly([python, "-c", f"import {module}"]).returncode == 0


def missing(module: str, requirements: Path = DEV_REQUIREMENTS) -> int:
    """
    Says which package is missing and how to install it, and returns the exit code for it.
    """
    print(f"{module} isn't installed here: python makefile.py setup "
          f"(or pip install -r {requirements.name})", file=sys.stderr)
    return 1


def tail(text: str, lines: int = FAILED_LINES_SHOWN) -> str:
    """
    The last lines of a step's output, where its error is.
    """
    return "\n".join(text.strip().splitlines()[-lines:])



def task_setup(args: argparse.Namespace, extra: list[str]) -> int:
    """
    Installs the pinned dev requirements (and with --docs the guide site's) into this Python.
    """
    command = [sys.executable, "-m", "pip", "install", "-r", DEV_REQUIREMENTS]
    if args.docs:
        command += ["-r", DOCS_REQUIREMENTS]
    return run([*command, *extra])


def task_test(args: argparse.Namespace, extra: list[str]) -> int:
    """
    Runs pytest on moto (no AWS account needed), passing on any pytest arguments.
    """
    if not has_module(sys.executable, "pytest"):
        return missing("pytest")
    return run([sys.executable, "-m", "pytest", *extra])


def task_lint(args: argparse.Namespace, extra: list[str]) -> int:
    """
    Runs ruff with the rules in ruff.toml.
    """
    if has_module(sys.executable, "ruff"):
        return run([sys.executable, "-m", "ruff", "check", ".", *extra])
    if shutil.which("ruff"):
        return run(["ruff", "check", ".", *extra])
    return missing("ruff")


def task_check(args: argparse.Namespace, extra: list[str]) -> int:
    """
    Runs what CI runs, plus the project's own rules: .claude/skills/check/run.py.
    """
    return run([sys.executable, CHECK, *extra])


def remove_old_builds(dist: Path) -> None:
    """
    Removes this package's earlier sdists and wheels from `dist`, so twine checks only the new ones.
    """
    for old_build in dist.glob("aws_analyzer-*"):
        old_build.unlink()


def task_build(args: argparse.Namespace, extra: list[str]) -> int:
    """
    Builds the sdist and wheel PyPI would get into dist/, and checks them with twine the way PyPI will.
    """
    for module in ("build", "twine"):
        if not has_module(sys.executable, module):
            return missing(module)
    dist = ROOT / "dist"
    remove_old_builds(dist)
    built = run([sys.executable, "-m", "build", "--outdir", dist, *extra])
    if built:
        return built
    return run([sys.executable, "-m", "twine", "check", "--strict", *sorted(dist.glob("aws_analyzer-*"))])


def task_docs(args: argparse.Namespace, extra: list[str]) -> int:
    """
    Builds the guide site with --strict, as the Docs workflow does: a broken link or anchor fails.
    """
    if not has_module(sys.executable, "mkdocs"):
        return missing("mkdocs", DOCS_REQUIREMENTS)
    return run([sys.executable, "-m", "mkdocs", "build", "--strict", *extra])


def task_docs_serve(args: argparse.Namespace, extra: list[str]) -> int:
    """
    Serves the guide site at http://127.0.0.1:8000, rebuilt as its files change.
    """
    if not has_module(sys.executable, "mkdocs"):
        return missing("mkdocs", DOCS_REQUIREMENTS)
    return run([sys.executable, "-m", "mkdocs", "serve", *extra])


def tour_for(service: str) -> Tour | None:
    """
    The demo commands for a service, or None for one without a tour.
    """
    return next((tour for tour in TOURS if tour.service == service), None)


def task_demo(args: argparse.Namespace, extra: list[str]) -> int:
    """
    Runs a service's commands on demo data in moto, from src/, and prints its reports as text.
    """
    tour = tour_for(args.service)
    code = args.code or (tour.demo_code if tour else "ui.help()")
    if not has_module(sys.executable, "moto"):
        return missing("moto")
    return run([sys.executable, DEMO, args.service, code, *extra])


def built_paths() -> list[Path]:
    """
    What the tasks build in this checkout: the folders in BUILT_FOLDERS and the ones BUILT_PATTERNS match.
    """
    found = [folder for folder in BUILT_FOLDERS if folder.exists()]
    for pattern in BUILT_PATTERNS:
        for match in sorted(ROOT.glob(pattern)):
            inside_another = any(folder in match.parents for folder in found)
            if match.is_dir() and not inside_another and ".git" not in match.parts:
                found.append(match)
    return found


def sandbox_caches() -> list[Path]:
    """
    Where the agent's Python sandbox keeps what it downloads outside the checkout: $XDG_CACHE_HOME (else ~/.cache)
    / aws-analyzer, as LocalSandbox's default_cache() puts it, and the tests' Pyodide folder when
    AWS_ANALYZER_TEST_PYODIDE names one.
    """
    base = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache")
    caches = [base / SANDBOX_CACHE_NAME]
    test_pyodide = os.environ.get(TEST_PYODIDE_ENV, "").strip()
    if test_pyodide:
        caches.append(Path(test_pyodide).expanduser())
    return [cache.resolve() for cache in caches if cache.exists()]


def folder_bytes(folder: Path) -> int:
    """
    The size of everything in a folder, not following links, for the line that says what was removed.
    """
    total = 0
    for path in folder.rglob("*"):
        if path.is_file() and not path.is_symlink():
            total += path.stat().st_size
    return total


def human_bytes(size: float) -> str:
    """
    1536 -> '1.5 KB'.
    """
    for unit in SIZE_UNITS:
        if size < BYTES_PER_UNIT or unit == SIZE_UNITS[-1]:
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= BYTES_PER_UNIT
    return f"{size:.1f} {SIZE_UNITS[-1]}"


def shown_path(path: Path) -> str:
    """
    A path from the checkout when it's in it, else from the home folder (~/.cache/aws-analyzer).
    """
    for base, prefix in ((ROOT, ""), (Path.home(), "~/")):
        if path.is_relative_to(base):
            return f"{prefix}{path.relative_to(base).as_posix()}/"
    return f"{path}/"


LEFT_ALONE = """Left alone (clean never touches them):
  - aws-analyzer installed into other environments by install-env: <python> -m pip uninstall aws-analyzer
    in each, and jupyter kernelspec remove <name> for a kernel it added (make envs lists both)
  - files the package saved beside your notebooks (s3-downloads/, lambda-downloads/, agent-charts/, saved pages and
    file lists): S3View().clean_downloads() removes S3's, delete the others yourself once you don't need them
  - Python environments new_env() made: SageMakerView().envs() shows the rm -rf command for each"""


def task_clean(args: argparse.Namespace, extra: list[str]) -> int:
    """
    Removes what these tasks built: .local-check/, dist/, build/, site/, the test and lint caches and __pycache__/
    folders, and with --all also the agent sandbox's downloads in ~/.cache/aws-analyzer.
    """
    targets = built_paths() + (sandbox_caches() if args.all else [])
    verb = "would remove" if args.dry_run else "removing"
    freed = 0
    for target in targets:
        size = folder_bytes(target)
        freed += size
        print(f"{verb} {shown_path(target)}  ({human_bytes(size)})")
        if not args.dry_run:
            shutil.rmtree(target)
    if not targets:
        print("nothing to remove")
    else:
        print(f"{'would free' if args.dry_run else 'freed'} {human_bytes(freed)}")
    kept_caches = [] if args.all else sandbox_caches()
    for cache in kept_caches:
        print(f"the agent sandbox's files are still in {shown_path(cache)} ({human_bytes(folder_bytes(cache))}): "
              f"clean --all removes them too")
    print(f"\n{LEFT_ALONE}")
    return 0


def venv_python(venv: Path = LOCAL_VENV) -> Path:
    """
    The Python of a virtual environment, where Windows and everything else put it.
    """
    if os.name == "nt":
        return venv / "Scripts" / "python.exe"
    return venv / "bin" / "python"


def moto_requirement() -> str:
    """
    The pinned moto line from requirements-dev.txt, with the services the demo data needs.
    """
    for line in DEV_REQUIREMENTS.read_text(encoding="utf-8").splitlines():
        if line.startswith("moto["):
            return line.strip()
    return "moto[s3,dynamodb,cloudwatch,sagemaker,awslambda,logs,iam,sqs]"


def make_venv(python: str) -> Path:
    """
    Makes a fresh virtual environment in .local-check/venv with `python`, replacing any earlier one.
    """
    if LOCAL_VENV.exists():
        shutil.rmtree(LOCAL_VENV)
    LOCAL.mkdir(exist_ok=True)
    made = run_quietly([python, "-m", "venv", LOCAL_VENV])
    if made.returncode:
        raise SystemExit(f"Couldn't make a virtual environment with {python}:\n{tail(made.stderr)}")
    return venv_python()


def build_wheel(python: Path) -> Path:
    """
    Builds this checkout's wheel into .local-check/dist with pip, the way pip builds one from an sdist.
    """
    if LOCAL_DIST.exists():
        shutil.rmtree(LOCAL_DIST)
    built = run_quietly([python, "-m", "pip", "wheel", *PIP_QUIET, "--no-deps", "--wheel-dir", LOCAL_DIST, ROOT])
    wheels = sorted(LOCAL_DIST.glob("aws_analyzer-*.whl"))
    if built.returncode or not wheels:
        raise SystemExit(f"Building the wheel failed:\n{tail(built.stdout + built.stderr)}")
    return wheels[-1]


def pip_install(python: Path, *requirements: str | Path) -> None:
    """
    Installs packages into the local environment, and stops `local` with pip's error when that fails.
    """
    installed = run_quietly([python, "-m", "pip", "install", *PIP_QUIET, *requirements])
    if installed.returncode:
        raise SystemExit(f"pip install failed:\n{tail(installed.stdout + installed.stderr)}")


def step(label: str) -> float:
    """
    Prints what `local` is doing now, and returns when it started.
    """
    print(f"- {label}...", flush=True)
    return time.monotonic()


def timed_tour(name: str, command: list[str | Path], *, saved: Path, live: bool) -> TourResult:
    """
    Runs one service's reports, saves what they printed to `saved`, and picks out the commands that failed.
    """
    started = time.monotonic()
    finished = run_quietly(command, cwd=LOCAL)
    output = finished.stdout + finished.stderr
    saved.write_text(output, encoding="utf-8")
    errors = COMMAND_ERROR.findall(output)
    seconds = time.monotonic() - started
    return TourResult(name, finished.returncode, errors, seconds, output, saved, live)


def demo_command(python: Path, tour: Tour, *, live: bool, args: argparse.Namespace) -> list[str | Path]:
    """
    The command that runs demo.py on the installed package: on moto's demo data, or with live=True the account.
    """
    suffix = "-live" if live else ""
    html = LOCAL_REPORTS / f"{tour.service}{suffix}.html"
    code = tour.live_code if live else tour.demo_code
    command: list[str | Path] = [python, "-I", "-c", DEMO_ON_INSTALLED, DEMO, tour.service, code, "--html", html]
    if not live:
        return command
    command.append("--live")
    if args.profile:
        command += ["--profile", args.profile]
    if args.region:
        command += ["--region", args.region]
    return command


def wants_agent(extras: str) -> bool:
    """
    Whether the extras asked for include the agent's packages.
    """
    return bool(AGENT_EXTRAS & set(extras.split(",")))


def runs_agent(python: str | Path) -> bool:
    """
    Whether a Python is new enough for the agent's packages.
    """
    version_check = f"import sys; sys.exit(sys.version_info < {AGENT_MIN_PYTHON})"
    try:
        return run_quietly([python, "-c", version_check]).returncode == 0
    except OSError:
        return False


def tours_to_run(python: Path, extras: str) -> tuple[list[Tour], list[str]]:
    """
    The services `local` runs, and why any were left out.
    """
    if not wants_agent(extras):
        skipped = ["agent: needs --extras with agent (or all), or run python makefile.py local-agent"]
        return [tour for tour in TOURS if not tour.needs_agent], skipped
    if not runs_agent(python):
        skipped = [f"agent: needs Python {'.'.join(map(str, AGENT_MIN_PYTHON))} or newer (--python)"]
        return [tour for tour in TOURS if not tour.needs_agent], skipped
    return list(TOURS), []


def install_locally(args: argparse.Namespace) -> Path:
    """
    The first half of `local`: a fresh environment, the wheel built and installed with only boto3, every module
    imported there, then the optional packages and moto for the demo data. Returns the environment's Python.
    """
    started = step(f"Making a fresh virtual environment in {LOCAL_VENV.relative_to(ROOT)}")
    python = make_venv(args.python)
    step("Building the wheel from this checkout")
    wheel = build_wheel(python)
    step(f"Installing {wheel.name} on its own (pip brings only boto3, as from PyPI)")
    pip_install(python, wheel)
    imported = run_quietly([python, "-I", "-c", IMPORT_EVERYTHING], cwd=LOCAL)
    if imported.returncode:
        raise SystemExit(f"FAIL  importing the installed package:\n{tail(imported.stdout + imported.stderr)}")
    print(f"PASS  {imported.stdout.strip()}")
    step(f"Installing aws-analyzer[{args.extras}] and moto for the demo data")
    pip_install(python, f"{wheel}[{args.extras}]", moto_requirement())
    print(f"      ready in {time.monotonic() - started:.0f}s", flush=True)
    return python


def print_results(results: list[TourResult], *, show: bool) -> None:
    """
    One line per service, then what failed ones printed (and with --show, every service's reports).
    """
    for result in results:
        print(f"{result.status}  {result.name:<24} {result.seconds:5.1f}s  {result.saved.relative_to(ROOT)}")
        for error in result.errors:
            print(f"      {error}")
    for result in results:
        if show or result.exit_code:
            print(f"\n==== {result.name} ====\n{result.output if show else tail(result.output)}")


def verdict(results: list[TourResult]) -> str:
    """
    The last line of `local`: whether the installed package works, and what to look at when it doesn't.
    """
    failed = [result.name for result in results if result.status == "FAIL"]
    warned = [result.name for result in results if result.status == "WARN"]
    if failed:
        return f"Failed: {', '.join(failed)}"
    if warned:
        return ("The installed package works on the demo data. Against your account, some commands couldn't "
                "read (the AWS errors above): check your credentials, --profile, --region and permissions.")
    return "The installed package works."


def run_tours(python: Path, tours: list[Tour], args: argparse.Namespace) -> list[TourResult]:
    """
    Runs each tour's reports on demo data from the installed package, and with --live against the user's account.
    """
    LOCAL_REPORTS.mkdir(parents=True, exist_ok=True)
    step("Running each service's reports on demo data, from the installed package")
    results = [timed_tour(tour.service, demo_command(python, tour, live=False, args=args),
                          saved=LOCAL_REPORTS / f"{tour.service}.txt", live=False) for tour in tours]
    if args.live:
        step("Running read-only commands against your AWS account")
        results += [timed_tour(f"{tour.service} (live)", demo_command(python, tour, live=True, args=args),
                               saved=LOCAL_REPORTS / f"{tour.service}-live.txt", live=True)
                    for tour in tours if tour.live_code]
    return results


def finish_local(results: list[TourResult], *, skipped: list[str], show: bool) -> int:
    """
    The end of `local` and `local-agent`: a line per service, where the reports are, and the verdict.
    """
    print()
    print_results(results, show=show)
    for reason in skipped:
        print(f"SKIP  {reason}")
    print(f"\nReports, as text and as the notebook's HTML: {LOCAL_REPORTS}")
    print("Try it in a notebook: python makefile.py notebook")
    print(f"\n{verdict(results)}")
    return 1 if any(result.status == "FAIL" for result in results) else 0


def task_local(args: argparse.Namespace, extra: list[str]) -> int:
    """
    Tries the package as pip would install it, without publishing it: builds the wheel, installs it in a fresh
    environment, imports it with only boto3, and runs every service's reports on demo data against that copy.
    """
    python = install_locally(args)
    tours, skipped = tours_to_run(python, args.extras)
    results = run_tours(python, tours, args)
    return finish_local(results, skipped=skipped, show=args.show)


def agent_python(requested: str | None) -> str:
    """
    The Python to make the agent's environment with: the one asked for, else this one when it's 3.11 or newer, else
    the newest python3.1x on PATH that is. Stops with what to do when there's none.
    """
    minimum = ".".join(map(str, AGENT_MIN_PYTHON))
    if requested:
        if not runs_agent(requested):
            raise SystemExit(f"The agent's packages need Python {minimum} or newer: {requested} isn't one, or wasn't found.")
        return requested
    if runs_agent(sys.executable):
        return sys.executable
    for name in AGENT_PYTHONS:
        found = shutil.which(name)
        if found and runs_agent(found):
            return found
    raise SystemExit(f"The agent's packages need Python {minimum} or newer, and none was found on PATH: install one, "
                     f"or pass its path with --python.")


def prepare_sandbox(python: Path) -> str:
    """
    Downloads the Pyodide and Vega files the agent's Python sandbox runs on, once, into its cache. A failure (no
    internet) is a warning, since the agent works without the sandbox and prepare() downloads them on first use.
    """
    step("Downloading the Python sandbox's files (Pyodide on Deno, once, into ~/.cache/aws-analyzer)")
    prepared = run_quietly([python, "-I", "-c", PREPARE_SANDBOX], cwd=LOCAL)
    print(prepared.stdout.rstrip(), flush=True)
    if prepared.returncode:
        return f"the Python sandbox's files didn't download:\n{tail(prepared.stderr)}"
    return ""


def task_local_agent(args: argparse.Namespace, extra: list[str]) -> int:
    """
    Installs the AI agent from this checkout with everything it needs, in one step: a fresh environment on Python
    3.11+, the wheel with every extra, the Python sandbox's files, and a question asked on demo data.
    """
    args.python = agent_python(args.python)
    args.extras = AGENT_INSTALL_EXTRAS
    python = install_locally(args)
    sandbox_problem = "" if args.no_sandbox else prepare_sandbox(python)
    results = run_tours(python, [tour for tour in TOURS if tour.needs_agent], args)
    if sandbox_problem:
        print(f"\nWARN  {sandbox_problem}")
    print("\nAsk your own account: from aws_analyzer import AgentView; AgentView().ask(...)  (see docs/agent.md)")
    return finish_local(results, skipped=[], show=args.show)


def task_notebook(args: argparse.Namespace, extra: list[str]) -> int:
    """
    Opens JupyterLab on the package `local` installed, in .local-check/notebooks, so downloads stay out of the
    checkout; it installs JupyterLab there the first time.
    """
    python = venv_python()
    if not python.exists():
        print("No local install yet: run python makefile.py local first.", file=sys.stderr)
        return 1
    if not has_module(python, "jupyterlab"):
        step("Installing JupyterLab into the local environment")
        pip_install(python, "jupyterlab")
    LOCAL_NOTEBOOKS.mkdir(parents=True, exist_ok=True)
    starter = LOCAL_NOTEBOOKS / "try-aws-analyzer.ipynb"
    if not starter.exists():
        starter.write_text(STARTER_NOTEBOOK, encoding="utf-8")
    return run([python, "-m", "jupyter", "lab", f"--notebook-dir={LOCAL_NOTEBOOKS}", *extra], cwd=LOCAL_NOTEBOOKS)


@dataclass(frozen=True)
class FoundEnv:
    """
    One Python environment `install-env` can install into: what SageMakerAnalyzer.environments() found, or a path
    the user named.
    """

    name: str
    prefix: Path
    python_version: str
    kind: str = "conda"
    kernels: tuple[str, ...] = ()
    kept: bool | None = None
    is_this_python: bool = False

    @property
    def python(self) -> Path:
        """
        The environment's own Python, which pip and ipykernel run on.
        """
        return venv_python(self.prefix)

    @property
    def runs_agent(self) -> bool:
        """
        Whether its Python is new enough for the agent's packages.
        """
        numbers = tuple(int(part) for part in self.python_version.split(".")[:2] if part.isdigit())
        return numbers >= AGENT_MIN_PYTHON


def finder_pythons() -> list[str]:
    """
    The Pythons that may have boto3, to read the environments with: this one, the checkout's .venv, `local`'s
    environment, and the conda Pythons SageMaker ships.
    """
    candidates = [sys.executable, str(venv_python(ROOT / ".venv")), str(venv_python())]
    candidates += [os.path.expanduser(path) for path in CONDA_PYTHONS]
    return [path for path in dict.fromkeys(candidates) if os.path.isfile(path)]


def found_env(env: dict) -> FoundEnv:
    """
    One environment from environments()' JSON. Which one is this Python is worked out here, since the Python that
    read them may be another.
    """
    prefix = Path(env["path"])
    is_this_python = prefix.resolve() == Path(sys.prefix).resolve()
    return FoundEnv(env["name"], prefix, env["python"], env["kind"], tuple(env["kernels"]), env["kept"],
                    is_this_python)


def find_environments() -> tuple[str, list[FoundEnv]] | None:
    """
    What kind of machine this is ('notebook instance', 'studio', 'local', ...) and its Python environments, read by
    sagemaker_env from this checkout's src/ with the first Python that has boto3, or None when none has it. Reads
    files, never runs the environments.
    """
    for python in finder_pythons():
        if not has_module(python, "botocore"):
            continue
        found = run_quietly([python, "-I", "-c", FIND_ENVIRONMENTS, SRC])
        if found.returncode:
            raise SystemExit(f"Couldn't read the environments:\n{tail(found.stderr)}")
        report = json.loads(found.stdout)
        return report["kind"], [found_env(env) for env in report["envs"]]
    return None


def environments_for(named: list[str]) -> tuple[str, list[FoundEnv]]:
    """
    The machine's kind and environments, or none when they can't be read but every environment named is a path.
    """
    found = find_environments()
    if found is not None:
        return found
    if named and all(env_at_path(env) for env in named):
        return "", []
    raise SystemExit("Reading the environments needs a Python with boto3, and none was found: pip install boto3 (or "
                     "python makefile.py setup), or name the environment by its path with --env /path/to/env.")


def installed_version(env: FoundEnv) -> str:
    """
    The aws-analyzer version installed in an environment, read from its dist-info folder: '' when there's none.
    """
    for pattern in ("lib/python3*/site-packages/aws_analyzer-*.dist-info", "Lib/site-packages/aws_analyzer-*.dist-info"):
        for dist_info in sorted(env.prefix.glob(pattern)):
            return dist_info.name[len("aws_analyzer-"):-len(".dist-info")]
    return ""


def kept_text(kept: bool | None) -> str:
    """
    Whether the environment survives the notebook stopping, for the table: yes, no, or - off SageMaker.
    """
    if kept is None:
        return "-"
    return "yes" if kept else "no"


def print_environments(kind: str, envs: list[FoundEnv]) -> None:
    """
    The environments as a numbered table: name, Python, kind, kernels, whether it's kept, aws-analyzer's version.
    """
    print(f"Python environments on this machine ({kind}):\n")
    rows = [("#", "Name", "Python", "Kind", "Jupyter kernel", "Kept", "aws-analyzer", "Path")]
    for number, env in enumerate(envs, 1):
        name = f"{env.name} (this Python)" if env.is_this_python else env.name
        rows.append((str(number), name, env.python_version or "?", env.kind, ", ".join(env.kernels) or "none",
                     kept_text(env.kept), installed_version(env) or "-", str(env.prefix)))
    widths = [max(len(row[column]) for row in rows) for column in range(len(rows[0]))]
    for row in rows:
        print("  " + "  ".join(cell.ljust(width) for cell, width in zip(row, widths)).rstrip())
    if any(env.kept is False for env in envs):
        print("\nKept 'no': the environment is on the disk SageMaker resets when the notebook stops, so what's "
              "installed in it goes too.")


def python_version_of(python: Path) -> str:
    """
    The version of a Python, by running it: '3.11.9', or '' when it doesn't run.
    """
    try:
        ran = run_quietly([python, "-c", "import platform; print(platform.python_version())"])
    except OSError:
        return ""
    return ran.stdout.strip() if ran.returncode == 0 else ""


def env_at_path(path: str) -> FoundEnv | None:
    """
    An environment the user named by its folder or its Python, found or not, or None when it isn't one: no
    conda-meta or pyvenv.cfg (the system's Python isn't one), or no Python that runs. Links aren't followed, since
    an environment's bin/python is a link to the Python it was made from.
    """
    given = Path(os.path.abspath(os.path.expanduser(path)))
    prefix = given.parent.parent if given.is_file() else given
    is_conda = (prefix / "conda-meta").is_dir()
    if not (is_conda or (prefix / "pyvenv.cfg").is_file()):
        return None
    version = python_version_of(venv_python(prefix))
    if not version:
        return None
    return FoundEnv(prefix.name, prefix, version, "conda" if is_conda else "venv")


def named_environment(word: str, envs: list[FoundEnv]) -> FoundEnv | None:
    """
    The environment one word names: its number in the table, its name, or its path (listed or not).
    """
    if word.isdigit() and 1 <= int(word) <= len(envs):
        return envs[int(word) - 1]
    by_name = [env for env in envs if word in (env.name, str(env.prefix))]
    if by_name:
        return by_name[0]
    return env_at_path(word)


def pick_environments(answer: str, envs: list[FoundEnv]) -> list[FoundEnv]:
    """
    The environments an answer names: numbers from the table ('1,3'), names, paths, or 'all'. Stops with what it
    didn't recognise.
    """
    if answer.strip().lower() == "all":
        return list(envs)
    picked: list[FoundEnv] = []
    for word in answer.replace(",", " ").split():
        env = named_environment(word, envs)
        if env is None:
            raise SystemExit(f"No environment {word!r}: give a number from the list, a name, or the path of a conda "
                             f"or virtual environment (python makefile.py envs lists them).")
        picked.append(env)
    return list(dict.fromkeys(picked))


def ask(question: str, default: str = "") -> str:
    """
    Asks a question in the terminal, and returns the answer, or `default` for Enter (and when there's no terminal).
    """
    suffix = f" [{default}]" if default else ""
    try:
        answer = input(f"{question}{suffix}: ").strip()
    except EOFError:
        return default
    return answer or default


def ask_yes(question: str, *, default: bool) -> bool:
    """
    A yes / no question: Enter takes the default.
    """
    answer = ask(question, "Y/n" if default else "y/N")
    if answer in ("Y/n", "y/N"):
        return default
    return answer.lower().startswith("y")


def kernel_appears_by_itself(env: FoundEnv, kind: str) -> bool:
    """
    Whether Jupyter lists the environment by itself once it has ipykernel: a notebook instance's Jupyter shows each
    conda environment in ~/anaconda3/envs as conda_<name>, with no kernel.json.
    """
    return kind == "notebook instance" and env.prefix.parent == NOTEBOOK_INSTANCE_CONDA_ENVS


def kernel_name(env: FoundEnv) -> str:
    """
    The name of the kernel `install-env` adds: the environment's name as Jupyter allows it ('My Env' -> 'my-env').
    """
    return KERNEL_NAME_UNSAFE.sub("-", env.name.lower()).strip("-") or "aws-analyzer"


def kernel_display_name(env: FoundEnv) -> str:
    """
    What Jupyter's kernel menu shows for the kernel it adds: 'Python 3.11 (pytorch)', as new_env() names them.
    """
    return f"Python {'.'.join(env.python_version.split('.')[:2])} ({env.name})"


@dataclass(frozen=True)
class InstallPlan:
    """
    What `install-env` does: the environments, the extras, and which of them get a Jupyter kernel.
    """

    envs: list[FoundEnv]
    extras: str
    kernels_for: list[FoundEnv]
    machine: str


def extras_note(plan: InstallPlan) -> str:
    """
    A warning when the agent was asked for in an environment whose Python is too old for it, else ''.
    """
    too_old = [env.name for env in plan.envs if wants_agent(plan.extras) and not env.runs_agent]
    if not too_old:
        return ""
    minimum = ".".join(map(str, AGENT_MIN_PYTHON))
    return (f"The agent's packages need Python {minimum}+, so they're left out in {', '.join(too_old)} (pip skips "
            f"them there). For the agent, pick a newer environment, or make one with SageMakerView().new_env('3.12').")


def plan_from_options(args: argparse.Namespace, kind: str, envs: list[FoundEnv]) -> InstallPlan:
    """
    The plan from --env, --extras and --kernel, with no questions.
    """
    picked = pick_environments(" ".join(args.env), envs)
    kernels_for = [env for env in picked if args.kernel and not env.kernels]
    extras = args.extras or DEFAULT_EXTRAS
    return InstallPlan(picked, "" if extras == "none" else extras, kernels_for, kind)


def plan_by_asking(args: argparse.Namespace, kind: str, envs: list[FoundEnv]) -> InstallPlan:
    """
    The plan from questions in the terminal: which environments, which extras, and a kernel for each that has none.
    """
    answer = ask("\nInstall into which? Numbers from the list (1,3), names, a path, or all; Enter to stop")
    if not answer:
        raise SystemExit("Nothing installed.")
    picked = pick_environments(answer, envs)
    print("\nExtras: data (pandas, pyarrow), files (Excel, PDF, ... previews), notebook (HTML reports, the windows, "
          "progress bars),\nagent (AgentView, Python 3.11+), sandbox (the agent's Python sandbox), all, or none.")
    extras = args.extras or ask("Which extras", DEFAULT_EXTRAS)
    kernels_for = []
    for env in picked:
        if env.kernels:
            continue
        question = f"{env.name} has no Jupyter kernel, so notebooks can't use it. Add one ({kernel_display_name(env)})?"
        if ask_yes(question, default=True):
            kernels_for.append(env)
    return InstallPlan(picked, "" if extras == "none" else extras, kernels_for, kind)


def describe_plan(plan: InstallPlan, version: str) -> str:
    """
    The plan in a few lines, shown before anything is installed.
    """
    package = f"aws-analyzer {version}" + (f" [{plan.extras}]" if plan.extras else "")
    lines = [f"Install {package} from this checkout into:"]
    for env in plan.envs:
        kernel = ", with a Jupyter kernel" if env in plan.kernels_for else ""
        lines.append(f"  {env.name}  (Python {env.python_version}, {env.prefix}){kernel}")
    note = extras_note(plan)
    return "\n".join([*lines, *([f"\n{note}"] if note else [])])


def checkout_version() -> str:
    """
    This checkout's __version__, read from its __init__.py.
    """
    text = (SRC / "aws_analyzer" / "__init__.py").read_text(encoding="utf-8")
    found = re.search(r'^__version__ = "([^"]+)"', text, re.MULTILINE)
    return found.group(1) if found else "?"


def make_sure_of_pip(env: FoundEnv) -> None:
    """
    Gives the environment pip when it has none (a conda environment made without it), with ensurepip.
    """
    if has_module(env.python, "pip"):
        return
    if run_quietly([env.python, "-m", "ensurepip", "--upgrade"]).returncode:
        raise SystemExit(f"{env.name} has no pip, and ensurepip couldn't add it: conda install -p {env.prefix} pip")


def add_kernel(env: FoundEnv, machine: str) -> int:
    """
    Installs ipykernel in the environment and, unless Jupyter lists it by itself, adds its kernel for this user
    (ipykernel install --user, as new_env() does).
    """
    installed = run([env.python, "-m", "pip", "install", *PIP_QUIET, "ipykernel"])
    if installed or kernel_appears_by_itself(env, machine):
        return installed
    return run([env.python, "-m", "ipykernel", "install", "--user", "--name", kernel_name(env),
                "--display-name", kernel_display_name(env)])


def install_into(env: FoundEnv, wheel: Path, plan: InstallPlan) -> bool:
    """
    Installs the wheel and its extras into one environment with its own pip, the wheel again even when the same
    version is there (the checkout changed, its number didn't), adds its kernel when asked, and checks it imports.
    """
    step(f"Installing into {env.name} ({env.prefix})")
    make_sure_of_pip(env)
    requirement = f"{wheel}[{plan.extras}]" if plan.extras else str(wheel)
    failed = run([env.python, "-m", "pip", "install", *PIP_QUIET, requirement])
    failed = failed or run([env.python, "-m", "pip", "install", *PIP_QUIET, "--force-reinstall", "--no-deps", wheel])
    if not failed and env in plan.kernels_for:
        failed = add_kernel(env, plan.machine)
    if failed:
        print(f"FAIL  {env.name}: pip's error is above")
        return False
    imported = run_quietly([env.python, "-I", "-c", INSTALLED_VERSION], cwd=LOCAL)
    if imported.returncode:
        print(f"FAIL  {env.name}: installed, but doesn't import:\n{tail(imported.stderr)}")
        return False
    print(f"PASS  {env.name}: aws-analyzer {imported.stdout.strip()} imports")
    return True


def task_envs(args: argparse.Namespace, extra: list[str]) -> int:
    """
    Lists the Python environments on this machine (conda environments, virtual environments) with their Python,
    their Jupyter kernel, whether SageMaker keeps them, and the aws-analyzer version each has.
    """
    kind, envs = environments_for([])
    print_environments(kind, envs)
    print("\nInstall this checkout into some: python makefile.py install-env")
    return 0


def task_install_env(args: argparse.Namespace, extra: list[str]) -> int:
    """
    Installs this checkout into Python environments already on this machine (a SageMaker notebook's conda ones):
    lists them, asks which and with which extras (or takes --env), and can add a Jupyter kernel for each.
    """
    kind, envs = environments_for(args.env)
    if not args.env and not sys.stdin.isatty():
        print_environments(kind, envs)
        print("\nNo terminal to ask in: name them with --env (python makefile.py install-env --env NAME).",
              file=sys.stderr)
        return 1
    if not args.env:
        print_environments(kind, envs)
    plan = plan_from_options(args, kind, envs) if args.env else plan_by_asking(args, kind, envs)
    print(f"\n{describe_plan(plan, checkout_version())}\n")
    if not args.yes and sys.stdin.isatty() and not ask_yes("Go ahead?", default=True):
        print("Nothing installed.")
        return 0

    step("Building the wheel from this checkout")
    builder = Path(sys.executable) if has_module(sys.executable, "pip") else plan.envs[0].python
    wheel = build_wheel(builder)
    results = [install_into(env, wheel, plan) for env in plan.envs]
    for env in plan.envs:
        if env.kept is False:
            print(f"\nNote: {env.name} is on the disk SageMaker resets when the notebook stops, so run this again "
                  f"after a restart, or keep your work in an environment made with SageMakerView().new_env().")
    print("\nIn a notebook, pick the environment's kernel (Kernel > Change Kernel), then: "
          "from aws_analyzer import S3View; S3View().help()")
    return 0 if all(results) else 1


TASKS = {
    "setup": task_setup,
    "test": task_test,
    "lint": task_lint,
    "check": task_check,
    "build": task_build,
    "docs": task_docs,
    "docs-serve": task_docs_serve,
    "demo": task_demo,
    "local": task_local,
    "local-agent": task_local_agent,
    "notebook": task_notebook,
    "envs": task_envs,
    "install-env": task_install_env,
    "clean": task_clean,
}
PASSES_ARGUMENTS_ON = {"setup", "test", "lint", "check", "build", "docs", "docs-serve", "demo", "notebook"}


def first_paragraph(text: str | None) -> str:
    """
    A docstring's first paragraph on one line, for the list of tasks.
    """
    return " ".join((text or "").strip().split("\n\n")[0].split())


def add_local_options(parser: argparse.ArgumentParser) -> None:
    """
    The options of `local`: which Python and extras to install with, and whether to read the user's account too.
    """
    parser.add_argument("--python", default=sys.executable,
                        help="the Python to make the environment with, e.g. python3.10 (default: this one)")
    parser.add_argument("--extras", default=DEFAULT_EXTRAS,
                        help=f"the optional packages to install (default: {DEFAULT_EXTRAS}; 'all' adds the agent)")
    parser.add_argument("--live", action="store_true",
                        help="also run read-only commands against your AWS account (your usual credentials)")
    parser.add_argument("--profile", help="with --live: the AWS profile to use")
    parser.add_argument("--region", help="with --live: the region to read")
    parser.add_argument("--show", action="store_true", help="print every service's reports, not only failures")


def add_local_agent_options(parser: argparse.ArgumentParser) -> None:
    """
    The options of `local-agent`: which Python to install with, and whether to skip the sandbox's download.
    """
    parser.add_argument("--python", default=None,
                        help="the Python 3.11+ to make the environment with (default: this one, or the newest found)")
    parser.add_argument("--no-sandbox", action="store_true",
                        help="don't download the Python sandbox's files now (the agent downloads them on first use)")
    parser.add_argument("--show", action="store_true", help="print the agent's report, not only failures")
    parser.set_defaults(live=False)


def add_install_env_options(parser: argparse.ArgumentParser) -> None:
    """
    The options of `install-env`: which environments and extras, and whether to add kernels, so it runs without
    questions too.
    """
    parser.add_argument("--env", action="append", default=[],
                        help="an environment to install into: its name or number from `envs`, or its path "
                             "(repeat for several, or 'all'); without it, it asks")
    parser.add_argument("--extras", help=f"the optional packages: data, files, notebook, agent, sandbox or all "
                                         f"(default: {DEFAULT_EXTRAS}; 'none' for none)")
    parser.add_argument("--kernel", action="store_true",
                        help="add a Jupyter kernel for an environment that has none")
    parser.add_argument("--yes", action="store_true", help="don't ask before installing")


def add_clean_options(parser: argparse.ArgumentParser) -> None:
    """
    The options of `clean`: whether to remove the sandbox's cache outside the checkout too, and a dry run.
    """
    parser.add_argument("--all", action="store_true",
                        help="also remove the agent sandbox's downloaded files in ~/.cache/aws-analyzer "
                             "(local-agent or the agent downloads them again when needed)")
    parser.add_argument("--dry-run", action="store_true", help="say what would be removed, and remove nothing")


def make_parser() -> argparse.ArgumentParser:
    """
    One subcommand per task, each described by its function's docstring.
    """
    intro, _, rest = (__doc__ or "").strip().partition("\n\n")
    parser = argparse.ArgumentParser(prog="python makefile.py", description=intro, epilog=rest,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    tasks = parser.add_subparsers(dest="task", metavar="task")
    for name, function in TASKS.items():
        tasks.add_parser(name, help=first_paragraph(function.__doc__), description=first_paragraph(function.__doc__))
    tasks.choices["setup"].add_argument("--docs", action="store_true", help="also install requirements-docs.txt")
    tasks.choices["demo"].add_argument("service", nargs="?", default="s3",
                                       help=f"the service: {', '.join(DEMO_SERVICES)} (default: s3)")
    tasks.choices["demo"].add_argument("code", nargs="?", help="the Python to run (default: the service's tour)")
    add_local_options(tasks.choices["local"])
    add_local_agent_options(tasks.choices["local-agent"])
    add_install_env_options(tasks.choices["install-env"])
    add_clean_options(tasks.choices["clean"])
    tasks.add_parser("help", help="this list")
    return parser


def main() -> int:
    parser = make_parser()
    args, extra = parser.parse_known_args()
    if args.task in (None, "help"):
        parser.print_help()
        return 0
    if extra and args.task not in PASSES_ARGUMENTS_ON:
        parser.error(f"{args.task} doesn't take {' '.join(extra)}")
    return TASKS[args.task](args, extra)


if __name__ == "__main__":
    sys.exit(main())
