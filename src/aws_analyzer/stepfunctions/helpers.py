"""
Small helpers for Step Functions: reading ARNs and the names in them, the execution references the commands take,
and JSON that may be text.
"""

from __future__ import annotations

import json
import re
from typing import Any


STATE_MACHINE_ARN_RE = re.compile(r"^arn:aws[\w-]*:states:(?P<region>[\w-]+):(?P<account>\d+):stateMachine:(?P<name>[^:]+)")

EXECUTION_ARN_RE = re.compile(
    r"^arn:aws[\w-]*:states:(?P<region>[\w-]+):(?P<account>\d+):(?P<kind>execution|express):(?P<machine>[^:]+):"
    r"(?P<name>[^:]+)"
)

LAMBDA_ARN_RE = re.compile(r"^arn:aws[\w-]*:lambda:(?P<region>[\w-]+):\d+:function:(?P<name>[^:]+)")

CONSOLE_EXECUTION_RE = re.compile(r"/states/home\?region=(?P<region>[\w-]+)#/v2/executions/details/(?P<arn>[^?&#]+)")

TERMINAL_STATUSES = ("SUCCEEDED", "FAILED", "TIMED_OUT", "ABORTED")

FAILED_STATUSES = ("FAILED", "TIMED_OUT", "ABORTED")

STATUS_TONES = {"SUCCEEDED": "ok", "FAILED": "bad", "TIMED_OUT": "bad", "ABORTED": "warn", "RUNNING": "",
                "PENDING_REDRIVE": "warn"}

STATUS_WORDS = {"SUCCEEDED": "succeeded", "FAILED": "failed", "TIMED_OUT": "timed out", "ABORTED": "aborted",
                "RUNNING": "running", "PENDING_REDRIVE": "waiting to redrive", "": "no ending logged"}


def machine_name(arn: str) -> str:
    """
    'arn:aws:states:us-east-1:123:stateMachine:order-pipeline' -> 'order-pipeline'; anything else as it is.
    """
    found = STATE_MACHINE_ARN_RE.match(arn or "")
    return found.group("name") if found else arn


def execution_name(arn: str) -> str:
    """
    An execution ARN -> its name ('ord-7f3a'); anything else as it is.
    """
    found = EXECUTION_ARN_RE.match(arn or "")
    return found.group("name") if found else arn


def arn_region(arn: str) -> str | None:
    """
    The region an ARN names, or None.
    """
    parts = (arn or "").split(":")
    return parts[3] if len(parts) > 4 and parts[0] == "arn" and parts[3] else None


def execution_arn(machine_arn: str, name: str) -> str:
    """
    The ARN of the Standard execution `name` of the state machine at `machine_arn`.
    """
    return machine_arn.replace(":stateMachine:", ":execution:", 1) + f":{name}"


def parse_execution_ref(ref: str) -> tuple[str | None, str]:
    """
    What an execution reference names, as (state machine, execution): an execution ARN or a console link gives the
    ARN as the execution (and no machine); 'order-pipeline/ord-7f3a' gives both; a bare name gives (None, name).
    """
    text = (ref or "").strip()
    if not text:
        raise ValueError("Name an execution: its ARN, or 'state-machine/execution-name'")
    console = CONSOLE_EXECUTION_RE.search(text)
    if console:
        text = console.group("arn").replace("%3A", ":").replace("%3a", ":")
    if EXECUTION_ARN_RE.match(text):
        return None, text
    if "/" in text:
        machine, _, name = text.partition("/")
        return machine.strip() or None, name.strip()
    return None, text


def execution_ref(machine: str, execution: str, arn: str) -> str:
    """
    How a call names an execution: 'machine/name' for a Standard one, its ARN for an Express one (whose ARN can't be
    made from its name).
    """
    return arn if ":express:" in arn else f"{machine}/{execution}"


def lambda_name(ref: str) -> str:
    """
    A Lambda function's name from its ARN ('arn:...:function:orders-etl:$LATEST' -> 'orders-etl'), or the name
    given, without a qualifier.
    """
    found = LAMBDA_ARN_RE.match(ref or "")
    if found:
        return found.group("name")
    return (ref or "").split(":")[0]


def loads(text: Any) -> Any:
    """
    JSON text as Python, or None when it isn't JSON (a value already parsed comes back as it is).
    """
    if not isinstance(text, str):
        return text
    try:
        return json.loads(text)
    except ValueError:
        return None


def short_cause(cause: str, width: int = 160) -> str:
    """
    An error's cause in one line: a Lambda error payload as 'KeyError: 'customer_id'', an AWS API error without its
    '(Service: ...; Request ID: ...)' tail, other text on one line.
    """
    payload = loads(cause)
    if isinstance(payload, dict) and ("errorMessage" in payload or "errorType" in payload):
        kind, message = payload.get("errorType") or "", str(payload.get("errorMessage") or "")
        text = f"{kind}: {message}" if kind and message else kind or message
    else:
        text = " ".join(str(cause or "").split()).split(" (Service: ")[0]
    return text if len(text) <= width else text[: width - 1] + "…"
