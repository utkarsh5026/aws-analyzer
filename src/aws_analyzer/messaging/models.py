"""
What MessagingAnalyzer returns: queues, topics, EventBridge rules and buses as read from AWS, their CloudWatch
numbers, and the reports built from them. Plain dataclasses; a few have to_df() (pandas, loaded when called).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from .._kit.deps import _require

from .helpers import PROTOCOL_WORDS, arn_kind, arn_name, arn_service


@dataclass
class Redrive:
    """
    Where a queue sends a message that has been received max_receives times without being deleted: its dead-letter
    queue.
    """

    dlq_arn: str
    max_receives: int

    @property
    def dlq_name(self) -> str:
        return arn_name(self.dlq_arn)


@dataclass
class Consumer:
    """
    A Lambda function reading a queue (an event source mapping): its state, batch size, whether it reports which
    messages of a batch failed, and the function's timeout (None when it couldn't be read).
    """

    function_arn: str
    uuid: str = ""
    state: str = ""
    batch_size: int = 0
    window_s: int = 0
    reports_failures: bool = False
    last_result: str = ""
    max_concurrency: int | None = None
    timeout_s: int | None = None

    @property
    def function(self) -> str:
        return arn_name(self.function_arn)

    @property
    def is_enabled(self) -> bool:
        return self.state.lower() in ("enabled", "enabling", "updating", "creating")


@dataclass
class Queue:
    """
    An SQS queue as GetQueueAttributes describes it: how many messages it holds now (visible, being processed,
    delayed), its settings, where failing messages go (redrive), which queues send it theirs (sources: it's their
    dead-letter queue) and which Lambda functions read it (consumers).
    """

    name: str
    url: str
    arn: str = ""
    fifo: bool = False
    visible: int = 0
    in_flight: int = 0
    delayed: int = 0
    retention_s: int = 345_600
    visibility_s: int = 30
    delay_s: int = 0
    wait_s: int = 0
    max_bytes: int = 262_144
    created: datetime | None = None
    encryption: str = ""
    kms_key: str = ""
    redrive: Redrive | None = None
    allows_redrive_from: str = ""
    policy: dict[str, Any] | None = None
    sources: list[str] = field(default_factory=list)
    consumers: list[Consumer] = field(default_factory=list)
    errors: dict[str, str] = field(default_factory=dict)

    @property
    def messages(self) -> int:
        return self.visible + self.in_flight + self.delayed

    @property
    def is_dlq(self) -> bool:
        return bool(self.sources)

    @property
    def region(self) -> str:
        parts = self.arn.split(":")
        return parts[3] if len(parts) > 3 else ""


@dataclass
class QueueNumbers:
    """
    A queue's CloudWatch numbers over the window: messages sent, received and deleted, receives that found nothing,
    and how old its oldest message got (the most, and the latest reading), in seconds; daily is (day, sent,
    deleted) for one queue's report.
    """

    sent: int = 0
    received: int = 0
    deleted: int = 0
    empty_receives: int = 0
    oldest_max_s: float | None = None
    oldest_now_s: float | None = None
    daily: list[tuple[datetime, int, int]] = field(default_factory=list)

    @property
    def requests(self) -> int:
        """
        The requests these messages took at most (one each to send, receive and delete, plus every empty receive):
        batches of up to 10 make it fewer.
        """
        return self.sent + self.received + self.deleted + self.empty_receives


@dataclass
class Subscription:
    """
    One subscription of a topic: where it delivers (protocol, endpoint), whether it's still waiting to be confirmed,
    its filter policy, and where messages it couldn't deliver go (dlq_arn: its own dead-letter queue).
    """

    arn: str
    topic_arn: str
    protocol: str
    endpoint: str
    pending: bool = False
    filter_policy: dict[str, Any] | None = None
    filter_scope: str = "MessageAttributes"
    raw: bool = False
    dlq_arn: str = ""

    @property
    def kind(self) -> str:
        return PROTOCOL_WORDS.get(self.protocol, self.protocol)

    @property
    def target(self) -> str:
        """
        The endpoint's short name: a queue's or function's name, else the URL, address or number as it is.
        """
        return arn_name(self.endpoint) if arn_service(self.endpoint) else self.endpoint


@dataclass
class Topic:
    """
    An SNS topic: its subscriptions, encryption, which protocols log their deliveries to CloudWatch Logs
    (logged: 'sqs', 'lambda', 'http', ...) and its access policy.
    """

    name: str
    arn: str
    fifo: bool = False
    display_name: str = ""
    kms_key: str = ""
    logged: list[str] = field(default_factory=list)
    policy: dict[str, Any] | None = None
    subscriptions: list[Subscription] = field(default_factory=list)
    errors: dict[str, str] = field(default_factory=dict)

    @property
    def pending(self) -> list[Subscription]:
        return [s for s in self.subscriptions if s.pending]


@dataclass
class TopicNumbers:
    """
    A topic's CloudWatch numbers over the window: messages published, deliveries made and failed, messages a
    subscription's filter dropped, and failed deliveries moved to a dead-letter queue (or not).
    """

    published: int = 0
    delivered: int = 0
    failed: int = 0
    filtered: int = 0
    redriven: int = 0
    redrive_failed: int = 0
    daily: list[tuple[datetime, int, int]] = field(default_factory=list)


@dataclass
class Target:
    """
    Where a rule sends a matching event: the target's ARN, its retry policy, its dead-letter queue (dlq_arn) for
    events it couldn't deliver, and whether it reshapes the event first.
    """

    id: str
    arn: str
    dlq_arn: str = ""
    max_attempts: int | None = None
    max_age_s: int | None = None
    role_arn: str = ""
    reshaped: bool = False

    @property
    def kind(self) -> str:
        return arn_kind(self.arn)

    @property
    def name(self) -> str:
        return arn_name(self.arn)


@dataclass
class Rule:
    """
    An EventBridge rule: the bus it listens on, the events it matches (pattern) or its schedule, whether it's on, and
    its targets.
    """

    name: str
    bus: str
    arn: str = ""
    state: str = "ENABLED"
    pattern: dict[str, Any] | None = None
    schedule: str = ""
    description: str = ""
    managed_by: str = ""
    targets: list[Target] = field(default_factory=list)
    errors: dict[str, str] = field(default_factory=dict)

    @property
    def is_enabled(self) -> bool:
        return self.state.upper().startswith("ENABLED")

    @property
    def label(self) -> str:
        """
        How reports name it: 'order-placed' on the default bus, 'shop/order-placed' on another.
        """
        return self.name if self.bus == "default" else f"{self.bus}/{self.name}"


@dataclass
class RuleNumbers:
    """
    A rule's CloudWatch numbers over the window: events it matched, target invocations, invocations that failed for
    good, failed ones sent to a dead-letter queue (or not), and throttled runs.
    """

    matched: int = 0
    invocations: int = 0
    failed: int = 0
    to_dlq: int = 0
    dlq_failed: int = 0
    throttled: int = 0
    daily: list[tuple[datetime, int, int]] = field(default_factory=list)


@dataclass
class Bus:
    """
    An EventBridge event bus, and the archives that keep a copy of its events (by name).
    """

    name: str
    arn: str = ""
    archives: list[str] = field(default_factory=list)


@dataclass
class FunctionFacts:
    """
    What a Lambda function a queue, subscription or rule hands messages to needs to be known: its timeout and its
    resource policy (who may invoke it), or None when it couldn't be read.
    """

    arn: str
    timeout_s: int | None = None
    policy: dict[str, Any] | None = None
    exists: bool = True
    error: str = ""


@dataclass
class Overview:
    """
    Every queue, topic, bus and rule in a region, with their CloudWatch numbers over `days` and the Lambda functions
    they hand messages to. errors names what couldn't be read (section -> error code), and cut_short the kinds that
    had more than the analyzer's limit.
    """

    region: str
    days: int
    queues: list[Queue] = field(default_factory=list)
    topics: list[Topic] = field(default_factory=list)
    buses: list[Bus] = field(default_factory=list)
    rules: list[Rule] = field(default_factory=list)
    queue_numbers: dict[str, QueueNumbers] = field(default_factory=dict)
    topic_numbers: dict[str, TopicNumbers] = field(default_factory=dict)
    rule_numbers: dict[str, RuleNumbers] = field(default_factory=dict)
    functions: dict[str, FunctionFacts] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)
    metrics_read: int = 0
    cut_short: list[str] = field(default_factory=list)

    def queue(self, arn: str) -> Queue | None:
        return next((q for q in self.queues if q.arn == arn), None)

    def topic(self, arn: str) -> Topic | None:
        return next((t for t in self.topics if t.arn == arn), None)

    def rule(self, arn: str) -> Rule | None:
        return next((r for r in self.rules if r.arn == arn), None)

    def queues_df(self) -> Any:
        """
        Every queue and its numbers as a pandas DataFrame.
        """
        pd = _require("pandas", "queues_df()")
        rows = []
        for queue in self.queues:
            numbers = self.queue_numbers.get(queue.arn) or QueueNumbers()
            rows.append({"queue": queue.name, "fifo": queue.fifo, "visible": queue.visible,
                         "in_flight": queue.in_flight, "delayed": queue.delayed,
                         "oldest_seconds": numbers.oldest_now_s, "sent": numbers.sent,
                         "received": numbers.received, "deleted": numbers.deleted,
                         "dead_letter_queue": queue.redrive.dlq_name if queue.redrive else None,
                         "is_dead_letter_queue": queue.is_dlq, "url": queue.url})
        return pd.DataFrame(rows)


@dataclass
class Feeder:
    """
    Something that sends messages into a queue or a Lambda function: an SNS subscription or an EventBridge rule's
    target. allowed says whether the receiver's policy lets it (None when that couldn't be told).
    """

    kind: str
    name: str
    arn: str
    detail: str = ""
    allowed: bool | None = None


@dataclass
class QueueReport:
    """
    One queue in full: the queue, its numbers (with a line per day), its dead-letter queue and that queue's numbers,
    what feeds it (SNS subscriptions, EventBridge rules) and the Lambda functions that read it.
    """

    queue: Queue
    days: int
    numbers: QueueNumbers | None = None
    dlq: Queue | None = None
    dlq_numbers: QueueNumbers | None = None
    feeders: list[Feeder] = field(default_factory=list)
    errors: dict[str, str] = field(default_factory=dict)
    metrics_read: int = 0


@dataclass
class TopicReport:
    """
    One topic in full: its subscriptions, numbers (with a line per day), and what's known about each subscriber
    (queues: their policy and dead-letter queue; functions: their resource policy).
    """

    topic: Topic
    days: int
    numbers: TopicNumbers | None = None
    queues: dict[str, Queue] = field(default_factory=dict)
    functions: dict[str, FunctionFacts] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)
    metrics_read: int = 0


@dataclass
class RuleReport:
    """
    One EventBridge rule in full: its targets, numbers (with a line per day), and what's known about each target
    (queues: their policy; functions: their resource policy).
    """

    rule: Rule
    days: int
    numbers: RuleNumbers | None = None
    queues: dict[str, Queue] = field(default_factory=dict)
    functions: dict[str, FunctionFacts] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)
    metrics_read: int = 0


@dataclass
class DeadLetter:
    """
    A dead-letter queue and what it catches failures from: queues (after too many receives), SNS subscriptions
    (deliveries that failed) and EventBridge targets (events that couldn't be delivered), as readable names.
    """

    queue: Queue
    numbers: QueueNumbers | None = None
    from_queues: list[str] = field(default_factory=list)
    from_subscriptions: list[str] = field(default_factory=list)
    from_targets: list[str] = field(default_factory=list)

    @property
    def catches(self) -> list[str]:
        return [*self.from_queues, *self.from_subscriptions, *self.from_targets]


@dataclass
class Hop:
    """
    One line of follow()'s map: how deep it sits, what it is (kind, name, ARN), how a message gets there (via), its
    numbers in a few words and what's wrong there (tone 'bad' or 'warn', with the reason). branch is the tree's
    lines before it ('│  ├─ '), and upstream marks what sends messages to where the map starts.
    """

    depth: int
    kind: str
    name: str
    arn: str = ""
    via: str = ""
    numbers: str = ""
    tone: str = ""
    problem: str = ""
    branch: str = ""
    upstream: bool = False


@dataclass
class MessageMap:
    """
    follow()'s answer: where it starts (kind 'queue', 'topic', 'rule' or 'bus', its name and ARN), the hops a message
    takes from there, and the overview they were read from.
    """

    kind: str
    name: str
    arn: str
    hops: list[Hop]
    overview: Overview
