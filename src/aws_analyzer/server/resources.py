"""
The things in an account the page searches, one service at a time: a service's functions, buckets, tables, state
machines, APIs, queues or topics, read with the cheap listing call its analyzer already makes (ListFunctions,
ListBuckets, ListTables, ...), never every service at once. Each `Resource` names the command and arguments that open
it, so the page can open its report in a tab until it has a page of its own.

A lister takes the service View's analyzer (`view.core`) and returns Resources; the runner keeps what one read for
NAMES_KEPT seconds, so searching again, or another tab searching, asks AWS nothing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable

NAMES_KEPT = 3600


@dataclass(frozen=True)
class Resource:
    """
    One thing the page can find: what kind it is ('function', 'bucket', ...), its name, a short line about it, and
    the command (with its arguments, as form texts) that opens its report.
    """

    kind: str
    name: str
    detail: str
    command: str
    arguments: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class ResourceIndex:
    """
    What one service's listing read: its resources by name, when, how long it took, and whether it came from what the
    server kept (from_memory) rather than from AWS just now.
    """

    service: str
    resources: list[Resource]
    read_at: datetime
    seconds: float
    from_memory: bool = False


def _short_date(moment: datetime | None) -> str:
    return moment.strftime("%d %b %Y") if moment else ""


def _function_detail(function: Any) -> str:
    runtime = function.runtime or "container image"
    return f"{runtime} · {function.memory} MB · timeout {function.timeout} s"


def lambda_resources(core: Any) -> list[Resource]:
    """
    The region's functions (ListFunctions), each opening function_info().
    """
    return [Resource("function", function.name, _function_detail(function), "function_info", {"name": function.name})
            for function in core.list_functions()]


def _bucket_detail(bucket: Any) -> str:
    made = _short_date(bucket.created)
    parts = [part for part in (bucket.region, f"made {made}" if made else "") if part]
    return " · ".join(parts)


def s3_resources(core: Any) -> list[Resource]:
    """
    Every bucket the credentials can see (ListBuckets, without looking up each one's region), each opening
    bucket_info().
    """
    return [Resource("bucket", bucket.name, _bucket_detail(bucket), "bucket_info", {"bucket": bucket.name})
            for bucket in core.list_buckets(with_region=False)]


def dynamodb_resources(core: Any) -> list[Resource]:
    """
    The region's tables (ListTables, names only), each opening table_info().
    """
    return [Resource("table", name, "DynamoDB table", "table_info", {"table": name})
            for name in core.list_table_names()]


def _workflow_detail(machine: dict[str, Any]) -> str:
    kind = str(machine.get("type", "STANDARD")).title()
    made = _short_date(machine.get("creationDate"))
    return f"{kind} workflow" + (f" · made {made}" if made else "")


def stepfunctions_resources(core: Any) -> list[Resource]:
    """
    The region's state machines (ListStateMachines), each opening state_machine().
    """
    return [Resource("state machine", machine["name"], _workflow_detail(machine), "state_machine",
                     {"name": machine["name"]}) for machine in core.list_state_machines()]


def apigateway_resources(core: Any) -> list[Resource]:
    """
    The region's REST, HTTP and WebSocket APIs (GetRestApis, GetApis), each opening api_info().
    """
    return [Resource("api", api.name, f"{api.kind} API · {api.id}", "api_info", {"name": api.name})
            for api in core.list_apis()]


def _last_part(text: str, separator: str) -> str:
    return text.rstrip(separator).rsplit(separator, 1)[-1]


def messaging_resources(core: Any) -> list[Resource]:
    """
    The region's SQS queues (ListQueues) and SNS topics (ListTopics), each opening queue_info() or topic_info().
    """
    queues = [Resource("queue", _last_part(url, "/"), "SQS queue", "queue_info", {"name": _last_part(url, "/")})
              for url in core.queue_urls()]
    topics = [Resource("topic", _last_part(arn, ":"), "SNS topic", "topic_info", {"name": _last_part(arn, ":")})
              for arn in core.topic_arns()]
    return queues + topics


LISTERS: dict[str, Callable[[Any], list[Resource]]] = {
    "lambda_functions": lambda_resources,
    "s3": s3_resources,
    "dynamodb": dynamodb_resources,
    "stepfunctions": stepfunctions_resources,
    "apigateway": apigateway_resources,
    "messaging": messaging_resources,
}


def is_searchable(key: str) -> bool:
    """
    Whether the page can search a service's resources yet.
    """
    return key in LISTERS


def sorted_resources(resources: list[Resource]) -> list[Resource]:
    """
    Resources in the order the page lists them: by name, case aside, then by kind.
    """
    return sorted(resources, key=lambda resource: (resource.name.lower(), resource.kind))
