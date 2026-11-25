"""
The "⚙ Ran with" fold a report ends with: the options its command ran with (the defaults filled in), other ways to
run it, ready to copy, and every option it takes, so a user who called performance('etl') learns since='7d' and
compare=False without opening help().

_BaseView._show adds it to every report a command draws (see _BaseView._with_options): @_friendly_errors records
the command and the arguments it was called with, and command_options() reads the rest from the command itself. Its
signature gives each option and its default; its docstring's examples (since='7d' looks further back; ...) give the
values worth trying and what each does, so a command documented the usual way gets its fold with no code of its own.
A command can add a suggestion from what its report found with _BaseView._suggest (were the 3 timeouts there before
yesterday? since='7d'), which comes first.

The fold is drawn like the data fold under it: closed, one line of the values used, with the first suggestions under
it; open, every suggestion and a table of every option. Its CSS goes in report.rules, so no service's stylesheet
changes. Text mode prints it open, without the table (help('name') has that).
"""

from __future__ import annotations

import ast
import inspect
import re
from dataclasses import dataclass, field
from typing import Any, Callable

from .blocks import _Report, _draws
from .render import _SELECT, _literal
from .text import _clip, _esc

_MOST_TRIES = 6
_TRIES_PEEKING = 2
_SHORTEST_WHY = 6
_VALUE_WIDTH = 40
_SUMMARY_VALUE_WIDTH = 28

_LITERAL = (r"'[^'\n]*'|\"[^\"\n]*\"|-?\d[\d_.]*\b|True\b|False\b|None\b"
            r"|\[[^\]\n]*\]|\([^)\n]*\)|\{[^}\n]*\}")
_EXAMPLE = re.compile(rf"(?<![\w.])(?P<name>[A-Za-z_]\w*)=(?P<value>{_LITERAL})")
_ALSO = re.compile(rf"^[\s(]*(?:,\s*)?or\s+(?P<value>{_LITERAL})")
_WHY_ENDS = re.compile(r"[;:]|\.(?:\s|$)|\)|,\s*\.\.\.|\s*[A-Za-z_]\w*=|\s+(?:or|and)\s+(?=[A-Za-z_]\w*\()")
_WHY_LEADS = re.compile(r"^[\s),`]+")
_DANGLING = re.compile(r"(?:\s+(?:and|or|your|the|a|to|with))+$")
_ELLIPSIS = "..."

_PARAM_WORDS: dict[str, str] = {
    "since": "how far back to read: '24h', '7d' or a date",
    "until": "where the time range ends",
    "days": "how many days back to read",
    "region": "the AWS region to read (None: the session's)",
    "regions": "which regions to read: 'all' or a list",
    "limit": "the most it reads before it stops",
    "n": "how many to show",
    "top": "how many to show",
    "where": "a filter on what's read",
    "match": "only the names that match this text",
    "path": "where the file is written",
    "status": "only those in this status",
    "search": "only what holds this text",
    "sort": "how the rows are sorted",
    "metrics": "also read CloudWatch's numbers",
    "max_size": "the largest it downloads or reads",
    "dry_run": "say what it would do, without doing it",
    "height": "the window's height",
    "kb": "the knowledge base (None: the one use() set)",
    "data_source": "only passages from these data sources",
    "index": "the index to read",
    "fields": "the fields to show as columns",
    "compare": "also read the time before, to compare",
    "pattern": "only names that match this pattern",
    "min_size": "only files at least this big",
    "modified_after": "only files changed since then",
    "modified_before": "only files changed before then",
    "extensions": "only files of these types",
    "file": "the file to show",
    "pages": "the pages to read",
}


@dataclass
class _Option:
    """
    One of a command's options: its name, the value it ran with (as Python), whether that's its default, other
    values it takes (from the docstring's examples) and what it does.
    """

    name: str
    value: str
    is_default: bool
    takes: list[str] = field(default_factory=list)
    what: str = ""
    required: bool = False


@dataclass
class _Try:
    """
    Another way to run the command: the one option it changes (since='7d'), the whole call, and why you would.
    """

    change: str
    call: str
    why: str = ""


@dataclass
class _Options:
    """
    The "⚙ Ran with" fold: the command's options as it ran (used), other ways to run it (tries), and the call that
    shows its whole description (help_call).
    """

    command: str
    used: list[_Option]
    tries: list[_Try]
    help_call: str


@dataclass
class _RunAgain:
    """
    A way to run a command again that its report found worth it: the options it changes, and why.
    """

    changes: dict[str, Any]
    why: str


@dataclass
class _Asked:
    """
    A command as it was called: the method, its arguments, and the ways to run it again its report found worth it
    (_BaseView._suggest), which come first in its fold.
    """

    function: Callable
    args: tuple
    kwargs: dict
    suggestions: list[_RunAgain] = field(default_factory=list)

    @property
    def name(self) -> str:
        return self.function.__name__


@dataclass
class _Example:
    """
    A value a docstring shows for one option (since='7d'), and what the words after it say it does.
    """

    value: str
    why: str


def _why_after(text: str) -> str:
    """
    "looks further back; reading stops..." -> 'looks further back': the words after an example, up to where its
    clause ends (a semicolon, a full stop, a bracket, or the next example), or '' when that leaves too little to say
    anything, or only another way to call it ("or what_if(...)").
    """
    text = _WHY_LEADS.sub("", text)
    end = _WHY_ENDS.search(text)
    why = " ".join((text[:end.start()] if end else text).split()).rstrip(" ,(")
    why = _DANGLING.sub("", why.split("(")[0].rstrip())
    says_something = len(why) >= _SHORTEST_WHY and why[0].isalpha() and not why.startswith("or ")
    return why if says_something else ""


def _also_values(text: str) -> tuple[list[str], str]:
    """
    "or '2026-10-01' looks further back" -> (["'2026-10-01'"], 'looks further back'): the other values an example
    gives with "or", and the text after them.
    """
    values = []
    while (match := _ALSO.match(text)) and _is_literal(match["value"].strip()):
        values.append(match["value"].strip())
        text = text[match.end():]
    return values, text


def docstring_examples(doc: str, names: set[str]) -> dict[str, list[_Example]]:
    """
    The examples a command's docstring gives for its options, name -> [_Example], in the order it gives them:
    "since='7d' or '2026-10-01' looks further back; compare=False skips reading the window before" ->
    {'since': [_Example("'7d'", 'looks further back'), _Example("'2026-10-01'", 'looks further back')],
    'compare': [_Example('False', 'skips reading the window before')]}. Only names in `names` and values Python
    reads as literals count.
    """
    found: dict[str, list[_Example]] = {}
    for match in _EXAMPLE.finditer(doc):
        name, value = match["name"], match["value"].strip()
        if name not in names or not _is_literal(value):
            continue
        also, rest = _also_values(doc[match.end():])
        why = _why_after(rest)
        examples = found.setdefault(name, [])
        for each in [value, *also]:
            if all(example.value != each for example in examples):
                examples.append(_Example(each, why))
    return found


def _is_literal(text: str) -> bool:
    """
    Whether Python reads the text as a literal value ('7d', 5_000, ['a']), so a call written with it runs as pasted.
    """
    try:
        ast.literal_eval(text)
    except (ValueError, SyntaxError, TypeError, MemoryError, RecursionError):
        return False
    return True


def _is_same_value(value: Any, other: Any) -> bool:
    """
    Whether an option's value is its default: the same object, or equal, without failing on values that can't say.
    """
    if value is other:
        return True
    try:
        return bool(value == other)
    except (TypeError, ValueError):
        return False


def _parameters(function: Callable) -> list[inspect.Parameter]:
    """
    A command's parameters a user passes: without self, *args and **kwargs.
    """
    variadic = (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD)
    parameters = list(inspect.signature(function).parameters.values())[1:]
    return [parameter for parameter in parameters if parameter.kind not in variadic]


def _bound(function: Callable, args: tuple, kwargs: dict) -> dict[str, Any] | None:
    """
    The arguments a command was called with, by name, or None when they don't fit its signature.
    """
    try:
        bound = inspect.signature(function).bind_partial(None, *args, **kwargs)
    except TypeError:
        return None
    return dict(list(bound.arguments.items())[1:])


@dataclass
class _Ran:
    """
    How a command ran: each option's value as Python, which ones were passed, and the examples its docstring gives.
    """

    parameters: list[inspect.Parameter]
    values: dict[str, str]
    passed: set[str]
    examples: dict[str, list[_Example]]

    def call(self, command: str, changes: dict[str, str]) -> str:
        """
        The command's call with the options it ran with and `changes` (name -> value as Python): the options without
        a default first, as positional arguments, then every other one that isn't at its default.
        """
        arguments = []
        for parameter in self.parameters:
            value = changes.get(parameter.name, self.values[parameter.name])
            if parameter.default is inspect.Parameter.empty:
                arguments.append(value)
            elif parameter.name in changes or parameter.name in self.passed:
                if parameter.name in changes and value == _literal(parameter.default):
                    continue
                arguments.append(f"{parameter.name}={value}")
        return f"{command}({', '.join(arguments)})"


def _read_ran(function: Callable, args: tuple, kwargs: dict) -> _Ran | None:
    """
    How the command ran, from its signature and the arguments it was called with; None when they don't fit it.
    """
    called = _bound(function, args, kwargs)
    if called is None:
        return None
    parameters = _parameters(function)
    values, passed = {}, set()
    for parameter in parameters:
        if parameter.name not in called:
            if parameter.default is inspect.Parameter.empty:
                return None
            values[parameter.name] = _literal(parameter.default)
            continue
        values[parameter.name] = _literal(called[parameter.name])
        if not _is_same_value(called[parameter.name], parameter.default):
            passed.add(parameter.name)
    names = {parameter.name for parameter in parameters}
    return _Ran(parameters, values, passed, docstring_examples(inspect.getdoc(function) or "", names))


def _options_used(ran: _Ran) -> list[_Option]:
    """
    Every option, with the value it ran with, the other values its docstring shows, and what it does.
    """
    options = []
    for parameter in ran.parameters:
        value = ran.values[parameter.name]
        examples = ran.examples.get(parameter.name, [])
        takes = [example.value for example in examples if example.value != value]
        whys = [example.why for example in examples if example.why]
        what = _PARAM_WORDS.get(parameter.name) or (whys[0] if whys else "")
        is_default = parameter.default is not inspect.Parameter.empty and parameter.name not in ran.passed
        required = parameter.default is inspect.Parameter.empty
        options.append(_Option(parameter.name, value, is_default, takes, what, required))
    return options


_COMMON_TRIES: dict[str, list[_Example]] = {
    "since": [_Example("'7d'", "looks a week back"), _Example("'30d'", "looks a month back")],
    "days": [_Example("7", "looks a week back"), _Example("30", "looks a month back")],
    "metrics": [_Example("False", "skips CloudWatch's numbers: faster")],
}
_MORE_SHOWN = ("n", "top")


def _common_examples(parameter: inspect.Parameter) -> list[_Example]:
    """
    Values worth trying for an option the docstring gives no example of, when it's one most commands share:
    since='7d', metrics=False, twice as many rows for n= and top=.
    """
    if parameter.name in _MORE_SHOWN and isinstance(parameter.default, int) and parameter.default > 0:
        return [_Example(str(parameter.default * 2), "shows twice as many")]
    return _COMMON_TRIES.get(parameter.name, [])


def _option_examples(ran: _Ran, parameter: inspect.Parameter) -> list[_Example]:
    """
    The values to try for one option: its docstring's examples, else the common ones, without placeholders
    (columns=[...]) that wouldn't run as pasted.
    """
    examples = ran.examples.get(parameter.name) or _common_examples(parameter)
    return [example for example in examples if _ELLIPSIS not in example.value]


def _example_tries(ran: _Ran, command: str) -> list[_Try]:
    """
    A way to run it per example value that differs from the one used, and the default again for an option that was
    passed, in the order of the command's options.
    """
    tries = []
    for parameter in ran.parameters:
        if parameter.default is inspect.Parameter.empty:
            continue
        name, value = parameter.name, ran.values[parameter.name]
        for example in _option_examples(ran, parameter):
            if example.value != value:
                why = example.why or _PARAM_WORDS.get(name, "")
                tries.append(_Try(f"{name}={example.value}", ran.call(command, {name: example.value}), why))
        if name in ran.passed:
            default = _literal(parameter.default)
            why = f"without {name}=" if parameter.default is None else "back to its default"
            tries.append(_Try(f"{name}={default}", ran.call(command, {name: default}), why))
    return tries


def _suggested_tries(ran: _Ran, command: str, suggestions: list[_RunAgain]) -> list[_Try]:
    """
    The ways to run it again that the command's report found worth it, for options it has.
    """
    tries = []
    for suggestion in suggestions:
        changes = {name: _literal(value) for name, value in suggestion.changes.items() if name in ran.values}
        if changes:
            change = ", ".join(f"{name}={value}" for name, value in changes.items())
            tries.append(_Try(change, ran.call(command, changes), suggestion.why))
    return tries


def _distinct(tries: list[_Try]) -> list[_Try]:
    """
    The tries without a second one of the same call, at most _MOST_TRIES.
    """
    kept: list[_Try] = []
    for attempt in tries:
        if all(attempt.call != other.call for other in kept):
            kept.append(attempt)
    return kept[:_MOST_TRIES]


def command_options(asked: _Asked, *, help_call: str, prefixed: Callable[[str], str]) -> _Options | None:
    """
    The fold for a command as it was called, or None when it has no option to change (every parameter is required,
    or the arguments don't fit it). Calls are written by `prefixed` (ui.performance(...)); the report's own
    suggestions come first, then the docstring's examples.
    """
    ran = _read_ran(asked.function, asked.args, asked.kwargs)
    if ran is None or all(parameter.default is inspect.Parameter.empty for parameter in ran.parameters):
        return None
    can_write_calls = all(_is_literal(value) for value in ran.values.values())
    tries = []
    if can_write_calls:
        tries = _suggested_tries(ran, asked.name, asked.suggestions) + _example_tries(ran, asked.name)
    tries = [_Try(attempt.change, prefixed(attempt.call), attempt.why) for attempt in _distinct(tries)]
    return _Options(asked.name, _options_used(ran), tries, help_call)


_OPTIONS_RULES = """
.{root} details.op{{margin:12px 0 2px;padding-top:8px;border-top:1px dashed rgba(127,127,127,.35)}}
.{root} details.op>summary{{cursor:pointer;list-style:none;display:flex;flex-wrap:wrap;align-items:baseline;gap:4px 10px}}
.{root} details.op>summary::-webkit-details-marker{{display:none}}
.{root} details.op>summary::before{{content:"\\25B8";font-size:11px;opacity:.55}}
.{root} details.op[open]>summary::before{{content:"\\25BE"}}
.{root} details.op>summary b{{font-size:12.5px}}
.{root} details.op .op-used{{font-family:var(--mono,ui-monospace,Menlo,Consolas,monospace);font-size:12px;opacity:.75}}
.{root} details.op .op-more{{font-size:12px;opacity:.6}}
.{root} details.op>.op-body{{margin:10px 0 4px 18px}}
.{root} .op-tries{{display:grid;grid-template-columns:auto minmax(0,max-content) minmax(0,1fr);gap:5px 12px;align-items:center}}
.{root} .op-peek{{margin:8px 0 2px 18px}}
.{root} details.op[open]+.op-peek{{display:none}}
.{root} .op-tries .op-ch{{justify-self:start;padding:1px 8px;border-radius:9px;background:rgba(127,127,127,.14);font-family:var(--mono,ui-monospace,Menlo,Consolas,monospace);font-size:11.5px;white-space:nowrap}}
.{root} .op-tries code{{justify-self:start;overflow-wrap:anywhere}}
.{root} .op-tries .op-why{{font-size:12px;opacity:.7}}
.{root} table.op-all{{margin-top:12px;border-collapse:collapse;width:100%;font-size:12.5px}}
.{root} table.op-all th{{padding:4px 10px 4px 0;border-bottom:1px solid rgba(127,127,127,.25);text-align:left;font-size:10.5px;font-weight:600;letter-spacing:.04em;text-transform:uppercase;opacity:.6}}
.{root} table.op-all td{{padding:5px 10px 5px 0;border-bottom:1px solid rgba(127,127,127,.12);vertical-align:top}}
.{root} table.op-all td.op-name{{font-family:var(--mono,ui-monospace,Menlo,Consolas,monospace);font-weight:600}}
.{root} table.op-all .op-takes{{display:inline-flex;flex-wrap:wrap;gap:4px}}
.{root} .op-tag{{margin-left:6px;padding:0 6px;border-radius:8px;background:rgba(127,127,127,.15);font-size:10px;opacity:.85}}
.{root} .op-tag.set{{background:var(--accent,#a85a26);color:#fff;opacity:1}}
.{root} .op-help{{margin-top:8px;font-size:12px;opacity:.7}}
"""


def _used_text(options: _Options) -> str:
    """
    "since='24h' · limit=5000 · compare=True": the options with a value, as the command ran with them.
    """
    shown = [option for option in options.used if not option.required and option.value != "None"]
    return " · ".join(f"{option.name}={_clip(option.value, _SUMMARY_VALUE_WIDTH)}" for option in shown)


def _summary_html(options: _Options) -> str:
    """
    The fold's line: what it ran with, and how many other ways to run it are inside.
    """
    used = _used_text(options)
    used_html = f'<span class="op-used">{_esc(used)}</span>' if used else ""
    count = len(options.tries)
    more = f"{count} other way{'s' if count != 1 else ''} to run it" if count else "every option it takes"
    return f'<summary><b>⚙ Ran with</b>{used_html}<span class="op-more">· {more}</span></summary>'


def _tries_html(tries: list[_Try]) -> str:
    """
    Ways to run it again, a line each: the change, the call to copy, and why.
    """
    rows = "".join(f'<span class="op-ch">{_esc(_clip(attempt.change, _VALUE_WIDTH))}</span>'
                   f"<code{_SELECT}>{_esc(attempt.call)}</code>"
                   f'<span class="op-why">{_esc(attempt.why)}</span>' for attempt in tries)
    return f'<div class="op-tries">{rows}</div>'


def _option_row_html(option: _Option) -> str:
    """
    One option's row in the table: its name, the value it ran with, other values it takes and what it does.
    """
    tag = '<span class="op-tag">default</span>' if option.is_default else '<span class="op-tag set">you set</span>'
    value = f"<code{_SELECT}>{_esc(_clip(option.value, _VALUE_WIDTH))}</code>{tag}"
    takes = "".join(f"<code{_SELECT}>{_esc(_clip(value_text, _VALUE_WIDTH))}</code>" for value_text in option.takes)
    return (f'<tr><td class="op-name">{_esc(option.name)}</td><td>{value}</td>'
            f'<td><span class="op-takes">{takes}</span></td><td>{_esc(option.what)}</td></tr>')


def _options_table_html(options: _Options) -> str:
    rows = "".join(_option_row_html(option) for option in options.used)
    head = "<tr><th>Option</th><th>This run</th><th>Also takes</th><th>What it does</th></tr>"
    return f'<table class="op-all">{head}{rows}</table>'


@_draws(_Options, "html")
def _options_html(options: _Options, report: _Report) -> str:
    report.rules.add(_OPTIONS_RULES.format(root=report.root or "op-any"))
    tries = _tries_html(options.tries) if options.tries else ""
    help_line = f'<div class="op-help">Every option in full: <code{_SELECT}>{_esc(options.help_call)}</code></div>'
    body = f'<div class="op-body">{tries}{_options_table_html(options)}{help_line}</div>'
    peek = options.tries[:_TRIES_PEEKING]
    peek_html = f'<div class="op-peek">{_tries_html(peek)}</div>' if peek else ""
    return f'<details class="op">{_summary_html(options)}{body}</details>{peek_html}'


@_draws(_Options, "text")
def _options_lines(options: _Options, report: _Report) -> list[str]:
    used = _used_text(options)
    head = f"Ran with: {used}" if used else f"Options of {options.command}()"
    if not options.tries:
        return ["", f"{head} · every option: {options.help_call}"]
    changes = [_clip(attempt.change, _VALUE_WIDTH) for attempt in options.tries]
    change_width = max(map(len, changes))
    call_width = max(len(attempt.call) for attempt in options.tries)
    lines = ["", f"{head} · other ways to run it:"]
    for change, attempt in zip(changes, options.tries):
        lines.append(f"  {change.ljust(change_width)}  {attempt.call.ljust(call_width)}  {attempt.why}".rstrip())
    lines.append(f"  Every option: {options.help_call}")
    return lines
