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
    python makefile.py clean                 # remove what these tasks built

`local` is the check before a release: it builds the wheel from this checkout, installs it in a fresh virtual
environment in .local-check/ (only boto3 comes with it, as from PyPI), imports every module there, then installs the
optional packages and runs every service's reports on demo data against that installed copy, not src/. Each
service's reports are saved in .local-check/reports/ as text and as the notebook's HTML, to open in a browser.

`local-agent` installs the AI agent (AgentView) the same way, in one step: it picks a Python 3.11 or newer (the
agent's packages need one) when this one is older, installs the wheel with every extra (the agent's LangChain
packages, Deno for its Python sandbox, and the data, file and notebook packages), downloads the sandbox's Pyodide
files, and asks the agent a question on demo data with a scripted model. `make notebook` then opens JupyterLab on it.
"""

from __future__ import annotations

import argparse
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
BUILT_FOLDERS = [LOCAL, ROOT / "dist", ROOT / "build", ROOT / "site", ROOT / ".pytest_cache", ROOT / ".ruff_cache"]
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


def task_clean(args: argparse.Namespace, extra: list[str]) -> int:
    """
    Removes what these tasks built: .local-check/, dist/, build/, site/ and the test and lint caches.
    """
    for folder in BUILT_FOLDERS:
        if folder.exists():
            print(f"removing {folder.relative_to(ROOT)}/")
            shutil.rmtree(folder)
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
