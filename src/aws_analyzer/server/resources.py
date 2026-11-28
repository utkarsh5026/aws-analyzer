"""
The things in an account the page searches, one service at a time: a service's functions, buckets, tables, state
machines, APIs, queues or topics, read with the cheap listing call its analyzer already makes (ListFunctions,
ListBuckets, ListTables, ...), never every service at once. Each `Resource` names the command and arguments that open
it, so the page can open its report in a tab until it has a page of its own, and the facts that listing call already
returned (a function's runtime, memory and last change; a bucket's region), for the quick look beside the results.

A lister takes the service View's analyzer (`view.core`) and returns Resources; the runner keeps what one read for
NAMES_KEPT seconds, so searching again, or another tab searching, asks AWS nothing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable

from .._kit.fmt import human_size

NAMES_KEPT = 3600
_DESCRIPTION_CHARS = 140


@dataclass(frozen=True)
class Fact:
    """
    One thing the listing said about a resource, as the quick look shows it: 'Runtime' -> 'python3.12'.
    """

    label: str
    value: str


@dataclass(frozen=True)
class Resource:
    """
    One thing the page can find: what kind it is ('function', 'bucket', ...), its name, a short line about it, the
    command (with its arguments, as form texts) that opens its report, and the facts its listing call returned.
    """

    kind: str
    name: str
    detail: str
    command: str
    arguments: dict[str, str] = field(default_factory=dict)
    facts: list[Fact] = field(default_factory=list)


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


def _facts(*pairs: tuple[str, str]) -> list[Fact]:
    """
    The pairs with a value, as Facts: a listing that didn't say something leaves it out.
    """
    return [Fact(label, value) for label, value in pairs if value]


def _clipped(text: str) -> str:
    text = " ".join(text.split())
    return text if len(text) <= _DESCRIPTION_CHARS else f"{text[:_DESCRIPTION_CHARS - 1]}…"


def _function_detail(function: Any) -> str:
    runtime = function.runtime or "container image"
    return f"{runtime} · {function.memory} MB · timeout {function.timeout} s"


def _function_facts(function: Any) -> list[Fact]:
    package = human_size(function.code_size) if function.code_size else ""
    return _facts(
        ("Runtime", function.runtime or "container image"),
        ("Handler", function.handler or ""),
        ("Memory", f"{function.memory:,} MB"),
        ("Timeout", f"{function.timeout:,} s"),
        ("Architecture", function.architecture),
        ("Package", package),
        ("Layers", f"{len(function.layers):,}" if function.layers else ""),
        ("In a VPC", function.vpc_id or ""),
        ("Last changed", _short_date(function.last_modified)),
        ("State", function.state if function.state and function.state != "Active" else ""),
        ("Description", _clipped(function.description)),
    )


def lambda_resources(core: Any) -> list[Resource]:
    """
    The region's functions (ListFunctions), each opening function_info().
    """
    return [Resource("function", function.name, _function_detail(function), "function_info", {"name": function.name},
                     _function_facts(function)) for function in core.list_functions()]


def _bucket_detail(bucket: Any) -> str:
    made = _short_date(bucket.created)
    parts = [part for part in (bucket.region, f"made {made}" if made else "") if part]
    return " · ".join(parts)


def s3_resources(core: Any) -> list[Resource]:
    """
    Every bucket the credentials can see (ListBuckets, without looking up each one's region), each opening
    bucket_info().
    """
    return [Resource("bucket", bucket.name, _bucket_detail(bucket), "bucket_info", {"bucket": bucket.name},
                     _facts(("Region", bucket.region or ""), ("Made", _short_date(bucket.created)),
                            ("Address", f"s3://{bucket.name}/")))
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
                     {"name": machine["name"]},
                     _facts(("Type", str(machine.get("type", "STANDARD")).title()),
                            ("Made", _short_date(machine.get("creationDate"))),
                            ("ARN", str(machine.get("stateMachineArn", "")))))
            for machine in core.list_state_machines()]


def _api_facts(api: Any) -> list[Fact]:
    return _facts(("Kind", f"{api.kind} API"), ("ID", api.id), ("Endpoint type", api.endpoint_type),
                  ("Made", _short_date(api.created)), ("Description", _clipped(api.description)))


def apigateway_resources(core: Any) -> list[Resource]:
    """
    The region's REST, HTTP and WebSocket APIs (GetRestApis, GetApis), each opening api_info().
    """
    return [Resource("api", api.name, f"{api.kind} API · {api.id}", "api_info", {"name": api.name}, _api_facts(api))
            for api in core.list_apis()]


def _last_part(text: str, separator: str) -> str:
    return text.rstrip(separator).rsplit(separator, 1)[-1]


def messaging_resources(core: Any) -> list[Resource]:
    """
    The region's SQS queues (ListQueues) and SNS topics (ListTopics), each opening queue_info() or topic_info().
    """
    queues = [Resource("queue", _last_part(url, "/"), "SQS queue", "queue_info", {"name": _last_part(url, "/")},
                       _facts(("Kind", "FIFO queue" if url.endswith(".fifo") else "Standard queue"), ("URL", url)))
              for url in core.queue_urls()]
    topics = [Resource("topic", _last_part(arn, ":"), "SNS topic", "topic_info", {"name": _last_part(arn, ":")},
                       _facts(("Kind", "FIFO topic" if arn.endswith(".fifo") else "Standard topic"), ("ARN", arn)))
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
