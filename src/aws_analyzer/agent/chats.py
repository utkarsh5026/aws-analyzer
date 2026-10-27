"""
The agent's saved chats (AgentAnalyzer(chats=True)): every conversation kept on this machine with a title, so it can be
listed, opened again and carried on after the kernel restarts.

A chat is a conversation (its thread), titled from its first question unless the user renames it. Two things are kept
in one local SQLite file (DEFAULT_CHATS unless chats= names another): each answer as the user saw it (the question, the
answer, its findings, plan, charts and tool calls, as JSON: answer_record / answer_from_record), which the reports and
the chat window draw again, and LangGraph's checkpoints of the conversation (open_checkpointer: its SqliteSaver), which
the model reads to carry on where it left off.

ChatStore is the file's chats and their answers; the other helpers here are pure. Nothing here calls AWS. What is kept
is what the agent showed: tool results had their secrets hidden before the model saw them, and environment variable
values and files' contents are never read. delete() removes a chat when the user asks; nothing else deletes one.
"""

from __future__ import annotations

import base64
import json
import os
import sqlite3
import threading
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .._kit.deps import _require
from .._kit.errors import _Hint
from .._kit.text import _clip
from .answers import AgentAnswer, AgentFinding, BigRead, PlanStep, ToolCall
from .llm import Rates
from .workspace.charts import Chart

DEFAULT_CHATS = Path("~/.cache/aws-analyzer/agent/chats.sqlite")
TITLE_CHARACTERS = 60
UNTITLED = "Untitled chat"
_RECORD_VERSION = 1
_COLUMNS = "SELECT thread, title, started, updated, questions, cost, model FROM aws_analyzer_chats"
_SCHEMA = """
CREATE TABLE IF NOT EXISTS aws_analyzer_chats (
    thread TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    started TEXT NOT NULL,
    updated TEXT NOT NULL,
    questions INTEGER NOT NULL DEFAULT 0,
    cost REAL,
    model TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS aws_analyzer_chat_answers (
    thread TEXT NOT NULL,
    number INTEGER NOT NULL,
    record TEXT NOT NULL,
    PRIMARY KEY (thread, number)
);
"""


@dataclass
class ChatSummary:
    """
    One saved chat as a list shows it: its ID (the conversation's thread), its title, when it started and when its
    last question was asked, how many questions it holds, what its model calls cost (None when no price was known)
    and the model that answered last.
    """

    thread: str
    title: str
    started: datetime
    updated: datetime
    questions: int = 0
    cost: float | None = None
    model: str = ""


def chats_path(chats: bool | str | os.PathLike | None) -> Path | None:
    """
    The SQLite file chats= keeps chats in: DEFAULT_CHATS for True, the path given ('~' expanded), or None when saving
    chats is off (False or None).
    """
    if chats is None or chats is False:
        return None
    if chats is True:
        return DEFAULT_CHATS.expanduser()
    return Path(chats).expanduser()


def chat_title(question: str) -> str:
    """
    A chat's title from its first question: the first line, spaces tidied, clipped at a word to TITLE_CHARACTERS.
    "Which Lambda failed last night?" stays as it is; an empty question gives UNTITLED.
    """
    lines = str(question or "").strip().splitlines()
    first_line = " ".join(lines[0].split()) if lines else ""
    if not first_line:
        return UNTITLED
    if len(first_line) <= TITLE_CHARACTERS:
        return first_line
    cut = first_line[:TITLE_CHARACTERS - 1].rsplit(" ", 1)[0].rstrip(" ,;:-")
    return f"{cut or first_line[:TITLE_CHARACTERS - 1]}…"


def clean_title(title: str) -> str:
    """
    A title the user gave, on one line and at most TITLE_CHARACTERS long. ValueError when it's empty.
    """
    text = " ".join(str(title or "").split())
    if not text:
        raise ValueError("A chat's title can't be empty: rename_chat('Nightly ETL failures')")
    return _clip(text, TITLE_CHARACTERS)


def _encode(value: Any) -> Any:
    """
    What json.dumps can't write by itself: bytes (a chart's picture) as base64, times as ISO text, anything else as
    its text.
    """
    if isinstance(value, bytes):
        return {"base64": base64.b64encode(value).decode("ascii")}
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def answer_record(answer: AgentAnswer) -> str:
    """
    An answer as the JSON a saved chat keeps: everything it shows (the question, the text, findings, plan, charts, tool
    calls with what they handed the model, tokens, cost), without the exception, which can't be kept.
    """
    record = asdict(answer)
    record.pop("exception", None)
    record["version"] = _RECORD_VERSION
    return json.dumps(record, default=_encode)


def _time(text: Any) -> datetime | None:
    if not text:
        return None
    try:
        return datetime.fromisoformat(str(text))
    except ValueError:
        return None


def _picture(data: Any) -> bytes:
    if isinstance(data, dict) and "base64" in data:
        return base64.b64decode(data["base64"])
    return b""


def _tool_call(record: dict[str, Any]) -> ToolCall:
    """
    A saved tool call, with its asks; fields a newer record has and this version doesn't know are left out.
    """
    asks = [BigRead(**ask) for ask in record.pop("asks", None) or []]
    nexts = [tuple(pair) for pair in record.pop("next", None) or []]
    reads = tuple(record.pop("reads", None) or ())
    known = {name: value for name, value in record.items() if name in ToolCall.__dataclass_fields__}
    return ToolCall(**known, asks=asks, next=nexts, reads=reads)


def answer_from_record(text: str) -> AgentAnswer:
    """
    The answer a saved chat's JSON holds, as AgentAnswer (answer_record's other half). ValueError when it isn't one.
    """
    record = json.loads(text)
    if not isinstance(record, dict) or "question" not in record:
        raise ValueError("Not a saved answer: it has no question")
    rates = record.get("rates")
    return AgentAnswer(
        question=str(record["question"]),
        text=str(record.get("text") or ""),
        findings=[AgentFinding(**finding) for finding in record.get("findings") or []],
        calls=[_tool_call(dict(call)) for call in record.get("calls") or []],
        plan=[PlanStep(**step) for step in record.get("plan") or []],
        charts=[Chart(**{**chart, "data": _picture(chart.get("data"))}) for chart in record.get("charts") or []],
        aws_calls=dict(record.get("aws_calls") or {}),
        blocked=list(record.get("blocked") or []),
        metrics_read=int(record.get("metrics_read") or 0),
        aws_cost=float(record.get("aws_cost") or 0.0),
        model=str(record.get("model") or ""),
        provider=str(record.get("provider") or ""),
        model_calls=int(record.get("model_calls") or 0),
        input_tokens=int(record.get("input_tokens") or 0),
        output_tokens=int(record.get("output_tokens") or 0),
        cost=record.get("cost"),
        rates=Rates(*rates) if rates else None,
        seconds=float(record.get("seconds") or 0.0),
        stopped=record.get("stopped"),
        error=record.get("error"),
        thread=str(record.get("thread") or ""),
        asked=_time(record.get("asked")),
    )


def _matches_number(chats: list[ChatSummary], match: str) -> list[ChatSummary]:
    """
    The chat a number picks, as chats() lists them: 1 is the newest. Empty when it's out of range.
    """
    number = int(match)
    return [chats[number - 1]] if 1 <= number <= len(chats) else []


def find_chats(chats: list[ChatSummary], match: str | int) -> list[ChatSummary]:
    """
    The saved chats `match` picks: a number as the list shows it (1 is the newest), a chat's ID, its whole title
    (any case), or words in its title, newest first. ValueError when `match` is empty.
    """
    text = str(match).strip()
    if not text:
        raise ValueError("Which chat? open_chat(1) opens the newest, or name it: open_chat('nightly etl')")
    if text.isdigit() and len(text) < 4:
        return _matches_number(chats, text)
    by_id = [chat for chat in chats if chat.thread == text]
    if by_id:
        return by_id
    wanted = text.casefold()
    exact = [chat for chat in chats if chat.title.casefold() == wanted]
    if exact:
        return exact
    words = wanted.split()
    return [chat for chat in chats if all(word in chat.title.casefold() for word in words)]


class ChatStore:
    """
    The saved chats in one SQLite file: each chat's title and times, and its answers in the order they were asked.
    One connection, shared by the window's worker thread and the notebook's, behind a lock.
    """

    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._db = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self._db.executescript(_SCHEMA)

    def _rows(self, sql: str, params: Iterable[Any] = ()) -> list[tuple[Any, ...]]:
        with self._lock:
            return list(self._db.execute(sql, tuple(params)))

    def save(self, answer: AgentAnswer) -> None:
        """
        Adds an answer to its chat, which starts with this answer's question as its title when it's new.
        """
        moment = (answer.asked or datetime.now(timezone.utc)).isoformat()
        with self._lock:
            self._db.execute("BEGIN")
            self._db.execute("INSERT OR IGNORE INTO aws_analyzer_chats (thread, title, started, updated) "
                             "VALUES (?, ?, ?, ?)", (answer.thread, chat_title(answer.question), moment, moment))
            self._db.execute("UPDATE aws_analyzer_chats SET updated = ?, questions = questions + 1, model = ?, "
                             "cost = CASE WHEN ? IS NULL THEN cost ELSE COALESCE(cost, 0) + ? END WHERE thread = ?",
                             (moment, answer.model, answer.cost, answer.cost, answer.thread))
            self._db.execute("INSERT INTO aws_analyzer_chat_answers (thread, number, record) VALUES (?, "
                             "(SELECT COUNT(*) FROM aws_analyzer_chat_answers WHERE thread = ?), ?)",
                             (answer.thread, answer.thread, answer_record(answer)))
            self._db.execute("COMMIT")

    def answers(self, thread: str) -> list[AgentAnswer]:
        """
        A chat's answers, in the order they were asked. One that can't be read (a broken record) is left out.
        """
        rows = self._rows("SELECT record FROM aws_analyzer_chat_answers WHERE thread = ? ORDER BY number", (thread,))
        answers: list[AgentAnswer] = []
        for (record,) in rows:
            try:
                answers.append(answer_from_record(record))
            except (ValueError, TypeError, KeyError):
                continue
        return answers

    @staticmethod
    def _summary(row: tuple[Any, ...]) -> ChatSummary:
        thread, title, started, updated, questions, cost, model = row
        return ChatSummary(thread, title, _time(started) or datetime.now(timezone.utc),
                           _time(updated) or datetime.now(timezone.utc), questions=int(questions or 0),
                           cost=cost, model=model or "")

    def chats(self) -> list[ChatSummary]:
        """
        Every saved chat, the one asked in last first.
        """
        rows = self._rows(f"{_COLUMNS} ORDER BY updated DESC")
        return [self._summary(row) for row in rows]

    def chat(self, thread: str) -> ChatSummary | None:
        """
        One saved chat by its ID, or None when it isn't saved (nothing asked in it yet).
        """
        rows = self._rows(f"{_COLUMNS} WHERE thread = ?", (thread,))
        return self._summary(rows[0]) if rows else None

    def rename(self, thread: str, title: str) -> bool:
        """
        Gives a chat a new title; False when the chat isn't saved.
        """
        with self._lock:
            changed = self._db.execute("UPDATE aws_analyzer_chats SET title = ? WHERE thread = ?",
                                       (clean_title(title), thread)).rowcount
        return changed > 0

    def delete(self, thread: str) -> bool:
        """
        Removes a chat and its answers from the file; False when it wasn't there.
        """
        with self._lock:
            self._db.execute("BEGIN")
            self._db.execute("DELETE FROM aws_analyzer_chat_answers WHERE thread = ?", (thread,))
            removed = self._db.execute("DELETE FROM aws_analyzer_chats WHERE thread = ?", (thread,)).rowcount
            self._db.execute("COMMIT")
        return removed > 0


def open_checkpointer(path: Path) -> Any:
    """
    LangGraph's SqliteSaver in the chats' file, so the model carries on a chat opened again after the kernel
    restarted (made, with its folder, when it isn't there). One connection, shared by the threads the agent runs on:
    the saver holds a lock around every statement.
    """
    savers = _require("langgraph.checkpoint.sqlite", "The agent's saved chats", package="langgraph-checkpoint-sqlite")
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(path), check_same_thread=False)
    saver = savers.SqliteSaver(connection)
    saver.setup()
    return saver


def no_chats_hint() -> _Hint:
    """
    What to do when chats aren't being saved.
    """
    return _Hint("Chats aren't saved: AgentView(chats=True) keeps every conversation, with a title, in "
                 "~/.cache/aws-analyzer/agent/chats.sqlite on this machine, so chats() lists them and open_chat() "
                 "carries one on, even after the kernel restarts.")
