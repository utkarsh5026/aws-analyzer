"""
A search across this notebook's files, the pure side of search_files() and read_file(): how a search is read
(words and "quoted phrases" a file must all hold, -words it mustn't, case ignored unless a word has a capital, or one
regular expression), what kind each file is, which folders and files are never read (environments, caches, hidden
folders, secrets files), a notebook turned into lines of text, and how a file is matched and ranked. Nothing here
reads the disk.
"""

from __future__ import annotations

import difflib
import fnmatch
import json
import posixpath
import re
from dataclasses import dataclass, field
from typing import Any, Iterable

from ..models import FileHit, LineHit

MOST_TERMS = 8

_QUERY_CHARACTERS = 200

_LINES_PER_FILE = 8

_CANDIDATE_LINES = 400

_CONTEXT_LINES = 1

_LINE_CHARACTERS = 400

_OUTPUT_LINES = 40

_REGEX_LINE_CHARACTERS = 2000

_NEAR_FILES = 10

_SUGGESTIONS = 5

_TERM_RE = re.compile(r'(-?)"([^"]+)"|(\S+)')

_NESTED_REPEAT_RE = re.compile(r"\([^()]*[*+}][^()]*\)[*+{]")

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")

_WORD_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{2,}")

FILE_KINDS: dict[str, str] = {
    ".ipynb": "notebook", ".py": "python", ".pyi": "python",
    ".r": "code", ".scala": "code", ".java": "code", ".js": "code", ".ts": "code", ".sh": "code", ".go": "code",
    ".jl": "code", ".sql": "sql",
    ".csv": "data", ".tsv": "data", ".jsonl": "data", ".ndjson": "data", ".psv": "data",
    ".json": "config", ".yaml": "config", ".yml": "config", ".toml": "config", ".ini": "config", ".cfg": "config",
    ".conf": "config", ".env.example": "config",
    ".md": "docs", ".txt": "docs", ".rst": "docs", ".html": "docs", ".tex": "docs",
    ".log": "log", ".out": "log", ".err": "log",
}

KIND_NAMES: dict[str, tuple[str, ...]] = {
    "notebook": ("notebook",), "notebooks": ("notebook",), "python": ("python",), "py": ("python",),
    "code": ("python", "code", "sql", "notebook"), "sql": ("sql",), "data": ("data",), "config": ("config",),
    "docs": ("docs",), "doc": ("docs",), "logs": ("log",), "log": ("log",),
}

_KIND_WEIGHT = {"notebook": 2.0, "python": 2.0, "code": 2.0, "sql": 2.0, "config": 1.0, "docs": 1.0}

SKIPPED_FOLDERS = frozenset({
    "__pycache__", "node_modules", "site-packages", "dist-packages", "lost+found", "anaconda3", "miniconda3",
    "miniforge3", "mambaforge", "micromamba",
})

ENVIRONMENT_MARKERS = ("conda-meta", "pyvenv.cfg")

_SECRET_NAME_RE = re.compile(
    r"(^|\.)(env|netrc|pgpass|htpasswd)$|\.(pem|key|p12|pfx|jks|keystore|kdbx)$|^id_(rsa|dsa|ecdsa|ed25519)"
    r"|^credentials(\.\w+)?$|^secrets?\.(json|ya?ml|toml|ini|txt)$|^\.env\.",
    re.IGNORECASE,
)

_SECRET_FOLDERS = frozenset({".aws", ".ssh", ".gnupg", ".docker", ".kube"})


def file_kind(path: str) -> str:
    """
    What a file is, from its name: 'notebook', 'python', 'code', 'sql', 'data', 'config', 'docs', 'log', or 'other'.
    """
    name = posixpath.basename(path).lower()
    for ending, kind in FILE_KINDS.items():
        if name.endswith(ending):
            return kind
    return "other"


def is_secret_file(name: str) -> bool:
    """
    Whether a file's name says it holds credentials (.env, .netrc, *.pem, id_rsa, credentials, secrets.json, ...),
    which a search never reads.
    """
    return bool(_SECRET_NAME_RE.search(name))


def is_skipped_folder(name: str) -> bool:
    """
    Whether a search never goes into a folder of this name: hidden ones (.git, .ipynb_checkpoints, .cache, .local,
    the trash), credentials (.aws, .ssh), and installed packages (__pycache__, node_modules, site-packages, conda
    installations).
    """
    return name.startswith(".") or name in SKIPPED_FOLDERS or name in _SECRET_FOLDERS


def is_environment(names: Iterable[str]) -> bool:
    """
    Whether a folder holding these names is a Python environment (conda-meta, pyvenv.cfg), whose installed
    packages a search leaves out.
    """
    return any(marker in names for marker in ENVIRONMENT_MARKERS)


def _wanted_kinds(part: str) -> tuple[str, ...]:
    return KIND_NAMES.get(part.strip().lower(), ())


def wants_file(path: str, files: str) -> bool:
    """
    Whether a file is one files= asks for: empty for every file, else parts separated by commas, each a kind
    ('notebooks', 'python', 'code', 'data', 'config', 'docs', 'logs'), a glob over the path or the name ('*.csv',
    'churn/*') or a folder ('churn/') it's under.
    """
    parts = [part.strip() for part in files.split(",") if part.strip()]
    if not parts:
        return True
    kind = file_kind(path)
    for part in parts:
        if kind in _wanted_kinds(part):
            return True
        folder = part.rstrip("/").lstrip("./")
        if folder and (path == folder or path.startswith(folder + "/")):
            return True
        glob = part.lower()
        if fnmatch.fnmatch(path.lower(), glob) or fnmatch.fnmatch(posixpath.basename(path).lower(), glob):
            return True
    return False


def unknown_files_parts(files: str) -> list[str]:
    """
    The parts of files= that are neither a kind, a glob nor a path: a word such as 'tables' that matches nothing.
    """
    unknown = []
    for part in (p.strip() for p in files.split(",")):
        looks_like_path = any(mark in part for mark in "*?/.[")
        if part and not looks_like_path and not _wanted_kinds(part):
            unknown.append(part)
    return unknown


@dataclass
class FileQuery:
    """
    A search as search_files() reads it: words and phrases a file must all hold (terms), words it mustn't
    (excluded), whether case counts (only when a word has a capital), or one regular expression (pattern).
    """

    text: str
    terms: list[str] = field(default_factory=list)
    excluded: list[str] = field(default_factory=list)
    case_sensitive: bool = False
    pattern: re.Pattern[str] | None = None

    def fold(self, text: str) -> str:
        """
        The text as the search compares it: lowercased unless case counts.
        """
        return text if self.case_sensitive else text.lower()

    def terms_in(self, text: str) -> set[int]:
        """
        Which of the terms (by place) a line or a path holds.
        """
        if self.pattern is not None:
            return {0} if self.pattern.search(text[:_REGEX_LINE_CHARACTERS]) else set()
        folded = self.fold(text)
        return {place for place, term in enumerate(self.terms) if term in folded}

    def excludes(self, text: str) -> bool:
        """
        Whether a line holds a word the search left out ('-test').
        """
        folded = self.fold(text)
        return any(word in folded for word in self.excluded)

    @property
    def wanted(self) -> list[str]:
        """
        What a file must hold, as the user wrote it: the words and phrases, or the regular expression.
        """
        return [self.pattern.pattern] if self.pattern is not None else self.terms


def _compiled(text: str) -> re.Pattern[str]:
    """
    A search's regular expression, refusing one that can run for hours on a long line ('(a+)+').
    """
    if _NESTED_REPEAT_RE.search(text):
        raise ValueError(f"{text!r} repeats a group that repeats itself, which can run for hours on a long line: "
                         "write it without the nested repeat")
    try:
        return re.compile(text)
    except re.error as exc:
        raise ValueError(f"{text!r} isn't a regular expression Python reads: {exc}") from None


def _split_terms(text: str) -> tuple[list[str], list[str]]:
    """
    'churn "feature store" -test' -> (['churn', 'feature store'], ['test']).
    """
    terms: list[str] = []
    excluded: list[str] = []
    for match in _TERM_RE.finditer(text):
        is_phrase = match[2] is not None
        word = match[2] if is_phrase else match[3]
        is_left_out = (match[1] == "-") if is_phrase else (word.startswith("-") and len(word) > 1)
        if is_left_out and not is_phrase:
            word = word[1:]
        target = excluded if is_left_out else terms
        if word and word not in target:
            target.append(word)
    return terms, excluded


def parse_file_query(text: str, *, regex: bool = False) -> FileQuery:
    """
    A search's text as search_files() reads it: words and "quoted phrases" every file found holds somewhere
    (in its text or its path), -words none holds, case ignored unless a word has a capital letter; or with
    regex=True the whole text as one Python regular expression ('(?i)' first ignores case). A ValueError says
    what's wrong.
    """
    text = str(text or "").strip()
    if not text:
        raise ValueError("Give words to look for, such as 'churn model' or '\"KeyError\" -test'")
    if len(text) > _QUERY_CHARACTERS:
        raise ValueError(f"The search is {len(text):,} characters; keep it under {_QUERY_CHARACTERS}")
    if regex:
        return FileQuery(text, case_sensitive=True, pattern=_compiled(text))
    terms, excluded = _split_terms(text)
    if not terms:
        raise ValueError(f"{text!r} only leaves words out: give at least one word to look for")
    if len(terms) > MOST_TERMS:
        raise ValueError(f"{len(terms)} words: give at most {MOST_TERMS}, the ones that matter most")
    case_sensitive = any(ch.isupper() for term in terms for ch in term)
    query = FileQuery(text, case_sensitive=case_sensitive)
    query.terms = [query.fold(term) for term in terms]
    query.excluded = [query.fold(word) for word in excluded]
    return query


def _cell_text(value: Any) -> list[str]:
    """
    A notebook field that holds text, as lines: Jupyter writes it as a list of lines or as one string.
    """
    if isinstance(value, list):
        value = "".join(str(part) for part in value)
    return str(value or "").splitlines()


def _output_lines(output: dict[str, Any]) -> list[str]:
    """
    The text one output of a code cell shows: what it printed, its value as text, or the error and its
    traceback (colours taken out). Pictures and HTML aren't text, so they're left out.
    """
    kind = output.get("output_type")
    if kind == "stream":
        return _cell_text(output.get("text"))
    if kind in ("execute_result", "display_data"):
        return _cell_text((output.get("data") or {}).get("text/plain"))
    if kind == "error":
        header = f"{output.get('ename', 'Error')}: {output.get('evalue', '')}"
        lines = [header]
        for frame in output.get("traceback") or []:
            lines.extend(_ANSI_RE.sub("", str(frame)).splitlines())
        return [header] + [line for line in lines[1:] if line.strip() != header]
    return []


def _cell_rows(number: int, cell: dict[str, Any]) -> list[tuple[str, str]]:
    """
    One notebook cell as (cell, line) rows: a '# %% cell 3 [code]' line, its source, then each text output's first
    _OUTPUT_LINES lines marked '# >> '.
    """
    label = f"cell {number}"
    rows = [(label, f"# %% {label} [{cell.get('cell_type', 'code')}]")]
    rows.extend((label, line) for line in _cell_text(cell.get("source")))
    for output in cell.get("outputs") or []:
        shown = _output_lines(output if isinstance(output, dict) else {})
        rows.extend((f"{label} output", f"# >> {line}") for line in shown[:_OUTPUT_LINES])
        if len(shown) > _OUTPUT_LINES:
            rows.append((f"{label} output", f"# >> ... {len(shown) - _OUTPUT_LINES:,} more lines"))
    return rows


def notebook_rows(raw: str) -> list[tuple[str, str]] | None:
    """
    A Jupyter notebook's text as (cell, line) rows, the way read_file() shows it: each cell under a
    '# %% cell 3 [code]' line, then its text outputs (printed lines, values, errors with their tracebacks) marked
    '# >> '. None when it isn't a notebook's JSON.
    """
    try:
        data = json.loads(raw)
    except ValueError:
        return None
    cells = data.get("cells") if isinstance(data, dict) else None
    if not isinstance(cells, list):
        return None
    rows: list[tuple[str, str]] = []
    for number, cell in enumerate(cells, 1):
        if isinstance(cell, dict):
            rows.extend(_cell_rows(number, cell))
    return rows


def text_rows(text: str) -> list[tuple[str, str]]:
    """
    A plain file's lines as (cell, line) rows, the cell always ''.
    """
    return [("", line) for line in text.splitlines()]


def _best_lines(rows: list[tuple[str, str]], candidates: list[tuple[int, int]]) -> list[LineHit]:
    """
    The _LINES_PER_FILE matching lines worth showing, those holding the most words first, in file order, each with
    _CONTEXT_LINES lines around it. `candidates` are (line number, words on it).
    """
    best = sorted(candidates, key=lambda candidate: (-candidate[1], candidate[0]))[:_LINES_PER_FILE]
    hits = []
    for number, terms in sorted(best):
        first, last = max(1, number - _CONTEXT_LINES), min(len(rows), number + _CONTEXT_LINES)
        context = [(n, rows[n - 1][1][:_LINE_CHARACTERS]) for n in range(first, last + 1) if n != number]
        cell, text = rows[number - 1]
        hits.append(LineHit(number, text[:_LINE_CHARACTERS], terms, cell, context))
    return hits


def match_file(path: str, rows: list[tuple[str, str]], query: FileQuery) -> FileHit | None:
    """
    How one file matches a search: None when it holds none of the words or one it should leave out; otherwise a
    FileHit with its best lines, how many lines matched, whether its path holds words, and the words it lacks
    (when it lacks any, it's a near miss). A word counts when it's in the file's path or anywhere in its text, so
    'churn train' finds train.py in a churn folder.
    """
    present = query.terms_in(path)
    in_name = bool(present)
    candidates: list[tuple[int, int]] = []
    matching = 0
    for number, (_, text) in enumerate(rows, 1):
        if query.excluded and query.excludes(text):
            return None
        found = query.terms_in(text)
        if not found:
            continue
        present |= found
        matching += 1
        if len(candidates) < _CANDIDATE_LINES:
            candidates.append((number, len(found)))
    if not present or query.excludes(path):
        return None
    wanted = query.wanted
    missing = [wanted[place] for place in range(len(wanted)) if place not in present]
    return FileHit(path, file_kind(path), 0, None, _best_lines(rows, candidates), matching, in_name, missing)


def hit_score(hit: FileHit, wanted: int) -> float:
    """
    How good a find is, higher first: a path holding the words (less when only the name matched, as in a binary
    file), a line holding all of them together, then how many words one line holds and how many lines matched, and notebooks and code over data and logs.
    """
    most_on_a_line = max((line.terms for line in hit.lines), default=0)
    together = wanted > 1 and most_on_a_line == wanted
    name_bonus = (3.0 if hit.lines else 1.5) if hit.in_name else 0.0
    return (name_bonus + (5.0 if together else 0.0) + most_on_a_line
            + min(hit.matching_lines, 20) / 10 + _KIND_WEIGHT.get(hit.kind, 0.0) - 3.0 * len(hit.missing))


def _newest_first(hit: FileHit) -> float:
    return -hit.modified.timestamp() if hit.modified else 0.0


def rank_hits(hits: list[FileHit]) -> list[FileHit]:
    """
    Finds the best first (hit_score), the newest first among equals.
    """
    return sorted(hits, key=lambda hit: (-hit.score, _newest_first(hit), hit.path))


def near_misses(hits: list[FileHit]) -> list[FileHit]:
    """
    The _NEAR_FILES files that hold the most of the words, for when none holds all of them.
    """
    return sorted(hits, key=lambda hit: (len(hit.missing), -hit.score, hit.path))[:_NEAR_FILES]


def words_of(text: str) -> set[str]:
    """
    The names in a line of code or text (three characters or more), for close_words().
    """
    return set(_WORD_RE.findall(text))


def close_words(missing: list[str], vocabulary: set[str], *, case_sensitive: bool = False) -> list[str]:
    """
    Words in the files read that are spelled like the words found nowhere: 'chrun' -> 'churn',
    'load_order' -> 'load_orders'.
    """
    pool = vocabulary if case_sensitive else {word.lower() for word in vocabulary}
    suggestions: list[str] = []
    for word in missing:
        for close in difflib.get_close_matches(word, pool, n=3, cutoff=0.75):
            if close != word and close not in suggestions:
                suggestions.append(close)
    return suggestions[:_SUGGESTIONS]
