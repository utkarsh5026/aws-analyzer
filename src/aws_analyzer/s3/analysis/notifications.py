"""
A bucket's event notifications: what sends which events where.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable


@dataclass
class EventNotification:
    """
    One of a bucket's event notifications (see parse_notifications): which events, for which keys, go where.
    """

    target: str  # 'Lambda function' | 'SQS queue' | 'SNS topic' | 'EventBridge'
    arn: str | None = None  # the function, queue or topic; None for EventBridge (every event goes to its default bus)
    events: list[str] = field(default_factory=list)  # 's3:ObjectCreated:*', 's3:ObjectCreated:Put', ...
    prefix: str = ""  # only keys that start with this ('' = any key); S3 compares case-sensitively
    suffix: str = ""  # only keys that end with this ('' = any key); case-sensitive too
    id: str = ""

    @property
    def name(self) -> str:
        """
        The function, queue or topic's name from its ARN: 'orders-etl'; 'EventBridge' for EventBridge.
        """
        parts = (self.arn or "").split(":")
        if self.target == "Lambda function" and len(parts) > 6:
            return parts[6]
        return parts[5] if len(parts) > 5 else self.arn or self.target

    @property
    def region(self) -> str | None:
        """
        The region in its ARN (a Lambda function or queue has to be in the bucket's own region).
        """
        parts = (self.arn or "").split(":")
        return parts[3] or None if len(parts) > 3 else None

    def matches(self, key: str) -> bool:
        """
        Whether an object with this key is covered by its prefix and suffix filter.
        """
        return key.startswith(self.prefix) and key.endswith(self.suffix)

    def on_created(self, multipart: bool | None = None) -> bool:
        """
        Whether a new object sends it. multipart=True: an object uploaded in parts, which sends
        s3:ObjectCreated:CompleteMultipartUpload; False: one uploaded whole (a PUT, a POST form or a copy); None:
        any new object.
        """
        events = set(self.events)
        if self.target == "EventBridge" or "s3:ObjectCreated:*" in events:
            return True
        if multipart is None:
            return any(e.startswith("s3:ObjectCreated:") for e in events)
        if multipart:
            return "s3:ObjectCreated:CompleteMultipartUpload" in events
        return bool(events & {"s3:ObjectCreated:Put", "s3:ObjectCreated:Post", "s3:ObjectCreated:Copy"})

    def describe(self) -> str:
        """
        In plain English: "new objects under 'incoming/' ending in '.csv' -> Lambda function orders-etl".
        """
        what = _event_words(self.events) if self.target != "EventBridge" else "every event"
        keys = " ".join(filter(None, [f"under '{self.prefix}'" if self.prefix else "",
                                      f"ending in '{self.suffix}'" if self.suffix else ""]))
        return f"{what}{' ' + keys if keys else ''} -> {self.target}" + (
            f" {self.name}" if self.target != "EventBridge" else " (the account's default event bus)")


_NOTIFICATION_TARGETS = (  # GetBucketNotificationConfiguration's lists -> (target, the key of its ARN)
    ("LambdaFunctionConfigurations", "Lambda function", "LambdaFunctionArn"),
    ("QueueConfigurations", "SQS queue", "QueueArn"),
    ("TopicConfigurations", "SNS topic", "TopicArn"),
)

_EVENT_WORDS = {  # S3 event types in plain English (EventNotification.describe)
    "s3:ObjectCreated:*": "new objects",
    "s3:ObjectCreated:Put": "PUT uploads",
    "s3:ObjectCreated:Post": "form (POST) uploads",
    "s3:ObjectCreated:Copy": "copies",
    "s3:ObjectCreated:CompleteMultipartUpload": "multipart uploads",
    "s3:ObjectRemoved:*": "deletions",
    "s3:ObjectRemoved:Delete": "deletions",
    "s3:ObjectRemoved:DeleteMarkerCreated": "new delete markers",
    "s3:ObjectRestore:*": "restores from Glacier",
    "s3:ObjectRestore:Post": "restores started",
    "s3:ObjectRestore:Completed": "restores completed",
    "s3:LifecycleExpiration:*": "lifecycle expirations",
    "s3:LifecycleTransition": "lifecycle transitions",
    "s3:ObjectTagging:*": "tag changes",
    "s3:Replication:*": "replication events",
}


def _event_words(events: Iterable[str]) -> str:
    """
    ['s3:ObjectCreated:Put', 's3:ObjectRemoved:*'] -> 'PUT uploads and deletions'.
    """
    words = list(dict.fromkeys(_EVENT_WORDS.get(e, e.removeprefix("s3:")) for e in events)) or ["no events"]
    return words[0] if len(words) == 1 else ", ".join(words[:-1]) + " and " + words[-1]


def parse_notifications(config: dict[str, Any] | None) -> list[EventNotification]:
    """
    A bucket's notification configuration (GetBucketNotificationConfiguration) as a list: each Lambda function,
    SQS queue and SNS topic it sends events to, with the events and the key prefix and suffix they're limited to,
    and EventBridge when it's turned on (every event then goes to the account's default event bus, where its rules
    pick them).
    """
    config = config or {}
    found: list[EventNotification] = []
    for section, target, arn_key in _NOTIFICATION_TARGETS:
        for entry in config.get(section) or []:
            rules = ((entry.get("Filter") or {}).get("Key") or {}).get("FilterRules") or []
            by_name = {str(rule.get("Name", "")).lower(): str(rule.get("Value", "")) for rule in rules}
            found.append(EventNotification(target, entry.get(arn_key), list(entry.get("Events") or []),
                                           by_name.get("prefix", ""), by_name.get("suffix", ""),
                                           entry.get("Id") or ""))
    if "EventBridgeConfiguration" in config:
        found.append(EventNotification("EventBridge"))
    return found
