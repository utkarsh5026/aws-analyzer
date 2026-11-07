"""
Reports built from a whole region's Overview, with no more reads: one queue's, topic's or rule's report as its own
command would read it, the region's dead-letter queues and what each catches, and the findings across everything.
Pure: no AWS calls.
"""

from __future__ import annotations

from ..._kit.fmt import _plural
from ..models import DeadLetter, Overview, Queue, QueueReport, Rule, RuleReport, Topic, TopicReport
from .access import feeders_of
from .queues import queue_findings
from .rules import rule_findings
from .topics import topic_findings


MOST_WARNINGS = 10

MOST_NOTES = 3

Finding = tuple[str, str]


def queue_report_from(overview: Overview, queue: Queue) -> QueueReport:
    """
    A queue's report from the overview: its numbers, its dead-letter queue and what feeds it.
    """
    dlq = overview.queue(queue.redrive.dlq_arn) if queue.redrive else None
    return QueueReport(queue=queue, days=overview.days, numbers=overview.queue_numbers.get(queue.arn), dlq=dlq,
                       dlq_numbers=overview.queue_numbers.get(dlq.arn) if dlq else None,
                       feeders=feeders_of(queue, overview.topics, overview.rules))


def topic_report_from(overview: Overview, topic: Topic) -> TopicReport:
    """
    A topic's report from the overview: its numbers and what's known about each subscriber.
    """
    return TopicReport(topic=topic, days=overview.days, numbers=overview.topic_numbers.get(topic.arn),
                       queues={q.arn: q for q in overview.queues}, functions=overview.functions)


def rule_report_from(overview: Overview, rule: Rule) -> RuleReport:
    """
    A rule's report from the overview: its numbers and what's known about each target.
    """
    return RuleReport(rule=rule, days=overview.days, numbers=overview.rule_numbers.get(rule.arn),
                      queues={q.arn: q for q in overview.queues}, functions=overview.functions)


def _holder(overview: Overview, found: dict[str, DeadLetter], arn: str) -> DeadLetter | None:
    """
    The DeadLetter entry for the queue at `arn`, made the first time it's named; None when the region has no such
    queue.
    """
    queue = overview.queue(arn) if arn else None
    if queue is None:
        return None
    return found.setdefault(arn, DeadLetter(queue, overview.queue_numbers.get(arn)))


def _catchers(overview: Overview) -> dict[str, DeadLetter]:
    """
    Every queue something names as its dead-letter queue, by ARN, with what it catches.
    """
    found: dict[str, DeadLetter] = {}
    for queue in overview.queues:
        holder = _holder(overview, found, queue.redrive.dlq_arn) if queue.redrive else None
        if holder is not None:
            holder.from_queues.append(f"queue {queue.name} (after {queue.redrive.max_receives} receives)")
    for topic in overview.topics:
        for subscription in topic.subscriptions:
            holder = _holder(overview, found, subscription.dlq_arn)
            if holder is not None:
                holder.from_subscriptions.append(f"topic {topic.name} → {subscription.target}")
    for rule in overview.rules:
        for target in rule.targets:
            holder = _holder(overview, found, target.dlq_arn)
            if holder is not None:
                holder.from_targets.append(f"rule {rule.label} → {target.name}")
    return found


def dead_letters_from(overview: Overview) -> list[DeadLetter]:
    """
    The region's dead-letter queues, the ones holding most messages first: every queue a queue, SNS subscription or
    EventBridge target sends its failures to, and every queue SQS knows feeds one (ListDeadLetterSourceQueues).
    """
    found = _catchers(overview)
    for queue in overview.queues:
        if queue.is_dlq and queue.arn not in found:
            found[queue.arn] = DeadLetter(queue, overview.queue_numbers.get(queue.arn),
                                          from_queues=[f"queue {name}" for name in queue.sources])
    return sorted(found.values(), key=lambda d: (-d.queue.messages, d.queue.name))


def _queue_findings(overview: Overview, queues: list[Queue], prices: dict[str, float]) -> list[Finding]:
    """
    The queues' findings without the ones about their senders: the topics' and rules' findings say those.
    """
    findings: list[Finding] = []
    for queue in queues:
        report = queue_report_from(overview, queue)
        report.feeders = []
        findings += queue_findings(report, prices)
    return findings


def _all_findings(overview: Overview, prices: dict[str, float]) -> list[Finding]:
    """
    Every finding in the region, each once, in the order they matter: failed messages waiting in dead-letter queues,
    then deliveries that failed or can't happen (topics, rules), then the other queues.
    """
    holding = [q for q in overview.queues if q.is_dlq and q.messages]
    findings = _queue_findings(overview, holding, prices)
    for topic in overview.topics:
        findings += topic_findings(topic_report_from(overview, topic), prices)
    for rule in overview.rules:
        findings += rule_findings(rule_report_from(overview, rule))
    findings += _queue_findings(overview, [q for q in overview.queues if q not in holding], prices)
    return list(dict.fromkeys(findings))


def overview_findings(overview: Overview, prices: dict[str, float]) -> list[Finding]:
    """
    What needs attention across the region's queues, topics and rules: the warnings first (at most MOST_WARNINGS),
    then a few notes, and a line saying how many more each command shows.
    """
    findings = _all_findings(overview, prices)
    warnings = [f for f in findings if f[0] == "warn"]
    notes = [f for f in findings if f[0] != "warn"]
    shown = warnings[:MOST_WARNINGS] + notes[:MOST_NOTES]
    hidden = len(findings) - len(shown)
    if hidden > 0:
        shown.append(("info", f"{_plural(hidden, 'more finding')} for single queues, topics and rules: each one's "
                              "own are in queue_info(), topic_info() and rule_info()."))
    return shown


def dead_letter_names(dead: DeadLetter) -> str:
    """
    What a dead-letter queue catches, in a few words: 'orders (after 5 receives), topic orders-events → hook'.
    """
    return ", ".join(dead.catches) or "nothing names it"


def rules_on(overview: Overview, bus: str) -> list[Rule]:
    return [rule for rule in overview.rules if rule.bus == bus]
