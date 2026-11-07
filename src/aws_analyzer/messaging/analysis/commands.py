"""
The AWS CLI commands findings show to fix what they found: add a dead-letter queue, move messages back from one,
lengthen a visibility timeout, turn on long polling, log SNS deliveries. Shown, never run. Pure: no AWS calls.
"""

from __future__ import annotations

import json
import shlex

from ..helpers import DEFAULT_BUS, rule_arn_parts


RECOMMENDED_RECEIVES = 5

LONG_POLL_SECONDS = 20


def _json_arg(value: object) -> str:
    return shlex.quote(json.dumps(value, separators=(",", ":")))


def redrive_command(queue_url: str, dlq_arn: str, max_receives: int = RECOMMENDED_RECEIVES) -> str:
    """
    The command that gives a queue a dead-letter queue: after max_receives failed tries a message moves there
    instead of being retried until it expires.
    """
    policy = json.dumps({"deadLetterTargetArn": dlq_arn, "maxReceiveCount": str(max_receives)},
                        separators=(",", ":"))
    return f"aws sqs set-queue-attributes --queue-url {queue_url} --attributes {_json_arg({'RedrivePolicy': policy})}"


def move_back_command(dlq_arn: str) -> str:
    """
    The command that moves a dead-letter queue's messages back to the queues they came from (SQS's redrive).
    """
    return f"aws sqs start-message-move-task --source-arn {dlq_arn}"


def queue_attribute_command(queue_url: str, name: str, value: int | str) -> str:
    """
    The command that sets one of a queue's attributes: VisibilityTimeout=720, ReceiveMessageWaitTimeSeconds=20, ...
    """
    return f"aws sqs set-queue-attributes --queue-url {queue_url} --attributes {name}={value}"


def subscription_dlq_command(subscription_arn: str, dlq_arn: str) -> str:
    """
    The command that gives an SNS subscription a dead-letter queue for the deliveries that fail for good.
    """
    value = json.dumps({"deadLetterTargetArn": dlq_arn}, separators=(",", ":"))
    return (f"aws sns set-subscription-attributes --subscription-arn {subscription_arn} "
            f"--attribute-name RedrivePolicy --attribute-value {shlex.quote(value)}")


def delivery_logging_command(topic_arn: str, protocol: str) -> str:
    """
    The command that logs a topic's failed deliveries to one kind of endpoint in CloudWatch Logs (it needs a role SNS
    can use to write logs).
    """
    prefix = {"sqs": "SQS", "lambda": "Lambda", "http": "HTTP", "https": "HTTP", "application": "Application",
              "firehose": "Firehose"}.get(protocol, protocol.upper())
    return (f"aws sns set-topic-attributes --topic-arn {topic_arn} --attribute-name {prefix}FailureFeedbackRoleArn "
            "--attribute-value <role-arn>")


def target_dlq_command(rule_arn: str, target_id: str, target_arn: str, dlq_arn: str) -> str:
    """
    The command that gives an EventBridge rule's target a dead-letter queue for the events it couldn't deliver.
    """
    bus, rule = rule_arn_parts(rule_arn)
    bus_part = "" if bus in ("", DEFAULT_BUS) else f" --event-bus-name {bus}"
    targets = [{"Id": target_id, "Arn": target_arn, "DeadLetterConfig": {"Arn": dlq_arn}}]
    return f"aws events put-targets --rule {rule}{bus_part} --targets {_json_arg(targets)}"


def enable_rule_command(rule_arn: str) -> str:
    """
    The command that turns a disabled EventBridge rule back on.
    """
    bus, rule = rule_arn_parts(rule_arn)
    bus_part = "" if bus in ("", DEFAULT_BUS) else f" --event-bus-name {bus}"
    return f"aws events enable-rule --name {rule}{bus_part}"
