"""
Small helpers for SQS, SNS and EventBridge: what a reference names (a queue URL, an ARN, a console link,
'bus/rule' or a bare name), the parts of an ARN, what kind of thing an ARN or a subscription's endpoint is, and
durations as words.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import NamedTuple
from urllib.parse import unquote


DEFAULT_BUS = "default"

QUEUE_URL_RE = re.compile(r"https://sqs\.(?P<region>[\w-]+)\.amazonaws\.com(?:\.cn)?/(?P<account>\d{12})/(?P<name>[\w.-]+)")

ARN_RE = re.compile(r"arn:aws[\w-]*:(?P<service>[\w-]+):(?P<region>[\w-]*):(?P<account>\d*):(?P<resource>[^\s\"'#?]+)")

CONSOLE_RULE_RE = re.compile(r"#/eventbus/(?P<bus>[^/]+)/rules/(?P<rule>[^/?#]+)")

CONSOLE_BUS_RE = re.compile(r"#/eventbus/(?P<bus>[^/?#]+)/?$")

SERVICE_WORDS = {"sqs": "SQS queue", "sns": "SNS topic", "lambda": "Lambda function", "states": "Step Functions",
                 "events": "EventBridge bus", "kinesis": "Kinesis stream", "firehose": "Firehose stream",
                 "logs": "log group", "ecs": "ECS task", "batch": "Batch job", "codebuild": "CodeBuild project",
                 "ssm": "Systems Manager", "sagemaker": "SageMaker pipeline", "redshift": "Redshift"}

PROTOCOL_WORDS = {"sqs": "SQS queue", "lambda": "Lambda function", "http": "HTTP endpoint", "https": "HTTPS endpoint",
                  "email": "email", "email-json": "email (JSON)", "sms": "SMS", "application": "mobile app",
                  "firehose": "Firehose stream"}


class Ref(NamedTuple):
    """
    What a reference names: kind is 'queue', 'topic', 'rule', 'bus' or '' (a bare name, looked up among all of them),
    with the rule's bus and the region the reference carries, when it carries one.
    """

    kind: str
    name: str
    bus: str = ""
    region: str = ""


@dataclass(frozen=True)
class ArnParts:
    """
    An ARN's service, region, account and resource ('orders', 'rule/shop/order-placed', 'function:etl:live').
    """

    service: str
    region: str
    account: str
    resource: str


def split_arn(arn: str) -> ArnParts | None:
    """
    An ARN's parts, or None when the text isn't one.
    """
    found = ARN_RE.search(arn or "")
    if not found:
        return None
    return ArnParts(found.group("service"), found.group("region"), found.group("account"), found.group("resource"))


def arn_name(arn: str) -> str:
    """
    The short name in an ARN: 'arn:aws:sqs:us-east-1:123:orders' -> 'orders', a function's ARN -> its name, a rule's
    -> 'shop/order-placed' (its bus first, unless default), a bus's -> its name. Text that isn't an ARN comes back as
    it is (an HTTPS endpoint, an email address).
    """
    parts = split_arn(arn)
    if parts is None:
        return arn or ""
    resource = parts.resource
    if parts.service == "lambda" and resource.startswith("function:"):
        return resource.split(":")[1]
    if parts.service == "events" and resource.startswith("rule/"):
        path = resource[len("rule/"):]
        return path[len(DEFAULT_BUS) + 1:] if path.startswith(f"{DEFAULT_BUS}/") else path
    if parts.service == "sns":
        return resource.split(":")[0]
    return resource.rsplit("/", 1)[-1].rsplit(":", 1)[-1] if "/" in resource or ":" in resource else resource


def arn_kind(arn: str) -> str:
    """
    What an ARN points at, in words: 'SQS queue', 'Lambda function', 'EventBridge bus', ... or 'endpoint'.
    """
    parts = split_arn(arn)
    if parts is None:
        return "endpoint"
    if parts.service == "events" and parts.resource.startswith("api-destination"):
        return "API destination"
    if parts.service == "events" and parts.resource.startswith("rule/"):
        return "EventBridge rule"
    return SERVICE_WORDS.get(parts.service, parts.service)


def arn_service(arn: str) -> str:
    """
    The service an ARN belongs to ('sqs', 'lambda', ...), or '' when it isn't an ARN.
    """
    parts = split_arn(arn)
    return parts.service if parts else ""


def function_of(arn: str) -> str:
    """
    A Lambda ARN without its version or alias, as GetPolicy and GetFunctionConfiguration take it: '...:function:etl'.
    """
    parts = split_arn(arn)
    if parts is None or parts.service != "lambda":
        return arn
    name = parts.resource.split(":")[1] if parts.resource.startswith("function:") else parts.resource
    return f"arn:aws:lambda:{parts.region}:{parts.account}:function:{name}"


def queue_url(arn: str) -> str:
    """
    A queue's URL from its ARN: arn:aws:sqs:us-east-1:123:orders -> https://sqs.us-east-1.amazonaws.com/123/orders.
    """
    parts = split_arn(arn)
    if parts is None:
        return ""
    return f"https://sqs.{parts.region}.amazonaws.com/{parts.account}/{parts.resource}"


def rule_arn_parts(arn: str) -> tuple[str, str]:
    """
    A rule ARN's (bus, rule): 'arn:aws:events:...:rule/shop/order-placed' -> ('shop', 'order-placed'); a rule on the
    default bus has no bus in its ARN.
    """
    parts = split_arn(arn)
    if parts is None or not parts.resource.startswith("rule/"):
        return "", ""
    path = parts.resource[len("rule/"):]
    bus, _, rule = path.rpartition("/")
    return bus or DEFAULT_BUS, rule


def _ref_from_arn(parts: ArnParts) -> Ref:
    """
    The Ref an ARN names.
    """
    resource = parts.resource
    if parts.service == "sqs":
        return Ref("queue", resource, region=parts.region)
    if parts.service == "sns":
        return Ref("topic", resource.split(":")[0], region=parts.region)
    if parts.service == "events" and resource.startswith("rule/"):
        path = resource[len("rule/"):]
        bus, _, rule = path.rpartition("/")
        return Ref("rule", rule, bus=bus or DEFAULT_BUS, region=parts.region)
    if parts.service == "events" and resource.startswith("event-bus/"):
        return Ref("bus", resource[len("event-bus/"):], region=parts.region)
    what = SERVICE_WORDS.get(parts.service, parts.service)
    raise ValueError(f"A {what} isn't something this reads: name a queue, a topic, an EventBridge rule or a bus")


def parse_ref(ref: str) -> Ref:
    """
    What a reference names: a queue URL, a queue / topic / rule / bus ARN, an SQS, SNS or EventBridge console link,
    'bus/rule' for a rule on a custom bus, or a bare name (kind '', looked up among queues, topics, rules and buses).
    """
    text = unquote((ref or "").strip())
    if not text:
        raise ValueError("Name a queue, a topic or an EventBridge rule: its name, URL, ARN or console link")
    url = QUEUE_URL_RE.search(text)
    if url:
        return Ref("queue", url.group("name"), region=url.group("region"))
    parts = split_arn(text)
    if parts is not None:
        return _ref_from_arn(parts)
    console_rule = CONSOLE_RULE_RE.search(text)
    if console_rule:
        return Ref("rule", console_rule.group("rule"), bus=console_rule.group("bus"))
    console_bus = CONSOLE_BUS_RE.search(text)
    if console_bus:
        return Ref("bus", console_bus.group("bus"))
    if "/" in text:
        bus, _, rule = text.rpartition("/")
        return Ref("rule", rule.strip(), bus=bus.strip() or DEFAULT_BUS)
    return Ref("", text)


def seconds_words(seconds: int | float | None) -> str:
    """
    A setting's duration as people say it: 345600 -> '4 days', 30 -> '30 seconds', 5400 -> '1.5 hours'.
    """
    if seconds is None:
        return "-"
    for unit, size in (("day", 86_400), ("hour", 3_600), ("minute", 60)):
        if seconds >= size:
            amount = seconds / size
            text = f"{amount:.0f}" if amount >= 10 else f"{amount:.1f}".rstrip("0").rstrip(".")
            return f"{text} {unit}{'' if text == '1' else 's'}"
    return f"{seconds:.0f} second{'' if seconds == 1 else 's'}"


def agree(count: int, one: str, many: str) -> str:
    """
    The verb that goes with a count: agree(1, 'has', 'have') -> 'has', agree(4, 'has', 'have') -> 'have'.
    """
    return one if count == 1 else many
