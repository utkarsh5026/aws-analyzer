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

It errs towards saying nothing: a claim it can't read is let through, so what it flags is worth checking.
"""

from __future__ import annotations

import bisect
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Iterable

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
    returned, numbers no tool returned or implies, regions it says it checked that no tool read, and claims of none
    that rest on an incomplete read. Empty lists when everything checked out, or when nothing was checked (no tools
    ran, so answer_findings() says that instead).
    """

    unknown_names: list[str] = field(default_factory=list)
    unknown_numbers: list[str] = field(default_factory=list)
    unchecked_regions: list[str] = field(default_factory=list)
    unbacked_nones: list[UnbackedNone] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not (self.unknown_names or self.unknown_numbers or self.unchecked_regions or self.unbacked_nones)



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


def _evidence(answer: AgentAnswer) -> _Evidence:
    """
    What the model was shown for the question: every tool call's result and arguments, the question and what the
    user had open.
    """
    found = _Evidence()
    found.add_text(answer.question)
    if answer.focus is not None:
        for part in answer.focus.parts:
            found.add_text(f"{part.key} {part.value} {part.text}")
    for call in answer.calls:
        _gather(call.args, found)
        _gather(call.result, found)
        _gather(call.coverage, found)
        found.regions |= _call_regions(call)
    found.finish()
    return found



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


def _claimed_text(answer: AgentAnswer) -> str:
    """
    Everything the answer states: its markdown, then each finding's message.
    """
    return "\n".join([answer.text, *(finding.message for finding in answer.findings)])


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


def _marked_names(text: str, answer: AgentAnswer) -> list[str]:
    """
    The names the answer marks as code or bold, and the first argument of each finding's call.
    """
    plain = _without_code(text)
    spans = [*_CODE_SPAN.findall(plain), *_BOLD.findall(plain)]
    for finding in answer.findings:
        first = _FIRST_ARG.search(finding.call or "")
        if first:
            spans.append(first.group(1))
    cleaned = [span.strip().rstrip(_TRAILING) for span in spans]
    return [span for span in cleaned if _is_name(span)]


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
    its times ('24h', '2026-11-02T18:00Z') or regions.
    """
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



def check_grounding(answer: AgentAnswer) -> Grounding:
    """
    What in an answer doesn't match what its tools returned: names and IDs, numbers, regions it says it checked,
    and claims of none over an incomplete read (Grounding). Nothing is checked for an answer that called no tool
    or that failed.
    """
    if not answer.calls or answer.error or not (answer.text or answer.findings):
        return Grounding()
    evidence = _evidence(answer)
    return Grounding(unknown_names(answer, evidence), unknown_numbers(answer, evidence),
                     unchecked_regions(answer, evidence), unbacked_nones(answer))


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


def grounding_findings(answer: AgentAnswer) -> list[tuple[str, str]]:
    """
    What check_grounding() found, as (level, message) pairs saying what to check: claims of none over an incomplete
    read first, then names and IDs no tool returned, numbers no tool returned, and regions no tool read.
    """
    grounding = check_grounding(answer)
    found = [_none_finding(none) for none in grounding.unbacked_nones[:_NONES_SHOWN]]
    if grounding.unknown_names:
        found.append(_names_finding(grounding.unknown_names))
    if grounding.unknown_numbers:
        found.append(_numbers_finding(grounding.unknown_numbers))
    if grounding.unchecked_regions:
        found.append(_regions_finding(grounding.unchecked_regions))
    return found
