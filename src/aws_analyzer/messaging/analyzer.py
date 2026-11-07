"""
MessagingAnalyzer: talks to SQS, SNS, EventBridge, CloudWatch and Lambda (which functions read a queue, and who may
invoke one) and returns the dataclasses in models. Never prints, and reads only: it never receives, sends, moves or
deletes a message, because receiving one hides it from its readers and counts as a try towards its dead-letter
queue. So it shows counts, ages and settings, not what's in a queue.
"""

from __future__ import annotations

import difflib
import threading
from typing import Any, Callable

from botocore.exceptions import BotoCoreError, ClientError, NoRegionError

from .._kit.analyzer import _BaseAnalyzer
from .._kit.errors import _error_code, _error_name, _Hint, _why

from .analysis import dead_letters_from, feeders_of, follow_map, parse_consumer, parse_queue, parse_rule
from .analysis import parse_json, parse_subscription, parse_topic
from .helpers import DEFAULT_BUS, Ref, arn_name, arn_service, function_of, parse_ref, queue_url
from .metrics import _MetricReads
from .models import (
    Bus, DeadLetter, FunctionFacts, MessageMap, Overview, Queue, QueueReport, Rule, RuleReport, Subscription, Topic,
    TopicReport,
)
from .prices import MESSAGING_PRICES


DEFAULT_LIMIT = 1_000

_MISSING_QUEUE = ("AWS.SimpleQueueService.NonExistentQueue", "QueueDoesNotExist")

_PERMISSIONS = {"queues": "sqs:ListQueues and sqs:GetQueueAttributes",
                "consumers": "lambda:ListEventSourceMappings", "topics": "sns:ListTopics and sns:GetTopicAttributes",
                "subscriptions": "sns:ListSubscriptions and sns:GetSubscriptionAttributes",
                "rules": "events:ListEventBuses, events:ListRules and events:ListTargetsByRule",
                "functions": "lambda:GetFunctionConfiguration and lambda:GetPolicy",
                "metrics": "cloudwatch:GetMetricData"}


def _sources_from_redrives(queues: list[Queue]) -> None:
    """
    Fills each queue's sources (the queues that send it their failures) from the others' redrive policies.
    """
    by_arn = {q.arn: q for q in queues}
    for queue in queues:
        dlq = by_arn.get(queue.redrive.dlq_arn) if queue.redrive else None
        if dlq is not None and queue.name not in dlq.sources:
            dlq.sources.append(queue.name)


def _closest(name: str, names: list[str]) -> str:
    close = difflib.get_close_matches(name, names, n=3, cutoff=0.5)
    return f" Did you mean {', '.join(repr(c) for c in close)}?" if close else ""


class MessagingAnalyzer(_MetricReads, _BaseAnalyzer):
    """
    Pure-logic SQS, SNS and EventBridge analysis: every method returns data; nothing is printed, sent or changed.

    Methods about one queue, topic or rule take its name, URL, ARN or console link (a rule on another bus as
    'bus/rule'). `prices` overrides MESSAGING_PRICES. `client` is the sqs client and `clients` other boto3 clients by
    service name ('sqs', 'sns', 'events', 'cloudwatch', 'lambda'). `limit` caps how many queues, topics and rules
    overview() reads.
    """

    _LIST_PRICES = MESSAGING_PRICES

    def __init__(  # noqa: PLR0913
        self,
        session: Any = None,
        *,
        region: str | None = None,
        profile: str | None = None,
        client: Any = None,
        clients: dict[str, Any] | None = None,
        prices: dict[str, float] | None = None,
        limit: int = DEFAULT_LIMIT,
    ):
        super().__init__(session, region=region, profile=profile, prices=prices)
        self._clients: dict[str, Any] = dict(clients or {})
        if client is not None:
            self._clients["sqs"] = client
        self._lock = threading.RLock()
        self.max_workers = 8
        self.metrics_read = 0
        self.limit = limit

    @property
    def client(self) -> Any:
        """
        The sqs client, made on first use so a missing region shows up as a readable error.
        """
        return self._cached_client("sqs", self._make_client)

    def _make_client(self) -> Any:
        try:
            return self.session.client("sqs", config=self._config)
        except NoRegionError:
            raise ValueError(
                "No AWS region is set, and queues, topics and rules are regional. Pass one: "
                "MessagingView(MessagingAnalyzer(region='us-east-1')), or set AWS_DEFAULT_REGION."
            ) from None

    def _sns(self) -> Any:
        return self._cached_client("sns", lambda: self.session.client("sns", region_name=self.region,
                                                                      config=self._config))

    def _events(self) -> Any:
        return self._cached_client("events", lambda: self.session.client("events", region_name=self.region,
                                                                         config=self._config))

    def _cloudwatch(self) -> Any:
        return self._cached_client("cloudwatch", lambda: self.session.client("cloudwatch", region_name=self.region,
                                                                             config=self._config))

    def _lambda(self) -> Any:
        return self._cached_client("lambda", lambda: self.session.client("lambda", region_name=self.region,
                                                                         config=self._config))

    def _pages(self, client: Any, operation: str, key: str, *, limit: int | None = None,
               **params: Any) -> list[dict[str, Any]]:
        """
        Every item under `key` from a list operation, following NextToken (up to `limit` items when given).
        """
        items: list[Any] = []
        while True:
            answer = getattr(client, operation)(**params)
            items += answer.get(key, [])
            token = answer.get("NextToken")
            if not token or (limit is not None and len(items) >= limit):
                return items if limit is None else items[:limit]
            params["NextToken"] = token

    def _check_region(self, ref: Ref) -> None:
        if ref.region and ref.region != self.region:
            raise _Hint(f"{ref.name} is in {ref.region}, and this reads {self.region}: "
                        f"MessagingView(MessagingAnalyzer(region='{ref.region}')) reads it there.")


    def queue_urls(self, *, limit: int | None = None) -> list[str]:
        """
        Every queue's URL in the region (up to `limit`).
        """
        return self._pages(self.client, "list_queues", "QueueUrls", limit=limit, MaxResults=1000)

    def read_queue(self, url: str) -> Queue:
        """
        One queue's attributes, as a Queue.
        """
        attributes = self.client.get_queue_attributes(QueueUrl=url, AttributeNames=["All"]).get("Attributes", {})
        return parse_queue(url, attributes)

    def _read_queue_safely(self, url: str) -> Queue | None:
        try:
            return self.read_queue(url)
        except ClientError as exc:
            if _error_code(exc) in _MISSING_QUEUE:
                return None
            name = url.rsplit("/", 1)[-1]
            return Queue(name=name, url=url, errors={"attributes": _error_code(exc)})

    def queues(self, *, limit: int | None = None) -> list[Queue]:
        """
        Every queue in the region with its attributes, which queues send it their failures (sources) and the Lambda
        functions that read it (consumers).
        """
        queues = [q for q in self._map(self._read_queue_safely, self.queue_urls(limit=limit)) if q is not None]
        _sources_from_redrives(queues)
        return queues

    def _mappings(self, queue_arn: str | None = None) -> list[dict[str, Any]]:
        params = {"EventSourceArn": queue_arn} if queue_arn else {}
        mappings = self._pages(self._lambda(), "list_event_source_mappings", "EventSourceMappings", **params)
        return [m for m in mappings if arn_service(m.get("EventSourceArn", "")) == "sqs"]

    def _attach_consumers(self, queues: list[Queue], mappings: list[dict[str, Any]]) -> None:
        """
        Puts each event source mapping on the queue it reads, with its function's timeout.
        """
        by_arn = {q.arn: q for q in queues}
        functions = self.functions([m.get("FunctionArn", "") for m in mappings], policies=False)
        for mapping in mappings:
            queue = by_arn.get(mapping.get("EventSourceArn", ""))
            if queue is None:
                continue
            consumer = parse_consumer(mapping)
            facts = functions.get(function_of(consumer.function_arn))
            consumer.timeout_s = facts.timeout_s if facts else None
            queue.consumers.append(consumer)

    def _function(self, arn: str, policies: bool) -> FunctionFacts:
        client = self._lambda()
        try:
            timeout = client.get_function_configuration(FunctionName=arn).get("Timeout")
        except ClientError as exc:
            if _error_code(exc) == "ResourceNotFoundException":
                return FunctionFacts(arn, exists=False)
            return FunctionFacts(arn, error=_error_code(exc))
        facts = FunctionFacts(arn, timeout_s=timeout)
        if policies:
            self._read_policy(facts)
        return facts

    def _read_policy(self, facts: FunctionFacts) -> None:
        try:
            facts.policy = parse_json(self._lambda().get_policy(FunctionName=facts.arn).get("Policy"))
        except ClientError as exc:
            if _error_code(exc) != "ResourceNotFoundException":
                facts.error = _error_code(exc)

    def functions(self, arns: list[str], *, policies: bool = True) -> dict[str, FunctionFacts]:
        """
        What's known about each Lambda function (by unversioned ARN): its timeout, and its resource policy unless
        policies=False.
        """
        unique = list(dict.fromkeys(function_of(a) for a in arns if arn_service(a) == "lambda"))
        found = self._map(lambda arn: self._function(arn, policies), unique)
        return {facts.arn: facts for facts in found}


    def _subscription_summaries(self, topic_arn: str | None = None) -> list[dict[str, Any]]:
        if topic_arn:
            return self._pages(self._sns(), "list_subscriptions_by_topic", "Subscriptions", TopicArn=topic_arn)
        return self._pages(self._sns(), "list_subscriptions", "Subscriptions")

    def _subscription(self, summary: dict[str, Any]) -> Subscription:
        """
        A subscription with its attributes (filter policy, dead-letter queue); one still waiting to be confirmed has
        none to read.
        """
        arn = summary.get("SubscriptionArn", "")
        if not arn.startswith("arn:"):
            return parse_subscription(summary)
        try:
            attributes = self._sns().get_subscription_attributes(SubscriptionArn=arn).get("Attributes", {})
        except ClientError:
            attributes = {}
        return parse_subscription(summary, attributes)

    def read_topic(self, arn: str, summaries: list[dict[str, Any]] | None = None) -> Topic:
        """
        One topic with its attributes and subscriptions (summaries: ListSubscriptions' entries for it, when they've
        been read already).
        """
        attributes = self._sns().get_topic_attributes(TopicArn=arn).get("Attributes", {})
        if summaries is None:
            summaries = self._subscription_summaries(arn)
        return parse_topic(arn, attributes, [self._subscription(s) for s in summaries])

    def _read_topic_safely(self, arn: str, summaries: list[dict[str, Any]]) -> Topic:
        try:
            return self.read_topic(arn, summaries)
        except ClientError as exc:
            topic = parse_topic(arn, {}, [self._subscription(s) for s in summaries])
            topic.errors["attributes"] = _error_code(exc)
            return topic

    def topic_arns(self, *, limit: int | None = None) -> list[str]:
        """
        Every topic's ARN in the region (up to `limit`).
        """
        return [t["TopicArn"] for t in self._pages(self._sns(), "list_topics", "Topics", limit=limit)]

    def topics(self, *, limit: int | None = None) -> list[Topic]:
        """
        Every topic in the region with its subscriptions.
        """
        arns = self.topic_arns(limit=limit)
        by_topic: dict[str, list[dict[str, Any]]] = {arn: [] for arn in arns}
        for summary in self._subscription_summaries():
            if summary.get("TopicArn") in by_topic:
                by_topic[summary["TopicArn"]].append(summary)
        return self._map(lambda arn: self._read_topic_safely(arn, by_topic[arn]), arns)

    def _find_topic(self, name: str) -> str:
        ref = parse_ref(name)
        self._check_region(ref)
        if ref.kind not in ("", "topic"):
            raise _Hint(f"{name} is a {ref.kind}, not a topic: {ref.kind}_info({ref.name!r}) shows it.")
        arns = self.topic_arns()
        found = next((arn for arn in arns if arn_name(arn) == ref.name), None)
        if found is None:
            raise _Hint(f"No topic named {ref.name!r} in {self.region}.{_closest(ref.name, [arn_name(a) for a in arns])}"
                        " overview() lists them.")
        return found


    def buses(self) -> list[Bus]:
        """
        Every event bus in the region, with the names of the archives that keep a copy of its events.
        """
        events = self._events()
        buses = [Bus(b["Name"], b.get("Arn", "")) for b in self._pages(events, "list_event_buses", "EventBuses")]
        try:
            archives = self._pages(events, "list_archives", "Archives")
        except ClientError:
            archives = []
        for bus in buses:
            bus.archives = [a["ArchiveName"] for a in archives if a.get("EventSourceArn") == bus.arn]
        return buses

    def _with_targets(self, described: dict[str, Any]) -> Rule:
        bus = described.get("EventBusName") or DEFAULT_BUS
        try:
            targets = self._pages(self._events(), "list_targets_by_rule", "Targets", Rule=described["Name"],
                                  EventBusName=bus)
        except ClientError as exc:
            rule = parse_rule(described)
            rule.errors["targets"] = _error_code(exc)
            return rule
        return parse_rule(described, targets)

    def rules(self, *, bus: str | None = None, buses: list[Bus] | None = None, limit: int | None = None) -> list[Rule]:
        """
        Every rule on one bus (bus=) or on every bus, with its targets (up to `limit` rules).
        """
        names = [bus] if bus else [b.name for b in (buses if buses is not None else self.buses())]
        described: list[dict[str, Any]] = []
        for name in names:
            described += [{**r, "EventBusName": r.get("EventBusName") or name}
                          for r in self._pages(self._events(), "list_rules", "Rules", EventBusName=name)]
        if limit is not None:
            described = described[:limit]
        return self._map(self._with_targets, described)

    def read_rule(self, name: str, *, bus: str = DEFAULT_BUS) -> Rule:
        """
        One rule with its targets.
        """
        return self._with_targets(self._events().describe_rule(Name=name, EventBusName=bus))

    def _find_rule(self, name: str, bus: str | None) -> Rule:
        ref = parse_ref(name if bus is None else f"{bus}/{name}")
        self._check_region(ref)
        if ref.kind not in ("", "rule"):
            raise _Hint(f"{name} is a {ref.kind}, not a rule: {ref.kind}_info({ref.name!r}) shows it.")
        if ref.kind == "rule":
            try:
                return self.read_rule(ref.name, bus=ref.bus)
            except ClientError as exc:
                if _error_code(exc) != "ResourceNotFoundException":
                    raise
        everything = self.rules()
        found = [r for r in everything if r.name == ref.name]
        if len(found) == 1:
            return found[0]
        if found:
            raise _Hint(f"{ref.name!r} is a rule on {len(found)} buses: name one, "
                        f"rule_info('{found[0].bus}/{ref.name}').")
        labels = [r.label for r in everything]
        raise _Hint(f"No rule named {ref.name!r} in {self.region}.{_closest(ref.name, labels)} overview() lists them.")


    def _safely(self, errors: dict[str, str], section: str, read: Callable[[], Any], default: Any) -> Any:
        """
        read(), or `default` with the error code (and the permission it needed) in errors[section].
        """
        try:
            return read()
        except (ClientError, BotoCoreError) as exc:
            code = _error_name(exc)
            errors[section] = _why(code, _PERMISSIONS.get(section, section))
            return default


    def _read_all(self, overview: Overview, progress: Callable[..., None] | None) -> None:
        """
        Fills the overview's queues, consumers, topics, buses and rules, noting each kind the limit cut short.
        """
        steps: list[tuple[str, Callable[[], None]]] = [
            ("queues", lambda: setattr(overview, "queues", self.queues(limit=self.limit))),
            ("consumers", lambda: self._attach_consumers(overview.queues, self._mappings())),
            ("topics", lambda: setattr(overview, "topics", self.topics(limit=self.limit))),
            ("rules", lambda: self._read_rules(overview)),
        ]
        for done, (section, read) in enumerate(steps, 1):
            self._safely(overview.errors, section, read, None)
            if progress:
                progress(done, len(steps) + 2)
        for kind, items in (("queues", overview.queues), ("topics", overview.topics), ("rules", overview.rules)):
            if len(items) >= self.limit:
                overview.cut_short.append(kind)

    def _read_rules(self, overview: Overview) -> None:
        overview.buses = self.buses()
        overview.rules = self.rules(buses=overview.buses, limit=self.limit)

    def _receivers(self, overview: Overview) -> list[str]:
        """
        The Lambda functions subscriptions and rules hand messages to.
        """
        arns = [s.endpoint for t in overview.topics for s in t.subscriptions if s.protocol == "lambda"]
        return arns + [t.arn for r in overview.rules for t in r.targets]

    def _read_numbers(self, overview: Overview) -> None:
        before = self.metrics_read
        days = overview.days
        overview.queue_numbers = self.queue_numbers(overview.queues, days=days)
        overview.topic_numbers = self.topic_numbers(overview.topics, days=days)
        overview.rule_numbers = self.rule_numbers(overview.rules, days=days)
        overview.metrics_read = self.metrics_read - before

    def overview(self, *, days: int = 7, progress: Callable[..., None] | None = None) -> Overview:
        """
        Every queue, topic, bus and rule in the region, with the Lambda functions that read the queues, the functions
        subscriptions and rules invoke, and their CloudWatch numbers over the last `days` whole UTC days.
        """
        overview = Overview(region=self.region, days=days)
        self._read_all(overview, progress)
        overview.functions = self._safely(overview.errors, "functions",
                                          lambda: self.functions(self._receivers(overview)), {})
        if progress:
            progress(5, 6)
        self._safely(overview.errors, "metrics", lambda: self._read_numbers(overview), None)
        if progress:
            progress(6, 6)
        return overview

    def dead_letters(self, *, days: int = 7) -> list[DeadLetter]:
        """
        The region's dead-letter queues, the ones holding most messages first, with what each catches failures from.
        """
        return dead_letters_from(self.overview(days=days))


    def _queue_url(self, name: str) -> str:
        ref = parse_ref(name)
        self._check_region(ref)
        if ref.kind not in ("", "queue"):
            raise _Hint(f"{name} is a {ref.kind}, not a queue: {ref.kind}_info({ref.name!r}) shows it.")
        try:
            return self.client.get_queue_url(QueueName=ref.name)["QueueUrl"]
        except ClientError as exc:
            if _error_code(exc) not in _MISSING_QUEUE:
                raise
        names = [url.rsplit("/", 1)[-1] for url in self.queue_urls()]
        raise _Hint(f"No queue named {ref.name!r} in {self.region}.{_closest(ref.name, names)} overview() lists them.")

    def _sources(self, queue: Queue) -> list[str]:
        urls = self._pages(self.client, "list_dead_letter_source_queues", "queueUrls", QueueUrl=queue.url)
        return [url.rsplit("/", 1)[-1] for url in urls]

    def _feeders(self, queue: Queue) -> tuple[list[Topic], list[Rule]]:
        """
        The topics with a subscription to a queue and the rules with it as a target, each with only those.
        """
        summaries = [s for s in self._subscription_summaries() if s.get("Endpoint") == queue.arn]
        topics = [parse_topic(s.get("TopicArn", ""), {}, [self._subscription(s)]) for s in summaries]
        rules = []
        for bus in self.buses():
            names = self._pages(self._events(), "list_rule_names_by_target", "RuleNames", TargetArn=queue.arn,
                                EventBusName=bus.name)
            rules += [self.read_rule(name, bus=bus.name) for name in names]
        return topics, rules

    def _read_dlq(self, queue: Queue, report: QueueReport) -> None:
        if queue.redrive is None:
            return
        dlq = self._read_queue_safely(queue_url(queue.redrive.dlq_arn))
        if dlq is not None:
            dlq.sources = self._safely(report.errors, "dead-letter queue", lambda: self._sources(dlq), [queue.name])
        report.dlq = dlq

    def queue_report(self, name: str, *, days: int = 7) -> QueueReport:
        """
        One queue in full: its settings and messages, its numbers with a line per day, its dead-letter queue, the
        Lambda functions that read it, and the topics and rules that send to it.
        """
        queue = self.read_queue(self._queue_url(name))
        report = QueueReport(queue=queue, days=days)
        queue.sources = self._safely(report.errors, "dead-letter sources", lambda: self._sources(queue), [])
        self._read_dlq(queue, report)
        self._safely(report.errors, "consumers",
                     lambda: self._attach_consumers([queue], self._mappings(queue.arn)), None)
        topics, rules = self._safely(report.errors, "senders", lambda: self._feeders(queue), ([], []))
        report.feeders = feeders_of(queue, topics, rules)
        before = self.metrics_read
        numbers = self._safely(report.errors, "metrics", lambda: self.queue_numbers(
            [q for q in (queue, report.dlq) if q is not None], days=days, daily=True), {})
        report.numbers = numbers.get(queue.arn)
        report.dlq_numbers = numbers.get(report.dlq.arn) if report.dlq else None
        report.metrics_read = self.metrics_read - before
        return report


    def _receiving_queues(self, arns: list[str], errors: dict[str, str]) -> dict[str, Queue]:
        """
        The queues among `arns` that this region and account hold, read for their policies and keys.
        """
        queues = {}
        for arn in dict.fromkeys(a for a in arns if arn_service(a) == "sqs"):
            queue = self._safely(errors, "queues", lambda arn=arn: self._read_queue_safely(queue_url(arn)), None)
            if queue is not None:
                queues[arn] = queue
        return queues

    def topic_report(self, name: str, *, days: int = 7) -> TopicReport:
        """
        One topic in full: its subscriptions with their filters and dead-letter queues, its numbers with a line per
        day, and whether each subscribed queue or function lets the topic deliver.
        """
        topic = self.read_topic(self._find_topic(name))
        report = TopicReport(topic=topic, days=days)
        endpoints = [s.endpoint for s in topic.subscriptions]
        report.queues = self._receiving_queues(endpoints, report.errors)
        report.functions = self._safely(report.errors, "functions", lambda: self.functions(endpoints), {})
        before = self.metrics_read
        numbers = self._safely(report.errors, "metrics", lambda: self.topic_numbers([topic], days=days, daily=True), {})
        report.numbers = numbers.get(topic.arn)
        report.metrics_read = self.metrics_read - before
        return report

    def rule_report(self, name: str, *, bus: str | None = None, days: int = 7) -> RuleReport:
        """
        One EventBridge rule in full: the events it matches, its targets with their retries and dead-letter queues,
        its numbers with a line per day, and whether each target queue or function lets the rule deliver.
        """
        rule = self._find_rule(name, bus)
        report = RuleReport(rule=rule, days=days)
        arns = [t.arn for t in rule.targets]
        report.queues = self._receiving_queues(arns, report.errors)
        report.functions = self._safely(report.errors, "functions", lambda: self.functions(arns), {})
        before = self.metrics_read
        numbers = self._safely(report.errors, "metrics", lambda: self.rule_numbers([rule], days=days, daily=True), {})
        report.numbers = numbers.get(rule.arn)
        report.metrics_read = self.metrics_read - before
        return report


    def _start(self, overview: Overview, name: str) -> tuple[str, str, str]:
        """
        Where follow() starts in the overview: (kind, name, ARN; a bus's ARN is its name).
        """
        ref = parse_ref(name)
        self._check_region(ref)
        candidates = {
            "queue": [(q.name, q.arn) for q in overview.queues],
            "topic": [(t.name, t.arn) for t in overview.topics],
            "rule": [(r.label, r.arn) for r in overview.rules] + [(r.name, r.arn) for r in overview.rules
                                                                  if r.bus == (ref.bus or r.bus)],
            "bus": [(b.name, b.name) for b in overview.buses],
        }
        label = f"{ref.bus}/{ref.name}" if ref.kind == "rule" and ref.bus != DEFAULT_BUS else ref.name
        for kind in ([ref.kind] if ref.kind else list(candidates)):
            found = next((arn for named, arn in candidates[kind] if named in (label, ref.name)), None)
            if found:
                return kind, label, found
        names = [named for pairs in candidates.values() for named, _ in pairs]
        raise _Hint(f"No queue, topic, rule or bus named {ref.name!r} in {self.region}.{_closest(ref.name, names)} "
                    "overview() lists them.")

    def follow(self, name: str, *, days: int = 7, progress: Callable[..., None] | None = None) -> MessageMap:
        """
        Where a message goes from a queue, topic, rule or bus: what feeds it, then every subscription, target, queue,
        reader and dead-letter queue downstream, each with its numbers and what's wrong there.
        """
        overview = self.overview(days=days, progress=progress)
        kind, label, arn = self._start(overview, name)
        return MessageMap(kind=kind, name=label, arn=arn, hops=follow_map(overview, kind, arn), overview=overview)
