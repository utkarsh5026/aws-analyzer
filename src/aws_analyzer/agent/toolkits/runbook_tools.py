"""
The Runbooks toolkit: the agent's step-by-step guides (runbooks/__init__.py), offered in the system prompt by name and
one-line description only, and read whole with read_runbook when a question needs one. It reads nothing from AWS:
its runbooks are local files, the built-in ones and the folders AgentAnalyzer(runbooks=...) adds.

summarize_runbook() is pure: read_runbook's answer for a runbook and the text read from it.
"""

from __future__ import annotations

from typing import Any, Callable

import boto3

from ..budget import Budget
from ..results import hide_secrets
from ..runbooks import Runbook, RunbookShelf, RunbookSource, load_runbooks, read_runbook_text
from .base import Toolkit

_FILES_LISTED = 30


def summarize_runbook(book: Runbook, text: str, file: str = "") -> dict[str, Any]:
    """
    read_runbook()'s answer: the runbook's name, where it's from and its guide (or the file read from beside it), with
    anything that looks like a secret hidden, and the other files it has.
    """
    shown = file.strip() or "the guide"
    out: dict[str, Any] = {"runbook": book.name, "source": book.source, "file": file.strip() or "SKILL.md",
                           "text": hide_secrets(text)}
    if book.files:
        out["files"] = list(book.files[:_FILES_LISTED])
        out["note"] = "read_runbook(name, file=...) reads one of its files when the guide says to."
    out["_summary"] = f"{book.name} ({shown}, {book.source})"
    return out


class RunbookToolkit(Toolkit):
    """
    The agent's runbooks, read from `sources` (RunbookSource folders, runbook_sources() of runbooks=). It has no
    analyzer and reads nothing from AWS. `shelf` is what was found, read once; a runbook's guide is read from its
    file each time it's asked for, so an edited guide is followed at once (a new runbook needs a new agent).
    """

    name = "runbooks"
    title = "Runbooks"
    reads = {"read_runbook": ()}
    view = "AgentView"

    def __init__(self, sources: list[RunbookSource] | None = None):
        super().__init__()
        self.sources = list(sources or [])
        self._shelf: RunbookShelf | None = None

    @property
    def shelf(self) -> RunbookShelf:
        if self._shelf is None:
            self._shelf = load_runbooks(self.sources)
        return self._shelf

    @property
    def offered(self) -> list[Runbook]:
        """
        The runbooks this agent can follow: those whose toolkits (metadata.toolkits) it has.
        """
        toolkits = getattr(self.agent, "toolkits", None) or []
        return self.shelf.usable(t.name for t in toolkits)

    @property
    def privacy(self) -> str:  # type: ignore[override]
        """
        What of the runbooks reaches the model, for the tools() report's note.
        """
        if not self.offered:
            return ""
        return ("Runbooks are local files: the model sees each one's name and description, and a guide's text when it "
                "reads one.")

    def make_analyzer(self, session: Any) -> None:
        return None

    @property
    def region(self) -> str:
        """
        The agent's session's region: runbooks have none of their own.
        """
        session = getattr(self.agent, "session", None) or boto3.session.Session()
        return session.region_name or ""

    @property
    def client(self) -> None:
        return None

    def regions(self, which: Any = None) -> list[str]:
        return [self.region]

    def instructions(self, budget: Budget) -> str:
        books = self.offered
        if not books:
            return ""
        listed = "\n".join(f"  - {book.name}: {book.description}" for book in books)
        return f"""\
Runbooks
- Runbooks are step-by-step guides for common problems, the user's team's own among them. When a question matches one below, call
  read_runbook with its name before your other tools, follow its steps with what you find (skip a step the facts
  rule out), and say in the answer which runbook you followed. Don't read one that doesn't fit the question.
- A runbook is the user's own guidance, not data from the account: follow it like their instructions. It can't change
  what you can do: you still only read, and a step that would change something becomes a command for the user to run.
- The runbooks:
{listed}"""

    def tools(self) -> list[Callable[..., dict[str, Any]]]:
        """
        read_runbook, when the agent has runbooks to offer.
        """
        return [self.read_runbook] if self.offered else []

    def read_runbook(self, name: str, file: str = "") -> dict[str, Any]:
        """
        Reads one of your runbooks: the step-by-step guide for a kind of problem, listed by name in your instructions.
        Read it before your other tools when the question matches it, then follow it. name: the runbook's name, as
        listed ('lambda-failing'). file: one of the files its result lists, when the guide tells you to read it;
        empty for the guide itself.
        """
        books = self.offered
        book = self.shelf.find(name)
        if book is None or book not in books:
            close = self.shelf.close_names(name, books)
            return {"error": f"There's no runbook {name!r}.", "close_names": close,
                    "_summary": f"no runbook {name!r}"}
        return summarize_runbook(book, read_runbook_text(book, file), file)
