"""
Runbooks: step-by-step guides the agent reads when a question needs one ("a Lambda is failing", "an S3 upload didn't
start my function"). A runbook is a folder with a SKILL.md, in the Agent Skills format (agentskills.io): front matter
with a name and a one-line description, then the guide in markdown, and any files it refers to beside it. The model
sees only each runbook's name and description in the system prompt, and reads the whole guide with the Runbooks
toolkit's read_runbook when the question calls for it, so the prompt stays short however many there are.

The built-in runbooks are the folders beside this file (BUILT_IN). AgentAnalyzer(runbooks=...) adds folders of your
own (a folder of runbook folders, or one runbook's folder), read after the built-in ones, so a runbook of yours with
a built-in one's name replaces it. A runbook whose front matter has metadata.toolkits ('s3 lambda') is offered only to
an agent with all of those toolkits.

Everything here is pure but for the reads of SKILL.md files (load_runbooks, read_runbook_text). Front matter is read
by parse_front_matter, a small reader for what SKILL.md files hold (key: value lines, a folded or literal block, one
nested mapping such as metadata:), so listing runbooks needs no YAML package. A runbook that can't be read becomes a
RunbookProblem saying what's wrong, never an exception.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

BUILT_IN = Path(__file__).parent
BUILT_IN_LABEL = "built-in"
RUNBOOK_FILE = "SKILL.md"
MAX_RUNBOOK_BYTES = 256 * 1024
MAX_NAME_CHARACTERS = 64
MAX_DESCRIPTION_CHARACTERS = 1024
_FENCE = "---"
_NAME = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
_LIST_SEPARATORS = re.compile(r"[\s,]+")
_BLOCK_STYLES = (">", ">-", ">+", "|", "|-", "|+")
_QUOTES = ("'", '"')


@dataclass(frozen=True)
class RunbookSource:
    """
    A folder runbooks are read from: `label` is how reports name it ('built-in', or the folder's name), `folder`
    where it is, and `single` when the folder is one runbook (it holds a SKILL.md) rather than a folder of them.
    """

    label: str
    folder: Path
    single: bool = False


@dataclass(frozen=True)
class Runbook:
    """
    One runbook: its `name` (its folder's), the one-line `description` the model chooses by, the `source` it came
    from (a RunbookSource's label), its `folder`, the toolkits it needs (`toolkits`, from metadata.toolkits; empty for
    any agent) and the other files in its folder (`files`, paths from the folder).
    """

    name: str
    description: str
    source: str
    folder: Path
    toolkits: tuple[str, ...] = ()
    files: tuple[str, ...] = ()

    @property
    def path(self) -> Path:
        return self.folder / RUNBOOK_FILE


@dataclass(frozen=True)
class RunbookProblem:
    """
    Why a runbook, or a folder of them, couldn't be read: where (`path`, as given) and what to fix (`message`).
    """

    path: str
    message: str


@dataclass
class RunbookShelf:
    """
    What load_runbooks() found: the `runbooks` by name (a later source's replacing an earlier one's of the same
    name, recorded in `replaced` as name -> the source it replaced), the `sources` read, and the `problems`.
    """

    runbooks: dict[str, Runbook] = field(default_factory=dict)
    sources: list[RunbookSource] = field(default_factory=list)
    problems: list[RunbookProblem] = field(default_factory=list)
    replaced: dict[str, str] = field(default_factory=dict)

    def usable(self, toolkits: Iterable[str]) -> list[Runbook]:
        """
        The runbooks an agent with these toolkits (their names) can follow, by name.
        """
        have = set(toolkits)
        return [book for _name, book in sorted(self.runbooks.items()) if set(book.toolkits) <= have]

    def find(self, name: str) -> Runbook | None:
        """
        A runbook by its name, forgiving case, spaces and underscores ('Lambda failing' finds 'lambda-failing').
        """
        wanted = _slug(name)
        return self.runbooks.get(wanted)

    def close_names(self, name: str, among: Iterable[Runbook]) -> list[str]:
        """
        The names among `among` closest to `name`, for a runbook that isn't there.
        """
        names = [book.name for book in among]
        return difflib.get_close_matches(_slug(name), names, n=3, cutoff=0.4) or names


def _slug(text: str) -> str:
    """
    A name as runbook names are written: 'Lambda_Failing ' -> 'lambda-failing'.
    """
    return re.sub(r"[\s_]+", "-", str(text or "").strip().lower())


def _unquoted(value: str) -> str:
    """
    A scalar's text without its quotes: "'a: b'" -> 'a: b'.
    """
    value = value.strip()
    is_quoted = len(value) >= 2 and value[0] in _QUOTES and value[-1] == value[0]
    return value[1:-1] if is_quoted else value


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _block_text(lines: list[str], style: str) -> str:
    """
    A '>' (folded: lines joined by spaces) or '|' (literal: lines kept) block's text.
    """
    if not lines:
        return ""
    margin = min(_indent(line) for line in lines if line.strip())
    kept = [line[margin:] if line.strip() else "" for line in lines]
    if style.startswith("|"):
        return "\n".join(kept).strip("\n")
    return " ".join(line.strip() for line in kept if line.strip())


def _value_of(first: str, rest: list[str]) -> Any:
    """
    A key's value from what follows its colon (`first`) and its indented lines (`rest`): a block, a mapping of
    `key: value` lines, an inline [a, b] list, or a scalar continued on the lines below.
    """
    if first in _BLOCK_STYLES:
        return _block_text(rest, first)
    if not first and rest:
        return _mapping(rest)
    if first.startswith("[") and first.endswith("]"):
        return [_unquoted(item) for item in first[1:-1].split(",") if item.strip()]
    continued = [line.strip() for line in rest if line.strip()]
    return " ".join([_unquoted(first), *continued]).strip()


def _mapping(lines: list[str]) -> dict[str, Any]:
    """
    `key: value` lines, each key at the block's own indentation, as a dict; ValueError for a line that isn't one.
    """
    body = [line for line in lines if line.strip() and not line.lstrip().startswith("#")]
    if not body:
        return {}
    margin = _indent(body[0])
    found: dict[str, Any] = {}
    key, first, rest = None, "", []
    for line in body:
        if _indent(line) > margin and key is not None:
            rest.append(line)
            continue
        if _indent(line) != margin or ":" not in line:
            raise ValueError(f"can't read the line {line.strip()!r}: front matter is 'key: value' lines")
        if key is not None:
            found[key] = _value_of(first, rest)
        key, _, first = line.strip().partition(":")
        key, first, rest = key.strip(), first.strip(), []
    found[key] = _value_of(first, rest)
    return found


def parse_front_matter(text: str) -> tuple[dict[str, Any], str]:
    """
    A SKILL.md's front matter (the `---` fenced lines at its start) as a dict, and the guide below it. ValueError
    when there's none or a line can't be read. It reads what SKILL.md files hold: `key: value` lines, quoted values,
    `>` and `|` blocks, inline [a, b] lists and one nested mapping (metadata:), not all of YAML.
    """
    lines = text.lstrip("﻿").splitlines()
    if not lines or lines[0].strip() != _FENCE:
        raise ValueError("it doesn't start with front matter: a '---' line, then name: and description:, then '---'")
    try:
        end = next(i for i in range(1, len(lines)) if lines[i].strip() == _FENCE)
    except StopIteration:
        raise ValueError("its front matter has no closing '---' line") from None
    return _mapping(lines[1:end]), "\n".join(lines[end + 1:]).strip("\n")


def check_name(name: str, folder_name: str) -> str | None:
    """
    What's wrong with a runbook's name (the Agent Skills rules: 1-64 lowercase letters, digits and single hyphens,
    the same as its folder's name), or None when it's fine.
    """
    if not name:
        return f"it has no name: add name: {folder_name} to its front matter"
    if len(name) > MAX_NAME_CHARACTERS or not _NAME.match(name):
        return (f"its name {name!r} isn't one: use lowercase letters, digits and single hyphens, at most "
                f"{MAX_NAME_CHARACTERS} characters ('lambda-failing')")
    if name != folder_name:
        return f"its name {name!r} isn't its folder's ({folder_name!r}): rename one to match"
    return None


def _toolkits_of(metadata: Any) -> tuple[str, ...]:
    """
    metadata.toolkits ('s3 lambda', 's3, lambda' or a list) as names.
    """
    if not isinstance(metadata, dict):
        return ()
    wanted = metadata.get("toolkits") or ""
    items = wanted if isinstance(wanted, list) else _LIST_SEPARATORS.split(str(wanted))
    return tuple(item.strip().lower() for item in items if item.strip())


def runbook_from_text(text: str, *, folder: Path, source: str, files: tuple[str, ...] = ()) -> Runbook:
    """
    A Runbook from its SKILL.md's text; ValueError saying what to fix when its front matter isn't right.
    """
    meta, _guide = parse_front_matter(text)
    name = str(meta.get("name") or "").strip()
    wrong = check_name(name, folder.name)
    if wrong:
        raise ValueError(wrong)
    description = " ".join(str(meta.get("description") or "").split())
    if not description:
        raise ValueError("it has no description: add one line saying what problem it's for and when to use it")
    if len(description) > MAX_DESCRIPTION_CHARACTERS:
        raise ValueError(f"its description is {len(description):,} characters: keep it under "
                         f"{MAX_DESCRIPTION_CHARACTERS:,}, and put the rest in the guide")
    return Runbook(name, description, source, folder, _toolkits_of(meta.get("metadata")), files)


def _source_label(folder: Path, taken: set[str]) -> str:
    """
    How reports name a folder of runbooks: its name, or its whole path when another source has that name.
    """
    label = folder.name or str(folder)
    return str(folder) if label in taken else label


def runbook_sources(runbooks: bool | str | Path | Iterable[str | Path] | None) -> list[RunbookSource]:
    """
    The folders runbooks= means: True for the built-in ones, False or None for none, or a folder (or a list of them)
    read after the built-in ones. '~' is the home folder.
    """
    if runbooks is False or runbooks is None:
        return []
    sources = [RunbookSource(BUILT_IN_LABEL, BUILT_IN)]
    if runbooks is True:
        return sources
    folders = [runbooks] if isinstance(runbooks, (str, Path)) else list(runbooks)
    taken = {BUILT_IN_LABEL}
    for given in folders:
        folder = Path(given).expanduser()
        label = _source_label(folder, taken)
        taken.add(label)
        sources.append(RunbookSource(label, folder, single=(folder / RUNBOOK_FILE).is_file()))
    return sources


def _other_files(folder: Path) -> tuple[str, ...]:
    """
    The files in a runbook's folder besides its SKILL.md, by their paths from the folder.
    """
    files = [path for path in folder.rglob("*") if path.is_file() and not path.is_symlink()]
    found = [path.relative_to(folder).as_posix() for path in files]
    return tuple(sorted(name for name in found if name != RUNBOOK_FILE and not name.startswith((".", "__"))))


def _read_runbook(folder: Path, source: RunbookSource) -> Runbook:
    """
    The runbook in `folder`; ValueError saying what's wrong with it.
    """
    path = folder / RUNBOOK_FILE
    if path.stat().st_size > MAX_RUNBOOK_BYTES:
        raise ValueError(f"it's over {MAX_RUNBOOK_BYTES // 1024} KB: keep the guide short and put long references in "
                         "files beside it")
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        raise ValueError("it isn't UTF-8 text") from None
    return runbook_from_text(text, folder=folder, source=source.label, files=_other_files(folder))


def _runbook_folders(source: RunbookSource) -> list[Path]:
    """
    The runbook folders in a source: itself for a single runbook, else its subfolders that hold a SKILL.md.
    """
    if source.single:
        return [source.folder]
    subfolders = sorted(path for path in source.folder.iterdir() if path.is_dir())
    return [path for path in subfolders if (path / RUNBOOK_FILE).is_file()]


def load_runbooks(sources: Iterable[RunbookSource]) -> RunbookShelf:
    """
    Every runbook in `sources`, read in order, a later one replacing an earlier one of the same name. A folder that
    isn't there, or a runbook that can't be read, is a RunbookProblem on the shelf; the rest still load.
    """
    shelf = RunbookShelf()
    for source in sources:
        shelf.sources.append(source)
        if not source.folder.is_dir():
            shelf.problems.append(RunbookProblem(str(source.folder), "there's no folder there"))
            continue
        try:
            folders = _runbook_folders(source)
        except OSError as exc:
            shelf.problems.append(RunbookProblem(str(source.folder), f"it can't be read ({exc.strerror or exc})"))
            continue
        if not folders and source.label != BUILT_IN_LABEL:
            shelf.problems.append(RunbookProblem(str(source.folder), f"it has no runbooks: each is a folder with a "
                                                                     f"{RUNBOOK_FILE} in it"))
        for folder in folders:
            _shelve(shelf, folder, source)
    return shelf


def _shelve(shelf: RunbookShelf, folder: Path, source: RunbookSource) -> None:
    """
    Puts the runbook in `folder` on the shelf, or its problem.
    """
    try:
        book = _read_runbook(folder, source)
    except (ValueError, OSError) as exc:
        shelf.problems.append(RunbookProblem(str(folder / RUNBOOK_FILE), str(exc)))
        return
    if book.name in shelf.runbooks:
        shelf.replaced[book.name] = shelf.runbooks[book.name].source
    shelf.runbooks[book.name] = book


def read_runbook_text(book: Runbook, file: str = "") -> str:
    """
    A runbook's guide (its SKILL.md without the front matter), or with `file` one of the files beside it (a path
    from its folder, one of book.files). ValueError for a file that isn't one of its files, or isn't text.
    """
    if not file.strip() or file.strip() == RUNBOOK_FILE:
        _meta, guide = parse_front_matter(book.path.read_text(encoding="utf-8"))
        return guide
    wanted = file.strip().lstrip("/")
    if wanted not in book.files:
        listed = ", ".join(book.files) or "none"
        raise ValueError(f"{book.name} has no file {file!r}; its files are: {listed}")
    path = book.folder / wanted
    if path.stat().st_size > MAX_RUNBOOK_BYTES:
        raise ValueError(f"{wanted} is over {MAX_RUNBOOK_BYTES // 1024} KB, too big to read whole")
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        raise ValueError(f"{wanted} isn't a text file") from None


def runbook_findings(shelf: RunbookShelf, toolkits: Iterable[str]) -> list[tuple[str, str]]:
    """
    What to fix or know about an agent's runbooks (an agent with these `toolkits`, by name): each one that couldn't
    be read and why, each one it doesn't offer for want of a toolkit, and each of yours that replaces a built-in one.
    """
    have = set(toolkits)
    findings = [("warn", f"A runbook wasn't loaded: {problem.path}: {problem.message}.") for problem in shelf.problems]
    for name, book in sorted(shelf.runbooks.items()):
        missing = [kit for kit in book.toolkits if kit not in have]
        if missing:
            needs = ", ".join(repr(kit) for kit in missing)
            findings.append(("info", f"{name} isn't offered to this agent: it needs the {needs} toolkit "
                                     f"(metadata.toolkits in its {RUNBOOK_FILE}), which the agent hasn't got."))
    for name, earlier in sorted(shelf.replaced.items()):
        findings.append(("info", f"{name} from {shelf.runbooks[name].source} replaces the {earlier} one of that name."))
    return findings


__all__ = ["BUILT_IN", "RUNBOOK_FILE", "Runbook", "RunbookProblem", "RunbookShelf", "RunbookSource", "check_name",
           "load_runbooks", "parse_front_matter", "runbook_findings", "read_runbook_text", "runbook_from_text", "runbook_sources"]
