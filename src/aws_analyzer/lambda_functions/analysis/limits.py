"""
How the services connected to a function limit it. Each caller has rules of its own that meet the function's
settings: API Gateway stops waiting after 29 s whatever the function's timeout, an SQS queue hands a message out again
once its visibility timeout ends, a stream stops at a batch that keeps failing, a mapping runs more copies than the
function may. connection_limits() lines each caller's rule up against the setting it meets, says what the two do
together, and gives the command that lines them up. Pure: no AWS calls.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..._kit.fmt import _plural

from ..helpers import human_ms
from ..models import AsyncConfig, FunctionDetail, QueueState, Trigger
from .fates import _queue_url
from .text import _cli, _region_flag, _window

API_GATEWAY_WAIT_S = 29
HTTP_API_WAIT_S = 30
LOAD_BALANCER_IDLE_S = 60
COGNITO_WAIT_S = 5
COGNITO_TRIES = 4
VISIBILITY_FACTOR = 6
FEWEST_RECEIVES = 5
FEWEST_MAPPING_CONCURRENCY = 2
STREAM_RETRIES_TO_TRY = 2
UNTIL_EXPIRED = -1

_QUEUE_KINDS = {"SQS queue"}
_STREAM_SOURCES = {"Kinesis stream", "DynamoDB stream"}


@dataclass
class ConnectionLimit:
    """
    One way something connected to a function limits it. `caller` is what it is ('API Gateway a1b2c3'), `limit` its
    rule in a few words ('waits 29 s for an answer'), `setting` the function's setting that meets it ('300 s
    timeout'), `effect` what the two do together, written as a finding ('what happens: why it matters.'), and `fix`
    the step or command that lines them up ('' when they already do). `tone` is 'warn' when they clash, 'ok' when
    they fit and '' when it's only worth knowing; `level` is the finding it makes ('warn', 'info', or '' for none),
    and `topic` the setting it's about ('timeout', 'concurrency', 'queue', 'stream' or 'async').
    """

    caller: str
    limit: str
    setting: str
    effect: str
    tone: str = ""
    fix: str = ""
    level: str = ""
    topic: str = "timeout"

    @property
    def finding(self) -> tuple[str, str] | None:
        """
        The (level, message) pair findings show, or None when it fits.
        """
        if not self.level:
            return None
        return self.level, f"{self.effect} {self.fix}".strip()


def _caller(trigger: Trigger) -> str:
    return f"{trigger.kind} {trigger.source}".strip()


def _timeout_command(detail: FunctionDetail, seconds: int) -> str:
    return f"aws lambda update-function-configuration {_cli(detail.function)} --timeout {seconds}"


def _longest_ms(detail: FunctionDetail) -> float | None:
    return detail.metrics.duration_max if detail.metrics is not None else None


def _api_gateway(detail: FunctionDetail, trigger: Trigger) -> ConnectionLimit:
    """
    API Gateway calls the function and waits for its answer, but only for 29 s (30 s for an HTTP API). Past that it
    answers the caller 504 and stops listening, while the run carries on, billed, to its own timeout, and its answer
    is thrown away. A caller that retries starts the same work again beside it.
    """
    timeout = detail.function.timeout
    limit = f"waits {API_GATEWAY_WAIT_S} s for an answer ({HTTP_API_WAIT_S} s for an HTTP API)"
    setting = f"{timeout:,} s timeout"
    if timeout <= HTTP_API_WAIT_S:
        return ConnectionLimit(_caller(trigger), limit, setting, (
            "Its timeout ends a run before API Gateway stops waiting, so a slow call fails with the function's own "
            "error, and no run is left working for a caller that's gone."), tone="ok")
    longest = _longest_ms(detail)
    is_happening = longest is not None and longest > API_GATEWAY_WAIT_S * 1000
    seen = (f"Its longest run took {human_ms(longest)}, so this already happens. " if is_happening else "")
    effect = (
        f"API Gateway stops waiting long before the function does: after {API_GATEWAY_WAIT_S} s the caller gets a "
        f"504, while the run goes on, billed, for up to {timeout:,} s and its answer is thrown away. A caller that "
        f"retries runs the work twice. {seen}".rstrip())
    fix = (f"Lower the timeout to what API Gateway waits ({_timeout_command(detail, API_GATEWAY_WAIT_S)}), or, "
           "for work that needs longer, answer at once and hand the work to a queue or Step Functions.")
    return ConnectionLimit(_caller(trigger), limit, setting, effect, tone="warn", fix=fix,
                           level="warn" if is_happening else "info")


def _load_balancer_wait(detail: FunctionDetail, trigger: Trigger) -> tuple[int, str, str]:
    """
    How long the load balancer in front of a target group waits (its idle timeout), and words for both cases: the
    one read from it, or the 60 s default when it couldn't be (no target group ARN, or the read was refused).
    """
    wait = detail.load_balancers.get(trigger.arn or "")
    if wait is None:
        return (LOAD_BALANCER_IDLE_S, f"waits {LOAD_BALANCER_IDLE_S} s by default (its idle timeout)",
                f"unless its idle timeout was raised, after {LOAD_BALANCER_IDLE_S} s")
    return (wait.idle_timeout, f"{wait.name} waits {wait.idle_timeout:,} s (its idle timeout)",
            f"{wait.name} waits {wait.idle_timeout:,} s, so after that")


def _idle_timeout_command(detail: FunctionDetail, trigger: Trigger) -> str:
    wait = detail.load_balancers.get(trigger.arn or "")
    arn = wait.arn if wait is not None else "<load balancer ARN>"
    return (f"aws elbv2 modify-load-balancer-attributes --load-balancer-arn {arn} --attributes "
            f"Key=idle_timeout.timeout_seconds,Value={detail.function.timeout}")


def _load_balancer(detail: FunctionDetail, trigger: Trigger) -> ConnectionLimit:
    """
    An Application Load Balancer waits for the function up to its idle timeout, 60 s unless someone raised it: the
    one read from the load balancer in front of the target group when it could be, else that default.
    """
    timeout = detail.function.timeout
    wait, limit, after = _load_balancer_wait(detail, trigger)
    setting = f"{timeout:,} s timeout"
    if timeout <= wait:
        return ConnectionLimit(_caller(trigger), limit, setting,
                               "Its timeout ends a run before the load balancer stops waiting.", tone="ok")
    is_read = trigger.arn in detail.load_balancers
    effect = (f"The load balancer stops waiting before the function does: {after} the caller gets a 504 while the "
              f"run goes on for up to {timeout:,} s, billed, and its answer is thrown away.")
    fix = (f"Raise the load balancer's idle timeout to match ({_idle_timeout_command(detail, trigger)}), or lower "
           f"the function's timeout to {wait} s ({_timeout_command(detail, wait)}).")
    return ConnectionLimit(_caller(trigger), limit, setting, effect, tone="warn", fix=fix,
                           level="warn" if is_read else "info")


def _cognito(detail: FunctionDetail, trigger: Trigger) -> ConnectionLimit:
    """
    Cognito waits 5 s for a trigger, then tries again, up to 3 more times, so a sign-in waits on every try.
    """
    timeout = detail.function.timeout
    limit = f"waits {COGNITO_WAIT_S} s, then tries again ({COGNITO_TRIES} tries)"
    setting = f"{timeout:,} s timeout"
    if timeout <= COGNITO_WAIT_S:
        return ConnectionLimit(_caller(trigger), limit, setting,
                               "Its timeout fits in the 5 s Cognito waits for a trigger.", tone="ok")
    effect = (f"Cognito gives up on it long before its timeout: it waits {COGNITO_WAIT_S} s for a trigger, then "
              f"calls it again, up to {COGNITO_TRIES} times, so a slow run fails the sign-in and may run "
              f"{COGNITO_TRIES} times side by side.")
    fix = f"Keep it under {COGNITO_WAIT_S} s: {_timeout_command(detail, COGNITO_WAIT_S)}."
    return ConnectionLimit(_caller(trigger), limit, setting, effect, tone="warn", fix=fix, level="info")


def _queue_visibility(detail: FunctionDetail, trigger: Trigger, queue: QueueState | None) -> ConnectionLimit:
    """
    While a run works on a message, SQS hides it from other readers for the visibility timeout. If that ends before
    the run does, the message shows again and another run takes it: the work is done twice, and each time counts as a
    receive towards the dead-letter queue. AWS asks for at least six times the timeout, plus the batch window.
    """
    timeout = detail.function.timeout
    wanted = VISIBILITY_FACTOR * timeout + trigger.batch_window
    setting = f"{timeout:,} s timeout"
    if queue is None or queue.error:
        why = f" ({queue.error})" if queue is not None and queue.error else ""
        return ConnectionLimit(_caller(trigger), "hides a message while it's read (visibility timeout)", setting,
                               f"Couldn't read the queue{why}, so it isn't known whether a message can come back "
                               f"while a run still works on it. It should hide one for at least {wanted:,} s.",
                               topic="queue")
    limit = f"hides a message {queue.visibility:,} s while it's read"
    command = (f"aws sqs set-queue-attributes --queue-url {_queue_url(queue.arn)} --attributes "
               f"VisibilityTimeout={wanted}")
    if queue.visibility >= wanted:
        return ConnectionLimit(_caller(trigger), limit, setting, (
            f"A message stays hidden {VISIBILITY_FACTOR} times as long as a run may take, so no other run takes "
            "it while it's worked on."), tone="ok", topic="queue")
    if queue.visibility < timeout:
        effect = (f"Messages from {queue.name} come back while they're still being worked on: it hides one for "
                  f"{queue.visibility:,} s, but a run may take {timeout:,} s, so another run takes it too, the work "
                  "is done twice, and each comeback counts towards its dead-letter queue.")
        return ConnectionLimit(_caller(trigger), limit, setting, effect, tone="warn",
                               fix=f"Hide them for at least {wanted:,} s: {command}.", level="warn", topic="queue")
    effect = (f"{queue.name} hides a message for less than AWS asks: {queue.visibility:,} s, against "
              f"{VISIBILITY_FACTOR} times the {timeout:,} s timeout (plus the batch window), which leaves room for "
              "retries after throttling.")
    return ConnectionLimit(_caller(trigger), limit, setting, effect, fix=f"To follow it: {command}.", level="info",
                           topic="queue")


def _queue_concurrency(detail: FunctionDetail, trigger: Trigger) -> ConnectionLimit | None:
    """
    Lambda reads a queue with as many calls at once as the region allows, unless the mapping's maximum concurrency
    says fewer. A function with reserved concurrency can't run that many: the rest are throttled, their messages go
    back to the queue, and each try counts as a receive, so messages reach the dead-letter queue without ever failing.
    """
    reserved = detail.function.reserved_concurrency
    if not reserved:
        return None
    most = trigger.max_concurrency
    reads = f"reads with up to {most:,} calls at once" if most else "reads with as many calls at once as it can"
    setting = f"reserved concurrency {reserved:,}"
    if most is not None and most <= reserved:
        return ConnectionLimit(_caller(trigger), reads, setting, (
            "Lambda reads the queue with no more calls at once than the function may run, so none is throttled."),
            tone="ok", topic="concurrency")
    effect = (f"Lambda reads {trigger.source} with more calls at once than the function may run ({reserved:,}): "
              "the extra calls are throttled, their messages go back to the queue, and each try counts as a "
              "receive, so messages can reach the dead-letter queue without ever failing.")
    if reserved < FEWEST_MAPPING_CONCURRENCY:
        fix = "Raise its reserved concurrency to 2 or more, and cap the mapping's maximum concurrency to match."
    else:
        fix = (f"Cap the mapping at the same number: aws lambda update-event-source-mapping --uuid "
               f"{trigger.uuid or '<uuid>'} --scaling-config MaximumConcurrency={reserved}"
               f"{_region_flag(detail.function)}.")
    return ConnectionLimit(_caller(trigger), reads, setting, effect, tone="warn", fix=fix, level="warn",
                           topic="concurrency")


def _queue_receives(trigger: Trigger, queue: QueueState | None) -> ConnectionLimit | None:
    """
    A queue whose redrive policy gives up after fewer than five receives: a message that's throttled or comes back a
    few times lands in the dead-letter queue even when the function would have handled it.
    """
    if queue is None or queue.error or queue.max_receives is None or queue.max_receives >= FEWEST_RECEIVES:
        return None
    effect = (f"{queue.name} gives up on a message after {_plural(queue.max_receives, 'receive')}: a throttled "
              "call or a message that comes back while still being worked on uses one up, so messages can land in "
              "its dead-letter queue without the function failing them.")
    return ConnectionLimit(_caller(trigger), f"moves a message on after {_plural(queue.max_receives, 'receive')}",
                           "its runs and throttles", effect,
                           fix=f"Raise maxReceiveCount in its redrive policy to {FEWEST_RECEIVES} or more.",
                           level="info", topic="queue")


def _stream(detail: FunctionDetail, trigger: Trigger) -> ConnectionLimit:
    """
    Lambda reads a stream's shard in order, so a batch that fails is tried again before anything behind it. With the
    defaults it's tried until its records expire, and the shard waits all that time.
    """
    retries, age = trigger.max_retries, trigger.max_record_age
    is_endless = retries in (None, UNTIL_EXPIRED) and age in (None, UNTIL_EXPIRED)
    tries = "until its records expire" if retries in (None, UNTIL_EXPIRED) else _plural(retries, "retry")
    limit = "reads each shard in order"
    setting = f"failed batch: {tries}" + (", split in two" if trigger.split_batches else "")
    uuid = trigger.uuid or "<uuid>"
    if is_endless:
        effect = (f"One bad record can stop {trigger.source}: a batch that fails is tried again until its records "
                  "expire (24 h for a DynamoDB stream, the stream's retention for Kinesis), and nothing behind it "
                  "in that shard is read meanwhile, so the data the function makes falls further behind.")
        fix = (f"Give up after a few tries, split a failing batch to find the bad record, and keep it: aws lambda "
               f"update-event-source-mapping --uuid {uuid} --maximum-retry-attempts {STREAM_RETRIES_TO_TRY} "
               f"--bisect-batch-on-function-error{_region_flag(detail.function)} (and an on-failure destination "
               "with --destination-config).")
        return ConnectionLimit(_caller(trigger), limit, setting, effect, tone="warn", fix=fix, level="warn",
                               topic="stream")
    if not trigger.failure_destination:
        effect = (f"A batch that still fails is skipped and its records are lost: {trigger.source} has no "
                  "on-failure destination to keep them.")
        fix = (f"Keep them in a queue: aws lambda update-event-source-mapping --uuid {uuid} --destination-config "
               "'{\"OnFailure\":{\"Destination\":\"<queue ARN>\"}}'.")
        return ConnectionLimit(_caller(trigger), limit, setting, effect, fix=fix, level="info", topic="stream")
    return ConnectionLimit(_caller(trigger), limit, setting, (
        "A failing batch is tried a few times, then kept aside, so the shard moves on."), tone="ok", topic="stream")


def _asynchronous(detail: FunctionDetail, triggers: list[Trigger]) -> ConnectionLimit | None:
    """
    S3, SNS, EventBridge and the rest don't wait for the function: Lambda takes the event, answers at once, and keeps
    it in its own queue until a run takes it. Events wait there while the function is busy or throttled, and one
    still waiting after the oldest age it keeps is dropped.
    """
    config = detail.async_config or AsyncConfig()
    callers = ", ".join(_caller(t) for t in triggers[:3]) + (f" and {len(triggers) - 3} more" if len(triggers) > 3
                                                              else "")
    tries = 1 + config.retries
    tries_text = "1 try" if tries == 1 else f"{tries} tries"
    reserved = detail.function.reserved_concurrency
    busy = (f" With its reserved concurrency of {reserved:,}, a burst waits there in line." if reserved else "")
    effect = (f"They don't wait for it: Lambda keeps their events and gives each {tries_text}, "
              f"dropping one still waiting after {_window(config.max_age)}, so a slow or throttled function delays "
              f"events rather than failing the sender.{busy}")
    return ConnectionLimit(callers, f"hand the event to Lambda's queue (kept {_window(config.max_age)})",
                           f"{tries_text} per event", effect, topic="async")


def _function_url(trigger: Trigger) -> ConnectionLimit:
    return ConnectionLimit(_caller(trigger), "waits as long as the function runs", "its timeout", (
        "A function URL adds no time limit of its own: a call waits for the run, up to its timeout (the caller's "
        "own HTTP client may give up sooner)."), tone="ok")


def _sync_limit(detail: FunctionDetail, trigger: Trigger) -> ConnectionLimit | None:
    if trigger.kind == "API Gateway":
        return _api_gateway(detail, trigger)
    if trigger.kind == "Load balancer":
        return _load_balancer(detail, trigger)
    if trigger.kind == "Cognito user pool":
        return _cognito(detail, trigger)
    if trigger.kind == "Function URL":
        return _function_url(trigger)
    return None


def _mapping_limits(detail: FunctionDetail, trigger: Trigger,
                    queues: dict[str, QueueState]) -> list[ConnectionLimit]:
    if trigger.kind in _STREAM_SOURCES:
        return [_stream(detail, trigger)]
    if trigger.kind not in _QUEUE_KINDS:
        return []
    queue = queues.get(trigger.arn or "")
    found = [_queue_visibility(detail, trigger, queue), _queue_concurrency(detail, trigger),
             _queue_receives(trigger, queue)]
    return [limit for limit in found if limit is not None]


def connection_limits(detail: FunctionDetail, queues: dict[str, QueueState] | None = None) -> list[ConnectionLimit]:
    """
    How each thing connected to the function limits it: a caller that stops waiting before its timeout (API Gateway,
    a load balancer, Cognito), a queue that hands a message out again or gives up on it, a stream that stops at a
    failing batch, a mapping that runs more copies than it may, and the events Lambda keeps for the asynchronous
    callers. `queues` are its SQS queues as they are now (QueueState by ARN), for their visibility timeouts.
    Clashes first, then what's worth knowing, then what fits.
    """
    queues = queues or {}
    found: list[ConnectionLimit] = []
    for trigger in detail.triggers:
        if trigger.via == "event source mapping":
            found += _mapping_limits(detail, trigger, queues)
            continue
        limit = _sync_limit(detail, trigger)
        if limit is not None:
            found.append(limit)
    asynchronous = [t for t in detail.triggers if t.asynchronous]
    if asynchronous:
        found.append(_asynchronous(detail, asynchronous))
    order = {"warn": 0, "": 1, "ok": 2}
    return sorted(found, key=lambda limit: order.get(limit.tone, 1))


_WAITING_CALLERS = {"API Gateway": API_GATEWAY_WAIT_S, "Cognito user pool": COGNITO_WAIT_S}


def raising_timeout_note(detail: FunctionDetail, new_timeout: int) -> str:
    """
    What else a longer timeout needs, from what's connected: a caller that stops waiting sooner whatever the timeout
    (raising it doesn't help its callers), and a queue whose visibility timeout must grow with it. '' when nothing
    connected minds.
    """
    notes = []
    for trigger in detail.triggers:
        wait = _WAITING_CALLERS.get(trigger.kind)
        if trigger.kind == "Load balancer":
            wait = _load_balancer_wait(detail, trigger)[0]
        if wait is not None and new_timeout > wait:
            notes.append(f"{_caller(trigger)} stops waiting after {wait} s whatever the timeout, so a longer one won't "
                         "help its callers: make it answer sooner, or answer at once and hand the work to a queue")
    for arn, queue in detail.queues.items():
        trigger = next((t for t in detail.triggers if t.arn == arn), None)
        window = trigger.batch_window if trigger is not None else 0
        wanted = VISIBILITY_FACTOR * new_timeout + window
        if not queue.error and queue.visibility < wanted:
            notes.append(f"raise {queue.name}'s visibility timeout with it, to {wanted:,} s, or its messages come back "
                         "while still being worked on")
    if not notes:
        return ""
    return " But " + "; and ".join(notes) + "."


def limit_findings(limits: list[ConnectionLimit]) -> list[tuple[str, str]]:
    """
    The findings the limits make: each clash, with the command that lines it up.
    """
    findings = []
    for limit in limits:
        finding = limit.finding
        if finding is not None:
            findings.append(finding)
    return findings
