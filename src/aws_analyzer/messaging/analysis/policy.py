"""
Who a queue's policy or a Lambda function's resource policy lets in: whether SNS or EventBridge may deliver to it
from a given topic or rule (allows), and the statement or command that would let it. Pure: no AWS calls.

This reads a policy the way IAM would for one service principal and one source ARN, and errs towards "allowed":
a condition it can't judge (an account, an organization) counts as met, so a warning only comes from a policy
that clearly leaves the sender out. An explicit Deny that matches wins.
"""

from __future__ import annotations

import json
import shlex
from fnmatch import fnmatchcase
from typing import Any

from ..helpers import arn_name


SNS_PRINCIPAL = "sns.amazonaws.com"

EVENTS_PRINCIPAL = "events.amazonaws.com"

SEND_ACTION = "sqs:SendMessage"

INVOKE_ACTION = "lambda:InvokeFunction"

AWS_MANAGED_SQS_KEY = "alias/aws/sqs"

_SOURCE_KEYS = ("aws:sourcearn",)


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _matches(pattern: str, value: str) -> bool:
    return fnmatchcase(value.lower(), str(pattern).lower())


def _principal_matches(principal: Any, service: str) -> bool:
    """
    Whether a statement's Principal takes in the service: '*', {'AWS': '*'} or {'Service': '<service>'}.
    """
    if principal == "*":
        return True
    if not isinstance(principal, dict):
        return False
    if "*" in _as_list(principal.get("AWS")):
        return True
    return service in _as_list(principal.get("Service"))


def _action_matches(statement: dict[str, Any], action: str) -> bool:
    return any(_matches(pattern, action) for pattern in _as_list(statement.get("Action")))


def _source_values(condition: dict[str, Any]) -> list[str]:
    """
    The aws:SourceArn values a statement's Condition names, under any operator (ArnEquals, ArnLike, StringEquals,
    ...), or [] when it names none.
    """
    values: list[str] = []
    for clauses in condition.values():
        if not isinstance(clauses, dict):
            continue
        for key, value in clauses.items():
            if key.lower() in _SOURCE_KEYS:
                values += [str(v) for v in _as_list(value)]
    return values


def _condition_matches(statement: dict[str, Any], source_arn: str) -> bool:
    sources = _source_values(statement.get("Condition") or {})
    return not sources or any(_matches(pattern, source_arn) for pattern in sources)


def _statement_applies(statement: dict[str, Any], service: str, action: str, source_arn: str) -> bool:
    return (_principal_matches(statement.get("Principal"), service) and _action_matches(statement, action)
            and _condition_matches(statement, source_arn))


def allows(policy: dict[str, Any] | None, *, service: str, action: str, source_arn: str) -> bool:
    """
    Whether a resource policy lets `service` (sns.amazonaws.com, events.amazonaws.com) call `action` on it for
    messages from `source_arn` (the topic or rule). No policy lets no service in.
    """
    statements = [s for s in _as_list((policy or {}).get("Statement")) if isinstance(s, dict)]
    applying = [s for s in statements if _statement_applies(s, service, action, source_arn)]
    if any(str(s.get("Effect", "")).lower() == "deny" for s in applying):
        return False
    return any(str(s.get("Effect", "")).lower() == "allow" for s in applying)


def queue_statement(queue_arn: str, *, service: str, source_arn: str) -> dict[str, Any]:
    """
    The statement a queue's policy needs so a topic (service sns.amazonaws.com) or a rule (events.amazonaws.com) can
    send it messages.
    """
    return {"Sid": f"Allow-{arn_name(source_arn).replace('/', '-')}", "Effect": "Allow",
            "Principal": {"Service": service}, "Action": SEND_ACTION, "Resource": queue_arn,
            "Condition": {"ArnEquals": {"aws:SourceArn": source_arn}}}


def queue_policy_with(policy: dict[str, Any] | None, statement: dict[str, Any]) -> dict[str, Any]:
    """
    A queue's policy with one more statement: SetQueueAttributes replaces the whole policy, so the command keeps
    what's there.
    """
    current = dict(policy or {"Version": "2012-10-17"})
    current["Statement"] = [*_as_list(current.get("Statement")), statement]
    return current


def queue_policy_command(queue_url: str, policy: dict[str, Any]) -> str:
    """
    The aws sqs set-queue-attributes command that sets a queue's whole policy.
    """
    attributes = json.dumps({"Policy": json.dumps(policy, separators=(",", ":"))}, separators=(",", ":"))
    return f"aws sqs set-queue-attributes --queue-url {queue_url} --attributes {shlex.quote(attributes)}"


def lambda_permission_command(function_arn: str, *, service: str, source_arn: str) -> str:
    """
    The aws lambda add-permission command that lets a topic or rule invoke a function.
    """
    who = "sns" if service == SNS_PRINCIPAL else "events"
    statement_id = f"{who}-{arn_name(source_arn).replace('/', '-')}"
    return (f"aws lambda add-permission --function-name {arn_name(function_arn)} --statement-id {statement_id} "
            f"--action {INVOKE_ACTION} --principal {service} --source-arn {source_arn}")


def blocks_services(kms_key: str) -> bool:
    """
    Whether a queue's encryption key keeps SNS and EventBridge out: the AWS managed key alias/aws/sqs can't be shared,
    so they can't encrypt what they send.
    """
    key = kms_key.lower()
    return key == AWS_MANAGED_SQS_KEY or key.endswith(f":{AWS_MANAGED_SQS_KEY}")
