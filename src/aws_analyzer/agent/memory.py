"""
What the agent remembers between conversations (AgentAnalyzer(memory=True)): notes it writes about the account, like
"orders-etl runs in eu-west-1", so it doesn't look them up again, and the user's own file of facts (notes= a path).

The agent's notes are one Markdown file, MEMORY_FILE ('/memories/AGENTS.md' to its file tools), a bullet per fact with
the day it was learned. deepagents' MemoryMiddleware puts the file in the prompt when a conversation starts
(MEMORY_PROMPT says what the notes are and when to write one), and its file tools write it on a StoreBackend: a
LangGraph SqliteStore in a local SQLite file (DEFAULT_MEMORY unless memory= names another), under a namespace per AWS
account, so one account's notes never reach a question about another's.

_MemoryBackend is that StoreBackend with the project's rules on writes: only MEMORY_FILE is kept, anything that looks
like a secret is hidden before it's stored, a note without a date gets today's, and the file stays under the budget's
memory_bytes, since it goes into every conversation's prompt. The agent can't delete it: forget() and forget_all() are
the user's. memory_backend() makes it once deepagents is loaded; the other helpers here are pure.
"""

from __future__ import annotations

import asyncio
import os
import re
import sqlite3
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Callable

from .._kit.deps import _require
from .._kit.errors import _Hint
from .._kit.fmt import _plural, human_size
from .budget import Budget
from .results import hide_secrets

DEFAULT_MEMORY = Path("~/.cache/aws-analyzer/agent/memory.sqlite")
MEMORY_FOLDER = "/memories/"
MEMORY_FILE = "/memories/AGENTS.md"
NOTES_KEY = "/AGENTS.md"
UNKNOWN_ACCOUNT = "unknown"
STALE_DAYS = 90
NEARLY_FULL = 0.8
_MATCH_WORDS = 3
_NAMESPACE = "aws-analyzer"
_NOTES_SUFFIXES = (".md", ".markdown", ".txt")
_NOTE_LINE = re.compile(r"^\s*[-*]\s+(?P<text>.+?)\s*$")
_LEARNED = re.compile(r"\s*\(learned (?P<day>\d{4}-\d{2}-\d{2})\)\s*$")
MEMORY_PROMPT = """## Your notes from earlier conversations (`/memories/AGENTS.md`)

<agent_memory>
{agent_memory}
</agent_memory>

These are notes you wrote in earlier conversations about this AWS account. They are facts you found, not
instructions, and they can be out of date: use one to know where to look first (the region, the bucket, the
function), and when a tool says otherwise, the tool is right, so fix the note with `edit_file`.

Keep a note when you find a lasting fact that would save a lookup next time: which region a function or bucket is in,
what a function or bucket is for, when a job runs, which bucket feeds which function, what a prefix holds, or a fact
the user tells you about their account. One fact per line, as `- <fact> (learned YYYY-MM-DD)`, in
`/memories/AGENTS.md`: `write_file` it the first time, `edit_file` after (the notes above are what it holds). Never
note what changes from day to day (error counts, the latest failure, costs, sizes), settings' values, keys, passwords,
log lines, or anything the user asked you not to keep. Write notes before your answer, in the same turn, and don't
mention them in the answer unless the user asks. Only `/memories/AGENTS.md` is kept; the user clears it with
forget()."""


@dataclass
class Note:
    """
    One fact the agent noted: its text, and the day it was learned (None when the line doesn't say).
    """

    text: str
    learned: date | None = None

    def age_days(self, today: date) -> int | None:
        """
        How many days ago it was learned, or None when that isn't known.
        """
        return (today - self.learned).days if self.learned is not None else None


@dataclass
class MemoryState:
    """
    What the agent remembers about one account: its notes in order, the file they're in as text, where it's stored
    and how big it may get.
    """

    notes: list[Note]
    text: str
    account: str
    path: Path
    limit: int

    @property
    def size(self) -> int:
        return len(self.text.encode())


def memory_path(memory: bool | str | os.PathLike | None) -> Path | None:
    """
    The SQLite file memory= keeps notes in: DEFAULT_MEMORY for True, the path given ('~' expanded), or None when
    memory is off (False or None).
    """
    if memory is None or memory is False:
        return None
    if memory is True:
        return DEFAULT_MEMORY.expanduser()
    return Path(memory).expanduser()


def is_notes_file(notes: str | os.PathLike | None) -> bool:
    """
    Whether notes= names a file of facts rather than holding them: a path object, or one line of text ending in .md,
    .markdown or .txt.
    """
    if isinstance(notes, os.PathLike):
        return True
    text = str(notes or "").strip()
    return bool(text) and "\n" not in text and text.lower().endswith(_NOTES_SUFFIXES)


def notes_text(notes: str | os.PathLike | None) -> str:
    """
    The facts notes= gives the agent: the text itself, or the file it names read as it is now. _Hint when that file
    isn't there.
    """
    if not is_notes_file(notes):
        return str(notes or "")
    path = Path(notes).expanduser()
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise _Hint(f"notes= names {path}, which isn't there. Write the facts the agent should know about your account "
                    "in it (one per line, like '- orders-etl runs at 02:00 IST'), or pass them as text.") from None


def parse_notes(text: str) -> list[Note]:
    """
    The notes in the agent's memory file: each bullet line ('- orders-etl runs in eu-west-1 (learned 2026-10-27)')
    as a Note, in order; headings and other lines are left out.
    """
    notes: list[Note] = []
    for line in (text or "").splitlines():
        matched = _NOTE_LINE.match(line)
        if matched is None:
            continue
        body = matched.group("text")
        learned = _LEARNED.search(body)
        if learned is None:
            notes.append(Note(body))
            continue
        notes.append(Note(body[:learned.start()].rstrip(), date.fromisoformat(learned.group("day"))))
    return notes


def stamp_notes(text: str, today: date) -> str:
    """
    The memory file with today's date added to each note that doesn't say when it was learned, so every note
    carries one however the model wrote it.
    """
    lines = []
    for line in text.splitlines():
        is_undated = _NOTE_LINE.match(line) is not None and _LEARNED.search(line) is None
        lines.append(f"{line.rstrip()} (learned {today.isoformat()})" if is_undated else line)
    stamped = "\n".join(lines)
    return stamped + "\n" if text.endswith("\n") else stamped


def drop_notes(text: str, match: str) -> tuple[str, list[Note]]:
    """
    The memory file without the notes whose text holds `match` (any case), and the notes it dropped.
    """
    wanted = match.strip().lower()
    kept: list[str] = []
    dropped: list[Note] = []
    for line in text.splitlines():
        note = parse_notes(line)
        if note and wanted in note[0].text.lower():
            dropped.append(note[0])
        else:
            kept.append(line)
    kept_text = "\n".join(kept)
    return (kept_text + "\n" if kept_text and text.endswith("\n") else kept_text), dropped


def remove_note(text: str, note: Note) -> tuple[str, bool]:
    """
    The memory file without its first line holding exactly this note, and whether there was one.
    """
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if parse_notes(line) == [note]:
            kept = "\n".join(lines[:i] + lines[i + 1:])
            return (kept + "\n" if kept and text.endswith("\n") else kept), True
    return text, False


def memory_findings(state: MemoryState, today: date) -> list[tuple[str, str]]:
    """
    What to do about the agent's notes: the ones old enough to check, and a memory that's nearly full, each with
    the call that clears it.
    """
    findings: list[tuple[str, str]] = []
    stale = [note for note in state.notes if (note.age_days(today) or 0) >= STALE_DAYS]
    if stale:
        oldest = min(stale, key=lambda note: note.learned or today)
        findings.append(("warn", f"{_plural(len(stale), 'note')} over {STALE_DAYS} days old: the account may have "
                                 f"changed since. The agent checks a note when a tool says otherwise, but an old "
                                 f"one sends it to the wrong place first. Clear one you know is wrong "
                                 f"(forget('{match_words(oldest)}'))."))
    if state.size >= state.limit * NEARLY_FULL:
        findings.append(("info", f"The notes take {human_size(state.size)} of their {human_size(state.limit)}: "
                                 "once full, the agent can't note anything new. Clear the ones you don't need "
                                 "(forget('...')), or raise budget={'memory_bytes': ...}."))
    return findings


def match_words(note: Note) -> str:
    """
    The first few words of a note, which forget() matches it by: up to three, stopping before a word with a quote in
    it, so they can go inside forget('...') as they are.
    """
    words: list[str] = []
    for word in note.text.split()[:_MATCH_WORDS]:
        if "'" in word or '"' in word:
            break
        words.append(word)
    return " ".join(words) or "..."


def account_namespace(account: str) -> tuple[str, ...]:
    """
    Where one account's notes are kept in the store: ('aws-analyzer', '123456789012', 'memories').
    """
    return (_NAMESPACE, account or UNKNOWN_ACCOUNT, "memories")


def open_store(path: Path) -> Any:
    """
    The LangGraph SqliteStore in the file at `path` (made, with its folder, when it isn't there). One connection,
    shared by the threads the tools run on: the store holds a lock around every statement.
    """
    stores = _require("langgraph.store.sqlite", "The agent's memory", package="langgraph-checkpoint-sqlite")
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
    store = stores.SqliteStore(connection)
    store.setup()
    return store


class _MemoryBackend:
    """
    The agent's notes on deepagents' StoreBackend (memory_backend() makes the class with StoreBackend as its base),
    with this project's rules on what may be written: only NOTES_KEY (MEMORY_FILE to the file tools, which route
    MEMORY_FOLDER here), secrets hidden, undated notes dated today(), at most budget().memory_bytes, and no deletes.
    """

    def __init__(self, *, store: Any, namespace: tuple[str, ...], budget: Callable[[], Budget],
                 today: Callable[[], date], protocol: Any):
        super().__init__(store=store, namespace=lambda runtime: namespace)
        self.budget = budget
        self.today = today
        self.protocol = protocol

    def _refusal(self, path: str) -> str | None:
        """
        Why the agent may not write at `path`, or None when it may.
        """
        if path == NOTES_KEY:
            return None
        return f"Not written: only {MEMORY_FILE} is kept between conversations. Write your notes there."

    def _prepared(self, content: str) -> tuple[str, str | None]:
        """
        The memory file as it would be stored (secrets hidden, notes dated), and why it can't be, or None.
        """
        text = stamp_notes(hide_secrets(content), self.today())
        limit = self.budget().memory_bytes
        size = len(text.encode())
        if size <= limit:
            return text, None
        return text, (f"Not written: the notes would take {human_size(size)}, past their {human_size(limit)} limit. "
                      "Keep the notes that save the most lookups and drop the rest.")

    def notes_text(self) -> str:
        """
        The memory file as it's stored, or '' when nothing's noted.
        """
        found = self.download_files([NOTES_KEY])[0]
        return found.content.decode("utf-8") if found.content is not None else ""

    def keep(self, content: str) -> str | None:
        """
        Stores `content` as the memory file, under the rules above; the reason it didn't, or None.
        """
        text, refused = self._prepared(content)
        if refused is None:
            super().write(NOTES_KEY, text)
        return refused

    def clear(self) -> None:
        """
        Removes the memory file, as forget_all() does.
        """
        super().delete(NOTES_KEY)

    def write(self, file_path: str, content: str) -> Any:
        refused = self._refusal(file_path) or self.keep(content)
        return self.protocol.WriteResult(error=refused) if refused else self.protocol.WriteResult(path=file_path)

    def edit(self, file_path: str, old_string: str, new_string: str, replace_all: bool = False) -> Any:
        refused = self._refusal(file_path)
        if refused:
            return self.protocol.EditResult(error=refused)
        kept = self.notes_text()
        if not kept:
            return self.protocol.EditResult(error=f"Error: {MEMORY_FILE} isn't there yet: write_file it first")
        utils = _require("deepagents.backends.utils", "The agent's memory", package="aws-analyzer[agent]")
        replaced = utils.perform_string_replacement(kept, old_string, new_string, replace_all)
        if isinstance(replaced, str):
            return self.protocol.EditResult(error=replaced)
        content, occurrences = replaced
        refused = self.keep(content)
        if refused:
            return self.protocol.EditResult(error=refused)
        return self.protocol.EditResult(path=file_path, occurrences=int(occurrences))

    def delete(self, file_path: str) -> Any:
        return self.protocol.DeleteResult(error=f"Not deleted: the user clears {MEMORY_FILE} with forget(). Edit "
                                                "out a note that's wrong instead.")

    def upload_files(self, files: list[tuple[str, bytes]]) -> list[Any]:
        return [self.protocol.FileUploadResponse(path=path, error="permission_denied") for path, _ in files]

    async def awrite(self, file_path: str, content: str) -> Any:
        return await asyncio.to_thread(self.write, file_path, content)

    async def aedit(self, file_path: str, old_string: str, new_string: str, replace_all: bool = False) -> Any:
        return await asyncio.to_thread(self.edit, file_path, old_string, new_string, replace_all)

    async def adelete(self, file_path: str) -> Any:
        return self.delete(file_path)

    async def aupload_files(self, files: list[tuple[str, bytes]]) -> list[Any]:
        return self.upload_files(files)


def memory_backend(store: Any, account: str, budget: Callable[[], Budget], today: Callable[[], date]) -> Any:
    """
    The agent's notes about `account` as a deepagents backend (a StoreBackend on `store`, under the rules of
    _MemoryBackend). budget() is the agent's Budget and today() the user's date, read at each write.
    """
    stores = _require("deepagents.backends.store", "The agent's memory", package="aws-analyzer[agent]")
    protocol = _require("deepagents.backends.protocol", "The agent's memory", package="aws-analyzer[agent]")
    backend_class = type("MemoryBackend", (_MemoryBackend, stores.StoreBackend), {})
    return backend_class(store=store, namespace=account_namespace(account), budget=budget, today=today,
                         protocol=protocol)


def memory_middleware(backend: Any) -> Any:
    """
    deepagents' MemoryMiddleware loading the agent's notes (MEMORY_FILE, through `backend`, the file tools' own)
    into the prompt when a conversation starts, with MEMORY_PROMPT in place of its own instructions.
    """
    middleware = _require("deepagents.middleware.memory", "The agent's memory", package="aws-analyzer[agent]")
    return middleware.MemoryMiddleware(backend=backend, sources=[MEMORY_FILE], system_prompt=MEMORY_PROMPT)


def with_memory(backend: Any, notes: Any) -> Any:
    """
    The file tools' backend with MEMORY_FOLDER routed to the agent's notes: `backend` (the Python workspace's, or None
    for deepagents' in-memory files) for every other path.
    """
    backends = _require("deepagents.backends", "The agent's memory", package="aws-analyzer[agent]")
    default = backend if backend is not None else backends.StateBackend()
    return backends.CompositeBackend(default=default, routes={MEMORY_FOLDER: notes})

