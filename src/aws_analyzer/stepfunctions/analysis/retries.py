"""
Whether a Retry would have saved a kind of failure: what kind of error it is (a throttle or service error that passes
in seconds, a timeout, running out of memory, or a fault in the code or its permissions that fails the same way every
time), whether the state already retries it, and the Retry rule to paste when one would help. Read from the error's
name and the state's definition, so it's a judgement, said as one. Pure.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from ..._kit.fmt import _plural

from ..models import FailureGroup, FailureReport, StateDef, StateMachine


Finding = tuple[str, str]

GROUPS_ADVISED = 3

TRANSIENT_ERRORS = (
    "Lambda.TooManyRequestsException", "Lambda.ServiceException", "Lambda.AWSLambdaException",
    "Lambda.SdkClientException", "Lambda.EC2ThrottledException", "ThrottlingException",
    "ProvisionedThroughputExceededException", "RequestLimitExceeded", "ServiceUnavailable", "InternalServerError",
    "InternalFailure", "TooManyRequestsException", "SlowDown",
)

TRANSIENT_WORDS = ("Throttl", "TooManyRequests", "ServiceUnavailable", "ServiceException", "InternalServer",
                   "SlowDown", "RequestLimitExceeded")

TIMEOUT_ERRORS = ("States.Timeout", "States.HeartbeatTimeout", "Sandbox.Timedout")

PERMANENT_ERRORS = (
    "States.Permissions", "States.DataLimitExceeded", "States.Runtime", "States.QueryEvaluationError",
    "Runtime.ImportModuleError", "Runtime.HandlerNotFound", "Runtime.UserCodeSyntaxError",
    "Lambda.ResourceNotFoundException", "Lambda.InvalidParameterValueException",
)

PERMANENT_WORDS = ("AccessDenied", "Unauthorized", "Validation", "NotFound", "InvalidParameter", "Forbidden")

NOT_RETRIED_BY_ALL = ("States.Runtime", "States.DataLimitExceeded")

_CODE_EXCEPTION_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(Error|Exception)$")

_MEANINGS = {
    "transient": "throttles and service errors pass in seconds",
    "timeout": "a retry runs it again for as long, so it times out again unless the slowness passed",
    "memory": "it runs out of memory the same way each time",
    "permanent": "it fails the same way every time it's given the same input",
}

NEW_RULE = {"IntervalSeconds": 2, "MaxAttempts": 6, "BackoffRate": 2, "JitterStrategy": "FULL"}

MOST_ATTEMPTS = 10


@dataclass
class RetryAdvice:
    """
    Whether a Retry would have saved a kind of failure: kind ('transient', 'timeout', 'memory', 'permanent' or
    'unknown'), would_help (True, False, or None when it depends), the state's rule that already matches its error
    (rule), what to say in a few words (verdict), and a Retry rule to paste when one would help (suggested).
    """

    group: FailureGroup
    kind: str
    would_help: bool | None
    verdict: str
    rule: dict[str, Any] | None = None
    suggested: dict[str, Any] | None = None
    state: StateDef | None = field(default=None, repr=False)


def error_kind(error: str, cause: str = "") -> str:
    """
    What kind of error this is, from its name (with or without its service's prefix: DynamoDB.ThrottlingException)
    and its cause, for Lambda's catch-alls: 'transient' (a throttle or service error), 'timeout', 'memory',
    'permanent' (a fault in the code, its permissions or its input) or 'unknown'.
    """
    said = f"{error} {cause}"
    if error in TIMEOUT_ERRORS or "Task timed out" in cause:
        return "timeout"
    if "OutOfMemory" in said or "Runtime exited with error: signal: killed" in cause:
        return "memory"
    bare = error.rsplit(".", 1)[-1]
    is_transient = error in TRANSIENT_ERRORS or bare in TRANSIENT_ERRORS
    if is_transient or any(word in error for word in TRANSIENT_WORDS):
        return "transient"
    if error in PERMANENT_ERRORS or any(word in error for word in PERMANENT_WORDS):
        return "permanent"
    if _CODE_EXCEPTION_RE.match(error):
        return "permanent"
    return "unknown"


def matching_retry(state: StateDef | None, error: str) -> dict[str, Any] | None:
    """
    The state's first Retry rule that catches this error, as Step Functions picks one: its name, States.ALL (but
    for the errors it leaves out), or States.TaskFailed (any task failure but a timeout).
    """
    if state is None:
        return None
    for rule in state.retries:
        named = rule.get("ErrorEquals") or []
        is_all = "States.ALL" in named and error not in NOT_RETRIED_BY_ALL
        is_task_failed = "States.TaskFailed" in named and error not in TIMEOUT_ERRORS
        if error in named or is_all or is_task_failed:
            return rule
    return None


def roomier_rule(error: str, rule: dict[str, Any] | None) -> dict[str, Any]:
    """
    A Retry rule for a transient error to paste: a new one, or the state's own with twice the attempts (at most
    MOST_ATTEMPTS, at least NEW_RULE's) and jitter, so the tries spread out.
    """
    if rule is None:
        return {"ErrorEquals": [error], **NEW_RULE}
    attempts = min(max(int(rule.get("MaxAttempts", 3)) * 2, NEW_RULE["MaxAttempts"]), MOST_ATTEMPTS)
    return {**rule, "MaxAttempts": attempts, "BackoffRate": max(float(rule.get("BackoffRate", 2.0)), 2.0),
            "JitterStrategy": "FULL"}


def _transient_advice(group: FailureGroup, state: StateDef | None, rule: dict[str, Any] | None) -> RetryAdvice:
    if rule is None:
        return RetryAdvice(group, "transient", True, "yes: add a Retry", None, roomier_rule(group.error, None), state)
    return RetryAdvice(group, "transient", True, "it retries, but ran out: give it more tries", rule,
                       roomier_rule(group.error, rule), state)


def retry_advice(machine: StateMachine, group: FailureGroup) -> RetryAdvice:
    """
    Whether a Retry would have saved a kind of failure, from its error's kind and the state's own Retry rules.
    """
    state = machine.state(group.state)
    kind = error_kind(group.error, group.cause)
    rule = matching_retry(state, group.error)
    if kind == "transient":
        return _transient_advice(group, state, rule)
    if kind == "permanent":
        verdict = "no, and its Retry repeats it" if rule is not None else "no: fix the cause"
        return RetryAdvice(group, kind, False, verdict, rule, None, state)
    if kind == "memory":
        return RetryAdvice(group, kind, False, "no: give it more memory", rule, None, state)
    if kind == "timeout":
        return RetryAdvice(group, kind, None, "not first: find why it's slow", rule, None, state)
    return RetryAdvice(group, kind, None, "can't tell from the error", rule, None, state)


def rule_json(rule: dict[str, Any]) -> str:
    """
    A Retry rule as the JSON to paste into the state's Retry list.
    """
    return json.dumps(rule)


def _failed(advice: RetryAdvice) -> str:
    group = advice.group
    executions = "the execution" if group.count == 1 else f"the {group.count:,} executions"
    return f"{executions} that failed at {group.state} with {group.error}"


def _rule_text(rule: dict[str, Any]) -> str:
    """
    A Retry rule's numbers in a few words: '×2, back-off 2'.
    """
    return f"×{rule.get('MaxAttempts', 3)}, back-off {rule.get('BackoffRate', 2.0)}"


def _transient_finding(advice: RetryAdvice) -> Finding:
    group, rule = advice.group, advice.rule
    if rule is None:
        return ("warn", f"A Retry would likely have saved {_failed(advice)}: {_MEANINGS['transient']}, and "
                        f"{group.state} has no Retry for it. Add this to its Retry: {rule_json(advice.suggested)}")
    ran_out = "once" if group.count == 1 else f"in {_plural(group.count, 'execution')}"
    return ("warn", f"{group.state} retries {group.error} ({_rule_text(rule)}) and still ran out of tries {ran_out}: "
                    "the throttling outlasted them. Give it more tries, further apart: "
                    f"{rule_json(advice.suggested)}, or raise the concurrency it's throttled on.")


def _permanent_finding(advice: RetryAdvice) -> Finding:
    group, rule = advice.group, advice.rule
    if rule is None:
        return ("info", f"A Retry won't help {_failed(advice)}: {_MEANINGS['permanent']}. Fix the cause, then "
                        "redrive the ones that still can.")
    label = ", ".join(rule.get("ErrorEquals") or [])
    return ("warn", f"{group.state} retries {group.error} though it fails the same way each time: its Retry on "
                    f"{label} ({_rule_text(rule)}) only repeats the failure, costing time and state transitions. List only the errors "
                    "that pass in its ErrorEquals (Lambda's own errors, throttles), not States.ALL.")


def advice_finding(advice: RetryAdvice) -> Finding | None:
    """
    What to do about a kind of failure's retries, as a finding (None when its error's kind can't be told).
    """
    group = advice.group
    if advice.kind == "transient":
        return _transient_finding(advice)
    if advice.kind == "permanent":
        return _permanent_finding(advice)
    if advice.kind == "memory":
        name = group.function or group.state
        return ("info", f"A Retry won't help {_failed(advice)}: {_MEANINGS['memory']}. Give {name} more memory "
                        "(its MemorySize), or have it read less at a time.")
    if advice.kind == "timeout":
        limit = (f" ({advice.state.timeout:,}s now)" if advice.state is not None and advice.state.timeout
                 else "")
        return ("info", f"A Retry isn't the first fix for {_failed(advice)}: {_MEANINGS['timeout']}. Find why it's "
                        f"slow, or raise its TimeoutSeconds{limit}, before retrying it.")
    return None


def retry_findings(report: FailureReport) -> list[Finding]:
    """
    For the GROUPS_ADVISED most common kinds of failure, whether a Retry would have saved them and what to change.
    """
    found = []
    for group in report.groups[:GROUPS_ADVISED]:
        finding = advice_finding(retry_advice(report.machine, group))
        if finding is not None:
            found.append(finding)
    return found
