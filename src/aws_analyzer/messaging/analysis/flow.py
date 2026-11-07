"""
follow()'s map of where a message goes: from a topic, bus, rule or queue, every hop downstream (subscriptions,
targets, queues, the Lambda functions that read them, the dead-letter queues that catch failures) and what feeds it
upstream, each hop with its numbers and what's wrong there. Built from an Overview: no AWS calls.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..._kit.fmt import _count
from ..helpers import arn_kind, arn_name, arn_service, function_of, seconds_words
from ..models import Consumer, Hop, Overview, Queue, Rule, Subscription, Target, Topic
from .access import subscriber_allowed, target_allowed
from .policy import blocks_services
from .words import describe_filter


@dataclass
class _Node:
    hop: Hop
    children: list[_Node] = field(default_factory=list)


def _queue_numbers(overview: Overview, queue: Queue) -> str:
    numbers = overview.queue_numbers.get(queue.arn)
    parts = [f"{queue.visible:,} waiting"]
    if numbers is not None and numbers.oldest_now_s:
        parts.append(f"oldest {seconds_words(numbers.oldest_now_s)}")
    if numbers is not None:
        parts.append(f"{_count(numbers.sent)} in, {_count(numbers.deleted)} done")
    return " · ".join(parts)


def _queue_problem(queue: Queue) -> tuple[str, str]:
    if queue.is_dlq and queue.messages:
        return "bad", f"holds {queue.messages:,} failed messages"
    if queue.visible and not queue.consumers and not queue.is_dlq:
        return "warn", "no Lambda function reads it"
    return "", ""


def _consumer_node(consumer: Consumer) -> _Node:
    timeout = f" · timeout {seconds_words(consumer.timeout_s)}" if consumer.timeout_s else ""
    tone, problem = ("warn", f"trigger {consumer.state.lower()}") if not consumer.is_enabled else ("", "")
    hop = Hop(0, "Lambda function", consumer.function, consumer.function_arn, via="reads it",
              numbers=f"batch {consumer.batch_size}{timeout}", tone=tone, problem=problem)
    return _Node(hop)


class _MapBuilder:
    """
    Builds the map's nodes from an Overview, each queue, topic or rule opened once (a loop shows as a line naming
    the hop again, not opened).
    """

    def __init__(self, overview: Overview):
        self.overview = overview
        self.queues = {q.arn: q for q in overview.queues}
        self.seen: set[str] = set()

    def _again(self, arn: str, via: str) -> _Node | None:
        if arn not in self.seen:
            self.seen.add(arn)
            return None
        return _Node(Hop(0, arn_kind(arn), arn_name(arn), arn, via=via, numbers="(the same queue, above)"))

    def queue(self, queue: Queue, via: str = "") -> _Node:
        again = self._again(queue.arn, via)
        if again is not None:
            return again
        tone, problem = _queue_problem(queue)
        node = _Node(Hop(0, "SQS queue", queue.name, queue.arn, via=via,
                         numbers=_queue_numbers(self.overview, queue), tone=tone, problem=problem))
        node.children = [_consumer_node(c) for c in queue.consumers]
        dlq = self.queues.get(queue.redrive.dlq_arn) if queue.redrive else None
        if dlq is not None:
            node.children.append(self.queue(dlq, via=f"after {queue.redrive.max_receives} failed receives"))
        return node

    def topic(self, topic: Topic, via: str = "") -> _Node:
        again = self._again(topic.arn, via)
        if again is not None:
            return again
        numbers = self.overview.topic_numbers.get(topic.arn)
        tone, problem = ("bad", f"{numbers.failed:,} deliveries failed") if numbers and numbers.failed else ("", "")
        text = f"{_count(numbers.published)} published, {_count(numbers.delivered)} delivered" if numbers else ""
        node = _Node(Hop(0, "SNS topic", topic.name, topic.arn, via=via, numbers=text, tone=tone, problem=problem))
        node.children = [self.subscription(s) for s in topic.subscriptions]
        return node

    def _receiver(self, arn: str, via: str) -> _Node:
        """
        A subscription's or target's receiver: a queue or topic opened further, else a line of its own.
        """
        if arn in self.queues:
            return self.queue(self.queues[arn], via=via)
        topic = self.overview.topic(arn)
        if topic is not None:
            return self.topic(topic, via=via)
        facts = self.overview.functions.get(function_of(arn)) if arn_service(arn) == "lambda" else None
        hop = Hop(0, arn_kind(arn), arn_name(arn), arn, via=via)
        if facts is not None and not facts.exists:
            hop.tone, hop.problem = "bad", "the function doesn't exist"
        elif facts is not None and facts.timeout_s:
            hop.numbers = f"timeout {seconds_words(facts.timeout_s)}"
        return _Node(hop)

    def subscription(self, subscription: Subscription) -> _Node:
        via = f"{subscription.protocol} subscription"
        if subscription.filter_policy:
            via += f", {describe_filter(subscription.filter_policy, subscription.filter_scope)}"
        if subscription.protocol in ("sqs", "lambda"):
            node = self._receiver(subscription.endpoint, via)
        else:
            node = _Node(Hop(0, subscription.kind, subscription.endpoint, via=via))
        allowed = subscriber_allowed(subscription, queues=self.queues, functions=self.overview.functions)
        self._mark(node.hop, allowed=allowed, pending=subscription.pending, sender="the topic")
        if subscription.dlq_arn:
            node.children.append(self._dlq(subscription.dlq_arn, "deliveries that fail"))
        return node

    def _dlq(self, arn: str, via: str) -> _Node:
        if arn in self.queues:
            return self.queue(self.queues[arn], via=via)
        return _Node(Hop(0, arn_kind(arn), arn_name(arn), arn, via=via))

    def _mark(self, hop: Hop, *, allowed: bool | None, pending: bool = False, sender: str) -> None:
        """
        Marks a receiver that can't get its messages: a policy that keeps its sender out, its key, or a subscription
        nobody confirmed.
        """
        queue = self.queues.get(hop.arn)
        if allowed is False:
            hop.tone, hop.problem = "bad", f"its policy keeps {sender} out"
        elif queue is not None and blocks_services(queue.kms_key):
            hop.tone, hop.problem = "bad", "its key (alias/aws/sqs) keeps senders out"
        elif pending:
            hop.tone, hop.problem = "warn", "waiting to be confirmed"

    def target(self, rule: Rule, target: Target) -> _Node:
        retries = f", {target.max_attempts} retries" if target.max_attempts is not None else ""
        node = self._receiver(target.arn, f"target {target.id}{retries}")
        allowed = target_allowed(rule, target, queues=self.queues, functions=self.overview.functions)
        self._mark(node.hop, allowed=allowed, sender="the rule")
        if target.dlq_arn:
            node.children.append(self._dlq(target.dlq_arn, "events it can't deliver"))
        return node

    def rule(self, rule: Rule, via: str = "") -> _Node:
        again = self._again(rule.arn, via)
        if again is not None:
            return again
        numbers = self.overview.rule_numbers.get(rule.arn)
        text = f"{_count(numbers.matched)} matched, {_count(numbers.invocations)} sent" if numbers else ""
        tone, problem = "", ""
        if numbers and numbers.failed:
            tone, problem = "bad", f"{numbers.failed:,} deliveries failed"
        elif not rule.is_enabled:
            tone, problem = "warn", "disabled"
        node = _Node(Hop(0, "EventBridge rule", rule.label, rule.arn, via=via, numbers=text, tone=tone,
                         problem=problem))
        node.children = [self.target(rule, t) for t in rule.targets]
        return node

    def bus(self, name: str) -> _Node:
        bus = next((b for b in self.overview.buses if b.name == name), None)
        rules = [r for r in self.overview.rules if r.bus == name]
        node = _Node(Hop(0, "EventBridge bus", name, bus.arn if bus else "", numbers=f"{len(rules)} rules"))
        node.children = [self.rule(r, via="rule") for r in rules]
        return node


def _upstream(overview: Overview, arn: str) -> list[Hop]:
    """
    What sends messages to `arn`: topics subscribing it, rules targeting it, and queues whose dead-letter queue it is.
    """
    hops = []
    for topic in overview.topics:
        if any(s.endpoint == arn for s in topic.subscriptions):
            hops.append(Hop(0, "SNS topic", topic.name, topic.arn, via="subscribes it", upstream=True))
    for rule in overview.rules:
        if any(t.arn == arn for t in rule.targets):
            hops.append(Hop(0, "EventBridge rule", rule.label, rule.arn, via="targets it", upstream=True))
    for queue in overview.queues:
        if queue.redrive and queue.redrive.dlq_arn == arn:
            hops.append(Hop(0, "SQS queue", queue.name, queue.arn, via="sends its failures here", upstream=True))
    return hops


def _flatten(node: _Node, *, depth: int = 0, prefix: str = "", last: bool = True) -> list[Hop]:
    hop = node.hop
    hop.depth = depth
    hop.branch = "" if depth == 0 else prefix + ("└─ " if last else "├─ ")
    below = "" if depth == 0 else prefix + ("   " if last else "│  ")
    hops = [hop]
    for i, child in enumerate(node.children):
        hops += _flatten(child, depth=depth + 1, prefix=below, last=i == len(node.children) - 1)
    return hops


def follow_map(overview: Overview, kind: str, arn: str) -> list[Hop]:
    """
    The map from one queue, topic, rule or bus (kind 'queue', 'topic', 'rule' or 'bus'; a bus by its name in arn):
    what feeds it first (upstream), then it and every hop a message takes from there.
    """
    builder = _MapBuilder(overview)
    if kind == "queue":
        root = builder.queue(builder.queues[arn])
    elif kind == "topic":
        root = builder.topic(overview.topic(arn))
    elif kind == "rule":
        root = builder.rule(overview.rule(arn))
    else:
        root = builder.bus(arn)
    return _upstream(overview, arn) + _flatten(root)
