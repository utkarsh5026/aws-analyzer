"""
Turning what SQS, SNS, EventBridge and Lambda return into the models: a queue from GetQueueAttributes, a topic and
its subscriptions from GetTopicAttributes / GetSubscriptionAttributes, a rule and its targets from DescribeRule /
ListTargetsByRule, and a queue's Lambda consumer from an event source mapping. Pure: no AWS calls.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from ..helpers import DEFAULT_BUS, arn_name
from ..models import Consumer, Queue, Redrive, Rule, Subscription, Target, Topic


DELIVERY_LOG_PROTOCOLS = {"SQS": "sqs", "Lambda": "lambda", "HTTP": "http", "Application": "application",
                          "Firehose": "firehose"}


def parse_json(text: Any) -> dict[str, Any] | None:
    """
    A JSON document AWS returns as text (a policy, a redrive policy, a filter policy), or None when it's empty or
    broken.
    """
    if isinstance(text, dict):
        return text
    if not text:
        return None
    try:
        value = json.loads(text)
    except (TypeError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _epoch(value: Any) -> datetime | None:
    try:
        return datetime.fromtimestamp(float(value), tz=timezone.utc)
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def _encryption(attributes: dict[str, Any]) -> str:
    """
    How a queue is encrypted at rest: 'KMS' with its own key, 'SQS-managed', or '' when it isn't.
    """
    if attributes.get("KmsMasterKeyId"):
        return "KMS"
    return "SQS-managed" if str(attributes.get("SqsManagedSseEnabled", "")).lower() == "true" else ""


def _redrive(attributes: dict[str, Any]) -> Redrive | None:
    policy = parse_json(attributes.get("RedrivePolicy"))
    if not policy or not policy.get("deadLetterTargetArn"):
        return None
    return Redrive(policy["deadLetterTargetArn"], _int(policy.get("maxReceiveCount"), 0))


def parse_queue(url: str, attributes: dict[str, Any]) -> Queue:
    """
    A Queue from its URL and GetQueueAttributes' attributes (AttributeNames=['All']).
    """
    name = url.rstrip("/").rsplit("/", 1)[-1]
    allow = parse_json(attributes.get("RedriveAllowPolicy")) or {}
    return Queue(
        name=name, url=url, arn=attributes.get("QueueArn", ""),
        fifo=str(attributes.get("FifoQueue", "")).lower() == "true" or name.endswith(".fifo"),
        visible=_int(attributes.get("ApproximateNumberOfMessages")),
        in_flight=_int(attributes.get("ApproximateNumberOfMessagesNotVisible")),
        delayed=_int(attributes.get("ApproximateNumberOfMessagesDelayed")),
        retention_s=_int(attributes.get("MessageRetentionPeriod"), 345_600),
        visibility_s=_int(attributes.get("VisibilityTimeout"), 30),
        delay_s=_int(attributes.get("DelaySeconds")),
        wait_s=_int(attributes.get("ReceiveMessageWaitTimeSeconds")),
        max_bytes=_int(attributes.get("MaximumMessageSize"), 262_144),
        created=_epoch(attributes.get("CreatedTimestamp")),
        encryption=_encryption(attributes), kms_key=attributes.get("KmsMasterKeyId", "") or "",
        redrive=_redrive(attributes),
        allows_redrive_from=str(allow.get("redrivePermission", "")),
        policy=parse_json(attributes.get("Policy")),
    )


def parse_consumer(mapping: dict[str, Any]) -> Consumer:
    """
    A queue's Lambda consumer from one of ListEventSourceMappings' mappings.
    """
    scaling = mapping.get("ScalingConfig") or {}
    return Consumer(
        function_arn=mapping.get("FunctionArn", ""), uuid=mapping.get("UUID", ""), state=mapping.get("State", ""),
        batch_size=_int(mapping.get("BatchSize")), window_s=_int(mapping.get("MaximumBatchingWindowInSeconds")),
        reports_failures="ReportBatchItemFailures" in (mapping.get("FunctionResponseTypes") or []),
        last_result=mapping.get("LastProcessingResult", "") or "",
        max_concurrency=_int(scaling["MaximumConcurrency"]) if scaling.get("MaximumConcurrency") else None,
    )


def parse_subscription(summary: dict[str, Any], attributes: dict[str, Any] | None = None) -> Subscription:
    """
    A Subscription from ListSubscriptionsByTopic's summary, with GetSubscriptionAttributes' attributes when they
    were read (the filter policy, raw delivery and dead-letter queue are only there). A subscription waiting for
    its endpoint to confirm it has the ARN 'PendingConfirmation'.
    """
    attributes = attributes or {}
    arn = summary.get("SubscriptionArn", "")
    redrive = parse_json(attributes.get("RedrivePolicy")) or {}
    pending = arn == "PendingConfirmation" or str(attributes.get("PendingConfirmation", "")).lower() == "true"
    return Subscription(
        arn=arn, topic_arn=summary.get("TopicArn", attributes.get("TopicArn", "")),
        protocol=summary.get("Protocol", attributes.get("Protocol", "")),
        endpoint=summary.get("Endpoint", attributes.get("Endpoint", "")),
        pending=pending, filter_policy=parse_json(attributes.get("FilterPolicy")),
        filter_scope=attributes.get("FilterPolicyScope", "MessageAttributes") or "MessageAttributes",
        raw=str(attributes.get("RawMessageDelivery", "")).lower() == "true",
        dlq_arn=redrive.get("deadLetterTargetArn", ""),
    )


def _logged_protocols(attributes: dict[str, Any]) -> list[str]:
    """
    The protocols whose deliveries a topic logs to CloudWatch Logs: SQSFailureFeedbackRoleArn set means 'sqs', ...
    """
    logged = []
    for prefix, protocol in DELIVERY_LOG_PROTOCOLS.items():
        if attributes.get(f"{prefix}FailureFeedbackRoleArn") or attributes.get(f"{prefix}SuccessFeedbackRoleArn"):
            logged.append(protocol)
    return logged


def parse_topic(arn: str, attributes: dict[str, Any], subscriptions: list[Subscription] | None = None) -> Topic:
    """
    A Topic from its ARN, GetTopicAttributes' attributes and its subscriptions.
    """
    name = arn_name(arn)
    return Topic(
        name=name, arn=arn, fifo=str(attributes.get("FifoTopic", "")).lower() == "true" or name.endswith(".fifo"),
        display_name=attributes.get("DisplayName", "") or "", kms_key=attributes.get("KmsMasterKeyId", "") or "",
        logged=_logged_protocols(attributes), policy=parse_json(attributes.get("Policy")),
        subscriptions=list(subscriptions or []),
    )


def parse_target(target: dict[str, Any]) -> Target:
    """
    A Target from one of ListTargetsByRule's targets.
    """
    retry = target.get("RetryPolicy") or {}
    reshaped = any(target.get(key) for key in ("Input", "InputPath", "InputTransformer"))
    return Target(
        id=target.get("Id", ""), arn=target.get("Arn", ""),
        dlq_arn=(target.get("DeadLetterConfig") or {}).get("Arn", ""),
        max_attempts=retry["MaximumRetryAttempts"] if "MaximumRetryAttempts" in retry else None,
        max_age_s=retry["MaximumEventAgeInSeconds"] if "MaximumEventAgeInSeconds" in retry else None,
        role_arn=target.get("RoleArn", "") or "", reshaped=bool(reshaped),
    )


def parse_rule(rule: dict[str, Any], targets: list[dict[str, Any]] | None = None) -> Rule:
    """
    A Rule from ListRules' or DescribeRule's description, with ListTargetsByRule's targets.
    """
    return Rule(
        name=rule.get("Name", ""), bus=rule.get("EventBusName") or DEFAULT_BUS, arn=rule.get("Arn", ""),
        state=rule.get("State", "ENABLED") or "ENABLED", pattern=parse_json(rule.get("EventPattern")),
        schedule=rule.get("ScheduleExpression", "") or "", description=rule.get("Description", "") or "",
        managed_by=rule.get("ManagedBy", "") or "", targets=[parse_target(t) for t in targets or []],
    )
