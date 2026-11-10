"""
Whether an answer says only what its tools returned. check_grounding() reads the answer's text and its findings for
what it states as fact and checks each against what the question's tool calls handed the model (their results and
arguments), the question itself and what the user had open; grounding_findings() turns what didn't check out into the
report's findings. All pure: no AWS, no model.

What it checks (Grounding):

    unknown_names      ARNs, s3:// paths, request IDs and the `names` and **names** an answer marks as code or bold
                       that nothing the tools returned holds: most likely made up or misspelt.
    unknown_numbers    numbers no tool returned that aren't a count of what one listed, a column's total, a share of
                       two of its numbers, a unit change (ms to s, bytes to GB, a share to %) or a rounding of one.
                       Dates, times of day, years, list numbers, thresholds ("over 5 s") and time windows ("the last
                       24 hours", "3h ago") are left alone: they're the answer's framing, not what it found.
    unchecked_regions  regions the answer says it checked that no tool read.
    unbacked_nones     sentences saying there was none of something ("orders-etl had no errors overnight") that rest
                       on a tool call that failed, was cut short or wasn't run, so it couldn't have shown that. A
                       sentence that says so itself ("of what I could read") is fine.
    unknown_refs       citations ('[9]') that name no tool call. Each tool result starts with its ref (the call's
                       number in the conversation, ToolCall.ref), and the answer cites it after a fact, "[2]", and
                       in a finding's evidence (check_citations, cited_refs).
    miscited           names, IDs and numbers a sentence states that some tool returned but not the calls it cites:
                       the fact may be right, so it's only noted, and `cited` is the share of sentences stating a
                       fact that cite a call.
    unbacked_causes    sentences naming a root cause ("the root cause is X", "caused by X") that don't show both
                       what backs it: the chain of timeline rows (cited tool results, cut at each citation) from X to
                       the failure, in time order, and one cited check that rules out another cause ("other
                       functions on the same role didn't fail [7]"), from a call that read everything. Hedged ones
                       ("a possible cause") are fine. hedge_root_causes() rewords what's left of them as possible
                       causes once the answer is final.

`earlier` are the answers before this one in the same conversation: a follow-up may repeat their tools' results and
cite their calls.

It errs towards saying nothing: a claim it can't read is let through, so what it flags is worth checking.

With AgentAnalyzer(verify=True), a draft that doesn't check out is sent back to the model once before the user sees
it: recheck_request() is the message (each problem, and how to correct it), recheck_said() the line the agent says
meanwhile, and Recheck what the answer keeps of it (answer.recheck), which grounding_findings() reports first.
"""

from __future__ import annotations

import bisect
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Iterable

from .._kit.fmt import _plural
from .._kit.text import _clip
from .budget import _REFUSED

if TYPE_CHECKING:
    from .answers import AgentAnswer, ToolCall

_MAX_DEPTH = 12
_RATIO_VALUES = 80
_LOOSE_FROM = 1000
_LOOSE_SHARE = 0.05
_EPSILON = 1e-9
_NAMED = 4
_NONES_SHOWN = 3
_SENTENCE_CHARACTERS = 160
_NAME_CHARACTERS = 120
_MIN_NAME = 3
_FIRST_YEAR, _LAST_YEAR = 1900, 2100
_WORDS_BEFORE = 3

_FENCE = re.compile(r"```.*?(?:```|$)", re.S)
_CODE_SPAN = re.compile(r"`([^`\n]+)`")
_BOLD = re.compile(r"\*\*([^*\n]+)\*\*")
_ARN = re.compile(r"\barn:aws[a-z-]*:[a-z0-9-]+:[a-z0-9-]*:\d{0,12}:[^\s`'\"<>()\[\]{},;]+")
_S3_PATH = re.compile(r"\bs3://[^\s`'\"<>()\[\]{},;]+")
_REQUEST_ID = re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.I)
_SHORT_ID = re.compile(r"\b(?=[0-9a-f]*\d)(?=[0-9a-f]*[a-f])[0-9a-f]{8}\b")
_REGION = re.compile(r"\b(?:us|eu|ap|sa|ca|me|af|il|mx)(?:-gov)?-(?:north|south|east|west|central|northeast|"
                     r"southeast|northwest|southwest)-\d\b")
_NAME = re.compile(r"[A-Za-z0-9][\w.:/-]*")
_NAME_MARKS = re.compile(r"[-_.]")
_RUNTIME = re.compile(r"(?:python|nodejs|java|ruby|dotnet|provided|go)[\d.]*(?:\.x)?(?:\.al2(?:023)?)?", re.I)
_VERSION = re.compile(r"v?[\d.]+")
_TRAILING = ".,;:!?"
_CITATION = re.compile(r"\s?\[(\d+(?:\s*[,\-–]\s*\d+)*)\]")
_RANGE = re.compile(r"(\d+)\s*[\-–]\s*(\d+)")
_MAX_RANGE = 50
_WORD = re.compile(r"[a-z0-9][\w.\-/:]*")
_RESOURCE_KEY = re.compile(r"(?:^|_)(?:name|function|bucket|key|id|arn|uri|path|location|knowledge_base|data_source|"
                           r"file)s?$", re.I)
_FIRST_ARG = re.compile(r"\(\s*'([^']+)'")
_TEXT_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")
_NUMBER = re.compile(r"(?P<money>\$)?(?P<digits>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)(?P<suffix>[A-Za-z%]*)")
_IDENTIFIER_BEFORE = re.compile(r"[\w\-/.:#+@]")
_IDENTIFIER_AFTER = re.compile(r"[-/:.]\w")
_PER_TIME = re.compile(r"/\s?(?:mo|month|day|hr|hour|yr|year|s|sec|min)\b", re.I)
_NEXT_WORD = re.compile(r"\s?(%|[A-Za-z]+)")
_SENTENCE_BREAK = re.compile(r"(?<=[.!?;])\s+|\n+")
_SENTENCE_EDGES = " \t-*#>|"

_TIME_UNITS = {"ms", "millisecond", "milliseconds", "s", "sec", "secs", "second", "seconds", "min", "mins", "minute",
               "minutes", "h", "hr", "hrs", "hour", "hours", "d", "day", "days", "week", "weeks"}
_SIZE_UNITS = {"b", "byte", "bytes", "kb", "mb", "gb", "tb", "kib", "mib", "gib", "tib"}
_ATTACHED = {"ms", "s", "m", "h", "d", "min", "kb", "mb", "gb", "tb", "b", "k"}
_THOUSANDS = {"k": 1e3}
_MILLIONS = "M"
_PERCENT = {"%", "percent"}
_MONTHS = {"jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "sept", "oct", "nov", "dec", "january",
           "february", "march", "april", "june", "july", "august", "september", "october", "november", "december"}
_WINDOW_WORDS = {"last", "past", "previous", "next", "within", "every", "first"}
_AGO = "ago"
_BOUNDS = {"over", "under", "above", "below", "exceeds", "exceeding", "beyond", "than", "least", "most", "to", "upto"}
_BOUND_MARKS = set("<>≥≤")
_HEDGES = {"about", "around", "roughly", "approximately", "approx", "nearly", "almost", "some", "~", "≈"}
_HEDGE_MARKS = set("~≈")
_SCALES = (1.0, 1e3, 1e-3, 60.0, 1 / 60, 3600.0, 1 / 3600, 86400.0, 1 / 86400, 1024.0, 1 / 1024, 1024.0 ** 2,
           1 / 1024 ** 2, 1024.0 ** 3, 1 / 1024 ** 3, 1e6, 1e-6, 1e9, 1e-9)

_CAUSE_PHRASE = re.compile(
    r"(?P<article>\b(?:the|its|their)\s+(?:root|main|real|underlying|actual)\s+cause\b)"
    r"|(?P<root>\broot\s+cause\b)"
    r"|(?P<is_the>\b(?:is|was)\s+the\s+cause\b)"
    r"|(?P<the_is>\bthe\s+cause\s+(?:is|was)\b)"
    r"|(?P<passive>\b(?:is|are|was|were)\s+caused\s+by\b)"
    r"|(?P<by>\bcaused\s+by\b)"
    r"|(?P<blame>\b(?:is|was)\s+to\s+blame\b)"
    r"|(?P<active>\bcaused\s+(?=(?:the|these|this|those|it|them|all|every|each|\d)\b))",
    re.I)
_CAUSE_BEFORE = {"is_the", "blame", "active"}
_OF_PART = re.compile(r"\s*of\b.*?(?:\b(?:is|was|are|were)\b|:)", re.S | re.I)
_HEDGED = {"article": "a possible cause", "root": "possible cause", "is_the": "{verb} a possible cause",
           "the_is": "a possible cause {verb}", "passive": "{may} caused by", "by": "possibly caused by",
           "blame": "{may} to blame", "active": "may have caused "}
_MAY = {"is": "may be", "are": "may be", "was": "may have been", "were": "may have been"}
_WORD_RUN = re.compile(r"[a-z0-9'’]+")
_HEDGE_WORD = re.compile(r"possibl[ey]|may|might|could|would|likely|probabl[ey]|perhaps|maybe|suspect\w*|seem\w*|"
                         r"appear\w*|unclear|unlikely|unknown|unconfirmed|if|whether|not|never|no|\w+n['’]t|cannot|"
                         r"guess\w*")
_HEDGE_BEFORE = 4
_HEDGE_AFTER = 3
_PLAIN_WORDS = {"the", "and", "its", "their", "this", "that", "these", "those", "from", "with", "for", "into", "then",
                "than", "after", "before", "when", "because", "which", "what", "was", "were", "are", "all", "each",
                "every", "since", "until", "about", "there", "has", "had", "have", "been", "also", "both", "only",
                "just", "not", "out", "one", "set", "new", "now"}
_FAILURE = re.compile(r"fail|error|exception|tim(?:e|ed|es|ing)[- ]?outs?\b|throttl|crash|\boom\b|out[- ]of[- ]memory|"
                      r"\b5\d\d\b|5xx|denied|killed|retr(?:y|ie)|dead[- ]letter|broke|stuck|backlog|alarm|latency|"
                      r"slow|reject|dropped|\blost\b", re.I)
_RULES_OUT = re.compile(
    r"\brul(?:e|ed|es|ing)\b.{0,30}\bout\b|\bnot (?:the|a|what) (?:cause|reason|problem|culprit)\b"
    r"|\b(?:isn't|wasn't|aren't|weren't|is not|was not|are not|were not)\s+(?:the\s+|a\s+)?(?:cause|reason|culprit|"
    r"to blame|related|involved|it)\b"
    r"|\b(?:didn't|did not|don't|do not|doesn't|does not|never|hadn't|had not)\s+(?:also\s+)?(?:fail|error|change|"
    r"time out|throttle|explain|cause|happen|run out|hit|reach)"
    r"|\bno (?:other|change|changes|new|deploys?|deployments?|errors?|failures?|sign|throttl\w*|timeouts?)\b"
    r"|\b(?:unchanged|unaffected|stayed the same|same as before)\b"
    r"|\b(?:still|kept)\s+(?:succeed\w*|work\w*|ran|running|pass\w*)\b"
    r"|\b(?:rather than|instead of|excludes?|excluded)\b"
    r"|\bonly\b.{0,60}\b(?:failed|fails|errors?)\b"
    r"|\b(?:other|others|rest)\b.{0,60}\b(?:fine|healthy|succeeded|ok|normal)\b")
_TIME_OF_DAY = re.compile(r"\b([01]?\d|2[0-3]):([0-5]\d)\b")
_HALF_DAY_MINUTES = 12 * 60

_NONE_SAID = re.compile(
    r"\b(?:no|zero|0)\s+(?:\w+\s+){0,2}?(?:errors?|failures?|failed|timeouts?|timed[- ]out|throttl\w*|issues?|"
    r"problems?|runs?|invocations?|calls?|files?|objects?|keys?|documents?|matches|results?|records?|lines?|"
    r"logs?|events?|exceptions?|uploads?|arrivals?|messages?)\b"
    r"|\b(?:didn't|did not|never|hasn't|has not|haven't|have not|wasn't|was not|weren't|were not)\s+(?:once\s+)?"
    r"(?:fail|failed|error|errored|run|ran|time out|timed out|throttle|throttled|been called|been invoked|invoked|"
    r"called|arrive|arrived|trigger|triggered|change|changed)\b"
    r"|\bnone of\b|\bnothing (?:failed|went wrong|ran|arrived|changed|matched)\b|\ball (?:clear|healthy|succeeded|"
    r"passed)\b|\bno sign of\b|\bnot a single\b|\b(?:is|are|was|were) empty\b",
    re.I)
_LIMIT_SAID = re.compile(
    r"couldn't|could not|can't tell|cannot tell|unable|partial|incomplete|cut short|permission|access ?denied|"
    r"\bonly (?:covers?|read|saw|checked|looked)|what i (?:could )?read|wasn't able|not able|limit|skipped|"
    r"didn't get to|did not get to|left out|not (?:all|every)|so far",
    re.I)
_CHECKED_SAID = re.compile(r"\b(?:checked|covered|looked at|searched|scanned|read)\b", re.I)
_NOT_CHECKED = re.compile(r"\b(?:not|never|didn't|did not|haven't|only)\b|n't\b", re.I)


@dataclass
class UnbackedNone:
    """
    A sentence of the answer saying there was none of something, and the incomplete tool calls it rests on.
    """

    sentence: str
    calls: list[ToolCall] = field(default_factory=list)


@dataclass
class Grounding:
    """
    What in an answer didn't check out against its tool calls (see the module's docstring): names and IDs no tool
    returned, numbers no tool returned or implies, regions it says it checked that no tool read, claims of none
    that rest on an incomplete read, citations that name no tool call, and root causes named without the chain
    from them to the failure and a check ruling out another cause (unbacked_causes). Empty lists when everything checked out,
    or when nothing was checked (no tools ran, so answer_findings() says that instead). `ok` leaves out `miscited`
    (a fact citing a call that doesn't hold it, which another call returned: the fact may be right) and `cited`
    (the share of its sentences stating a fact that cite a call; None when none does), which say how well it cites.
    """

    unknown_names: list[str] = field(default_factory=list)
    unknown_numbers: list[str] = field(default_factory=list)
    unchecked_regions: list[str] = field(default_factory=list)
    unbacked_nones: list[UnbackedNone] = field(default_factory=list)
    unknown_refs: list[str] = field(default_factory=list)
    miscited: list[str] = field(default_factory=list)
    cited: float | None = None
    unbacked_causes: list[UnbackedCause] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        flagged = (self.unknown_names, self.unknown_numbers, self.unchecked_regions, self.unbacked_nones,
                   self.unknown_refs, self.unbacked_causes)
        return not any(flagged)



@dataclass
class _Evidence:
    """
    Everything the model was shown: its text (lowercased, for finding names in), its numbers sorted (for bisect), the
    shares (as %) of numbers that belong together (two of one dict's, an item's against its column's total, and two
    column totals), and the regions the tools read.
    """

    texts: list[str] = field(default_factory=list)
    numbers: list[float] = field(default_factory=list)
    shares: list[float] = field(default_factory=list)
    regions: set[str] = field(default_factory=set)
    text: str = ""

    def add_text(self, value: str) -> None:
        self.texts.append(value)
        for found in _TEXT_NUMBER.findall(value):
            self.numbers.append(float(found.replace(",", "")))

    def add_shares(self, values: Iterable[float]) -> None:
        """
        Each value as a share of each larger one, in %: 35 of 1,200 is 2.9%.
        """
        distinct = sorted({v for v in values if v > 0})[:_RATIO_VALUES]
        for i, part in enumerate(distinct):
            self.shares.extend(part / whole * 100 for whole in distinct[i:])

    def finish(self) -> None:
        self.text = "\n".join(self.texts).lower()
        self.numbers.sort()
        self.shares.sort()


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _gather_dict(value: dict[Any, Any], found: _Evidence, depth: int) -> None:
    """
    A dict's keys as text, the total of its numbers, and what each value holds.
    """
    numbers = [float(v) for v in value.values() if _is_number(v)]
    if len(numbers) > 1:
        found.numbers.append(sum(numbers))
        found.add_shares(numbers)
    for key, item in value.items():
        found.add_text(str(key))
        _gather(item, found, depth + 1)


def _column_totals(items: list[Any]) -> dict[str, float]:
    """
    The total of each numeric field across a list of dicts: [{'errors': 3}, {'errors': 4}] -> {'errors': 7}.
    """
    totals: dict[str, float] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        for key, value in item.items():
            if _is_number(value):
                totals[key] = totals.get(key, 0.0) + float(value)
    return totals


def _column_shares(items: list[Any], totals: dict[str, float]) -> list[float]:
    """
    Each item's value as a share of its column's total, in %: a cause's 31 of 35 errors is 88.6%.
    """
    shares = []
    for item in items:
        if not isinstance(item, dict):
            continue
        for key, total in totals.items():
            if total > 0 and _is_number(item.get(key)):
                shares.append(float(item[key]) / total * 100)
    return shares


def _gather_list(value: list[Any], found: _Evidence, depth: int) -> None:
    """
    How many items a list holds, its columns' totals and the shares they give, its numbers' total, and what each
    item holds.
    """
    found.numbers.append(float(len(value)))
    totals = _column_totals(value)
    found.numbers.extend(totals.values())
    found.add_shares(totals.values())
    found.shares.extend(_column_shares(value, totals))
    numbers = [float(v) for v in value if _is_number(v)]
    if len(numbers) > 1:
        found.numbers.append(sum(numbers))
    for item in value:
        _gather(item, found, depth + 1)


def _gather(value: Any, found: _Evidence, depth: int = 0) -> None:
    """
    Adds what a tool result (or part of one) holds to `found`.
    """
    if depth > _MAX_DEPTH or value is None or isinstance(value, bool):
        return
    if _is_number(value):
        found.numbers.append(float(value))
    elif isinstance(value, str):
        found.add_text(value)
    elif isinstance(value, dict):
        _gather_dict(value, found, depth)
    elif isinstance(value, (list, tuple)):
        _gather_list(value, found, depth)


def _call_regions(call: ToolCall) -> set[str]:
    """
    The regions a call read: its coverage's region and regions, and any region its arguments name.
    """
    coverage = call.coverage or {}
    regions = {str(r) for r in coverage.get("regions") or [] if r}
    if coverage.get("region"):
        regions.add(str(coverage["region"]))
    regions.update(_REGION.findall(" ".join(str(v) for v in call.args.values())))
    return regions


def _evidence_of(calls: Iterable[ToolCall], asked: Iterable[str] = ()) -> _Evidence:
    """
    What these tool calls handed the model (results, arguments and coverage), with `asked`: the questions and what
    the user had open, which the model may repeat too. A task handed to an investigator isn't evidence: its
    conclusion and its task are a model's words, and the calls the investigator made hold what AWS returned.
    """
    found = _Evidence()
    for text in asked:
        found.add_text(text)
    for call in calls:
        if call.is_investigation:
            continue
        _gather(call.args, found)
        _gather(call.result, found)
        _gather(call.coverage, found)
        found.regions |= _call_regions(call)
    found.finish()
    return found


def _asked_of(answer: AgentAnswer) -> list[str]:
    """
    The question as the model was sent it: its words, and each part of what the user had open.
    """
    if answer.focus is None:
        return [answer.question]
    return [answer.question, *(f"{part.key} {part.value} {part.text}" for part in answer.focus.parts)]


def _asked_in(answers: Iterable[AgentAnswer]) -> list[str]:
    """
    What the model was asked across these answers: each question and what the user had open.
    """
    asked: list[str] = []
    for each in answers:
        asked.extend(_asked_of(each))
    return asked


def _evidence(answer: AgentAnswer, earlier: Iterable[AgentAnswer] = ()) -> _Evidence:
    """
    What the model was shown in the conversation up to this answer: every tool call's result and arguments, each
    question and what the user had open, this answer's and the earlier ones' in the same conversation.
    """
    calls: list[ToolCall] = []
    for each in [*earlier, answer]:
        calls.extend(each.calls)
    return _evidence_of(calls, _asked_in([*earlier, answer]))



def _without_code(text: str) -> str:
    """
    The answer with its fenced code blocks taken out: commands to copy, not claims.
    """
    return _FENCE.sub(" ", text)


def _number_text(text: str) -> str:
    """
    The answer's sentences as numbers are read from them: no code blocks, and each code span a blank.
    """
    return _CODE_SPAN.sub(" ", _without_code(text))


def _sentences(text: str) -> list[str]:
    """
    The answer as sentences (and list items and table rows), code blocks out and code spans kept as their text.
    """
    plain = _CODE_SPAN.sub(r"\1", _without_code(text)).replace("**", "")
    parts = (part.strip(_SENTENCE_EDGES) for part in _SENTENCE_BREAK.split(plain))
    return [part for part in parts if part]


def _without_citations(text: str) -> str:
    """
    The text with its citations ('[3]', '[2, 5]') taken out: they name tool calls, not facts.
    """
    return _CITATION.sub("", text)


def _claimed_text(answer: AgentAnswer) -> str:
    """
    Everything the answer states, citations left out: its markdown, then each finding's message.
    """
    return _without_citations("\n".join([answer.text, *(finding.message for finding in answer.findings)]))


def _is_name(token: str) -> bool:
    """
    Whether a code or bold span is a resource's name worth checking: one word with a - _ or . in it ('orders-etl',
    'customer_id', 'handler.py'), not a number, a version, a runtime, a region, an ARN or an s3:// path (checked
    on their own).
    """
    is_word = _NAME.fullmatch(token) is not None and _MIN_NAME <= len(token) <= _NAME_CHARACTERS
    if not is_word or not _NAME_MARKS.search(token):
        return False
    is_other = token.lower().startswith(("s3://", "arn:")) or _REGION.fullmatch(token) is not None
    return not (is_other or _VERSION.fullmatch(token) or _RUNTIME.fullmatch(token))


def _spans_named(spans: list[str]) -> list[str]:
    cleaned = [span.strip().rstrip(_TRAILING) for span in spans]
    return [span for span in cleaned if _is_name(span)]


def _names_marked_in(text: str) -> list[str]:
    """
    The names a piece of text marks as code or bold.
    """
    plain = _without_code(text)
    return _spans_named([*_CODE_SPAN.findall(plain), *_BOLD.findall(plain)])


def _marked_names(text: str, answer: AgentAnswer) -> list[str]:
    """
    The names the answer marks as code or bold, and the first argument of each finding's call.
    """
    calls = [_FIRST_ARG.search(finding.call or "") for finding in answer.findings]
    return [*_names_marked_in(text), *_spans_named([first.group(1) for first in calls if first])]


def _ids(text: str) -> list[str]:
    """
    The ARNs, s3:// paths and request IDs the answer gives, code blocks included (a command it writes must name the
    real resource).
    """
    found = [*_ARN.findall(text), *_S3_PATH.findall(text), *_REQUEST_ID.findall(text), *_SHORT_ID.findall(text)]
    return [item.rstrip(_TRAILING) for item in found]


def _s3_known(path: str, hay: str) -> bool:
    """
    Whether an s3:// path is in what the tools returned: the whole path, or its bucket and its key (or prefix), which
    a listing gives apart.
    """
    path = path.lower()
    bucket, _, key = path[len("s3://"):].partition("/")
    return path in hay or (bucket in hay and key.strip("/") in hay)


def _known(name: str, evidence: _Evidence) -> bool:
    if name.lower().startswith("s3://"):
        return _s3_known(name, evidence.text)
    return name.lower() in evidence.text


def unknown_names(answer: AgentAnswer, evidence: _Evidence | None = None) -> list[str]:
    """
    The names and IDs the answer gives that nothing the tools returned (nor the question) holds, each once, in order.
    """
    evidence = evidence or _evidence(answer)
    text = _claimed_text(answer)
    named = dict.fromkeys([*_ids(text), *_marked_names(text, answer)])
    return [name for name in named if not _known(name, evidence)]



@dataclass
class _Number:
    """
    A number the answer states: its value in the unit it's written in, how far a rounding of it can be from what it
    rounds, what kind it is ('count', 'unit', 'percent' or 'money'), and how it reads ('42 failures').
    """

    value: float
    tolerance: float
    kind: str
    shown: str


def _words_before(prose: str, start: int) -> list[str]:
    return prose[max(0, start - 40):start].lower().split()[-_WORDS_BEFORE:]


def _next_word(prose: str, end: int) -> str:
    found = _NEXT_WORD.match(prose, end)
    return found.group(1).lower() if found else ""


def _is_part_of_something(prose: str, match: re.Match[str]) -> bool:
    """
    Whether a number is part of a name, a date, a time of day or a version rather than a number of its own:
    'python3.12', 'us-east-1', '2026-10-15', '08:00', '7-day', '10/15', 'v2'.
    """
    start, end = match.start(), match.end()
    before = prose[start - 1:start] if start else ""
    after = prose[end:end + 2]
    if before and _IDENTIFIER_BEFORE.match(before):
        return True
    is_rate = bool(match.group("money")) and _PER_TIME.match(prose, end) is not None
    return bool(_IDENTIFIER_AFTER.match(after)) and not is_rate


def _is_list_marker(prose: str, match: re.Match[str]) -> bool:
    line_start = prose.rfind("\n", 0, match.start()) + 1
    return not prose[line_start:match.start()].strip() and prose[match.end():match.end() + 1] in (".", ")")


def _mark_before(prose: str, start: int) -> str:
    """
    The last character before a number that isn't a space ('<' in '< 5 s', '~' in '~40'), or ''.
    """
    before = prose[max(0, start - 2):start].rstrip()
    return before[-1:]


def _is_window(prose: str, match: re.Match[str], before: list[str], next_word: str) -> bool:
    """
    Whether a number is a time window's length: 'the last 24 hours', 'every 5 min', '3 hours ago', '2h ago'.
    """
    is_attached_unit = match.group("suffix").lower() in _TIME_UNITS
    if not (is_attached_unit or next_word in _TIME_UNITS):
        return False
    words_after = prose[match.end():].split()
    word_after_unit = words_after[0:1] if is_attached_unit else words_after[1:2]
    return bool(before and before[-1] in _WINDOW_WORDS) or word_after_unit == [_AGO]


def _is_year(match: re.Match[str], value: float) -> bool:
    is_bare = not (match.group("suffix") or match.group("money")) and match.group("digits").isdigit()
    return is_bare and _FIRST_YEAR <= value <= _LAST_YEAR


def _is_framing(prose: str, match: re.Match[str], value: float) -> bool:
    """
    Whether a number frames the answer rather than reporting what was found: a list number, a year, a day of a month,
    a time window ('the last 24 hours', '3 hours ago'), a threshold ('over 5 s', 'more than 10') or a 0 or 1.
    """
    before = _words_before(prose, match.start())
    next_word = _next_word(prose, match.end())
    is_date = next_word in _MONTHS or bool(before and before[-1] in _MONTHS)
    is_bound = bool(before and before[-1] in _BOUNDS) or _mark_before(prose, match.start()) in _BOUND_MARKS
    is_trivial = match.group("digits").isdigit() and value in (0, 1)
    if is_date or is_bound or is_trivial or _is_year(match, value):
        return True
    return _is_list_marker(prose, match) or _is_window(prose, match, before, next_word)


def _kind(match: re.Match[str], next_word: str) -> str:
    suffix = match.group("suffix").lower()
    if match.group("money"):
        return "money"
    if suffix == "%" or next_word in _PERCENT:
        return "percent"
    units = _TIME_UNITS | _SIZE_UNITS
    return "unit" if suffix in units or next_word in units else "count"


def _hedged(prose: str, start: int) -> bool:
    before = _words_before(prose, start)
    char_before = prose[max(0, start - 1):start]
    return bool(before and before[-1] in _HEDGES) or char_before in _HEDGE_MARKS


def _number_claim(prose: str, match: re.Match[str]) -> _Number | None:
    """
    The number a match is, when it's one the answer reports ('35 failures', '$12.40', '2.3 s', '18%', '1.2K
    requests'); None for one that's part of a name or frames the answer.
    """
    suffix = match.group("suffix")
    is_unit_suffix = not suffix or suffix == "%" or suffix.lower() in _ATTACHED or suffix == _MILLIONS
    if not is_unit_suffix or _is_part_of_something(prose, match):
        return None
    digits = match.group("digits").replace(",", "")
    value = float(digits)
    if _is_framing(prose, match, value):
        return None
    scale = _THOUSANDS.get(suffix, 1e6 if suffix == _MILLIONS else 1.0)
    decimals = len(digits.partition(".")[2])
    tolerance = 0.5 * 10 ** -decimals * scale + _EPSILON
    value *= scale
    if _hedged(prose, match.start()) or (not decimals and value >= _LOOSE_FROM):
        tolerance = max(tolerance, value * _LOOSE_SHARE)
    next_word = _next_word(prose, match.end())
    shown = match.group(0) + (f" {next_word}" if next_word and not suffix and next_word != "%" else "")
    return _Number(value, tolerance, _kind(match, next_word), shown)


def _holds_between(values: list[float], low: float, high: float) -> bool:
    """
    Whether a sorted list holds a value from low to high.
    """
    i = bisect.bisect_left(values, low)
    return i < len(values) and values[i] <= high


def _supported(number: _Number, evidence: _Evidence) -> bool:
    """
    Whether a number is one the tools returned, or follows from them: rounded, in another unit, or as a share.
    """
    low, high = number.value - number.tolerance, number.value + number.tolerance
    if _holds_between(evidence.numbers, low, high):
        return True
    if number.kind == "percent":
        return _holds_between(evidence.numbers, low / 100, high / 100) or _holds_between(evidence.shares, low, high)
    if number.kind == "unit":
        return any(_holds_between(evidence.numbers, low / scale, high / scale) for scale in _SCALES)
    return False


def unknown_numbers(answer: AgentAnswer, evidence: _Evidence | None = None) -> list[str]:
    """
    The numbers the answer reports that no tool returned or implies, as it writes them ('42 failures'), each once.
    """
    evidence = evidence or _evidence(answer)
    prose = _number_text(_claimed_text(answer))
    claims = (_number_claim(prose, match) for match in _NUMBER.finditer(prose))
    unknown = [claim.shown for claim in claims if claim is not None and not _supported(claim, evidence)]
    return list(dict.fromkeys(unknown))



def unchecked_regions(answer: AgentAnswer, evidence: _Evidence | None = None) -> list[str]:
    """
    The regions the answer says it checked ("I checked us-east-1 and eu-west-1") that no tool call read.
    """
    evidence = evidence or _evidence(answer)
    said: list[str] = []
    for sentence in _sentences(answer.text):
        if _CHECKED_SAID.search(sentence) and not _NOT_CHECKED.search(sentence):
            said.extend(_REGION.findall(sentence))
    return [region for region in dict.fromkeys(said) if region not in evidence.regions]


def _is_incomplete(call: ToolCall) -> bool:
    """
    Whether a call can't show that something wasn't there: it failed, was cut short, or wasn't run (but not one
    the user raised a limit for, which runs again as a new call).
    """
    return bool(call.error or (call.refused and call.refused != "raised") or (call.coverage or {}).get("cut_short"))


def _names_of(call: ToolCall) -> list[str]:
    """
    What a call was about, as the answer would name it: its text arguments ('orders-etl', 's3://acme-raw/'), but not
    its times ('24h', '2026-11-02T18:00Z') or regions. A task handed to an investigator names nothing: the calls made
    for it do.
    """
    if call.is_investigation:
        return []
    names = []
    for value in call.args.values():
        is_name = isinstance(value, str) and len(value) >= _MIN_NAME and not value[:1].isdigit()
        if is_name and not _REGION.fullmatch(value):
            names.append(value.lower())
    return names


def _resource_values(value: Any, depth: int = 0) -> set[str]:
    """
    The resources a result names (what its name, function, bucket, key, ID, ARN, path ... fields hold), lowercased.
    """
    if depth > _MAX_DEPTH:
        return set()
    if isinstance(value, (list, tuple)):
        return set().union(*(_resource_values(item, depth + 1) for item in value))
    if not isinstance(value, dict):
        return set()
    found: set[str] = set()
    for key, item in value.items():
        values = item if isinstance(item, (list, tuple)) else [item]
        if _RESOURCE_KEY.search(str(key)):
            found.update(str(v).lower() for v in values if v)
        found |= _resource_values(item, depth + 1)
    return found


def _rests_on(sentence: str, calls: list[ToolCall]) -> list[ToolCall]:
    """
    The incomplete calls a claim of none rests on: the ones about what the sentence names. When it names nothing a
    call was about, every incomplete one, unless it names a resource a complete call's result holds
    ('thumbnailer', from function_health's list of every function). None when a complete call covers it.
    """
    lowered = sentence.lower()
    about = [call for call in calls if any(name in lowered for name in _names_of(call))]
    if about:
        is_covered = any(not _is_incomplete(call) for call in about)
        return [] if is_covered else about
    words = {word.rstrip(_TRAILING) for word in _WORD.findall(lowered)}
    complete = [call for call in calls if not _is_incomplete(call)]
    is_covered = any(words & _resource_values(call.result) for call in complete)
    return [] if is_covered else [call for call in calls if _is_incomplete(call)]


def unbacked_nones(answer: AgentAnswer) -> list[UnbackedNone]:
    """
    The sentences saying there was none of something that rest on an incomplete read, unless the sentence, or the
    one after it, says the read was partial.
    """
    sentences = _sentences(answer.text)
    found = []
    for i, sentence in enumerate(sentences):
        nearby = " ".join(sentences[i:i + 2])
        if not _NONE_SAID.search(sentence) or _LIMIT_SAID.search(nearby):
            continue
        calls = _rests_on(sentence, answer.calls)
        if calls:
            found.append(UnbackedNone(sentence, calls))
    return found


def _refs_of(marker: str) -> list[int]:
    """
    The refs one citation's text names: '3' -> [3], '2, 5' -> [2, 5], '2-4' -> [2, 3, 4] (a range of at most
    _MAX_RANGE).
    """
    refs: list[int] = []
    for part in marker.split(","):
        span = _RANGE.fullmatch(part.strip())
        if span and 0 <= int(span.group(2)) - int(span.group(1)) <= _MAX_RANGE:
            refs.extend(range(int(span.group(1)), int(span.group(2)) + 1))
        elif part.strip().isdigit():
            refs.append(int(part.strip()))
    return refs


def cited_refs(text: str) -> list[int]:
    """
    Every ref the text cites ('orders-etl failed 35 times [2]. Its logs [3, 5] ...' -> [2, 3, 5]), each once, in
    order; code blocks left out.
    """
    refs: list[int] = []
    for marker in _CITATION.findall(_without_code(text)):
        refs.extend(_refs_of(marker))
    return list(dict.fromkeys(refs))


def _cited_pieces(answer: AgentAnswer) -> list[tuple[str, list[int]]]:
    """
    The answer's sentences (and table rows), each with the refs it cites, a citation that stands alone after a
    sentence going with it; then each finding's message with its evidence.
    """
    pieces: list[tuple[str, list[int]]] = []
    for sentence in _sentences(answer.text):
        refs = cited_refs(sentence)
        is_only_citations = not _without_citations(sentence).strip(_SENTENCE_EDGES + _TRAILING)
        if is_only_citations and pieces:
            pieces[-1] = (pieces[-1][0], [*pieces[-1][1], *refs])
        else:
            pieces.append((sentence, refs))
    pieces += [(finding.message, list(finding.evidence)) for finding in answer.findings]
    return pieces


@dataclass
class _Claims:
    """
    What a piece of an answer states as fact: the names and IDs it gives, and the numbers it reports.
    """

    names: list[str]
    numbers: list[_Number]

    @classmethod
    def in_text(cls, text: str) -> _Claims:
        plain = _without_citations(text)
        prose = _number_text(plain)
        numbers = [_number_claim(prose, match) for match in _NUMBER.finditer(prose)]
        return cls([*_ids(plain), *_names_marked_in(plain)], [number for number in numbers if number is not None])

    def __bool__(self) -> bool:
        return bool(self.names or self.numbers)


def calls_by_ref(answer: AgentAnswer, earlier: Iterable[AgentAnswer] = ()) -> dict[int, ToolCall]:
    """
    Every tool call the conversation made up to this answer, by its ref.
    """
    by_ref: dict[int, ToolCall] = {}
    for each in [*earlier, answer]:
        for call in each.calls:
            if call.ref:
                by_ref[call.ref] = call
    return by_ref


def _cited_calls(refs: list[int], by_ref: dict[int, ToolCall]) -> list[ToolCall]:
    """
    The calls these refs cite. Citing a task handed to an investigator cites the calls the investigator made for it,
    which hold what its conclusion says.
    """
    cited: list[ToolCall] = []
    for ref in refs:
        call = by_ref.get(ref)
        if call is None:
            continue
        cited.append(call)
        if call.is_investigation:
            cited.extend(made for made in by_ref.values() if made.within == ref)
    return cited


def _miscited(claims: _Claims, refs: list[int], cited: _Evidence, evidence: _Evidence) -> list[str]:
    """
    The claims a piece states that some tool returned but not the calls it cites, as "'35 failures' [2]".
    """
    marker = f"[{', '.join(map(str, refs))}]"
    found = [f"'{name}' {marker}" for name in claims.names if _known(name, evidence) and not _known(name, cited)]
    for number in claims.numbers:
        if _supported(number, evidence) and not _supported(number, cited):
            found.append(f"'{number.shown}' {marker}")
    return found


@dataclass
class _Citations:
    """
    How an answer cites its tool calls: refs that name no call, claims that cite a call not holding them, and the
    share of its sentences and findings stating a fact that cite one (None when none states one).
    """

    unknown: list[str] = field(default_factory=list)
    miscited: list[str] = field(default_factory=list)
    share: float | None = None


def check_citations(answer: AgentAnswer, evidence: _Evidence, earlier: Iterable[AgentAnswer] = ()) -> _Citations:
    """
    The answer's citations checked against the conversation's tool calls: each ref it cites (in its text and its
    findings' evidence) names a call, and each sentence's names, IDs and numbers are in the calls it cites.
    """
    earlier = list(earlier)
    by_ref = calls_by_ref(answer, earlier)
    asked = _asked_in([*earlier, answer])
    found = _Citations()
    stating = cited = 0
    for text, refs in _cited_pieces(answer):
        found.unknown += [f"[{ref}]" for ref in refs if ref not in by_ref]
        claims = _Claims.in_text(text)
        calls = _cited_calls(refs, by_ref)
        if claims and calls:
            found.miscited += _miscited(claims, refs, _evidence_of(calls, asked), evidence)
        stating += bool(claims)
        cited += bool(claims and refs)
    found.unknown = list(dict.fromkeys(found.unknown))
    found.miscited = list(dict.fromkeys(found.miscited))
    found.share = cited / stating if stating else None
    return found


@dataclass
class UnbackedCause:
    """
    A sentence of the answer (or a finding's message) that names a root cause without what backs it: `missing` holds
    'chain' (no chain of cited tool results from the cause to the failure, in time order) and 'check' (no cited check
    that rules out another cause), either or both.
    """

    sentence: str
    missing: list[str] = field(default_factory=list)


@dataclass
class _Row:
    """
    One row of the answer's timeline: the text up to and including a citation (or a sentence's uncited end), where it
    is in the answer, lowercased with its citations out, and the refs it cites.
    """

    start: int
    end: int
    text: str
    refs: list[int]


@dataclass
class _CauseClaim:
    """
    A sentence that names a root cause with no hedge: where it is, and its root-cause phrases (each to reword).
    """

    start: int
    end: int
    phrases: list[re.Match[str]]


def _prose_of(text: str) -> str:
    """
    The text with its fenced code blocks blanked out, the same length, so positions in it are positions in the text.
    """
    return _FENCE.sub(lambda block: re.sub(r"[^\n]", " ", block.group()), text)


def _sentence_spans(prose: str) -> list[tuple[int, int]]:
    """
    Where each sentence (and list item and table row) of the prose starts and ends.
    """
    spans, start = [], 0
    for brk in _SENTENCE_BREAK.finditer(prose):
        spans.append((start, brk.start()))
        start = brk.end()
    spans.append((start, len(prose)))
    return [(begin, end) for begin, end in spans if prose[begin:end].strip(_SENTENCE_EDGES)]


def _row_text(piece: str) -> str:
    return _without_citations(piece).strip(_SENTENCE_EDGES + _TRAILING).lower().replace("’", "'")


def _rows_in(prose: str, begin: int, end: int, rows: list[_Row]) -> None:
    """
    Adds a sentence's rows: its text cut after each citation, a citation standing alone going with the row before.
    """
    position = begin
    for citation in _CITATION.finditer(prose, begin, end):
        text = _row_text(prose[position:citation.end()])
        refs = _refs_of(citation.group(1))
        if not text and rows:
            rows[-1].refs.extend(refs)
        else:
            rows.append(_Row(position, citation.end(), text, refs))
        position = citation.end()
    tail = _row_text(prose[position:end])
    if tail:
        rows.append(_Row(position, end, tail, []))


def _timeline_rows(prose: str) -> list[_Row]:
    rows: list[_Row] = []
    for begin, end in _sentence_spans(prose):
        _rows_in(prose, begin, end, rows)
    return rows


def _is_hedged(prose: str, begin: int, phrase: re.Match[str]) -> bool:
    """
    Whether a root-cause phrase is hedged or denied by the words around it in its sentence: 'a possible root cause',
    'the root cause may be', 'isn't caused by', 'if the root cause is'.
    """
    before = _WORD_RUN.findall(prose[begin:phrase.start()].lower())[-_HEDGE_BEFORE:]
    after = _WORD_RUN.findall(prose[phrase.end():phrase.end() + _NAME_CHARACTERS].lower())[:_HEDGE_AFTER]
    return any(_HEDGE_WORD.fullmatch(word) for word in [*before, *_WORD_RUN.findall(phrase.group().lower()), *after])


def _cause_claims(prose: str) -> list[_CauseClaim]:
    """
    The sentences that name a root cause and don't hedge it, each with its root-cause phrases.
    """
    claims = []
    for begin, end in _sentence_spans(prose):
        phrases = [found for found in _CAUSE_PHRASE.finditer(prose, begin, end) if not _is_hedged(prose, begin, found)]
        if phrases:
            claims.append(_CauseClaim(begin, end, phrases))
    return claims


def _cause_text(prose: str, claim: _CauseClaim) -> str:
    """
    The part of a claim that names the cause, up to its first citation: what comes after 'the root cause (of the
    errors) is' or 'caused by', or before 'is the cause' or 'caused the'.
    """
    phrase = claim.phrases[0]
    if phrase.lastgroup in _CAUSE_BEFORE:
        named = prose[claim.start:phrase.start()]
    else:
        named = prose[phrase.end():claim.end]
        of_part = _OF_PART.match(named)
        named = named[of_part.end():] if of_part else named
    return _CITATION.split(named)[0]


def _cause_words(named: str) -> set[str]:
    """
    The words a claim names its cause with, less the failure's words and the small ones.
    """
    words = set(_WORD_RUN.findall(named.lower()))
    return {word for word in words if len(word) >= _MIN_NAME and word not in _PLAIN_WORDS and not _FAILURE.search(word)}


def _minute_of(text: str) -> int | None:
    found = _TIME_OF_DAY.search(text)
    return int(found.group(1)) * 60 + int(found.group(2)) if found else None


def _in_order(cause: _Row, failure: _Row) -> bool:
    """
    Whether a cause's row can have led to a failure's: true unless both give a time of day and the cause's is later,
    by less than half a day (more is the night between them).
    """
    cause_minute, failure_minute = _minute_of(cause.text), _minute_of(failure.text)
    if cause_minute is None or failure_minute is None:
        return True
    return not 0 < cause_minute - failure_minute < _HALF_DAY_MINUTES


def _has_chain(rows: list[_Row], words: set[str]) -> bool:
    """
    Whether the rows hold a chain: a cited row about the cause (one of its words) and another cited row about the
    failure, in time order.
    """
    causes = [row for row in rows if words & set(_WORD_RUN.findall(row.text))]
    failures = [row for row in rows if _FAILURE.search(row.text)]
    return any(cause is not failure and _in_order(cause, failure) for cause in causes for failure in failures)


def _missing_backing(rows: list[_Row], named: str, by_ref: dict[int, ToolCall]) -> list[str]:
    """
    What a root-cause claim lacks among the rows it stands with: 'chain' and 'check' (see UnbackedCause).
    """
    cited = [row for row in rows if any(ref in by_ref for ref in row.refs)]
    checks = [row for row in cited if _RULES_OUT.search(row.text)]
    complete_checks = [row for row in checks if any(not _is_incomplete(by_ref[r]) for r in row.refs if r in by_ref)]
    links = [row for row in cited if row not in checks]
    missing = [] if _has_chain(links, _cause_words(named)) else ["chain"]
    return missing if complete_checks else [*missing, "check"]


def _passage(rows: list[_Row], claims: list[_CauseClaim], i: int) -> list[_Row]:
    """
    The rows a claim stands with: from the end of the claim before it to the start of the one after it.
    """
    low = claims[i - 1].end if i else 0
    high = claims[i + 1].start if i + 1 < len(claims) else max((row.end for row in rows), default=0) + 1
    return [row for row in rows if low <= row.start < high]


@dataclass
class _CauseCheck:
    """
    One piece of the answer checked for root causes: its text, the claims in it, and what each lacks.
    """

    text: str
    claims: list[_CauseClaim]
    missing: list[list[str]]

    def unbacked(self) -> list[tuple[_CauseClaim, list[str]]]:
        return [(claim, missing) for claim, missing in zip(self.claims, self.missing) if missing]


def _checked_causes(answer: AgentAnswer, earlier: Iterable[AgentAnswer]) -> list[_CauseCheck]:
    """
    The answer's text, then each finding's message, with the root causes they name and what backs each: the text's
    claims by the rows around them, a finding's by the whole text and its own message, cited or with its evidence.
    """
    by_ref = calls_by_ref(answer, earlier)
    prose = _prose_of(answer.text)
    rows = _timeline_rows(prose)
    claims = _cause_claims(prose)
    checks = [_CauseCheck(answer.text, claims, [
        _missing_backing(_passage(rows, claims, i), _cause_text(prose, claim), by_ref)
        for i, claim in enumerate(claims)])]
    for finding in answer.findings:
        own = _timeline_rows(finding.message)
        if own and not own[-1].refs:
            own[-1].refs = list(finding.evidence)
        found = _cause_claims(finding.message)
        checks.append(_CauseCheck(finding.message, found, [
            _missing_backing([*rows, *own], _cause_text(finding.message, claim), by_ref) for claim in found]))
    return checks


def _said(text: str, claim: _CauseClaim) -> str:
    """
    A claim's sentence as it reads: without its citations, code and bold marks, or a list's dash.
    """
    sentence = _CODE_SPAN.sub(r"\1", _without_citations(text[claim.start:claim.end]))
    return sentence.replace("**", "").strip(_SENTENCE_EDGES)


def unbacked_causes(answer: AgentAnswer, earlier: Iterable[AgentAnswer] = ()) -> list[UnbackedCause]:
    """
    The sentences naming a root cause ("The root cause is the memory setting.") without the chain of cited tool
    results from it to the failure, in time order, or without a cited check that rules out another cause ("Other
    functions on the same role didn't fail [7]."), from a call that read everything. Hedged ones ("a possible cause")
    are fine.
    """
    found = []
    for check in _checked_causes(answer, list(earlier)):
        for claim, missing in check.unbacked():
            found.append(UnbackedCause(_said(check.text, claim), missing))
    return found


def _hedged_phrase(phrase: re.Match[str]) -> str:
    """
    A root-cause phrase as a possible cause: 'The root cause' -> 'A possible cause', 'were caused by' -> 'may have
    been caused by', 'caused the' -> 'may have caused the'.
    """
    said = phrase.group()
    verb = said.split()[0].lower() if phrase.lastgroup in ("is_the", "passive", "blame") else ""
    if phrase.lastgroup == "the_is":
        verb = said.split()[-1].lower()
    hedged = _HEDGED[phrase.lastgroup or ""].format(verb=verb, may=_MAY.get(verb, ""))
    return hedged[:1].upper() + hedged[1:] if said[:1].isupper() else hedged


def _reworded(text: str, claims: list[_CauseClaim]) -> str:
    phrases = sorted((phrase for claim in claims for phrase in claim.phrases), key=lambda found: found.start())
    for phrase in reversed(phrases):
        text = text[:phrase.start()] + _hedged_phrase(phrase) + text[phrase.end():]
    return text


def hedge_root_causes(answer: AgentAnswer, earlier: Iterable[AgentAnswer] = ()) -> list[str]:
    """
    Rewords each root cause the answer names without its chain and its ruling-out check (unbacked_causes) as a
    possible one, in its text and findings ("The root cause is X" -> "A possible cause is X"), and returns the
    sentences as they were first written. Pure but for the answer it changes; nothing for a sound answer.
    """
    checks = _checked_causes(answer, list(earlier))
    first: list[str] = []
    for check in checks:
        first += [_said(check.text, claim) for claim, _ in check.unbacked()]
    answer.text = _reworded(answer.text, [claim for claim, _ in checks[0].unbacked()])
    for finding, check in zip(answer.findings, checks[1:]):
        finding.message = _reworded(finding.message, [claim for claim, _ in check.unbacked()])
    return first


def check_grounding(answer: AgentAnswer, earlier: Iterable[AgentAnswer] = ()) -> Grounding:
    """
    What in an answer doesn't match what its tools returned: names and IDs, numbers, regions it says it checked,
    claims of none over an incomplete read, its citations, and root causes it names without their evidence
    (Grounding). `earlier` are the answers before it in
    the same conversation, whose tool calls it may repeat and cite. Nothing is checked for an answer that failed, or
    when no tool has been called in the conversation.
    """
    earlier = list(earlier)
    has_calls = bool(answer.calls) or any(each.calls for each in earlier)
    if not has_calls or answer.error or not (answer.text or answer.findings):
        return Grounding()
    evidence = _evidence(answer, earlier)
    citations = check_citations(answer, evidence, earlier)
    return Grounding(unknown_names(answer, evidence), unknown_numbers(answer, evidence),
                     unchecked_regions(answer, evidence), unbacked_nones(answer), citations.unknown,
                     citations.miscited, citations.share, unbacked_causes(answer, earlier))


def _listed(items: list[str]) -> str:
    """
    "'a', 'b' and 'c'", or the first _NAMED and how many more.
    """
    quoted = [f"'{item}'" for item in items[:_NAMED]]
    if len(items) > _NAMED:
        return ", ".join(quoted) + f" and {len(items) - _NAMED} more"
    return quoted[0] if len(quoted) == 1 else ", ".join(quoted[:-1]) + f" and {quoted[-1]}"


@dataclass
class _Words:
    """
    How a finding about a list of things reads, one or more: 'A name ... isn't ... It ... it', or '3 names ... aren't
    ... They ... them'.
    """

    some: str
    isnt: str
    they: str
    them: str

    @classmethod
    def of(cls, items: list[str], noun: str) -> _Words:
        if len(items) == 1:
            return cls(f"A {noun}", "isn't", "It", "it")
        return cls(f"{len(items)} {noun}s", "aren't", "They", "them")


def _why_incomplete(call: ToolCall) -> str:
    if call.error:
        return f"couldn't read everything ({call.error})"
    if call.refused:
        return f"was {_REFUSED.get(call.refused, 'not run')}"
    return "stopped before the end of what it was reading"


def _none_finding(none: UnbackedNone) -> tuple[str, str]:
    reasons = "; ".join(f"{call.call} {_why_incomplete(call)}" for call in none.calls)
    return ("warn", f"The answer says there was none, but the read behind it was incomplete: "
                    f"“{_clip(none.sentence, _SENTENCE_CHARACTERS)}” rests on {reasons}. That read can't show there "
                    "was none, so treat it as unchecked until the call reads everything.")


def _names_finding(names: list[str]) -> tuple[str, str]:
    w = _Words.of(names, "name")
    return ("warn", f"{w.some} in the answer {w.isnt} in anything the tools returned: {_listed(names)}. {w.they} may "
                    f"be made up or misspelt, so check {w.them} in What it checked below before you act on {w.them}, "
                    f"or ask again naming {w.them} so the agent looks {w.them} up.")


def _numbers_finding(numbers: list[str]) -> tuple[str, str]:
    w = _Words.of(numbers, "number")
    return ("warn", f"{w.some} in the answer {w.isnt} in anything the tools returned: {_listed(numbers)}. Nor can "
                    f"{w.they.lower()} be worked out from their numbers as a count, total, share, rounding or unit "
                    f"change, so the model may have made {w.them} up; check {w.them} in What it checked below before "
                    f"you rely on {w.them}.")


def _regions_finding(regions: list[str]) -> tuple[str, str]:
    w = _Words.of(regions, "region")
    return ("warn", f"The answer says it checked {w.some.lower()} no tool read: {_listed(regions)}. Ask again naming "
                    f"the region, or all regions, to have {w.them} checked.")


def _refs_finding(refs: list[str]) -> tuple[str, str]:
    w = _Words.of(refs, "citation")
    return ("warn", f"{w.some} in the answer {'points' if len(refs) == 1 else 'point'} at no tool call: "
                    f"{', '.join(refs[:_NAMED])}. What {w.they.lower()} {'backs' if len(refs) == 1 else 'back'} has "
                    "no source, so check it in What it checked below, where # is each call's ref.")


def _miscited_finding(miscited: list[str]) -> tuple[str, str]:
    w = _Words.of(miscited, "fact")
    return ("info", f"{w.some} in the answer {'cites' if len(miscited) == 1 else 'cite'} a tool call that doesn't hold "
                    f"{w.them}: {', '.join(miscited[:_NAMED])}. Another call returned {w.them}, so {w.they.lower()} "
                    "may be right; find the source in What it checked below, where # is each call's ref.")


_BACKING = {"chain": "chain of cited tool results from the cause to the failure",
            "check": "cited check that rules out another cause"}


def _backing_missing(missing: list[str]) -> str:
    return " and no ".join(_BACKING[part] for part in missing)


def _cause_finding(cause: UnbackedCause) -> tuple[str, str]:
    sentence = _clip(cause.sentence, _SENTENCE_CHARACTERS)
    return ("warn", f"The answer names a root cause without the evidence for it: “{sentence}” shows no "
                    f"{_backing_missing(cause.missing)}. Treat it as a possible cause; ask the agent for the timeline "
                    "from the cause to the failure and what it ruled out.")


def _hedged_finding(answer: AgentAnswer) -> list[tuple[str, str]]:
    """
    What the reader should know when a root cause the answer named was reworded as a possible one.
    """
    if not answer.hedged:
        return []
    quoted = "; ".join(f"“{_clip(sentence, _SENTENCE_CHARACTERS)}”" for sentence in answer.hedged[:_NAMED])
    return [("info", f"It named a root cause it didn't back up, so the answer says “a possible cause” instead: "
                     f"{quoted} A root cause needs the chain of cited tool results from it to the failure and one "
                     "cited check that rules out another cause. Ask for the timeline and what it ruled out to confirm "
                     "it.")]


def grounding_findings(answer: AgentAnswer, earlier: Iterable[AgentAnswer] = ()) -> list[tuple[str, str]]:
    """
    What check_grounding() found, as (level, message) pairs saying what to check: how its first draft was corrected
    (answer.recheck) and the root causes reworded as possible ones (answer.hedged) first, then root causes named
    without their evidence, claims of none over an incomplete read, names and IDs no tool returned, numbers no
    tool returned, regions no tool read, citations that name no tool call, and facts citing a call that doesn't hold
    them. `earlier` are the answers before it in the conversation.
    """
    grounding = check_grounding(answer, earlier)
    found = _recheck_findings(answer) + _hedged_finding(answer)
    found += [_cause_finding(cause) for cause in grounding.unbacked_causes[:_NONES_SHOWN]]
    found += [_none_finding(none) for none in grounding.unbacked_nones[:_NONES_SHOWN]]
    if grounding.unknown_names:
        found.append(_names_finding(grounding.unknown_names))
    if grounding.unknown_numbers:
        found.append(_numbers_finding(grounding.unknown_numbers))
    if grounding.unchecked_regions:
        found.append(_regions_finding(grounding.unchecked_regions))
    if grounding.unknown_refs:
        found.append(_refs_finding(grounding.unknown_refs))
    if grounding.miscited:
        found.append(_miscited_finding(grounding.miscited))
    return found


@dataclass
class Recheck:
    """
    How an answer's first draft was checked before the user saw it (AgentAnalyzer(verify=True)): what in it no tool
    returned, and how the model's one turn to correct it went. Plain data, so a saved chat keeps it.

        names, numbers, regions  what check_grounding() flagged in the draft (Grounding's lists)
        nones       the draft's sentences that claimed none over an incomplete read
        refs        the citations in the draft that named no tool call ('[9]')
        causes      the draft's sentences that named a root cause without its chain and its ruling-out check
        first_text  the draft as the model first wrote it
        tool_calls  the tool calls the model made to correct it
        fixed       whether the answer it gave then checks out; None while unfinished, or when correcting failed
        failed      why correcting it failed, when it did: the answer is then the first draft
    """

    names: list[str] = field(default_factory=list)
    numbers: list[str] = field(default_factory=list)
    regions: list[str] = field(default_factory=list)
    nones: list[str] = field(default_factory=list)
    refs: list[str] = field(default_factory=list)
    causes: list[str] = field(default_factory=list)
    first_text: str = ""
    tool_calls: int = 0
    fixed: bool | None = None
    failed: str = ""

    @classmethod
    def of(cls, grounding: Grounding, first_text: str) -> Recheck:
        return cls(list(grounding.unknown_names), list(grounding.unknown_numbers), list(grounding.unchecked_regions),
                   [none.sentence for none in grounding.unbacked_nones], list(grounding.unknown_refs),
                   [cause.sentence for cause in grounding.unbacked_causes], first_text=first_text)


_RECHECK = """[Before the user sees your answer, check it: part of it isn't in what your tools returned.
{problems}
Fix each one: call a tool to read what it needs, take the claim out, or say you couldn't check it. A number you
worked out from the tools' numbers is fine if the answer says how. Don't call the tools you already called again with
the same arguments. Then give the whole answer again{how}, as if for the first time: the user never saw this draft.]"""
_RECHECK_REPLY = " with final_answer, with its findings"


def _none_problem(none: UnbackedNone) -> str:
    reasons = "; ".join(f"{call.call} {_why_incomplete(call)}" for call in none.calls)
    return f"- “{_clip(none.sentence, _SENTENCE_CHARACTERS)}” says there was none, but {reasons}: it can't show that."


def _cause_problem(cause: UnbackedCause) -> str:
    return (f"- “{_clip(cause.sentence, _SENTENCE_CHARACTERS)}” names a root cause but shows no "
            f"{_backing_missing(cause.missing)}. Show both, each step cited with its time (\"Root cause: memory cut "
            "from 1024 to 256 MB at 01:52 [4], then out-of-memory errors from 02:00 [3]. Other functions on the same "
            "role didn't fail [7].\"), reading what you need, or call it \"a possible cause\".")


def recheck_request(grounding: Grounding, *, reply: bool = True) -> str:
    """
    What the model is sent when its draft doesn't check out: each problem check_grounding() found, a line each,
    and how to correct it (read it, take it out, or say it couldn't be checked), then to answer again, with
    final_answer when `reply`.
    """
    problems = [_none_problem(none) for none in grounding.unbacked_nones]
    problems += [_cause_problem(cause) for cause in grounding.unbacked_causes]
    if grounding.unknown_names:
        problems.append(f"- Names and IDs no tool returned: {_listed(grounding.unknown_names)}.")
    if grounding.unknown_numbers:
        problems.append(f"- Numbers no tool returned, nor a count, total, share or unit change of theirs: "
                        f"{_listed(grounding.unknown_numbers)}.")
    if grounding.unchecked_regions:
        problems.append(f"- You say you checked {_listed(grounding.unchecked_regions)}, but no tool read "
                        f"{_Words.of(grounding.unchecked_regions, 'region').them}.")
    if grounding.unknown_refs:
        problems.append(f"- Citations no tool result carries: {', '.join(grounding.unknown_refs)}. Cite only the "
                        "refs your tool results start with.")
    return _RECHECK.format(problems="\n".join(problems), how=_RECHECK_REPLY if reply else "")


def _flagged_parts(recheck: Recheck) -> str:
    """
    What a check flagged, in a few words: "2 names, a number and a claim of none over an incomplete read".
    """
    kinds = ((recheck.names, "name"), (recheck.numbers, "number"), (recheck.refs, "citation"))
    parts = [_Words.of(items, noun).some.lower() for items, noun in kinds if items]
    if recheck.regions:
        parts.append(f"{_Words.of(recheck.regions, 'region').some.lower()} said to be checked")
    if recheck.nones:
        parts.append(f"{_Words.of(recheck.nones, 'claim').some.lower()} of none over an incomplete read")
    if recheck.causes:
        parts.append(f"{_Words.of(recheck.causes, 'root cause').some.lower()} without its evidence")
    return parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + f" and {parts[-1]}"


def recheck_said(grounding: Grounding) -> str:
    """
    The line the agent says while it corrects its draft (the chat window shows it as one of its steps).
    """
    flagged = _flagged_parts(Recheck.of(grounding, ""))
    return (f"Before I answer, I checked my draft against what the tools returned: {flagged} didn't match, so "
            "I'll correct it.")


def _recheck_findings(answer: AgentAnswer) -> list[tuple[str, str]]:
    """
    What the reader should know when the answer's first draft was corrected: what didn't check out, and whether the
    answer they see is the correction or, when correcting failed, the draft.
    """
    recheck = answer.recheck
    if recheck is None:
        return []
    flagged = _flagged_parts(recheck)
    found = (f"It checked its first draft against what the tools returned and found {flagged} they didn't back up: "
             "it was asked to correct the draft before you saw it.")
    if recheck.failed:
        return [("info", f"{found} That failed ({recheck.failed}), so the answer shown is the draft; check what "
                         "the warnings here name.")]
    read = f"read more with {_plural(recheck.tool_calls, 'tool call')} and " if recheck.tool_calls else ""
    if recheck.fixed is False:
        return [("info", f"{found} It {read}rewrote it, but some of it still doesn't check out: see the warnings "
                         "here.")]
    return [("info", f"{found} It {read}rewrote it, and the answer shown checks out.")]
