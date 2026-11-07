"""
The tables and settings lines MessagingView's reports show: queues, topics, rules, subscriptions, targets, readers,
senders, dead-letter queues and a line per day, each cell in the units people read.
"""

from __future__ import annotations

from datetime import timezone
from typing import Any

from ..._kit.blocks import _Table, _Tone
from ..._kit.fmt import _count, human_money

from ..analysis import (
    describe_filter, describe_pattern, move_back_command, queue_monthly, subscriber_allowed, target_allowed,
    topic_monthly,
)
from ..analysis.queues import EXPIRY_SHARE
from ..helpers import arn_name, seconds_words
from ..models import (
    Consumer, DeadLetter, Feeder, FunctionFacts, Overview, Queue, QueueNumbers, Rule, Topic,
)


def _age_cell(queue: Queue, numbers: QueueNumbers | None) -> Any:
    oldest = numbers.oldest_now_s if numbers else None
    if not oldest:
        return "-"
    text = seconds_words(oldest)
    return _Tone(text, "warn") if oldest >= EXPIRY_SHARE * queue.retention_s else text


def _waiting_cell(queue: Queue) -> Any:
    if queue.is_dlq and queue.messages:
        return _Tone(f"{queue.messages:,}", "bad")
    if queue.visible and not queue.consumers and not queue.is_dlq:
        return _Tone(f"{queue.visible:,}", "warn")
    return f"{queue.visible:,}"


def _allowed_cell(allowed: bool | None) -> Any:
    if allowed is None:
        return "-"
    return _Tone("yes", "ok") if allowed else _Tone("no: its policy keeps it out", "bad")


def _failed_cell(failed: int | None) -> Any:
    if failed is None:
        return "-"
    return _Tone(f"{failed:,}", "bad") if failed else "0"


def _queue_kind(queue: Queue) -> str:
    if queue.is_dlq:
        return "dead-letter (FIFO)" if queue.fifo else "dead-letter"
    return "FIFO" if queue.fifo else "standard"


def queues_table(overview: Overview, prices: dict[str, float]) -> _Table:
    """
    Every queue, the dead-letter queues holding messages first, then by how many wait.
    """
    def order(queue: Queue) -> tuple[int, int, str]:
        return (-(queue.messages if queue.is_dlq else 0), -queue.visible, queue.name)

    rows = []
    for queue in sorted(overview.queues, key=order):
        numbers = overview.queue_numbers.get(queue.arn)
        readers = ", ".join(c.function for c in queue.consumers) or "-"
        rows.append([
            queue.name, _queue_kind(queue), _waiting_cell(queue), f"{queue.in_flight:,}", _age_cell(queue, numbers),
            _count(numbers.sent) if numbers else "-", _count(numbers.deleted) if numbers else "-", readers,
            queue.redrive.dlq_name if queue.redrive else "-",
            human_money(queue_monthly(queue, numbers, overview.days, prices)),
        ])
    days = overview.days
    return _Table(["Queue", "Kind", "Waiting", "In flight", "Oldest", f"In · {days}d", f"Done · {days}d", "Read by",
                   "Dead-letter queue", "$ / month"], rows, title="Queues: the dead-letter queues holding messages "
                   "first, then the fullest")


def _subscriptions_cell(topic: Topic) -> Any:
    count = f"{len(topic.subscriptions):,}"
    pending = len(topic.pending)
    return _Tone(f"{count} ({pending} unconfirmed)", "warn") if pending else count


def topics_table(overview: Overview, prices: dict[str, float]) -> _Table:
    """
    Every topic, the ones with failed deliveries first.
    """
    numbers = overview.topic_numbers

    def failed(topic: Topic) -> int:
        return numbers[topic.arn].failed if topic.arn in numbers else 0

    rows = []
    for topic in sorted(overview.topics, key=lambda t: (-failed(t), t.name)):
        found = numbers.get(topic.arn)
        rows.append([
            topic.name, _subscriptions_cell(topic), _count(found.published) if found else "-",
            _count(found.delivered) if found else "-", _failed_cell(found.failed if found else None),
            _count(found.filtered) if found else "-", human_money(topic_monthly(topic, found, overview.days, prices)),
        ])
    days = overview.days
    return _Table(["Topic", "Subscriptions", f"Published · {days}d", "Delivered", "Failed", "Filtered out",
                   "$ / month"], rows, title="Topics: the ones failing deliveries first")


def _what_it_matches(rule: Rule) -> str:
    if rule.schedule:
        return f"on a schedule: {rule.schedule}"
    return describe_pattern(rule.pattern)


def _state_cell(rule: Rule) -> Any:
    return "on" if rule.is_enabled else _Tone("off", "warn")


def rules_table(overview: Overview) -> _Table:
    """
    Every rule on every bus, the ones failing deliveries first, skipping the ones AWS services manage for themselves.
    """
    numbers = overview.rule_numbers

    def order(rule: Rule) -> tuple[int, int, str]:
        found = numbers.get(rule.arn)
        return (-(found.failed if found else 0), -(found.matched if found else 0), rule.label)

    rows = []
    for rule in sorted((r for r in overview.rules if not r.managed_by), key=order):
        found = numbers.get(rule.arn)
        targets = ", ".join(t.name for t in rule.targets) or "-"
        rows.append([rule.label, _state_cell(rule), _what_it_matches(rule), targets,
                     _count(found.matched) if found else "-", _failed_cell(found.failed if found else None)])
    return _Table(["Rule", "State", "Matches", "Sends to", f"Matched · {overview.days}d", "Failed"], rows,
                  title="EventBridge rules: the ones failing deliveries first", prose_cols=(2,))


def queue_settings(queue: Queue) -> _Table:
    """
    A queue's settings, each with what it means for a message.
    """
    redrive = queue.redrive
    rows = [
        ["Kind", "FIFO" if queue.fifo else "standard",
         "in order, each message once" if queue.fifo else "fast, a message can arrive twice or out of order"],
        ["Visibility timeout", seconds_words(queue.visibility_s),
         "a message being worked on stays hidden this long, then comes back unless it was deleted"],
        ["Retention", seconds_words(queue.retention_s), "a message not handled by then is deleted"],
        ["Delay", seconds_words(queue.delay_s) if queue.delay_s else "none", "how long a new message waits first"],
        ["Receive wait", seconds_words(queue.wait_s) if queue.wait_s else "none (short polling)",
         "how long a reader waits for a message before an empty answer"],
        ["Dead-letter queue", f"{redrive.dlq_name}, after {redrive.max_receives} receives" if redrive else "none",
         "where a message that keeps failing goes" if redrive else "a message that keeps failing is retried until "
                                                                  "it expires"],
        ["Encryption", queue.encryption or "none", f"key {queue.kms_key}" if queue.kms_key else ""],
    ]
    if queue.sources:
        rows.append(["Catches failures from", ", ".join(queue.sources), "it's their dead-letter queue"])
    return _Table(["Setting", "Value", "What it means"], rows, title="Settings", sortable=False)


def senders_table(feeders: list[Feeder]) -> _Table:
    rows = [[f.kind, f.name, f.detail, _allowed_cell(f.allowed)] for f in feeders]
    return _Table(["From", "Name", "What it sends", "Allowed in"], rows, title="What sends to it",
                  prose_cols=(2,), sortable=False)


def readers_table(consumers: list[Consumer]) -> _Table:
    rows = []
    for consumer in consumers:
        state = consumer.state.lower() if consumer.is_enabled else _Tone(consumer.state.lower(), "warn")
        rows.append([consumer.function, state, f"{consumer.batch_size:,}",
                     seconds_words(consumer.timeout_s) if consumer.timeout_s else "-",
                     "yes" if consumer.reports_failures else "no (the whole batch retries)"])
    return _Table(["Lambda function", "Trigger", "Batch", "Timeout", "Reports failed messages"], rows,
                  title="What reads it", sortable=False)


def days_table(daily: list[tuple[Any, ...]], headers: list[str]) -> _Table:
    """
    A line per day, newest first: CloudWatch's daily numbers cover whole UTC days, so the days are UTC's.
    """
    rows = [[day.astimezone(timezone.utc).strftime("%Y-%m-%d"), *(f"{v:,}" for v in values)]
            for day, *values in reversed(daily)]
    return _Table(["Day (UTC)", *headers], rows, title="By day", collapsed=True, sortable=False)


def subscriptions_table(topic: Topic, queues: dict[str, Queue], functions: dict[str, FunctionFacts]) -> _Table:
    rows = []
    for subscription in topic.subscriptions:
        status = _Tone("waiting to be confirmed", "warn") if subscription.pending else "confirmed"
        allowed = subscriber_allowed(subscription, queues=queues, functions=functions)
        rows.append([subscription.kind, subscription.target, status,
                     describe_filter(subscription.filter_policy, subscription.filter_scope),
                     arn_name(subscription.dlq_arn) if subscription.dlq_arn else "none", _allowed_cell(allowed)])
    return _Table(["Delivers to", "Where", "Status", "Lets through", "Dead-letter queue", "Allowed in"], rows,
                  title="Subscriptions", prose_cols=(3,), sortable=False)


def _retries(max_attempts: int | None, max_age_s: int | None) -> str:
    attempts = "185 retries" if max_attempts is None else f"{max_attempts} retries"
    age = "24 hours" if max_age_s is None else seconds_words(max_age_s)
    return f"up to {attempts} over {age}"


def targets_table(rule: Rule, queues: dict[str, Queue], functions: dict[str, FunctionFacts]) -> _Table:
    rows = []
    for target in rule.targets:
        allowed = target_allowed(rule, target, queues=queues, functions=functions)
        rows.append([target.kind, target.name, _retries(target.max_attempts, target.max_age_s),
                     arn_name(target.dlq_arn) if target.dlq_arn else "none",
                     "yes" if target.reshaped else "no", _allowed_cell(allowed)])
    return _Table(["Sends to", "Name", "Retries", "Dead-letter queue", "Reshapes the event", "Allowed in"], rows,
                  title="Targets", sortable=False)


def dead_letters_table(dead: list[DeadLetter]) -> _Table:
    rows = []
    for entry in dead:
        queue, numbers = entry.queue, entry.numbers
        oldest = numbers.oldest_now_s if numbers else None
        move = move_back_command(queue.arn) if entry.from_queues and queue.messages else "-"
        rows.append([queue.name, _Tone(f"{queue.messages:,}", "bad") if queue.messages else "0",
                     seconds_words(oldest) if oldest else "-", seconds_words(queue.retention_s),
                     ", ".join(entry.catches) or "-", move])
    return _Table(["Dead-letter queue", "Messages", "Oldest", "Keeps them", "Catches failures from",
                   "Move them back"], rows, title="Dead-letter queues, the fullest first", code_cols=(5,))
