"""The mechanical parts of a release: where main stands since the last one, the CHANGELOG's Unreleased entries moved
under a new version with __version__ set to match, and one version's entry as the GitHub release notes.

    python .claude/skills/release/release.py status          # the last release, what's on main since, ready or not
    python .claude/skills/release/release.py bump minor      # or patch, major, 0.2.0 (--date 2026-10-05)
    python .claude/skills/release/release.py notes 0.2.0     # that version's CHANGELOG entry (default: __version__)

status reads git, gh (CI on main) and PyPI and changes nothing. bump edits CHANGELOG.md and
src/aws_analyzer/__init__.py only: no commit, no tag, nothing sent anywhere. notes prints.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import subprocess
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
CHANGELOG = ROOT / "CHANGELOG.md"
INIT = ROOT / "src" / "aws_analyzer" / "__init__.py"
PYPROJECT = ROOT / "pyproject.toml"

VERSION_LINE = re.compile(r'^__version__ = "([^"]+)"$', re.M)
HEADING = re.compile(r"^## \[([^\]]+)\](?: - (\d{4}-\d{2}-\d{2}))?[ \t]*$", re.M)
LINK_REF = re.compile(r"^\[([^\]]+)\]: (\S+)[ \t]*$", re.M)
SEMVER = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")

# What a changed path means for a release: only "package" changes reach people who pip install it, and README.md is
# the PyPI page. The guide site publishes from main on its own.
KINDS = (
    ("package", ("analyzers/", "src/", "pyproject.toml")),
    ("PyPI page", ("README.md",)),
    ("guide site", ("docs/", "mkdocs.yml", "requirements-docs.txt")),
    ("development", ("tests/", ".github/", ".claude/", "requirements-dev.txt", "ruff.toml", "mypy.ini", "CLAUDE.md")),
)


def run(*command: str) -> str:
    """A command's output, or '' if it failed or isn't installed."""
    try:
        proc = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return proc.stdout.strip() if proc.returncode == 0 else ""


def setting(pattern: str, path: Path, what: str) -> str:
    match = re.search(pattern, path.read_text(encoding="utf-8"), re.M)
    if not match:
        sys.exit(f"can't find {what} in {path.relative_to(ROOT)}")
    return match.group(1)


def current_version() -> str:
    return setting(VERSION_LINE.pattern, INIT, '__version__ = "..."')


def parse(version: str) -> tuple[int, int, int]:
    match = SEMVER.match(version)
    if not match:
        sys.exit(f"{version!r} isn't a version like 0.2.0")
    major, minor, patch = (int(part) for part in match.groups())
    return major, minor, patch


def released() -> list[str]:
    """Released versions from the v* tags, newest first."""
    tags = run("git", "tag", "--list", "v*", "--sort=-v:refname").splitlines()
    return [tag[1:] for tag in tags if SEMVER.match(tag[1:])]


def sections(text: str) -> list[tuple[str, str | None, int, int]]:
    """(name, date, where its heading starts, where it ends) for each '## [...]' section of the changelog."""
    heads = list(HEADING.finditer(text))
    found = []
    for i, head in enumerate(heads):
        if i + 1 < len(heads):
            end = heads[i + 1].start()
        else:  # the last section ends at the link definitions under it
            refs = LINK_REF.search(text, head.end())
            end = refs.start() if refs else len(text)
        found.append((head.group(1), head.group(2), head.start(), end))
    return found


def section(text: str, name: str) -> tuple[str, int, int] | None:
    """(body, start, end) of one section, by its name ('Unreleased' or a version)."""
    for found, _, start, end in sections(text):
        if found.lower() == name.lower():
            return text[start:end].partition("\n")[2].strip(), start, end
    return None


def drop_empty_groups(body: str) -> str:
    """The body without '### Added' style headings that have nothing under them."""
    parts = re.split(r"(?m)^(?=### )", body)
    return "".join(part for part in parts if not part.startswith("### ") or part.partition("\n")[2].strip()).strip()


def suggested_bump(body: str) -> str:
    if "**Breaking**" in body or re.search(r"(?m)^### (Added|Removed)\s*$", drop_empty_groups(body)):
        return "minor"
    return "patch"


def pypi_version() -> str | None:
    name = setting(r'^name = "([^"]+)"', PYPROJECT, "the project name")
    try:
        with urllib.request.urlopen(f"https://pypi.org/pypi/{name}/json", timeout=10) as response:
            return json.load(response)["info"]["version"]
    except Exception:  # offline, not on PyPI yet, or PyPI's cache hasn't caught up with an upload
        return None


def changes_since(last: str | None) -> list[str]:
    """One line per pull request (or direct commit) on origin/main since the last release."""
    span = f"v{last}..origin/main" if last else "origin/main"
    lines = []
    for record in run("git", "log", "--first-parent", "--format=%h%x1f%s%x1f%b%x1e", span).split("\x1e"):
        sha, subject, body = (record.strip("\n").split("\x1f") + ["", ""])[:3]
        if not sha:
            continue
        pr = re.match(r"Merge pull request #(\d+) from (\S+)", subject)
        if pr:
            title = body.strip().splitlines()[0] if body.strip() else subject
            tag = "  (dependencies)" if "dependabot/" in pr.group(2) else ""
            lines.append(f"{sha}  #{pr.group(1)}  {title}{tag}")
        else:
            lines.append(f"{sha}  {subject}")
    return lines


def changed_kinds(last: str | None) -> dict[str, int]:
    span = f"v{last}..origin/main" if last else "origin/main"
    counts: dict[str, int] = {}
    for path in run("git", "diff", "--name-only", span).splitlines():
        kind = next((k for k, prefixes in KINDS if path.startswith(prefixes)), "other")
        counts[kind] = counts.get(kind, 0) + 1
    return counts


def ci_on_main() -> tuple[str, str]:
    """(result, url) of the CI run on origin/main's newest commit."""
    head = run("git", "rev-parse", "origin/main")
    out = run("gh", "run", "list", "--workflow", "ci.yml", "--branch", "main", "--commit", head, "--limit", "1",
              "--json", "status,conclusion,url")
    runs = json.loads(out) if out else []
    if not runs:
        return ("no run found" if out else "unknown (gh not available)"), ""
    latest = runs[0]
    return (latest["conclusion"] or latest["status"]), latest["url"]


def status() -> int:
    run("git", "fetch", "--quiet", "--tags", "origin")
    text = CHANGELOG.read_text(encoding="utf-8")
    version, versions = current_version(), released()
    last = versions[0] if versions else None
    branch = run("git", "rev-parse", "--abbrev-ref", "HEAD")
    dirty = run("git", "status", "--porcelain")
    behind, _, ahead = run("git", "rev-list", "--left-right", "--count", "origin/main...HEAD").partition("\t")
    ci, ci_url = ci_on_main()
    pypi = pypi_version()
    unreleased = section(text, "Unreleased")
    pending = unreleased[0] if unreleased else ""
    entries = re.findall(r"(?m)^- ", pending)
    changes, kinds = changes_since(last), changed_kinds(last)

    print(f"__version__     {version}")
    print(f"Last release    {'v' + last if last else 'none (no v* tags)'}")
    print(f"On PyPI         {pypi or 'unknown (offline, not published, or PyPI is behind)'}")
    print(f"CI on main      {ci}  {ci_url}")
    print(f"This checkout   {branch}, {'uncommitted changes' if dirty else 'clean'}, "
          f"{behind or '?'} behind and {ahead or '?'} ahead of origin/main")
    print(f"Unreleased      {len(entries)} entries in CHANGELOG.md" if unreleased else "Unreleased      no section")
    print()
    print(f"On main since {'v' + last if last else 'the start'}: {len(changes)}")
    for line in changes:
        print(f"  {line}")
    if kinds:
        print("Files changed: " + ", ".join(f"{kind} {n}" for kind, n in sorted(kinds.items())))
    print()

    problems, notes = [], []
    if last and parse(version) < parse(last):
        problems.append(f"__version__ {version} is older than the last release v{last}")
    elif last and version != last:
        notes.append(f"__version__ is {version} but the last release is v{last}: bumped without a release?")
    if not unreleased:
        problems.append("CHANGELOG.md has no '## [Unreleased]' section")
    if section(text, version) is None:
        problems.append(f"CHANGELOG.md has no entry for {version}")
    if dirty:
        problems.append("the working tree has uncommitted changes")
    if ci != "success":
        problems.append(f"CI on main's newest commit: {ci}")
    if not changes:
        problems.append(f"nothing on main since v{last}")
    elif not kinds.get("package") and not kinds.get("PyPI page"):
        notes.append("only docs and development files changed: pip users get nothing new, and the guide site "
                     "already published from main")
    if branch != "main" and not branch.startswith("release/"):
        notes.append(f"releases are cut from origin/main; what's only on {branch} isn't in one until it's merged")
    elif branch == "main" and behind not in ("", "0"):
        notes.append("local main is behind origin/main: git pull --ff-only")
    if last and pypi and pypi != last:
        notes.append(f"PyPI shows {pypi}, the last tag is v{last} (PyPI can lag a few minutes after an upload)")
    if changes and not entries:
        notes.append("Unreleased is empty: write the entries before bumping")
    if entries and last:
        notes.append(f"suggested bump: {suggested_bump(pending)}")

    for note in notes:
        print(f"note: {note}")
    for problem in problems:
        print(f"not ready: {problem}")
    if not problems:
        print("ready to release")
    return 1 if problems else 0


def bump(target: str, date: str) -> int:
    text = CHANGELOG.read_text(encoding="utf-8")
    version, versions = current_version(), released()
    last = versions[0] if versions else None
    if target in ("patch", "minor", "major"):
        major, minor, patch = parse(last or version)
        new_version = {"major": f"{major + 1}.0.0", "minor": f"{major}.{minor + 1}.0",
                       "patch": f"{major}.{minor}.{patch + 1}"}[target]
    else:
        new_version = target.removeprefix("v")
        parse(new_version)
        if last and parse(new_version) <= parse(last):
            sys.exit(f"{new_version} isn't newer than the last release, v{last}")
    if section(text, new_version) is not None:
        sys.exit(f"CHANGELOG.md already has a [{new_version}] section: bumped earlier without a release? (see status)")
    unreleased = section(text, "Unreleased")
    if unreleased is None:
        sys.exit("CHANGELOG.md has no '## [Unreleased]' section")
    body, start, end = unreleased
    body = drop_empty_groups(body)
    if not body:
        sys.exit("nothing under '## [Unreleased]' in CHANGELOG.md: write the entries first")

    text = text[:start] + f"## [Unreleased]\n\n## [{new_version}] - {date}\n\n{body}\n\n" + text[end:].lstrip("\n")
    repo = setting(r'^Source = "([^"]+)"', PYPROJECT, "the Source URL").rstrip("/")
    diff = f"{repo}/compare/v{last}...v{new_version}" if last else f"{repo}/releases/tag/v{new_version}"
    links = f"[Unreleased]: {repo}/compare/v{new_version}...HEAD\n[{new_version}]: {diff}"
    if re.search(r"(?m)^\[Unreleased\]: \S+[ \t]*$", text):
        text = re.sub(r"(?m)^\[Unreleased\]: \S+[ \t]*$", lambda _: links, text, count=1)
    else:
        text = text.rstrip("\n") + f"\n\n{links}\n"
    CHANGELOG.write_text(text, encoding="utf-8")
    INIT.write_text(VERSION_LINE.sub(f'__version__ = "{new_version}"', INIT.read_text(encoding="utf-8"), count=1),
                    encoding="utf-8")
    print(f"__version__ {version} -> {new_version}; CHANGELOG.md's Unreleased entries are now [{new_version}] - {date}")
    print()
    print(body)
    return 0


def unwrap(body: str) -> str:
    """The entry with its hard-wrapped lines joined: GitHub shows every newline in a release's notes as a break."""
    lines: list[str] = []
    fenced = False
    for line in body.splitlines():
        starts = line.lstrip()
        if starts.startswith("```"):
            fenced = not fenced
        elif (not fenced and lines and lines[-1].strip() and not lines[-1].lstrip().startswith(("#", "```", "|"))
              and starts and not starts.startswith(("- ", "* ", "#", "|")) and not re.match(r"\d+\. ", starts)):
            lines[-1] += " " + starts
            continue
        lines.append(line)
    return "\n".join(lines)


def notes(version: str | None) -> int:
    text = CHANGELOG.read_text(encoding="utf-8")
    version = (version or current_version()).removeprefix("v")
    found = section(text, version)
    if not found or not found[0]:
        print(f"CHANGELOG.md has no entry for {version}", file=sys.stderr)
        return 1
    print(unwrap(found[0]))
    diff = dict(LINK_REF.findall(text)).get(version, "")
    if "/compare/" in diff:
        print(f"\n**Full diff**: {diff}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("status", help="the last release, what's on main since, and whether it's ready")
    bumping = commands.add_parser("bump", help="move the Unreleased entries under a new version and set __version__")
    bumping.add_argument("target", help="patch, minor, major or a version such as 0.2.0")
    bumping.add_argument("--date", default=dt.date.today().isoformat(), help="the release date (default: today)")
    showing = commands.add_parser("notes", help="one version's CHANGELOG entry, for the GitHub release")
    showing.add_argument("version", nargs="?", help="default: __version__")
    args = parser.parse_args()
    if args.command == "status":
        return status()
    if args.command == "bump":
        return bump(args.target, args.date)
    return notes(args.version)


if __name__ == "__main__":
    sys.exit(main())
