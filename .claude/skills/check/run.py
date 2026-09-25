"""Run what CI runs, locally, plus the project-rule checks CI doesn't have. Prints one line per step and the
output of any step that failed.

    python .claude/skills/check/run.py                  # lint, standalone imports, rules, tests, helper drift
    python .claude/skills/check/run.py --matrix         # also pytest on every other Python in CI's matrix (uv)
    python .claude/skills/check/run.py -- -k policy     # pass arguments through to pytest

Steps:
  ruff        ruff check . (the rules in ruff.toml)
  imports     each analyzer copied alone into an empty directory and imported with every package except
              boto3 / botocore blocked, like CI's "only boto3 installed" job
  rules       .claude/skills/check/rules.py: read-only AWS calls, lazy optional imports, View conventions,
              IAM permissions listed in README.md
  pytest      python -m pytest -q (moto; no AWS account needed)
  matrix      with --matrix: pytest on each Python version in .github/workflows/ci.yml other than this one,
              in throwaway environments built by uv from requirements-dev.txt (pandas 2 / IPython 8 on 3.10,
              pandas 3 / IPython 9 on 3.11+)
  drift       .claude/skills/sync-helpers/drift.py --summary: duplicated helpers that differ between analyzers
              (information only; some differences are intentional)
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent

# Run inside the temporary directory: block every import that isn't the standard library or boto3's own
# dependencies, then import the analyzer. Mirrors CI, where only boto3 is installed.
BOTO3_ONLY = """
import sys
ALLOWED = {"boto3", "botocore", "s3transfer", "jmespath", "dateutil", "urllib3", "six", "_distutils_hack"}
class OnlyBoto3:
    def find_spec(self, name, path=None, target=None):
        root = name.split(".")[0]
        if root in sys.stdlib_module_names or root in ALLOWED or root == MODULE:
            return None
        raise ModuleNotFoundError(f"No module named {root!r} (CI imports each analyzer with only boto3 installed)")
sys.meta_path.insert(0, OnlyBoto3())
import importlib
importlib.import_module(MODULE)
"""


def step(name: str, command: list[str], *, cwd: Path = ROOT, tail: int = 80) -> tuple[str, bool, str, str]:
    """(status line, passed, the output to show if it failed, full output)"""
    started = time.monotonic()
    proc = subprocess.run(command, cwd=cwd, capture_output=True, text=True)
    output = (proc.stdout + proc.stderr).strip()
    seconds = time.monotonic() - started
    ok = proc.returncode == 0
    summary = last_line(output) if name in ("rules", "pytest") or name.startswith("py") else ""
    shown = "\n".join(output.splitlines()[-tail:]) if not ok else ""
    return f"{'PASS' if ok else 'FAIL'}  {name:<8} {seconds:5.1f}s  {summary}".rstrip(), ok, shown, output


def last_line(output: str) -> str:
    lines = [line for line in output.strip().splitlines() if line.strip()]
    return lines[-1] if lines else ""


def main() -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    parser.add_argument("--matrix", action="store_true", help="also run pytest on CI's other Python versions")
    parser.add_argument("pytest_args", nargs="*", help="arguments for pytest (after --)")
    args = parser.parse_args()
    python = sys.executable
    results: list[tuple[str, bool, str, str]] = []

    # ruff: prefer the pinned one in this environment, like CI.
    ruff = [python, "-m", "ruff"] if subprocess.run([python, "-m", "ruff", "--version"], capture_output=True
                                                    ).returncode == 0 else ["ruff"]
    if ruff == ["ruff"] and not shutil.which("ruff"):
        results.append(("SKIP  ruff     ruff isn't installed: pip install -r requirements-dev.txt", True, "", ""))
    else:
        results.append(step("ruff", [*ruff, "check", "."]))

    # imports: each file alone, boto3 only.
    failures = []
    for path in sorted((ROOT / "analyzers").glob("*.py")):
        with tempfile.TemporaryDirectory() as tmp:
            shutil.copy(path, tmp)
            code = f"MODULE = {path.stem!r}\n{BOTO3_ONLY}"
            proc = subprocess.run([python, "-c", code], cwd=tmp, capture_output=True, text=True)
            if proc.returncode:
                failures.append(f"{path.name}: {last_line(proc.stderr)}")
    results.append((f"{'FAIL' if failures else 'PASS'}  imports  each analyzer alone with only boto3",
                    not failures, "\n".join(failures), ""))

    rules = step("rules", [python, str(HERE / "rules.py")])
    results.append(rules)

    pytest = step("pytest", [python, "-m", "pytest", "-q", "-p", "no:cacheprovider", *args.pytest_args], tail=120)
    results.append(pytest)

    if args.matrix:
        ci = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
        match = re.search(r"python-version:\s*\[([^\]]+)\]", ci)
        versions = re.findall(r'"([\d.]+)"', match.group(1)) if match else []
        current = f"{sys.version_info.major}.{sys.version_info.minor}"
        if not shutil.which("uv"):
            results.append(("SKIP  matrix   uv isn't installed (https://docs.astral.sh/uv/)", True, "", ""))
        for version in versions:
            if version == current or not shutil.which("uv"):
                continue
            results.append(step(f"py{version}", [
                "uv", "run", "--quiet", "--no-project", "--python", version, "--with-requirements",
                "requirements-dev.txt", "python", "-m", "pytest", "-q", "-p", "no:cacheprovider",
                *args.pytest_args], tail=60))

    drift = subprocess.run([python, str(ROOT / ".claude/skills/sync-helpers/drift.py"), "--summary"],
                           cwd=ROOT, capture_output=True, text=True)
    drift_out = drift.stdout.strip()
    results.append((f"INFO  drift    {drift_out.splitlines()[0] if drift_out else drift.stderr.strip()}", True, "",
                    drift_out))

    print(f"Python {sys.version.split()[0]} ({python})\n")
    for line, *_ in results:
        print(line)
    rules_warnings = [ln for ln in rules[3].splitlines() if ln.startswith("warning:")]
    if rules_warnings and rules[1]:
        print("\nRule warnings:\n" + "\n".join(rules_warnings))
    for line, ok, shown, _ in results:
        if not ok and shown:
            print(f"\n==== {line.split()[1]} ====\n{shown}")
    failed = [line.split()[1] for line, ok, *_ in results if not ok]
    print(f"\n{'All checks passed.' if not failed else 'Failed: ' + ', '.join(failed)}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
