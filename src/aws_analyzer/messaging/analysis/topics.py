"""
What needs attention about one SNS topic: deliveries that failed (and whether anything kept them), subscribers whose
policy keeps the topic out, subscriptions nobody confirmed, messages published to no one, and filters that drop most
messages. Each finding says why it matters and what to run. Pure: no AWS calls.
"""

from __future__ import annotations

from ..._kit.fmt import _plural, human_money
from ..helpers import agree, function_of
from ..models import Subscription, TopicReport
from .access import blocked_finding, locked_queues, subscriber_allowed
from .commands import delivery_logging_command, subscription_dlq_command
from .cost import topic_monthly


FILTERED_SHARE = 0.5

Finding = tuple[str, str]


def _without_dlq(subscriptions: list[Subscription]) -> list[Subscription]:
    return [s for s in subscriptions if not s.dlq_arn and not s.pending]


def _failed_deliveries(report: TopicReport) -> Finding | None:
    numbers, topic = report.numbers, report.topic
    if numbers is None or not numbers.failed:
        return None
    attempts = numbers.delivered + numbers.failed
    share = numbers.failed / attempts if attempts else 0.0
    lost = _without_dlq(topic.subscriptions)
    kept = (f" {_plural(len(lost), 'subscription')} ({', '.join(s.target for s in lost[:3])}) "
            f"{agree(len(lost), 'has', 'have')} no dead-letter queue, so what failed there is gone.") if lost else " "
    unlogged = list(dict.fromkeys(s.protocol for s in topic.subscriptions if s.protocol not in topic.logged))
    why = (f" To see why, log failed deliveries: {delivery_logging_command(topic.arn, unlogged[0])}"
           if unlogged else " Its delivery logs in CloudWatch Logs say why.")
    return ("warn", f"{numbers.failed:,} deliveries from {topic.name} failed in the last "
                    f"{_plural(report.days, 'day')} ({share:.1%} of {attempts:,}), after SNS's retries.{kept}{why}")


def _redrive_failed(report: TopicReport) -> Finding | None:
    numbers = report.numbers
    if numbers is None or not numbers.redrive_failed:
        return None
    return ("warn", f"{numbers.redrive_failed:,} failed deliveries from {report.topic.name} couldn't be put in their "
                    "dead-letter queue either, so they're gone: the dead-letter queue's policy must let "
                    "sns.amazonaws.com send it messages (and its key, if it has its own).")


def _blocked_subscribers(report: TopicReport) -> list[Finding]:
    findings = []
    for subscription in report.topic.subscriptions:
        if subscriber_allowed(subscription, queues=report.queues, functions=report.functions) is not False:
            continue
        findings.append(blocked_finding(report.topic.name, subscription.kind, subscription.target,
                                        "sns.amazonaws.com"))
    return findings


def _missing_subscribers(report: TopicReport) -> list[Finding]:
    findings = []
    for subscription in report.topic.subscriptions:
        facts = report.functions.get(function_of(subscription.endpoint))
        if facts is not None and not facts.exists:
            findings.append(("warn", f"{report.topic.name} delivers to the Lambda function {subscription.target}, "
                                     "which doesn't exist any more: every message fails there. Point the "
                                     "subscription somewhere else, or remove it."))
    return findings


def _locked_subscribers(report: TopicReport) -> list[Finding]:
    arns = [s.endpoint for s in report.topic.subscriptions if s.protocol == "sqs"]
    return [("warn", f"{report.topic.name} can't deliver to {queue.name}: the queue is encrypted with the AWS managed "
                     "key alias/aws/sqs, which SNS can't use, so every message fails there. Switch the queue to "
                     "SQS-managed encryption, or a key of your own whose policy lets sns.amazonaws.com use it.")
            for queue in locked_queues(arns, report.queues)]


def _pending(report: TopicReport) -> Finding | None:
    pending = report.topic.pending
    if not pending:
        return None
    where = ", ".join(f"{s.kind} {s.endpoint}" for s in pending[:3])
    return ("warn", f"{_plural(len(pending), 'subscription')} of {report.topic.name} "
                    f"{agree(len(pending), 'is', 'are')} still waiting to be confirmed ({where}): "
                    f"{agree(len(pending), 'it gets', 'they get')} nothing until then. An email is confirmed from the link SNS sent; an "
                    "HTTPS endpoint has to answer the SubscriptionConfirmation message.")


def _nobody(report: TopicReport, prices: dict[str, float]) -> Finding | None:
    numbers, topic = report.numbers, report.topic
    if topic.subscriptions or numbers is None or not numbers.published:
        return None
    cost = topic_monthly(topic, numbers, report.days, prices)
    return ("warn", f"{numbers.published:,} messages published to {topic.name} in {_plural(report.days, 'day')} went "
                    f"to no one (costing {human_money(cost)}/month): it has no subscriptions. Subscribe what should "
                    "get them, or stop publishing.")


def _filtered(report: TopicReport) -> Finding | None:
    numbers = report.numbers
    if numbers is None or not numbers.filtered:
        return None
    share = numbers.filtered / (numbers.filtered + numbers.delivered)
    if share < FILTERED_SHARE:
        return None
    return ("info", f"Filters dropped {share:.0%} of the deliveries from {report.topic.name} ({numbers.filtered:,}): if a "
                    "subscriber is missing messages, compare what's published with its filter (the Subscriptions "
                    "table).")


def _subscriptions_without_dlq(report: TopicReport) -> Finding | None:
    numbers = report.numbers
    lost = _without_dlq(report.topic.subscriptions)
    if not lost or (numbers is not None and numbers.failed):
        return None
    first = lost[0]
    return ("info", f"{_plural(len(lost), 'subscription')} of {report.topic.name} {agree(len(lost), 'has', 'have')} "
                    "no dead-letter queue: a delivery "
                    "that fails after SNS's retries is dropped. Give each one: "
                    f"{subscription_dlq_command(first.arn, '<dlq-arn>')}")


def _quiet(report: TopicReport) -> Finding | None:
    numbers, topic = report.numbers, report.topic
    if numbers is None or numbers.published or not topic.subscriptions:
        return None
    return ("info", f"Nothing was published to {topic.name} in the last {_plural(report.days, 'day')}: if messages "
                    "are missing downstream, the publisher never sent them.")


def topic_findings(report: TopicReport, prices: dict[str, float]) -> list[Finding]:
    """
    What needs attention about one topic, warnings first: failed deliveries and where they went, subscribers that
    keep it out, unconfirmed subscriptions, messages to no one, and filters dropping most messages.
    """
    candidates = [
        _failed_deliveries(report), _redrive_failed(report), *_blocked_subscribers(report), *_missing_subscribers(report), *_locked_subscribers(report),
        _pending(report), _nobody(report, prices), _filtered(report), _subscriptions_without_dlq(report), _quiet(report),
    ]
    findings = [f for f in candidates if f]
    return [f for f in findings if f[0] == "warn"] + [f for f in findings if f[0] != "warn"]
