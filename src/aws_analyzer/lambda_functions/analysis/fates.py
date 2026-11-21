"""
What happened to a function's failed events: lost, kept in a queue, waiting to be tried again, or unknown, worked out
from what calls it, how each caller treats a failure, CloudWatch's counts and the queues as they are now.

Lambda treats a failure three ways. An asynchronous caller (S3, SNS, EventBridge) hands the event to Lambda, which
tries it 1 + MaximumRetryAttempts times and then gives up: it goes to the function's on-failure destination or
dead-letter queue, or nowhere (AsyncEventsDropped counts them either way). A queue Lambda reads (SQS) gets a failed
message back, and after maxReceiveCount reads moves it to its own dead-letter queue, or deletes it when its
retention runs out. A caller that waits for the answer (a function URL, API Gateway) gets the error, and Lambda
keeps nothing. Streams hold the shard until the record succeeds, expires or reaches the mapping's limits.
"""

from __future__ import annotations

import json

from ..._kit.errors import _why
from ..._kit.fmt import _plural

from ..helpers import _STREAM_KINDS
from ..models import AsyncConfig, AsyncCounts, EventFate, Function, QueueState, Trigger
from .text import _cli, _region_flag, _window


LOST, KEPT, WAITING, AT_RISK, SAFE, UNKNOWN = "lost", "kept", "waiting", "at risk", "safe", "unknown"

FATE_TONES = {LOST: "bad", AT_RISK: "warn", KEPT: "warn", WAITING: "", SAFE: "ok", UNKNOWN: ""}

_DEFAULT_RETRIES = 2

_NEAR_DELETION = 0.75

_SUGGESTED_RECEIVES = 5

_ASYNC_BASIS = "Lambda's count (AsyncEventsDropped)"

_QUEUE_BASIS = "the queue, now"

_QUEUE_PERMISSION = "sqs:GetQueueAttributes"

_STREAMS = set(_STREAM_KINDS.values()) - {"SQS queue"}


def queue_name(arn: str) -> str:
    """
    'arn:aws:sqs:us-east-1:123456789012:orders-dlq' -> 'orders-dlq' (or the text itself when it isn't an ARN).
    """
    return arn.rsplit(":", 1)[-1] if arn else ""


def parse_redrive(policy: str | None) -> tuple[str | None, int | None]:
    """
    A queue's RedrivePolicy attribute (JSON) -> (the dead-letter queue's ARN, maxReceiveCount); (None, None) when
    it has none or it can't be read.
    """
    if not policy:
        return None, None
    try:
        doc = json.loads(policy)
    except ValueError:
        return None, None
    if not isinstance(doc, dict) or not doc.get("deadLetterTargetArn"):
        return None, None
    try:
        receives = int(doc.get("maxReceiveCount"))
    except (TypeError, ValueError):
        receives = None
    return str(doc["deadLetterTargetArn"]), receives


def parse_async_config(config: dict | None) -> AsyncConfig | None:
    """
    GetFunctionEventInvokeConfig's answer -> AsyncConfig; {} (no config: Lambda's defaults) -> AsyncConfig();
    None (not read) -> None.
    """
    if config is None:
        return None
    destinations = config.get("DestinationConfig") or {}
    return AsyncConfig(
        int(config.get("MaximumRetryAttempts", _DEFAULT_RETRIES)),
        int(config.get("MaximumEventAgeInSeconds", 6 * 3600)),
        (destinations.get("OnSuccess") or {}).get("Destination"),
        (destinations.get("OnFailure") or {}).get("Destination"),
    )


def keep_failed_command(fn: Function) -> str:
    """
    The AWS CLI command that sends the asynchronous events Lambda gives up on to a queue named after the function.
    """
    account = fn.arn.split(":")[4] if fn.arn.count(":") >= 5 else "123456789012"
    queue = f"arn:aws:sqs:{fn.region or 'us-east-1'}:{account}:{fn.name}-failed"
    return (f"aws lambda put-function-event-invoke-config {_cli(fn)} --destination-config "
            f"'{{\"OnFailure\":{{\"Destination\":\"{queue}\"}}}}'")


def _sources(triggers: list[Trigger]) -> str:
    """
    'S3 bucket acme-uploads', 'S3 bucket a and SNS topic b', or 'its asynchronous callers'.
    """
    names = list(dict.fromkeys(f"{t.kind} {t.source}".strip() for t in triggers))
    if not names:
        return "its asynchronous callers"
    if len(names) == 1:
        return names[0]
    return ", ".join(names[:-1]) + f" and {names[-1]}"


def _still_there(triggers: list[Trigger]) -> str:
    """
    What a dropped event leaves behind, when its caller is S3: the file, never processed.
    """
    if any(t.kind == "S3 bucket" for t in triggers):
        return " The files are still in the bucket: they were never processed."
    return ""


def _async_fates(
    fn: Function, triggers: list[Trigger], config: AsyncConfig | None, counts: AsyncCounts | None
) -> list[EventFate]:
    """
    The fates of the events asynchronous callers sent: dropped with nothing to keep them, kept in the function's
    on-failure destination or dead-letter queue, or lost on the way there.
    """
    callers = [t for t in triggers if t.asynchronous]
    source = _sources(callers)
    if counts is None:
        if not callers:
            return []
        return [EventFate(UNKNOWN, source, f"Events from {source}: not counted", (
            "CloudWatch's count of the events Lambda gave up on couldn't be read, so whether any were dropped "
            "isn't known."), basis="CloudWatch, not read")]
    if not callers and not counts.received:
        return []
    dropped = round(counts.dropped)
    tries = (config.retries if config else _DEFAULT_RETRIES) + 1
    if not dropped and not counts.received:
        return [EventFate(SAFE, source, "No event dropped", (
            f"CloudWatch counted no events from {source} in this window, so none were given up on."),
            basis=_ASYNC_BASIS, count=0)]
    if not dropped:
        return [EventFate(SAFE, source, "No event dropped", (
            f"Lambda gave up on none of the {_plural(round(counts.received), 'event')} {source} sent in this "
            "window: the ones that failed succeeded when they were tried again."), basis=_ASYNC_BASIS, count=0)]
    keeper = (config.on_failure if config else None) or fn.dead_letter
    if keeper is None:
        return [EventFate(LOST, source, f"{_plural(dropped, 'event')} from {source} dropped", (
            f"Lambda tried each {tries} times, gave up, and nothing kept them: the function has no dead-letter "
            f"queue or on-failure destination.{_still_there(callers)}"), basis=_ASYNC_BASIS, count=dropped)]
    undelivered = min(dropped, round(counts.dead_letter_errors + counts.destination_failures))
    place = queue_name(keeper)
    fates = []
    if dropped > undelivered:
        fates.append(EventFate(KEPT, source, f"{_plural(dropped - undelivered, 'event')} kept in {place}", (
            f"Lambda gave up on them after {tries} tries and sent them to {place}, each with the event it was "
            "given: they can be sent to the function again once it's fixed."), basis=_ASYNC_BASIS,
            count=dropped - undelivered, place=place))
    if undelivered:
        fates.append(EventFate(LOST, source, f"{_plural(undelivered, 'event')} not delivered to {place}", (
            f"Lambda gave up on them and couldn't put them in {place}, so nothing kept them.{_still_there(callers)}"),
            basis="Lambda's count (DeadLetterErrors, DestinationDeliveryFailures)", count=undelivered, place=place))
    return fates


def _oldest(queue: QueueState) -> str:
    """
    ' The oldest is 6 hours old, and the queue keeps them 4 days.' (or how long it keeps them, when the age isn't
    known).
    """
    keeps = f"keeps them {_window(queue.retention)}"
    if queue.oldest_age is None:
        return f" The queue {keeps}."
    return f" The oldest is {_window(queue.oldest_age)} old, and the queue {keeps}."


def _near_deletion(queue: QueueState) -> bool:
    """
    Whether the queue's oldest message has lived _NEAR_DELETION of its retention, so it's deleted before long.
    """
    return queue.oldest_age is not None and queue.oldest_age >= _NEAR_DELETION * queue.retention


def _dead_letter_fate(trigger: Trigger, queue: QueueState) -> EventFate:
    """
    The fate of the messages a queue moved to its dead-letter queue: kept there, at risk when the oldest of them is
    near its retention, unknown when it couldn't be read.
    """
    dead = queue.dead_letter
    place = queue_name(queue.dead_letter_arn or "")
    source = f"{trigger.kind} {trigger.source}".strip()
    if dead is None or dead.error:
        why = f" ({_why(dead.error, _QUEUE_PERMISSION)})" if dead is not None and dead.error else ""
        return EventFate(UNKNOWN, source, f"{place}: not read", (
            f"Messages that fail {queue.max_receives or 'several'} times go to {place}, which couldn't be "
            f"read{why}."), basis=_QUEUE_BASIS, place=place)
    if not dead.held:
        return EventFate(SAFE, source, f"{place} is empty", (
            f"No message has failed the {queue.max_receives or 'allowed'} reads that send it there."),
            basis=_QUEUE_BASIS, count=0, place=place, queue=dead)
    outcome = AT_RISK if _near_deletion(dead) else KEPT
    return EventFate(outcome, source, f"{_plural(dead.held, 'message')} kept in {place}", (
        f"Moved there after failing {queue.max_receives or 'several'} reads: from any cause, and from any queue "
        f"that sends its failed messages there.{_oldest(dead)}"), basis=_QUEUE_BASIS, count=dead.held, place=place,
        queue=dead)


def _queue_fates(trigger: Trigger, queue: QueueState | None) -> list[EventFate]:
    """
    The fates of an SQS trigger's failed messages: still in the queue to be read again, kept in its dead-letter
    queue, or at risk of deletion when it has none.
    """
    source = f"{trigger.kind} {trigger.source}".strip()
    if queue is None or queue.error:
        why = f" ({_why(queue.error, _QUEUE_PERMISSION)})" if queue is not None and queue.error else ""
        return [EventFate(UNKNOWN, source, f"{trigger.source}: not read", (
            f"The queue couldn't be read{why}, so what happened to its failed messages isn't known."),
            basis=_QUEUE_BASIS, place=trigger.source)]
    fates = []
    if queue.held:
        outcome = AT_RISK if _near_deletion(queue) and not queue.dead_letter_arn else WAITING
        fates.append(EventFate(outcome, source, f"{_plural(queue.held, 'message')} still in {queue.name}", (
            f"They'll be read again.{_oldest(queue)}"), basis=_QUEUE_BASIS, count=queue.held, place=queue.name,
            queue=queue))
    if queue.dead_letter_arn:
        fates.append(_dead_letter_fate(trigger, queue))
    else:
        fates.append(EventFate(AT_RISK, source, f"No dead-letter queue on {queue.name}", (
            f"A message that keeps failing is read again and again until the queue's retention runs out "
            f"({_window(queue.retention)}), then deleted: nothing keeps it."), basis="the queue's settings",
            place=queue.name, queue=queue))
    return fates


def _other_fates(triggers: list[Trigger]) -> list[EventFate]:
    """
    What can't be counted: streams (the record holds up its shard) and callers that wait for the answer.
    """
    fates = []
    for trigger in triggers:
        if trigger.kind in _STREAMS:
            source = f"{trigger.kind} {trigger.source}".strip()
            fates.append(EventFate(UNKNOWN, source, f"{source}: not checked", (
                "A record that keeps failing holds up its shard until it succeeds, expires or reaches the mapping's "
                "retry limits; this report doesn't read the stream."), basis="not read"))
    waiting = [t for t in triggers if not t.asynchronous and t.via != "event source mapping"]
    if waiting:
        source = _sources(waiting)
        fates.append(EventFate(UNKNOWN, source, "Callers that wait for the answer", (
            f"{source} get the error back, and Lambda keeps nothing: whether the call is made again is up to "
            "them."), basis="Lambda keeps no count"))
    return fates


def event_fates(
    fn: Function,
    triggers: list[Trigger],
    *,
    async_config: AsyncConfig | None = None,
    counts: AsyncCounts | None = None,
    queues: dict[str, QueueState] | None = None,
) -> list[EventFate]:
    """
    What happened to a function's failed events, by what calls it: asynchronous callers' events dropped or kept
    (from CloudWatch's AsyncEventsDropped), each SQS trigger's messages still in the queue or in its dead-letter
    queue (`queues`, by queue ARN, as they are now), and what can't be counted (streams, callers that wait for the
    answer). The worst first.
    """
    queues = queues or {}
    fates = _async_fates(fn, triggers, async_config, counts)
    for trigger in triggers:
        if trigger.kind == "SQS queue":
            fates += _queue_fates(trigger, queues.get(trigger.arn or ""))
    fates += _other_fates(triggers)
    order = [LOST, AT_RISK, KEPT, WAITING, UNKNOWN, SAFE]
    return sorted(fates, key=lambda fate: order.index(fate.outcome))


def _queue_url(arn: str) -> str:
    """
    'arn:aws:sqs:us-east-1:123456789012:orders' -> 'https://sqs.us-east-1.amazonaws.com/123456789012/orders'.
    """
    parts = arn.split(":")
    if len(parts) < 6:
        return arn
    return f"https://sqs.{parts[3]}.amazonaws.com/{parts[4]}/{parts[5]}"


def _redrive_command(fn: Function, queue_arn: str) -> str:
    """
    The command that gives a queue a dead-letter queue named after it (made first), moving a message there after
    _SUGGESTED_RECEIVES failed reads.
    """
    dead = f"{queue_arn}-dlq"
    compact = (",", ":")
    policy = json.dumps({"deadLetterTargetArn": dead, "maxReceiveCount": str(_SUGGESTED_RECEIVES)}, separators=compact)
    attributes = json.dumps({"RedrivePolicy": policy}, separators=compact)
    return (f"aws sqs set-queue-attributes --queue-url {_queue_url(queue_arn)} --attributes '{attributes}'"
            f"{_region_flag(fn)}")


def _fate_finding(fn: Function, fate: EventFate) -> tuple[str, str] | None:
    """
    What to do about one fate, as a finding; None when there's nothing to do.
    """
    queue = fate.queue
    if fate.outcome == LOST and fate.place:
        return ("warn", (
            f"{fate.headline}: Lambda gave up on them and couldn't put them in {fate.place}, so they're gone. Check "
            f"that the function's role may send to it (sqs:SendMessage or sns:Publish), and that {fate.place} "
            "still exists."))
    if fate.outcome == LOST:
        return ("warn", (
            f"{fate.headline}: nothing kept them, and the next ones will go the same way. Keep them in a queue "
            f"(create it first): {keep_failed_command(fn)}."))
    if fate.outcome == AT_RISK and fate.count is None and queue is not None:
        return ("warn", (
            f"{fate.headline}: a message that keeps failing is deleted after {_window(queue.retention)}, and nothing "
            f"keeps it. Give the queue one (create it first): {_redrive_command(fn, queue.arn)}."))
    if fate.outcome == AT_RISK:
        keeps = _window(queue.retention) if queue is not None else "its retention period"
        return ("warn", (
            f"{fate.headline}, and the oldest are deleted before long: the queue deletes messages after {keeps}. "
            "Fix what fails them, or read them before they go."))
    if fate.outcome == KEPT and queue is not None:
        return ("info", (
            f"{fate.headline}: they wait there until it's fixed. Then send them back to be read again: "
            f"aws sqs start-message-move-task --source-arn {queue.arn}{_region_flag(fn)}."))
    if fate.outcome == KEPT:
        return ("info", (
            f"{fate.headline}: each message holds the event Lambda was given (requestPayload). Once it's fixed, "
            "send those events to the function again."))
    return None


def fate_findings(fn: Function, fates: list[EventFate]) -> list[tuple[str, str]]:
    """
    What to do about the events event_fates() found lost, at risk or kept: keep the next ones in a queue, give a
    queue a dead-letter queue, or send kept messages back once the function is fixed.
    """
    found = []
    for fate in fates:
        finding = _fate_finding(fn, fate)
        if finding is not None:
            found.append(finding)
    return found
