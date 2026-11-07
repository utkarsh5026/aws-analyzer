"""
What needs attention about one queue: messages waiting in a dead-letter queue, messages close to expiring, a backlog
nobody reads, a visibility timeout shorter than its function needs, senders its policy or key keeps out, and the
settings that lose or repeat messages. Each finding says why it matters and what to run. Pure: no AWS calls.
"""

from __future__ import annotations

from ..._kit.fmt import _plural, human_money
from ..helpers import seconds_words
from ..models import Consumer, Queue, QueueNumbers, QueueReport
from .commands import (
    LONG_POLL_SECONDS, RECOMMENDED_RECEIVES, move_back_command, queue_attribute_command, redrive_command,
)
from .access import blocked_finding
from .cost import empty_receives_monthly
from .policy import blocks_services


EXPIRY_SHARE = 0.5

VISIBILITY_FACTOR = 6

LONGEST_RETENTION_S = 1_209_600

GROWING_SHARE = 1.1

COSTLY_POLLING_USD = 1.0

Finding = tuple[str, str]


def dlq_holding(queue: Queue, numbers: QueueNumbers | None) -> Finding | None:
    """
    A dead-letter queue holding messages: how many, how old the oldest is, when they expire, and the command that
    moves them back.
    """
    if not queue.is_dlq or not queue.messages:
        return None
    oldest = numbers.oldest_now_s if numbers else None
    expires = f"They're deleted after {seconds_words(queue.retention_s)}"
    if oldest:
        left = max(queue.retention_s - oldest, 0)
        expires += f": the oldest is {seconds_words(oldest)} old, so it goes in {seconds_words(left)}"
    return ("warn", f"{queue.name} holds {_plural(queue.messages, 'message')} that failed too many times in "
                    f"{', '.join(queue.sources)}: each is work that never happened. {expires}. Find out why they "
                    f"failed (the reader's logs), then move them back: {move_back_command(queue.arn)}")


def _near_expiry(queue: Queue, numbers: QueueNumbers | None) -> Finding | None:
    oldest = numbers.oldest_now_s if numbers else None
    if queue.is_dlq or not oldest or not queue.messages or oldest < EXPIRY_SHARE * queue.retention_s:
        return None
    left = max(queue.retention_s - oldest, 0)
    return ("warn", f"The oldest message in {queue.name} is {seconds_words(oldest)} old and the queue deletes messages "
                    f"after {seconds_words(queue.retention_s)}: in {seconds_words(left)} it's gone unread. Whatever "
                    "reads it isn't keeping up, or skips it; a longer retention buys time: "
                    f"{queue_attribute_command(queue.url, 'MessageRetentionPeriod', LONGEST_RETENTION_S)}")


def _unread(queue: Queue, numbers: QueueNumbers | None, days: int) -> Finding | None:
    if queue.is_dlq or not queue.visible or queue.consumers or numbers is None or numbers.received:
        return None
    return ("warn", f"{_plural(queue.visible, 'message')} wait in {queue.name} and nothing received one in the "
                    f"last {_plural(days, 'day')}: no Lambda function reads it, and no other reader came. Check "
                    "whatever should read it is running.")


def _growing(queue: Queue, numbers: QueueNumbers | None, days: int) -> Finding | None:
    if queue.is_dlq or numbers is None or not queue.visible or numbers.sent <= GROWING_SHARE * numbers.deleted:
        return None
    if not numbers.received:
        return None
    behind = numbers.sent - numbers.deleted
    return ("warn", f"{queue.name} is falling behind: {numbers.sent:,} messages came in over "
                    f"{_plural(days, 'day')} and {numbers.deleted:,} were handled, {behind:,} short, and "
                    f"{queue.visible:,} wait now. Give its consumer more room (concurrency, batch size), or find "
                    "out why it fails.")


def _disabled_consumers(queue: Queue) -> list[Finding]:
    findings = []
    for consumer in queue.consumers:
        if consumer.is_enabled:
            continue
        findings.append(("warn", f"{consumer.function} is set to read {queue.name}, but that trigger is "
                                 f"{consumer.state.lower()}: its messages wait. Turn it on: aws lambda "
                                 f"update-event-source-mapping --uuid {consumer.uuid} --enabled"))
    return findings


def _visibility(queue: Queue, consumer: Consumer) -> Finding | None:
    """
    A visibility timeout shorter than the function needs: a message comes back while it's still being worked on.
    AWS's advice is six times the function's timeout.
    """
    timeout = consumer.timeout_s
    if not timeout or queue.visibility_s >= VISIBILITY_FACTOR * timeout:
        return None
    wanted = VISIBILITY_FACTOR * timeout
    level = "warn" if queue.visibility_s < timeout else "info"
    return (level, f"The visibility timeout of {queue.name} ({seconds_words(queue.visibility_s)}) is less than six "
                   f"times the timeout of {consumer.function} ({seconds_words(timeout)}), AWS's advice: a message can come back "
                   "while it's still being worked on and be handled twice. Set it to "
                   f"{seconds_words(wanted)}: {queue_attribute_command(queue.url, 'VisibilityTimeout', wanted)}")


def _batch_failures(queue: Queue, consumer: Consumer) -> Finding | None:
    if consumer.reports_failures or consumer.batch_size <= 1:
        return None
    return ("info", f"One bad message makes {consumer.function} retry its whole batch of up to "
                    f"{consumer.batch_size} from {queue.name}, so good ones run again. Report failed messages "
                    f"one by one: aws lambda update-event-source-mapping --uuid {consumer.uuid} "
                    "--function-response-types ReportBatchItemFailures (the function returns batchItemFailures)")


def _consumer_findings(queue: Queue) -> list[Finding]:
    findings = _disabled_consumers(queue)
    for consumer in queue.consumers:
        findings += [f for f in (_visibility(queue, consumer), _batch_failures(queue, consumer)) if f]
    return findings


def _tries(count: int) -> str:
    return f"{count} {'try' if count == 1 else 'tries'}"


def _few_receives(queue: Queue) -> Finding | None:
    redrive = queue.redrive
    if redrive is None or not queue.consumers or redrive.max_receives >= RECOMMENDED_RECEIVES:
        return None
    return ("info", f"{queue.name} sends a message to {redrive.dlq_name} after {_tries(redrive.max_receives)}"
                    ": with a Lambda consumer a throttled batch counts as a try, so good messages land there too. "
                    f"AWS suggests at least {RECOMMENDED_RECEIVES}: "
                    f"{redrive_command(queue.url, redrive.dlq_arn, RECOMMENDED_RECEIVES)}")


def _queue_without_dlq(queue: Queue) -> Finding | None:
    if queue.redrive is not None or queue.is_dlq or not queue.consumers:
        return None
    return ("info", f"{queue.name} has no dead-letter queue: a message that keeps failing is retried until it expires "
                    f"after {seconds_words(queue.retention_s)}, and nothing keeps it for you to look at. Make one and "
                    f"point it there: {redrive_command(queue.url, '<dlq-arn>')}")


def dlq_too_short(queue: Queue, dlq: Queue | None) -> Finding | None:
    """
    A dead-letter queue that keeps messages no longer than its source: a message's age counts from when it was first
    sent, so a failure can expire in the dead-letter queue before anyone sees it.
    """
    if dlq is None or queue.fifo or dlq.retention_s > queue.retention_s:
        return None
    return ("warn", f"{dlq.name} keeps messages {seconds_words(dlq.retention_s)}, no longer than {queue.name} "
                    f"({seconds_words(queue.retention_s)}): a message's age counts from when it was first sent, so "
                    "a failure can expire there before you see it. Keep them 14 days: "
                    f"{queue_attribute_command(dlq.url, 'MessageRetentionPeriod', LONGEST_RETENTION_S)}")


def _polling(queue: Queue, numbers: QueueNumbers | None, days: int, prices: dict[str, float]) -> Finding | None:
    monthly = empty_receives_monthly(queue, numbers, days, prices)
    if queue.wait_s or monthly < COSTLY_POLLING_USD or numbers is None:
        return None
    return ("info", f"{numbers.empty_receives:,} receives from {queue.name} found nothing in "
                    f"{_plural(days, 'day')} (costing {human_money(monthly)}/month): its readers ask again at once. "
                    "Long polling waits for a message instead: "
                    f"{queue_attribute_command(queue.url, 'ReceiveMessageWaitTimeSeconds', LONG_POLL_SECONDS)}")


def _locked_queue(queue: Queue, feeders: int) -> Finding | None:
    if not feeders or not blocks_services(queue.kms_key):
        return None
    return ("warn", f"SNS and EventBridge can't send to {queue.name}: it's encrypted with the AWS managed key "
                    "alias/aws/sqs, which they can't use, so everything they send it is lost. Switch it to "
                    "SQS-managed encryption, or a key of your own whose policy lets them use it.")


def _blocked_feeders(report: QueueReport) -> list[Finding]:
    findings = []
    for feeder in report.feeders:
        if feeder.allowed is not False:
            continue
        sender = "sns.amazonaws.com" if feeder.kind == "SNS topic" else "events.amazonaws.com"
        findings.append(blocked_finding(feeder.name, "SQS queue", report.queue.name, sender))
    return findings


def queue_findings(report: QueueReport, prices: dict[str, float]) -> list[Finding]:
    """
    What needs attention about one queue, warnings first: its messages failing, expiring or waiting unread, the
    senders it keeps out, and settings that lose or repeat messages, each with the command to fix it.
    """
    queue, numbers, days = report.queue, report.numbers, report.days
    candidates = [
        *_blocked_feeders(report), _locked_queue(queue, len(report.feeders)), dlq_holding(queue, numbers),
        dlq_holding(report.dlq, report.dlq_numbers) if report.dlq else None, _near_expiry(queue, numbers),
        _unread(queue, numbers, days), _growing(queue, numbers, days), *_consumer_findings(queue),
        dlq_too_short(queue, report.dlq), _few_receives(queue), _queue_without_dlq(queue),
        _polling(queue, numbers, days, prices),
    ]
    findings = [f for f in candidates if f]
    return [f for f in findings if f[0] == "warn"] + [f for f in findings if f[0] != "warn"]
