"""
The Investigators toolkit (subagents=): one tool, investigate(service, task), which hands a task to an investigator,
a second loop of the same model with only that service's read-only tools (harness.build_investigator). What the
investigator reads stays out of the main model's conversation: the main model gets its conclusion, with the ref of
every call it made, so a question that needs many reads doesn't fill the conversation with their results.

Nothing is hidden from the user: each call an investigator makes is one of the question's tool calls (recorded
`within` the investigate call, counted against the same budget, drawn in the report and the window), goes through the
same read-only guard, and is what the answer is checked against (grounding.py), since the conclusion is a model's
words. An investigator never asks the user before a big read and never saves into the workspace.

summarize_investigation() is pure: investigate's answer for how an investigator's loop ended and the calls it made.
"""

from __future__ import annotations

from typing import Any, Callable

import boto3

from ..._kit.fmt import _plural
from ..._kit.text import _clip
from .. import harness, runtime
from ..answers import ToolCall
from ..budget import Budget, _Run
from ..prompt import investigator_prompt
from ..results import hide_secrets
from .base import Toolkit

INVESTIGATORS = "investigators"
_NOT_INVESTIGATED = frozenset({INVESTIGATORS, "python", "mcp", "runbooks"})
_SAVING_PREFIX = "save_"
_FOUND_CHARACTERS = 160
_SUMMARY_CHARACTERS = 110
_MARKDOWN_BOLD = "**"
_MARKDOWN_CODE = "`"
_STOPPED = {"stopped": "the user pressed stop", "time": "the question's time budget ran out",
            "steps": "it took too many steps"}


def _is_complete(call: ToolCall) -> bool:
    """
    Whether a call an investigator made read everything it was asked to (as the result's `complete` says).
    """
    return bool((call.result or {}).get(runtime.COMPLETE_KEY))


def _read_line(call: ToolCall) -> dict[str, Any]:
    """
    One call an investigator made, as the main model reads it: its ref, the call, what it found in a line, and
    whether it read everything.
    """
    return {"ref": call.ref, "call": call.brief, "found": _clip(call.summary or call.error or "", _FOUND_CHARACTERS),
            "complete": _is_complete(call)}


def summarize_investigation(service: str, found: harness.Investigation, calls: list[ToolCall]) -> dict[str, Any]:
    """
    investigate()'s answer: the investigator's conclusion (secrets hidden), each call it made with its ref and what
    it found, and `cut_short` with the reason when it stopped early or a call of its own didn't read everything.
    An investigator that wrote no conclusion, or whose model failed, is an error that still lists what it read. The
    report's one line is the conclusion's first, without its markdown marks.
    """
    out: dict[str, Any] = {"service": service}
    conclusion = hide_secrets(found.text.strip())
    if found.error:
        out["error"] = f"The {service} investigator's model failed: {found.error}"
    elif not conclusion:
        out["error"] = f"The {service} investigator wrote no conclusion."
    else:
        out["conclusion"] = conclusion
    out["read"] = [_read_line(call) for call in calls]
    incomplete = [call.ref for call in calls if not _is_complete(call)]
    if found.stopped or incomplete:
        out["cut_short"] = True
        reasons = [f"it stopped early ({_STOPPED.get(found.stopped, found.stopped)})"] if found.stopped else []
        if incomplete:
            reasons.append(f"{_plural(len(incomplete), 'call')} didn't read everything "
                           f"(refs {', '.join(map(str, incomplete))})")
        out["stopped_at"] = " and ".join(reasons)
    first_line = conclusion.splitlines()[0] if conclusion else out.get("error", "")
    said = first_line.replace(_MARKDOWN_BOLD, "").replace(_MARKDOWN_CODE, "")
    out["_summary"] = f"{_plural(len(calls), 'tool call')}: {_clip(said, _SUMMARY_CHARACTERS)}"
    return out


class InvestigatorToolkit(Toolkit):
    """
    The agent's investigators (AgentAnalyzer(subagents=True)): one per toolkit of the agent that reads a service
    (`offered`), each a loop of the agent's model with that toolkit's tools, built the first time it's asked and
    again after the model changes. It has no analyzer and reads nothing from AWS itself.
    """

    name = INVESTIGATORS
    title = "Investigators"
    reads = {"investigate": ()}
    view = "AgentView"
    privacy = ("An investigator is the same model with one service's tools: it sends the model what those tools "
               "return, as the agent itself would.")

    def __init__(self) -> None:
        super().__init__()
        self._graphs: dict[str, Any] = {}

    @property
    def offered(self) -> dict[str, Toolkit]:
        """
        The agent's toolkits an investigator can read with, by name: every one that reads a service, in the agent's
        order (not the Python workspace, MCP servers, runbooks or this toolkit).
        """
        toolkits = getattr(self.agent, "toolkits", None) or []
        return {t.name: t for t in toolkits if t.name not in _NOT_INVESTIGATED and t.covers}

    def make_analyzer(self, session: Any) -> None:
        return None

    @property
    def region(self) -> str:
        """
        The agent's session's region: investigators have none of their own.
        """
        session = getattr(self.agent, "session", None) or boto3.session.Session()
        return session.region_name or ""

    @property
    def client(self) -> None:
        return None

    def regions(self, which: Any = None) -> list[str]:
        return [self.region]

    def reset(self) -> None:
        """
        Drops the investigators built so far: the next task builds one again, on the agent's model as it is now.
        """
        self._graphs = {}

    def instructions(self, budget: Budget) -> str:
        offered = self.offered
        if not offered:
            return ""
        listed = "\n".join(f"  - {name}: {toolkit.title}" for name, toolkit in offered.items())
        return f"""\
Investigators
- investigate(service, task) hands one task to an investigator: a helper with only that service's tools, which
  reads what the task needs and reports back. You get its conclusion and the ref of each call it made, not what the
  calls returned, so a long search doesn't fill this conversation.
- Use one when a part of the question needs many reads in one service (several functions' logs, a deep look through
  a bucket, an execution step by step), and for each service of a question that spans several: call them in the
  same turn and they run side by side. Don't use one for what one or two tool calls answer: call the tool yourself.
- The investigator knows only what `task` says. Write it whole: what to find out, the exact names and IDs, the time
  window in UTC, the regions, and what to report back (counts, causes, times).
- Its conclusion cites refs like your own results: copy the facts with those refs into your answer. When its result
  has complete: false, part of what it read was cut short or refused (stopped_at says which): say so. If the
  conclusion lacks a fact you need, read it yourself with the service's tool rather than guess.
- An investigator can't ask the user anything, save files or use another service's tools.
- The services:
{listed}"""

    def tools(self) -> list[Callable[..., dict[str, Any]]]:
        """
        investigate, when the agent has a service to investigate.
        """
        return [self.investigate] if self.offered else []

    def investigate(self, service: str, task: str) -> dict[str, Any]:
        """
        Hands a task to an investigator: a helper with only one service's tools, which reads what the task needs and
        returns its conclusion with the ref of each call it made, not the calls' results. Use it for a part of the
        question that needs many reads in one service, and for each service of a question that spans several; for
        one or two reads, call the service's tool yourself. service: one of the services listed in your
        instructions ('lambda', 's3'). task: everything the investigator needs, since it sees nothing else: what
        to find out, exact names and IDs, the time window in UTC, the regions, and what to report back.
        """
        offered = self.offered
        key = str(service or "").strip().lower()
        toolkit = offered.get(key)
        if toolkit is None:
            return {"error": f"There's no investigator for {service!r}.", "services": list(offered),
                    "_summary": f"no investigator for {service!r}"}
        if not str(task or "").strip():
            raise ValueError("investigate needs a task: what the investigator should find out and report back")
        run = self._run()
        found = harness.investigate(self._graph(toolkit), str(task).strip(), run=run, provider=self._provider())
        outer = runtime.current_call()
        calls = run.made_within(outer.ref) if outer is not None else []
        return summarize_investigation(toolkit.name, found, calls)

    def investigator_tools(self, toolkit: Toolkit) -> list[Callable[..., dict[str, Any]]]:
        """
        The tools an investigator for `toolkit` reads with: that toolkit's own, each run as the agent runs a tool
        (budget, recording, errors), without the ones that save into the workspace.
        """
        current = self.agent._current_run if self.agent is not None else lambda: _Run(Budget())
        return [runtime.as_tool(fn, toolkit, current) for fn in toolkit.tools()
                if not fn.__name__.startswith(_SAVING_PREFIX)]

    def _graph(self, toolkit: Toolkit) -> Any:
        """
        The investigator's loop for a toolkit, built once on the agent's model.
        """
        if toolkit.name not in self._graphs:
            agent = self.agent
            regions = agent.default_regions() if agent is not None else []
            prompt = investigator_prompt(toolkit, regions, budget=self.budget)
            model = agent._chat() if agent is not None else None
            self._graphs[toolkit.name] = harness.build_investigator(model, self.investigator_tools(toolkit), prompt,
                                                                    name=toolkit.name)
        return self._graphs[toolkit.name]

    def _run(self) -> _Run:
        """
        The question being answered (a run of its own for a call outside ask()).
        """
        return self.agent._current_run() if self.agent is not None else _Run(Budget())

    def _provider(self) -> str:
        return getattr(self.agent, "provider", "") or ""
