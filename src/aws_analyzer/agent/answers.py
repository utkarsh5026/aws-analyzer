"""
What a question gives back (AgentAnswer, with a ToolCall per tool call and the plan it worked to), and what the reader should know about
how it was reached: answer_findings() and the commands worth running next, answer_next(). All pure: no AWS, no
model.

The model ends a question by filling REPLY_SCHEMA (its final_answer): the answer's markdown and its findings about
the account (AgentFinding: warn or info, the message, a View call). parse_reply() reads what it sent forgivingly,
checked_call() keeps a call only when it names a real command of one of the agent's Views with arguments it takes,
and reply_findings() / answer_next() put them in the report's findings and next calls.

A BigRead is what a tool asks the user before a read big enough to cost time or money (interrupt_on): what it would
read, and what each of CHOICES does. choice_of() reads the user's answer forgivingly ('Go', 'yes', 'small').
"""

from __future__ import annotations

import ast
import inspect
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Iterable

from .._kit.fmt import _duration, _fmt_dt, _plural, human_money
from .._kit.focus import Focus
from .._kit.render import _call
from .._kit.text import _clip
from .budget import Budget, raised_limit
from .workspace.charts import Chart
from .llm import Rates
from .memory import MEMORY_FOLDER, parse_notes

_OPERATION = re.compile(r"[a-z0-9-]+:[A-Za-z0-9]+")


CODE_ARGS = {"run_python": "code", "write_file": "content"}
_BRIEF_CHARACTERS = 40
PLAN_STATUSES = ("pending", "in_progress", "completed")
_PLAN_STEP_CHARACTERS = 200
_PLAN_STEPS_NAMED = 3
CHOICES = ("go", "smaller", "skip")
_CHOICE_WORDS = {"go": "go", "yes": "go", "y": "go", "ok": "go", "all": "go", "smaller": "smaller", "small": "smaller",
                 "less": "smaller", "skip": "skip", "no": "skip", "n": "skip"}
_CALL_KEY_LENGTH = 10


def _short(value: Any) -> Any:
    """
    An argument as a call's line shows it: text clipped to _BRIEF_CHARACTERS (edit_file's old and new text).
    """
    return _clip(value, _BRIEF_CHARACTERS) if isinstance(value, str) else value


def services_of(reads: Iterable[str]) -> tuple[str, ...]:
    """
    The AWS services a list of reads names, each once, in order: ('lambda:GetFunction', 'logs:FilterLogEvents',
    'lambda:GetPolicy') -> ('lambda', 'logs'). A read that isn't an operation ('the package's download link') names
    none.
    """
    prefixes = [read.split(":", 1)[0] for read in reads if _OPERATION.fullmatch(read)]
    return tuple(dict.fromkeys(prefixes))


def choice_of(value: Any) -> str:
    """
    The user's answer to a BigRead as one of CHOICES: 'Go', 'yes' -> 'go'; 'small', 'Smaller' -> 'smaller'; 'no',
    'skip' -> 'skip'. ValueError for anything else.
    """
    choice = _CHOICE_WORDS.get(str(value or "").strip().lower())
    if choice is None:
        raise ValueError(f"The choice is 'go', 'smaller' or 'skip', not {value!r}")
    return choice


@dataclass
class BigRead:
    """
    A read big enough that the tool asks the user before it runs it (AgentAnalyzer(interrupt_on=...)): what it would
    read, and what each choice does. The agent stays read-only whichever they pick; this is about time and cost.

        tool     the tool that asks: 'find_files'
        what     what it would read, in a sentence: "find_files has listed 20,000 keys under s3://acme-raw/ and
                 there are more; the bucket holds about 41,200 files."
        go       what Go reads: 'list on, up to 100,000 keys (about 80 more requests, <$0.01)'
        smaller  what Smaller reads instead: 'stop at the 20,000 keys listed so far'
        cost     the estimated USD of Go's AWS requests, None when they're free
        choice   'go', 'smaller' or 'skip' once the user has answered, else None
        limit    for a used-up limit of the budget, the refusal it lifts ('tool_calls', 'time', ...), else ''
        raise_to what Go raises that limit to for the question
    """

    tool: str
    what: str
    go: str
    smaller: str
    cost: float | None = None
    choice: str | None = None
    limit: str = ""
    raise_to: float | None = None

    @property
    def question(self) -> str:
        """
        The question as the user reads it: what it would read, then each choice.
        """
        return f"{self.what} Go: {self.go}. Smaller: {self.smaller}. Skip: answer without it."

    def as_value(self, call_key: str) -> dict[str, Any]:
        """
        What the pause carries (plain data, which LangGraph keeps with the conversation), tied to its call by
        `call_key`.
        """
        return {"call": call_key, "tool": self.tool, "what": self.what, "go": self.go, "smaller": self.smaller,
                "cost": self.cost}


def limit_read(tool: str, reason: str, budget: Budget, *, needed: float | None = None) -> BigRead | None:
    """
    What a tool asks when the question has run out of a limit of its budget (`reason`, a refusal's: 'tool_calls',
    'time', ...): Go raises it for the question, Smaller has the agent answer with what it found. None for a refusal
    no raise lifts.
    """
    raised = raised_limit(budget, reason, needed=needed)
    if raised is None:
        return None
    limit, now, to = raised
    return BigRead(tool, what=f"This question has used its {limit.shown(now)} ({limit.what}), so {tool} can't run.",
                   go=f"raise it to {limit.shown(to)} for this question and go on",
                   smaller="answer now with what it found", limit=reason, raise_to=to)


def _call_key() -> str:
    return uuid.uuid4().hex[:_CALL_KEY_LENGTH]


@dataclass
class ToolCall:
    """
    One call a tool made for a question: what it was asked, what it covered and found, or why it didn't run.

        summary   one line: what it found
        coverage  its window, regions, whether it was cut short, ...
        error     why it failed: 'AccessDeniedException; needs logs:FilterLogEvents'
        refused   why it wasn't run: 'tool_calls', 'time', 'cost', 'log_window', 'skipped' or 'stopped'
        next      View calls that show more, and what they show
        first     the tool's first parameter when it has no default, which the call is written without the name of
        reads     the AWS operations the tool reads with ('lambda:GetFunction', 'logs:FilterLogEvents', ...), its
                  toolkit's `reads` for it
        result    what the tool handed the model (JSON-ready, secrets hidden), once it has run
        asks      the big reads it asked the user about (BigRead), in order, each with the choice they made
        paused    it's paused, waiting for the user's choice on its last ask (or was, and runs again when resumed)
        key       what ties a pause to this call
    """

    name: str
    args: dict[str, Any] = field(default_factory=dict)
    seconds: float = 0.0
    summary: str = ""
    coverage: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    refused: str | None = None
    next: list[tuple[str, str]] = field(default_factory=list)
    first: str | None = "function"
    reads: tuple[str, ...] = ()
    result: dict[str, Any] | None = field(default=None, repr=False)
    asks: list[BigRead] = field(default_factory=list)
    paused: bool = False
    key: str = field(default_factory=_call_key, repr=False)

    @property
    def waiting(self) -> BigRead | None:
        """
        The big read it's waiting for the user's choice on, or None.
        """
        return self.asks[-1] if self.asks and self.asks[-1].choice is None else None

    @property
    def services(self) -> tuple[str, ...]:
        """
        The AWS services the tool reads, in the order its reads name them: ('lambda', 'logs', 'cloudwatch').
        """
        return services_of(self.reads)

    @property
    def ok(self) -> bool:
        return self.error is None and self.refused is None

    @property
    def call(self) -> str:
        """
        The call as the model made it: "function_errors('orders-etl', since='2026-10-15T12:30Z')".
        """
        args = {k: v for k, v in self.args.items() if v not in ("", None)}
        first = args.pop(self.first, None) if self.first else None
        return _call(self.name, first, **args) if first is not None else _call(self.name, **args)

    @property
    def code(self) -> str:
        """
        The code the call was given to run (run_python's), or '' for a tool that takes none.
        """
        arg = CODE_ARGS.get(self.name)
        return str(self.args.get(arg) or "") if arg else ""

    @property
    def brief(self) -> str:
        """
        The call as a line shows it: the code it was given left out (it's drawn under the line) and long text clipped,
        "write_file('/analysis.py')", or "run_python(…)" when the code is all it was given.
        """
        code_arg = CODE_ARGS.get(self.name)
        args = {k: _short(v) for k, v in self.args.items() if k != code_arg and v not in ("", None)}
        if not args and self.code:
            return f"{self.name}(…)"
        first = args.pop(self.first, None) if self.first else None
        return _call(self.name, first, **args) if first is not None else _call(self.name, **args)


@dataclass
class PlanStep:
    """
    One step of the plan the agent wrote for a question (its write_todos list): what it means to do, in plain words,
    and how far it got: 'pending', 'in_progress' or 'completed'.
    """

    text: str
    status: str = "pending"

    @property
    def done(self) -> bool:
        return self.status == "completed"


def _plan_step(item: Any) -> PlanStep | None:
    """
    One item of a write_todos list as a PlanStep ({'content': ..., 'status': ...}, or plain text), or None when it
    says nothing. A status the tool doesn't know reads as pending.
    """
    if isinstance(item, dict):
        text, status = item.get("content"), item.get("status")
    else:
        text, status = item, "pending"
    text = _clip(" ".join(str(text or "").split()), _PLAN_STEP_CHARACTERS)
    if not text:
        return None
    return PlanStep(text, status if status in PLAN_STATUSES else "pending")


def parse_plan(todos: Any) -> list[PlanStep]:
    """
    The plan in a write_todos call's `todos` argument, in order: [{'content': 'check every function's health',
    'status': 'completed'}, ...] -> [PlanStep(...), ...]. Anything that isn't a list gives an empty plan.
    """
    if not isinstance(todos, (list, tuple)):
        return []
    steps = [_plan_step(item) for item in todos]
    return [step for step in steps if step is not None]


@dataclass
class AgentFinding:
    """
    Something the agent found in the account that the user should act on or know, as it filled it in its reply
    (REPLY_SCHEMA): 'warn' or 'info', what's wrong and why it matters, and the View call that shows more ('' when it
    named none that runs).

        level    'warn' or 'info'
        message  "orders-etl fails on KeyError 'customer_id' since the 03:00 deploy: 14 runs lost. Read the code ..."
        call     "LambdaView().errors('orders-etl', since='24h')", checked by checked_call()
        shows    what that call shows, in a few words: "its errors, grouped by cause"
    """

    level: str
    message: str
    call: str = ""
    shows: str = ""


REPLY_TOOL = "final_answer"
LEVELS = ("warn", "info")
_MAX_FINDINGS = 8
_FINDING_CHARACTERS = 600
_SHOWS_CHARACTERS = 80
REPLY_SCHEMA: dict[str, Any] = {
    "title": REPLY_TOOL,
    "description": "Gives the user your answer, with what you found that they should act on or know. Call it once, "
                   "when you're done, instead of writing the answer as a message.",
    "type": "object",
    "properties": {
        "answer": {"type": "string",
                   "description": "The answer in markdown, as 'How to answer' says."},
        "findings": {
            "type": "array",
            "description": "What needs the user's attention, the most important first; empty when nothing does.",
            "items": {
                "type": "object",
                "properties": {
                    "level": {"type": "string", "enum": list(LEVELS),
                              "description": "warn: something is broken, at risk or costing money; info: worth "
                                             "knowing."},
                    "message": {"type": "string",
                                "description": "What's wrong: why it matters (with a number or a price when the "
                                               "tools gave one). What to do about it. One to three sentences."},
                    "call": {"type": "string",
                             "description": "One View call that shows more or helps fix it, from 'Calls a finding "
                                            "can name', with its arguments filled in: "
                                            "LambdaView().errors('orders-etl', since='24h'). Empty when none fits."},
                    "shows": {"type": "string",
                              "description": "What that call shows, in a few words: 'its errors, by cause'."},
                },
                "required": ["level", "message"],
            },
        },
    },
    "required": ["answer", "findings"],
}


def _level_of(value: Any) -> str:
    """
    A finding's level as one of LEVELS: 'warn' (or 'warning', 'bad', 'error'), else 'info'.
    """
    word = str(value or "").strip().lower()
    return "warn" if word in ("warn", "warning", "bad", "error") else "info"


def _finding_of(item: Any) -> AgentFinding | None:
    """
    One item of a reply's findings as an AgentFinding, or None when it says nothing.
    """
    if isinstance(item, str):
        item = {"message": item}
    if not isinstance(item, dict):
        return None
    message = _clip(" ".join(str(item.get("message") or "").split()), _FINDING_CHARACTERS)
    if not message:
        return None
    shows = _clip(" ".join(str(item.get("shows") or "").split()), _SHOWS_CHARACTERS)
    return AgentFinding(_level_of(item.get("level")), message, str(item.get("call") or "").strip(), shows)


def parse_reply(reply: Any) -> tuple[str, list[AgentFinding]]:
    """
    The model's reply (the final_answer arguments REPLY_SCHEMA asks for) as (the answer's markdown, its findings):
    forgiving about what a model sends, so a missing list, a finding given as plain text or an unknown level still
    reads. At most _MAX_FINDINGS findings; their calls are as the model wrote them (checked_call() checks them).
    """
    if not isinstance(reply, dict):
        return "", []
    items = reply.get("findings")
    if not isinstance(items, (list, tuple)):
        items = []
    findings = [finding for finding in map(_finding_of, items) if finding is not None]
    return str(reply.get("answer") or "").strip(), findings[:_MAX_FINDINGS]


def _call_parts(node: ast.expr) -> tuple[str, str, list[Any], dict[str, Any]] | None:
    """
    A View call's parts, (View, command, arguments, keyword arguments), when `node` is `<View>().<command>(...)`
    with literal arguments only; else None.
    """
    is_command = isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    made = node.func.value if is_command else None
    is_view = isinstance(made, ast.Call) and isinstance(made.func, ast.Name) and not made.args and not made.keywords
    if not is_view or any(keyword.arg is None for keyword in node.keywords):
        return None
    try:
        args = [ast.literal_eval(arg) for arg in node.args]
        kwargs = {keyword.arg: ast.literal_eval(keyword.value) for keyword in node.keywords}
    except ValueError:
        return None
    return made.func.id, node.func.attr, args, kwargs


def checked_call(text: str, commands: dict[str, dict[str, Callable[..., Any]]]) -> str:
    """
    A call the model put in a finding, written as a report writes it ("LambdaView().errors('etl', since='24h')"),
    when it names a command of one of the agent's Views (`commands`: View name -> command name -> its function)
    with arguments that command takes, all literal values; else ''. So a call the model made up, misspelt or gave
    code to is never shown as one to run.
    """
    source = str(text or "").strip().strip("`").strip()
    try:
        node = ast.parse(source, mode="eval").body
    except SyntaxError:
        return ""
    parts = _call_parts(node)
    if parts is None:
        return ""
    view, command, args, kwargs = parts
    function = commands.get(view, {}).get(command)
    if function is None:
        return ""
    try:
        inspect.signature(function).bind(None, *args, **kwargs)
    except TypeError:
        return ""
    return f"{view}()." + _call(command, *args, **kwargs)


def check_findings(findings: list[AgentFinding], commands: dict[str, dict[str, Callable[..., Any]]]) -> None:
    """
    Keeps only the calls in the findings that run (checked_call()), written as reports write them; a finding whose
    call doesn't run keeps its message, without the call.
    """
    for finding in findings:
        finding.call = checked_call(finding.call, commands) if finding.call else ""
        if not finding.call:
            finding.shows = ""


@dataclass
class AgentStep:
    """
    Something the agent did while answering, told as it happens (ask(on_step=...)), so a window can show its work.

        kind  'thinking':  the model's words before it calls tools (text)
              'tool':      a tool call starts (call)
              'asking':    that call paused for the user's choice on a big read (call.waiting)
              'tool_done': that call finished: call.summary, call.error or call.refused say how
              'plan':      the model wrote or updated its plan (plan, the whole list as it stands now)
        call  the same ToolCall object for 'tool' and 'tool_done', which the answer's calls hold too
        plan  the plan's steps for 'plan'

    Tools run side by side on LangGraph's threads, so on_step is called from them: a window queues the steps and
    draws them on its own thread.
    """

    kind: str
    text: str = ""
    call: ToolCall | None = None
    plan: list[PlanStep] = field(default_factory=list)


@dataclass
class AgentAnswer:
    """
    One question and its answer, with every tool call it made, the AWS reads, the tokens and what it cost.

        text          the answer, in markdown
        findings      what the agent found that the user should act on or know (AgentFinding: level, message, the
                      View call that shows more), as it filled them in its reply; empty when it found nothing, or
                      answered in plain text (findings=False, or a model that can't fill a set shape)
        plan          the steps the agent planned for it (write_todos), as it last left them; empty when it
                      didn't plan (a question of a step or two)
        charts        the charts it drew to show with the text (show_chart), in order: .title, .data, .mime
        aws_calls     the AWS reads it made: 'logs:FilterLogEvents' -> 12
        blocked       AWS calls the read-only guard refused
        metrics_read  CloudWatch metrics requested (GetMetricData bills per 1,000)
        aws_cost      USD for those metrics ($0.01 per 1,000 at list price), S3's requests and knowledge base answers
                      (estimated); the rest are free
        model         the deployment or model that answered
        provider      who served it: 'bedrock', 'openai', 'azure', another LangChain provider, or '' for a chat model
                      passed in
        cost          estimated USD for the model's tokens; None when its prices aren't known
        rates         the prices `cost` is at (Rates: per 1M input and output tokens, yours or Bedrock's list prices)
        stopped       None when it finished, else 'tool_calls', 'time', 'cost', 'steps', 'stopped' or 'error'
        waiting       the calls paused for the user's choice on a big read (resume() goes on); empty once answered
        runbooks      the runbooks it read to answer (read_runbook), by name, in the order it read them
        error         what went wrong with the model or the agent loop, when something did
        thread        the conversation it belongs to
        focus         what the user had open in an explorer when they asked (ask(focus=)), or None
        exception     the error itself, to raise when debugging
    """

    question: str
    text: str = ""
    findings: list[AgentFinding] = field(default_factory=list)
    calls: list[ToolCall] = field(default_factory=list)
    plan: list[PlanStep] = field(default_factory=list)
    charts: list[Chart] = field(default_factory=list)
    aws_calls: dict[str, int] = field(default_factory=dict)
    blocked: list[str] = field(default_factory=list)
    metrics_read: int = 0
    aws_cost: float = 0.0
    model: str = ""
    provider: str = ""
    model_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost: float | None = None
    rates: Rates | None = None
    seconds: float = 0.0
    stopped: str | None = None
    error: str | None = None
    thread: str = ""
    asked: datetime | None = None
    focus: Focus | None = None
    exception: BaseException | None = field(default=None, repr=False)

    @property
    def waiting(self) -> list[ToolCall]:
        """
        The calls paused for the user's choice on a big read, in the order they asked.
        """
        return [call for call in self.calls if call.waiting is not None]

    @property
    def runbooks(self) -> list[str]:
        """
        The runbooks it read to answer (read_runbook calls that found one), by name, each once, in the order read.
        """
        names: list[str] = []
        for call in self.calls:
            name = (call.result or {}).get("runbook") if call.name == "read_runbook" else None
            if name and name not in names:
                names.append(name)
        return names


def _choice_findings(call: ToolCall) -> list[tuple[str, str]]:
    """
    What the user's choices on a call's big reads left out: a read they skipped, or the smaller one they picked.
    """
    found = []
    for ask in call.asks:
        if ask.choice == "skip":
            found.append(("info", f"You skipped {call.call}: {ask.what} The answer leaves it out; ask again and pick "
                                  "Go to read it."))
        elif ask.choice == "smaller":
            found.append(("info", f"You picked the smaller read for {call.call}: {ask.smaller}. The answer covers "
                                  "that only; ask again and pick Go for the rest."))
    return found


def _coverage_findings(call: ToolCall, budget: Budget) -> list[tuple[str, str]]:
    """
    What a tool call didn't cover, and how to get the rest: a big read the user skipped or made smaller, a log window
    over the budget's log_days, a listing stopped at s3_keys, a read stopped where its result says (stopped_at), or a log read cut at its line limit.
    """
    coverage = call.coverage
    choices = _choice_findings(call)
    if choices:
        return choices
    if call.refused == "log_window":
        window = coverage.get("window") or {}
        days = max(1, round((window.get("hours") or 24 * budget.log_days) / 24))
        return [("info", f"{call.call} wasn't run: its {_plural(days, 'day')} of logs are more than the "
                         f"{budget.log_days:g}-day limit for a log read, which keeps questions fast. "
                         "CloudWatch's counts cover longer windows; to read the logs too, raise the limit: "
                         f"AgentView(budget={{'log_days': {days}}}).")]
    if not coverage.get("cut_short"):
        return []
    if coverage.get("scanned"):
        keys = int(coverage["scanned"])
        return [("info", f"{call.call} stopped after {keys:,} S3 keys (the budget's s3_keys), in key order: "
                         "its counts cover those keys only. Ask about a narrower prefix, or raise the limit: "
                         f"AgentView(budget={{'s3_keys': {max(keys, budget.s3_keys) * 5}}}).")]
    if coverage.get("stopped_at"):
        return [("info", f"{call.call} stopped at {coverage['stopped_at']}.")]
    reach = coverage.get("covered_from")
    covered = f": it covers {_fmt_dt(reach, zone=True)} to the end of the window" if isinstance(reach, datetime) else ""
    return [("info", f"{call.name} for {call.args.get('function') or 'the functions'} stopped at its line limit"
                     f"{covered}. The newest lines were kept.")]


def _plan_findings(answer: AgentAnswer) -> list[tuple[str, str]]:
    """
    The steps of its plan the agent didn't mark done before it answered: the answer may leave them out.
    """
    left = [step.text for step in answer.plan if not step.done]
    if not left:
        return []
    named = "; ".join(f"'{text}'" for text in left[:_PLAN_STEPS_NAMED])
    if len(left) > _PLAN_STEPS_NAMED:
        named += " and more"
    if len(left) == 1:
        return [("info", f"One step of its plan wasn't marked done ({named}): the answer may not cover it. Check it "
                         "does, or ask about it in a follow-up.")]
    return [("info", f"{len(left)} steps of its plan weren't marked done ({named}): the answer may not cover them. "
                     "Check it does, or ask about them in a follow-up.")]


_NOTE_TOOLS = {"write_file": "content", "edit_file": "new_string"}
_NOTE_CHARACTERS = 90


def _noted(call: ToolCall) -> list[str]:
    """
    The notes a tool call wrote into the agent's memory (a write_file or edit_file under MEMORY_FOLDER that went
    through), as their text; empty for any other call.
    """
    written = _NOTE_TOOLS.get(call.name)
    path = str(call.args.get("file_path") or "")
    if written is None or not call.ok or not path.startswith(MEMORY_FOLDER):
        return []
    return [note.text for note in parse_notes(str(call.args.get(written) or ""))]


def _memory_findings(answer: AgentAnswer) -> list[tuple[str, str]]:
    """
    What the agent noted for next time while answering, with the calls that show and clear its notes.
    """
    noted = [text for call in answer.calls for text in _noted(call)]
    if not noted:
        return []
    shown = "; ".join(_clip(text, _NOTE_CHARACTERS) for text in noted[:2])
    more = f" and {_plural(len(noted) - 2, 'more')}" if len(noted) > 2 else ""
    return [("info", f"It kept {_plural(len(noted), 'note')} for next time ({shown}{more}): the next conversation "
                     "starts from them. See them all with memory(), or clear one with forget('...').")]


def answer_findings(answer: AgentAnswer, budget: Budget | None = None) -> list[tuple[str, str]]:
    """
    What the reader should know about how an answer was reached: where it stopped short, what it couldn't read or
    was refused, a call the guard blocked, steps of its plan it didn't finish, and an answer that used no tool.
    (level, message) pairs, each with what to do about it.
    """
    budget = budget or Budget()
    found: list[tuple[str, str]] = []
    stops = {
        "tool_calls": f"Stopped at the budget of {_plural(budget.tool_calls, 'tool call')}: the answer leaves out what "
                      "it didn't get to check. Ask a narrower question, or raise the budget: "
                      f"AgentView(budget={{'tool_calls': {budget.tool_calls * 2}}}).",
        "time": f"Stopped at the time budget ({_duration(budget.seconds)}): the answer covers what it read before then. "
                f"Raise it with AgentView(budget={{'seconds': {int(budget.seconds * 2)}}}).",
        "cost": f"Stopped at the cost budget ({human_money(budget.usd)} a question): the answer covers what it read "
                f"before then. Raise it with AgentView(budget={{'usd': {(budget.usd or 1) * 2:g}}}).",
        "steps": f"Stopped after {budget.steps} steps (the agent's recursion limit): it was still working, so the answer "
                 "may be incomplete. Ask a narrower question.",
        "stopped": "You stopped it: the answer covers what it read before then.",
    }
    if answer.stopped in stops:
        found.append(("warn" if answer.stopped != "stopped" else "info", stops[answer.stopped]))
    if answer.error:
        found.append(("warn", f"The model call failed, so the question wasn't answered: {answer.error}"))
    for op in dict.fromkeys(answer.blocked):
        found.append(("warn", f"Blocked {op}: it would change your account, and the agent only reads. Nothing was "
                              "sent to AWS."))
    for call in answer.calls:
        if call.error:
            found.append(("warn", f"{call.name} couldn't read everything ({call.error}): the answer leaves that "
                                  "out. README lists the read-only IAM permissions each tool needs."))
        found += _coverage_findings(call, budget)
    found += _plan_findings(answer)
    found += _memory_findings(answer)
    if not answer.calls and not answer.error and answer.stopped is None:
        found.append(("info", "It answered without calling a tool, so nothing in the answer comes from your account. "
                              "Ask about your functions or buckets, or a time window."))
    return found


def reply_findings(answer: AgentAnswer) -> list[tuple[str, str]]:
    """
    What the agent found in the account, as (level, message) pairs: each finding of its reply, with the call that
    shows more after the message when the message doesn't already name it.
    """
    pairs = []
    for finding in answer.findings:
        named = not finding.call or finding.call in finding.message
        pairs.append((finding.level, finding.message if named else f"{finding.message} ({finding.call})"))
    return pairs


def _command_key(command: str) -> str:
    """
    What makes two next calls the same: the command and its first argument, "LambdaView().errors('etl'".
    """
    key = re.match(r"(?:\w+\(\)\.)?(\w+)\(('[^']*')?", command)
    return key.group(0) if key else command


def answer_next(answer: AgentAnswer, n: int = 3) -> list[tuple[str, str]]:
    """
    The commands worth running next: the calls the agent's findings name, the most important first, then the View
    calls behind the tools the answer used, the most specific first, without repeats: one per command and function
    (errors('etl') once, with the latest window).
    """
    seen: dict[str, tuple[str, str]] = {}
    for finding in answer.findings:
        if finding.call:
            what = finding.shows or _clip(finding.message, _SHOWS_CHARACTERS)
            seen.setdefault(_command_key(finding.call), (finding.call, what))
    for call in reversed(answer.calls):
        for command, what in call.next:
            seen.setdefault(_command_key(command), (command, what))
    return list(seen.values())[:n]
