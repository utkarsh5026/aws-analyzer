"""
MessagingView: the notebook UI, which renders what MessagingAnalyzer returns as cards and tables.
"""

from __future__ import annotations

import json
from typing import Any

from ..._kit.blocks import _Cards, _Findings, _Next, _Note, _Table, _Text, _Title, _Tone
from ..._kit.fmt import _count, _plural, human_money
from ..._kit.render import _call
from ..._kit.deps import _require
from ..._kit.errors import _Hint
from ..._kit.view import _BaseView, _friendly_errors

from ..analysis import (
    Blocked, access_fixes, dead_letters_from, describe_pattern, dlq_holding, dlq_too_short, overview_findings,
    queue_findings, queue_monthly, queue_policy_command, queue_policy_with, queue_statement, rule_findings,
    topic_findings, topic_monthly,
)
from ..analysis.policy import EVENTS_PRINCIPAL, SNS_PRINCIPAL
from ..analyzer import MessagingAnalyzer
from ..helpers import seconds_words
from ..models import Hop, MessageMap, Overview, QueueReport, RuleReport, TopicReport
from ..style import _render_html
from .tables import (
    dead_letters_table, days_table, queue_settings, queues_table, readers_table, rules_table, senders_table,
    subscriptions_table, targets_table, topics_table,
)


_CANT_PEEK = ("This never shows what's in a queue: reading a message (ReceiveMessage) hides it from its readers for "
              "the visibility timeout and counts as a try towards its dead-letter queue, so even a look changes "
              "what happens to it. Why messages failed is in their reader's logs.")

_PRICES_NOTE = ("Costs are estimates at the first price tier after the free tier: SQS $0.40 per million requests "
                "($0.50 FIFO; every message sent, received and deleted counted as one, though batches make it "
                "fewer), SNS $0.50 per million published plus $0.60 per million HTTPS and $20 per million email "
                "deliveries (SQS and Lambda are free).")


def _card(card: tuple[str, ...]) -> tuple[str, ...]:
    """
    A card without an empty tone: (label, value) or (label, value, tone).
    """
    return card if len(card) == 2 or card[2] else card[:2]


def _warned(findings: list[tuple[str, str]], *words: str) -> bool:
    text = " ".join(message for level, message in findings if level == "warn")
    return any(word in text for word in words)


def _fixes_block(blocked: list[Blocked]) -> list[Any]:
    """
    The commands that let blocked senders in, under 'Let them in', one per receiver.
    """
    if not blocked:
        return []
    lines = []
    for entry in blocked:
        note = f" ({entry.note})" if entry.note else ""
        lines += [f"# {entry.sender} -> {entry.receiver_kind} {entry.receiver}{note}", entry.command, ""]
    return [_Text("\n".join(lines).rstrip(), title="Let them in", code=True)]


def _error_notes(errors: dict[str, str], metrics_read: int, metric_price: float) -> list[Any]:
    notes: list[Any] = []
    for part, code in errors.items():
        notes.append(_Note(f"Its {part} couldn't be read ({code}): README lists the read-only permissions.", "warn"))
    if metrics_read:
        notes.append(_Note(f"Read {metrics_read:,} CloudWatch metrics (about {human_money(metrics_read * metric_price)})."))
    return notes


def _hop_label(hop: Hop) -> str:
    return f"{hop.branch}{hop.name}"


def _hop_problem(hop: Hop) -> Any:
    return _Tone(hop.problem, hop.tone) if hop.problem else ""


def _info_call(hop: Hop) -> str:
    """
    The command that shows one hop in full: queue_info, topic_info or rule_info, or '' for anything else.
    """
    command = {"SQS queue": "queue_info", "SNS topic": "topic_info", "EventBridge rule": "rule_info"}.get(hop.kind)
    return _call(command, hop.name) if command else ""


class MessagingView(_BaseView):
    """
    Notebook UI for SQS, SNS and EventBridge. Every public method renders a report and returns None; the data is on
    self.core (a MessagingAnalyzer).
    """

    _render_html = staticmethod(_render_html)
    _GROUPS = {
        "🗺️ Where messages go": ("overview", "follow", "dead_letters"),
        "🔎 One queue, topic or rule": ("queue_info", "topic_info", "rule_info"),
        "🖱️ Explore by clicking": ("explore",),
        "❓ Help": ("help",),
    }
    _START = (
        ("overview()", "every queue, topic and rule: what's waiting, what failed, what it costs"),
        ("follow('topic-or-queue')", "where a message goes from there, hop by hop, and where it gets lost"),
        ("dead_letters()", "the messages that failed for good, and how to move them back"),
    )
    _PROGRESS = ("Reading", "parts")

    explorer: Any = None

    def __init__(
        self,
        core: MessagingAnalyzer | None = None,
        *,
        mode: str = "auto",
        max_rows: int = 50,
        progress: str = "auto",
    ):
        super().__init__(mode=mode, max_rows=max_rows, progress=progress)
        self.core: MessagingAnalyzer = core or MessagingAnalyzer()

    def _explain(self, code: str, message: str) -> str:
        """
        The AWS error message plus what to do about it.
        """
        text = message.rstrip() + ("" if message.rstrip().endswith((".", "!", "?")) else ".")
        if code in ("AWS.SimpleQueueService.NonExistentQueue", "QueueDoesNotExist", "NotFound",
                    "ResourceNotFoundException"):
            return f"{text} Queues, topics and rules are regional (this is {self.core.region}): overview() lists them."
        if code in ("AccessDenied", "AccessDeniedException", "AuthorizationError") or "not authorized" in message:
            return f"{text} README lists the read-only IAM permissions each command needs."
        if code in ("ThrottlingException", "Throttling", "RequestThrottled"):
            return f"{text} AWS throttled the call: wait a few seconds and retry."
        return message


    @_friendly_errors
    def overview(self, *, days: int = 7) -> None:
        """
        Every queue, topic and EventBridge rule in the region: how many messages wait and how old the oldest is,
        what's in the dead-letter queues, which deliveries failed and what it all costs over the last 7 days, with
        what needs attention first.
        """
        with self._progress("Reading queues, topics and rules", unit="parts") as tick:
            overview = self.core.overview(days=days, progress=tick)
        self._show(self._overview_blocks(overview))

    def _overview_cards(self, overview: Overview) -> list[tuple[str, ...]]:
        prices, days = self.core.prices, overview.days
        dead = sum(q.messages for q in overview.queues if q.is_dlq)
        failed = sum(n.failed for n in overview.topic_numbers.values())
        failed += sum(n.failed for n in overview.rule_numbers.values())
        costs = [queue_monthly(q, overview.queue_numbers.get(q.arn), days, prices) for q in overview.queues]
        costs += [topic_monthly(t, overview.topic_numbers.get(t.arn), days, prices) for t in overview.topics]
        known = [c for c in costs if c is not None]
        has_numbers = "metrics" not in overview.errors
        return [
            ("Queues · topics · rules", f"{len(overview.queues):,} · {len(overview.topics):,} · "
                                        f"{len([r for r in overview.rules if not r.managed_by]):,}"),
            ("Waiting", _count(sum(q.visible for q in overview.queues if not q.is_dlq))),
            ("In dead-letter queues", _count(dead), "bad" if dead else ""),
            (f"Failed deliveries · {days}d", _count(failed) if has_numbers else "?", "bad" if failed else ""),
            ("Est. $ / month", human_money(sum(known)) if known else "-"),
        ]

    def _overview_notes(self, overview: Overview, *, costs: bool = True) -> list[Any]:
        notes: list[Any] = []
        for part, code in overview.errors.items():
            notes.append(_Note(f"The {part} couldn't be read ({code}), so the report leaves them out: README lists the "
                               "read-only permissions.", "warn"))
        if overview.cut_short:
            notes.append(_Note(f"Read the first {self.core.limit:,} {' and '.join(overview.cut_short)} only: pass "
                               "MessagingAnalyzer(limit=...) to read more.", "warn"))
        if overview.metrics_read:
            cost = human_money(overview.metrics_read * self.core.prices["metric_read"])
            notes.append(_Note(f"Read {overview.metrics_read:,} CloudWatch metrics (about {cost})."
                               + (f" {_PRICES_NOTE}" if costs else "")))
        return notes

    def _overview_blocks(self, overview: Overview) -> list[Any]:
        if not (overview.queues or overview.topics or overview.rules):
            return [_Title(f"Messaging in {overview.region}"), *self._overview_notes(overview),
                    _Note(f"No queues, topics or rules in {overview.region}. They're regional: pass "
                          "MessagingAnalyzer(region='...') to look in another region.")]
        findings = overview_findings(overview, self.core.prices)
        blocked = access_fixes(topics=overview.topics, rules=overview.rules,
                               queues={q.arn: q for q in overview.queues}, functions=overview.functions)
        blocks: list[Any] = [
            _Title(f"Queues, topics and rules in {overview.region}",
                   f"the last {_plural(overview.days, 'day')} (CloudWatch's daily numbers, in UTC days) · "
                   f"{self._price_basis()}"),
            _Cards([_card(c) for c in self._overview_cards(overview)]),
            _Findings(findings, empty="Nothing failed, no dead-letter queue holds messages and nothing waits unread."),
            *_fixes_block(blocked),
            *self._overview_notes(overview),
        ]
        if overview.queues:
            blocks.append(queues_table(overview, self.core.prices))
        if overview.topics:
            blocks.append(topics_table(overview, self.core.prices))
        if any(not r.managed_by for r in overview.rules):
            blocks.append(rules_table(overview))
        blocks.append(_Next(self._overview_next(overview)))
        return blocks

    def _overview_next(self, overview: Overview) -> list[tuple[str, str]]:
        steps = []
        if any(q.is_dlq and q.messages for q in overview.queues):
            steps.append(("dead_letters()", "the messages that failed for good, and how to move them back"))
        failing = [t for t in overview.topics if overview.topic_numbers.get(t.arn) and
                   overview.topic_numbers[t.arn].failed]
        start = failing[0].name if failing else (overview.topics[0].name if overview.topics else "")
        if start:
            steps.append((_call("follow", start), "where its messages go, hop by hop"))
        fullest = max(overview.queues, key=lambda q: q.visible, default=None)
        if fullest is not None:
            steps.append((_call("queue_info", fullest.name), "its settings, readers and senders"))
        return steps[:3]


    @_friendly_errors
    def queue_info(self, name: str, *, days: int = 7) -> None:
        """
        One SQS queue: how many messages wait and how old the oldest is, what came in and was handled each day, its
        settings in plain English, the Lambda functions that read it, the topics and rules that send to it (and
        whether its policy lets them), and its dead-letter queue. name: its name, URL, ARN or console link.
        """
        with self._progress(f"Reading {name}", unit="parts"):
            report = self.core.queue_report(name, days=days)
        self._show(self._queue_blocks(report))

    def _queue_cards(self, report: QueueReport, findings: list[tuple[str, str]]) -> list[tuple[str, ...]]:
        queue, numbers, days = report.queue, report.numbers, report.days
        oldest = numbers.oldest_now_s if numbers else None
        dead = report.dlq.messages if report.dlq else (queue.messages if queue.is_dlq else 0)
        return [
            ("Waiting", f"{queue.visible:,}", "warn" if _warned(findings, "nothing received") else ""),
            ("In flight", f"{queue.in_flight:,}"),
            ("Oldest", seconds_words(oldest) if oldest else "-", "warn" if _warned(findings, "gone unread") else ""),
            (f"In · {days}d", _count(numbers.sent) if numbers else "?",
             "warn" if _warned(findings, "falling behind") else ""),
            (f"Done · {days}d", _count(numbers.deleted) if numbers else "?"),
            ("Dead letters", _count(dead), "bad" if dead else ""),
            ("Est. $ / month", human_money(queue_monthly(queue, numbers, days, self.core.prices))),
        ]

    def _queue_blocks(self, report: QueueReport) -> list[Any]:
        queue = report.queue
        findings = queue_findings(report, self.core.prices)
        blocked = [Blocked(f.name, queue.name, "SQS queue", self._queue_fix(report, f.arn, f.kind))
                   for f in report.feeders if f.allowed is False]
        kind = "dead-letter queue" if queue.is_dlq else ("FIFO queue" if queue.fifo else "standard queue")
        blocks: list[Any] = [
            _Title(queue.name, f"SQS {kind} · {queue.url} · the last {_plural(report.days, 'day')} (UTC days)"),
            _Cards([_card(c) for c in self._queue_cards(report, findings)]),
            _Findings(findings, empty="Nothing to fix: messages are read as they come, and failures have somewhere "
                                      "to go."),
            *_fixes_block(blocked),
            *_error_notes({**queue.errors, **report.errors}, report.metrics_read, self.core.prices["metric_read"]),
            queue_settings(queue),
        ]
        if report.feeders:
            blocks.append(senders_table(report.feeders))
        if queue.consumers:
            blocks.append(readers_table(queue.consumers))
        if report.numbers and report.numbers.daily:
            blocks.append(days_table(report.numbers.daily, ["In", "Done"]))
        if queue.messages:
            blocks.append(_Note(_CANT_PEEK))
        blocks.append(_Next(self._queue_next(report)))
        return blocks

    def _queue_fix(self, report: QueueReport, sender_arn: str, kind: str) -> str:
        service = SNS_PRINCIPAL if kind == "SNS topic" else EVENTS_PRINCIPAL
        queue = report.queue
        statement = queue_statement(queue.arn, service=service, source_arn=sender_arn)
        return queue_policy_command(queue.url, queue_policy_with(queue.policy, statement))

    def _queue_next(self, report: QueueReport) -> list[tuple[str, str]]:
        queue = report.queue
        steps = [(_call("follow", queue.name), "where its messages come from and go, hop by hop")]
        if report.dlq is not None:
            steps.append((_call("queue_info", report.dlq.name), "its dead-letter queue"))
        if queue.consumers:
            function = queue.consumers[0].function
            steps.append((f"LambdaView().errors({function!r})", "why its reader fails (the Lambda module)"))
        return steps[:3]


    @_friendly_errors
    def topic_info(self, name: str, *, days: int = 7) -> None:
        """
        One SNS topic: messages published, delivered, failed and filtered out each day, every subscription with what
        it lets through, whether it's confirmed, its dead-letter queue and whether the queue or function lets the
        topic deliver. name: its name, ARN or console link.
        """
        with self._progress(f"Reading {name}", unit="parts"):
            report = self.core.topic_report(name, days=days)
        self._show(self._topic_blocks(report))

    def _topic_cards(self, report: TopicReport, findings: list[tuple[str, str]]) -> list[tuple[str, ...]]:
        topic, numbers, days = report.topic, report.numbers, report.days
        return [
            ("Subscriptions", f"{len(topic.subscriptions):,}", "warn" if topic.pending else ""),
            (f"Published · {days}d", _count(numbers.published) if numbers else "?",
             "warn" if _warned(findings, "went to no one") else ""),
            ("Delivered", _count(numbers.delivered) if numbers else "?"),
            ("Failed", _count(numbers.failed) if numbers else "?", "bad" if numbers and numbers.failed else ""),
            ("Filtered out", _count(numbers.filtered) if numbers else "?"),
            ("Est. $ / month", human_money(topic_monthly(topic, numbers, days, self.core.prices))),
        ]

    def _topic_blocks(self, report: TopicReport) -> list[Any]:
        topic = report.topic
        findings = topic_findings(report, self.core.prices)
        blocked = access_fixes(topics=[topic], rules=[], queues=report.queues, functions=report.functions)
        kind = "FIFO topic" if topic.fifo else "standard topic"
        logged = f" · logs deliveries to {', '.join(topic.logged)}" if topic.logged else ""
        blocks: list[Any] = [
            _Title(topic.name, f"SNS {kind} · {topic.arn}{logged} · the last {_plural(report.days, 'day')} (UTC days)"),
            _Cards([_card(c) for c in self._topic_cards(report, findings)]),
            _Findings(findings, empty="Nothing to fix: every delivery got through and failures have somewhere to go."),
            *_fixes_block(blocked),
            *_error_notes({**topic.errors, **report.errors}, report.metrics_read, self.core.prices["metric_read"]),
        ]
        if topic.subscriptions:
            blocks.append(subscriptions_table(topic, report.queues, report.functions))
        if report.numbers and report.numbers.daily:
            blocks.append(days_table(report.numbers.daily, ["Published", "Failed"]))
        blocks.append(_Next(self._topic_next(report)))
        return blocks

    def _topic_next(self, report: TopicReport) -> list[tuple[str, str]]:
        topic = report.topic
        steps = [(_call("follow", topic.name), "every hop its messages take, and where they get lost")]
        queue = next((s for s in topic.subscriptions if s.protocol == "sqs"), None)
        if queue is not None:
            steps.append((_call("queue_info", queue.target), "the queue it delivers to"))
        if any(s.dlq_arn for s in topic.subscriptions):
            steps.append(("dead_letters()", "the deliveries that failed for good"))
        return steps[:3]


    @_friendly_errors
    def rule_info(self, name: str, *, bus: str | None = None, days: int = 7) -> None:
        """
        One EventBridge rule: which events it matches, in plain English, how many it matched, sent and failed to
        deliver each day, and its targets with their retries, dead-letter queues and whether each lets the rule
        deliver. name: its name, 'bus/rule', ARN or console link; bus= names its bus.
        """
        with self._progress(f"Reading {name}", unit="parts"):
            report = self.core.rule_report(name, bus=bus, days=days)
        self._show(self._rule_blocks(report))

    def _rule_cards(self, report: RuleReport, findings: list[tuple[str, str]]) -> list[tuple[str, ...]]:
        rule, numbers, days = report.rule, report.numbers, report.days
        return [
            ("State", "on" if rule.is_enabled else "off", "warn" if _warned(findings, "is disabled") else ""),
            (f"Matched · {days}d", _count(numbers.matched) if numbers else "?"),
            ("Sent", _count(numbers.invocations) if numbers else "?"),
            ("Failed", _count(numbers.failed) if numbers else "?", "bad" if numbers and numbers.failed else ""),
            ("To dead-letter", _count(numbers.to_dlq) if numbers else "?"),
            ("Targets", f"{len(rule.targets):,}", "warn" if _warned(findings, "has no targets") else ""),
        ]

    def _rule_blocks(self, report: RuleReport) -> list[Any]:
        rule = report.rule
        findings = rule_findings(report)
        blocked = access_fixes(topics=[], rules=[rule], queues=report.queues, functions=report.functions)
        matches = f"Runs on a schedule: {rule.schedule}." if rule.schedule else \
            f"Matches {describe_pattern(rule.pattern)}."
        blocks: list[Any] = [
            _Title(rule.label, f"EventBridge rule on the {rule.bus} bus · the last {_plural(report.days, 'day')} "
                               "(UTC days)"),
            _Cards([_card(c) for c in self._rule_cards(report, findings)]),
            _Findings(findings, empty="Nothing to fix: it delivers what it matches, and failures have somewhere to go."),
            *_fixes_block(blocked),
            _Note(matches + (f" {rule.description}" if rule.description else "")),
            *_error_notes({**rule.errors, **report.errors}, report.metrics_read, self.core.prices["metric_read"]),
        ]
        if rule.targets:
            blocks.append(targets_table(rule, report.queues, report.functions))
        if rule.pattern:
            blocks.append(_Text(json.dumps(rule.pattern, indent=2), title="Event pattern", collapsed=True))
        if report.numbers and report.numbers.daily:
            blocks.append(days_table(report.numbers.daily, ["Matched", "Failed"]))
        blocks.append(_Next(self._rule_next(report)))
        return blocks

    def _rule_next(self, report: RuleReport) -> list[tuple[str, str]]:
        rule = report.rule
        steps = [(_call("follow", rule.label), "every hop its events take, and where they get lost")]
        queue = next((t for t in rule.targets if t.kind == "SQS queue"), None)
        if queue is not None:
            steps.append((_call("queue_info", queue.name), "the queue it sends to"))
        if any(t.dlq_arn for t in rule.targets):
            steps.append(("dead_letters()", "the events it couldn't deliver"))
        return steps[:3]


    @_friendly_errors
    def dead_letters(self, *, days: int = 7) -> None:
        """
        Every dead-letter queue in the region, the fullest first: how many failed messages each holds, how old the
        oldest is and when they expire, what it catches failures from (queues, SNS subscriptions, EventBridge
        targets), and the command that moves them back once the cause is fixed.
        """
        with self._progress("Reading queues, topics and rules", unit="parts") as tick:
            overview = self.core.overview(days=days, progress=tick)
        self._show(self._dead_blocks(overview))

    def _dead_blocks(self, overview: Overview) -> list[Any]:
        dead = dead_letters_from(overview)
        title = _Title(f"Dead-letter queues in {overview.region}", "where failed messages wait")
        if not dead:
            return [title, *self._overview_notes(overview),
                    _Note("No queue, subscription or rule target in this region has a dead-letter queue: a message "
                          "that keeps failing is retried until it expires, then lost. queue_info() shows the command "
                          "that adds one.", "warn" if overview.queues else "info")]
        findings = [dlq_holding(d.queue, d.numbers) for d in dead]
        queues = {q.arn: q for q in overview.queues}
        findings += [dlq_too_short(q, queues.get(q.redrive.dlq_arn)) for q in overview.queues if q.redrive]
        holding = [d for d in dead if d.queue.messages]
        oldest = max((d.numbers.oldest_now_s or 0 for d in holding if d.numbers), default=0)
        cards: list[tuple[str, ...]] = [
            ("Dead-letter queues", f"{len(dead):,}"),
            ("Holding messages", f"{len(holding):,}", "bad" if holding else ""),
            ("Messages", _count(sum(d.queue.messages for d in dead)), "bad" if holding else ""),
            ("Oldest", seconds_words(oldest) if oldest else "-"),
        ]
        blocks: list[Any] = [
            title, _Cards([_card(c) for c in cards]),
            _Findings([f for f in findings if f], empty="Every dead-letter queue is empty: nothing failed for good."),
            *self._overview_notes(overview, costs=False), dead_letters_table(dead), _Note(_CANT_PEEK),
        ]
        if holding:
            blocks.append(_Next([(_call("queue_info", holding[0].queue.name), "the fullest one, with what feeds it"),
                                 (_call("follow", holding[0].queue.name), "how messages get there")]))
        return blocks


    @_friendly_errors
    def follow(self, name: str, *, days: int = 7) -> None:
        """
        Where a message goes from a topic, queue, EventBridge rule or bus: what sends to it, then every hop downstream
        (subscriptions and their filters, rule targets, queues, the Lambda functions that read them, the dead-letter
        queues that catch failures), each with its numbers over 7 days and where messages get lost.
        """
        with self._progress(f"Following {name}", unit="parts") as tick:
            found = self.core.follow(name, days=days, progress=tick)
        self._show(self._follow_blocks(found))

    def _follow_findings(self, found: MessageMap) -> list[tuple[str, str]]:
        findings = []
        for hop in found.hops:
            if not hop.problem:
                continue
            level = "warn" if hop.tone == "bad" else "info"
            how = f" ({hop.via})" if hop.via else ""
            more = f" {_info_call(hop)} shows why and what to run." if _info_call(hop) else ""
            findings.append((level, f"{hop.kind} {hop.name}: {hop.problem}{how}.{more}"))
        return list(dict.fromkeys(findings))

    def _follow_blocks(self, found: MessageMap) -> list[Any]:
        upstream = [h for h in found.hops if h.upstream]
        downstream = [h for h in found.hops if not h.upstream]
        findings = self._follow_findings(found)
        problems = [h for h in found.hops if h.problem]
        cards: list[tuple[str, ...]] = [
            ("Hops", f"{len(downstream) - 1:,}"),
            ("Sends here", f"{len(upstream):,}"),
            ("Problems", f"{len(problems):,}", "bad" if any(h.tone == "bad" for h in problems) else
             ("warn" if problems else "")),
        ]
        rows = [[_hop_label(h), h.kind, h.via or "-", h.numbers or "-", _hop_problem(h)] for h in downstream]
        blocks: list[Any] = [
            _Title(f"Where a message goes from {found.name}",
                   f"{found.kind} · the numbers are the last {_plural(found.overview.days, 'day')} (UTC days)"),
            _Cards([_card(c) for c in cards]),
            _Findings(findings, empty="Every hop gets its messages, and nothing waits in a dead-letter queue."),
            *self._overview_notes(found.overview, costs=False),
        ]
        if upstream:
            sender_rows = [[h.name, h.kind, h.via, _hop_problem(h)] for h in upstream]
            blocks.append(_Table(["Sends here", "Kind", "How", "Problem"], sender_rows, title="What sends to it",
                                 sortable=False))
        blocks.append(_Table(["Hop", "Kind", "How it gets there", "Numbers", "Problem"], rows, title="The way down",
                             tree=True, max_rows=0, prose_cols=(2,)))
        blocks.append(_Next(self._follow_next(found)))
        return blocks

    def _follow_next(self, found: MessageMap) -> list[tuple[str, str]]:
        steps: list[tuple[str, str]] = []
        for hop in found.hops:
            call = _info_call(hop)
            if hop.problem and call and call not in [c for c, _ in steps]:
                steps.append((call, f"why: {hop.problem}"))
        if any(h.kind == "SQS queue" and "failed messages" in h.problem for h in found.hops):
            steps.append(("dead_letters()", "every dead-letter queue, and how to move messages back"))
        steps.append(("overview()", "every queue, topic and rule in the region"))
        return steps[:3]

    @_friendly_errors
    def explore(self, where: str | None = None, *, tab: str | None = None, days: int = 7,
                height: int | str | None = None) -> None:
        """
        The explorer window: every queue, topic and rule, the one you click in full, where its messages go hop by
        hop (click a hop to follow it), the dead-letter queues, and Live, a queue's backlog every few seconds while
        you watch it drain. Needs Jupyter and ipywidgets; view.explorer is the window.
        """
        if not self.use_html:
            raise _Hint(
                "The explorer window needs Jupyter (SageMaker, JupyterLab or VS Code). Here, overview() lists every "
                "queue, topic and rule, and follow('name') maps where a message goes."
            )
        _require("ipywidgets", "The explorer window")
        from ..explorer.window import MessagingExplorer

        self.explorer = MessagingExplorer(where, tab=tab, view=self, days=days, height=height, mode="widgets")
