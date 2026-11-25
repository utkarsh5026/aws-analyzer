"""
A state machine's definition (Amazon States Language) read into its states, in order, with what each one calls,
where it goes next and how it handles errors; and the definition drawn as a Mermaid flowchart.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any

from ..helpers import lambda_name, loads
from ..models import Execution, StateDef, StateMachine


LAMBDA_INVOKE = "arn:aws:states:::lambda:invoke"

LAMBDA_SERVICE_ERRORS = ("Lambda.ServiceException", "Lambda.AWSLambdaException", "Lambda.SdkClientException",
                         "Lambda.TooManyRequestsException")

CATCH_ALL_ERRORS = ("States.ALL", "States.TaskFailed")

_SERVICE_PREFIX = "arn:aws:states:::"

_MERMAID_ID_RE = re.compile(r"\W")

_MERMAID_SHAPES = {"Choice": ("{", "}"), "Fail": ("[/", "/]"), "Succeed": ("([", "])"), "Map": ("[[", "]]"),
                   "Parallel": ("[[", "]]"), "Wait": ("(", ")")}


def _function_of(resource: str, parameters: dict[str, Any]) -> str:
    """
    The Lambda function a Task resource calls: the one named in its parameters for lambda:invoke, the ARN's for a
    direct Lambda ARN, '' for anything else or a name taken from the input at run time.
    """
    if resource.startswith(LAMBDA_INVOKE):
        name = parameters.get("FunctionName")
        return lambda_name(name) if isinstance(name, str) else ""
    if resource.startswith("arn:aws:lambda:"):
        return lambda_name(resource)
    return ""


def _next_states(body: dict[str, Any]) -> list[str]:
    """
    Every state a state can go to: Next, each Choice rule's and its Default, and each Catch's.
    """
    targets = [body.get("Next")]
    targets += [rule.get("Next") for rule in body.get("Choices") or []]
    targets.append(body.get("Default"))
    targets += [catch.get("Next") for catch in body.get("Catch") or []]
    return list(dict.fromkeys(t for t in targets if isinstance(t, str)))


_COMPARISONS = (("GreaterThanEquals", ">="), ("LessThanEquals", "<="), ("GreaterThan", ">"), ("LessThan", "<"),
                ("Equals", "="), ("Matches", "matches"))

_CHECKS = {"IsPresent": ("is present", "is missing"), "IsNull": ("is null", "isn't null"),
           "IsString": ("is a string", "isn't a string"), "IsNumeric": ("is a number", "isn't a number"),
           "IsBoolean": ("is true or false", "isn't true or false"),
           "IsTimestamp": ("is a timestamp", "isn't a timestamp")}

_VALUE_TYPES = ("String", "Numeric", "Boolean", "Timestamp")

_ANY_ERROR = "any error"


def _comparison(variable: str, key: str, value: Any) -> str:
    """
    One JSONPath comparison in words: ('$.total', 'NumericGreaterThanPath', '$.limit') -> '$.total > $.limit'.
    """
    if key in _CHECKS:
        is_word, not_word = _CHECKS[key]
        return f"{variable} {is_word if value else not_word}"
    operator = key.removesuffix("Path")
    for prefix in _VALUE_TYPES:
        operator = operator.removeprefix(prefix)
    symbol = dict(_COMPARISONS).get(operator, key)
    shown = value if key.endswith("Path") else json.dumps(value)
    return f"{variable} {symbol} {shown}"


def describe_rule(rule: dict[str, Any]) -> str:
    """
    A Choice rule's condition in words, as the graph labels its arrow: its Comment when it has one, a JSONata
    Condition as written, '$.priority = true', 'not ($.total > 100)', or 'a and b' for an And of two.
    """
    if isinstance(rule.get("Comment"), str) and rule["Comment"]:
        return rule["Comment"]
    if isinstance(rule.get("Condition"), str):
        return rule["Condition"].removeprefix("{%").removesuffix("%}").strip()
    if isinstance(rule.get("Not"), dict):
        return f"not ({describe_rule(rule['Not'])})"
    for joiner in ("And", "Or"):
        parts = rule.get(joiner)
        if isinstance(parts, list):
            return f" {joiner.lower()} ".join(describe_rule(p) for p in parts if isinstance(p, dict))
    variable = str(rule.get("Variable") or "")
    keys = [key for key in rule if key not in ("Variable", "Next", "Comment", "Assign", "Output")]
    return _comparison(variable, keys[0], rule[keys[0]]) if keys else variable


def describe_errors(errors: list[str]) -> str:
    """
    A Catch's errors in words, as the graph labels its arrow: 'any error' for States.ALL, else their names.
    """
    return _ANY_ERROR if "States.ALL" in errors else ", ".join(errors)


def _labels(body: dict[str, Any]) -> dict[str, str]:
    """
    What sends a state to each target that needs saying: each Choice rule's condition (two rules to one target
    joined with 'or'), 'Default', and each Catch's errors.
    """
    labels: dict[str, str] = {}

    def add(target: Any, words: str) -> None:
        if isinstance(target, str):
            labels[target] = f"{labels[target]} or {words}" if target in labels else words

    for rule in body.get("Choices") or []:
        if isinstance(rule, dict):
            add(rule.get("Next"), describe_rule(rule))
    add(body.get("Default"), "Default")
    for catch in body.get("Catch") or []:
        if isinstance(catch, dict):
            add(catch.get("Next"), describe_errors(list(catch.get("ErrorEquals") or ["?"])))
    return labels


def _is_distributed(body: dict[str, Any]) -> bool:
    processor = body.get("ItemProcessor")
    config = processor.get("ProcessorConfig") if isinstance(processor, dict) else None
    return isinstance(config, dict) and config.get("Mode") == "DISTRIBUTED"


def _state(name: str, body: dict[str, Any], path: str, depth: int, branch: int = 0) -> StateDef:
    resource = str(body.get("Resource") or "")
    parameters = body.get("Parameters") or body.get("Arguments") or {}
    return StateDef(
        name=name,
        type=str(body.get("Type") or "?"),
        path=f"{path} > {name}" if path else name,
        resource=resource,
        function=_function_of(resource, parameters if isinstance(parameters, dict) else {}),
        next=_next_states(body),
        end=bool(body.get("End")) or body.get("Type") in ("Succeed", "Fail"),
        retries=list(body.get("Retry") or []),
        catches=list(body.get("Catch") or []),
        timeout=body.get("TimeoutSeconds") if isinstance(body.get("TimeoutSeconds"), int) else None,
        heartbeat=body.get("HeartbeatSeconds") if isinstance(body.get("HeartbeatSeconds"), int) else None,
        comment=str(body.get("Comment") or ""),
        depth=depth,
        labels=_labels(body),
        branch=branch,
        starts=[str(inner.get("StartAt") or "") for inner in _inner_machines(body)],
        max_concurrency=body.get("MaxConcurrency") if isinstance(body.get("MaxConcurrency"), int) else None,
        distributed=_is_distributed(body),
    )


def _inner_machines(body: dict[str, Any]) -> list[dict[str, Any]]:
    """
    The state machines nested in a Map (ItemProcessor, or the older Iterator) or a Parallel state (its Branches).
    """
    inner = [body.get("ItemProcessor"), body.get("Iterator")] + list(body.get("Branches") or [])
    return [machine for machine in inner if isinstance(machine, dict)]


def _walk(machine: dict[str, Any], path: str, depth: int, out: list[StateDef], branch: int = 0) -> None:
    for name, body in (machine.get("States") or {}).items():
        if not isinstance(body, dict):
            continue
        state = _state(name, body, path, depth, branch)
        out.append(state)
        for number, inner in enumerate(_inner_machines(body)):
            _walk(inner, state.path, depth + 1, out, number)


def parse_definition(definition: str | dict[str, Any]) -> tuple[str, list[StateDef]]:
    """
    An ASL definition (text or parsed) -> (StartAt, every state in order), the states inside a Map or Parallel
    right after it, one level deeper. A definition that isn't JSON gives ('', []).
    """
    parsed = loads(definition)
    if not isinstance(parsed, dict):
        return "", []
    states: list[StateDef] = []
    _walk(parsed, "", 0, states)
    return str(parsed.get("StartAt") or ""), states


def parse_state_machine(described: dict[str, Any]) -> StateMachine:
    """
    DescribeStateMachine's answer -> StateMachine, with its states parsed and its logging read.
    """
    logging = described.get("loggingConfiguration") or {}
    groups = [d.get("cloudWatchLogsLogGroup", {}).get("logGroupArn", "") for d in logging.get("destinations") or []]
    start_at, states = parse_definition(described.get("definition") or "")
    return StateMachine(
        name=described.get("name", ""),
        arn=described.get("stateMachineArn", ""),
        type=described.get("type", "STANDARD"),
        created=described.get("creationDate"),
        status=described.get("status", ""),
        description=described.get("description", ""),
        role=described.get("roleArn", ""),
        definition=described.get("definition", ""),
        states=states,
        start_at=start_at,
        log_level=logging.get("level", "OFF"),
        log_data=bool(logging.get("includeExecutionData")),
        log_groups=[g for g in groups if g],
        tracing=bool((described.get("tracingConfiguration") or {}).get("enabled")),
    )


def parse_execution(item: dict[str, Any], machine: str = "") -> Execution:
    """
    A ListExecutions item or DescribeExecution's answer -> Execution.
    """
    start: datetime | None = item.get("startDate")
    return Execution(
        arn=item.get("executionArn", ""),
        name=item.get("name", ""),
        machine=machine or item.get("stateMachineArn", "").rsplit(":", 1)[-1],
        status=item.get("status", ""),
        start=start,
        stop=item.get("stopDate"),
        redrive_count=int(item.get("redriveCount") or 0),
        redrive_status=item.get("redriveStatus", ""),
        error=item.get("error", ""),
        cause=item.get("cause", ""),
        input=item.get("input", ""),
        output=item.get("output", ""),
    )


def resource_label(state: StateDef) -> str:
    """
    What a state does, in a few words: 'λ orders-etl', 'dynamodb:updateItem', 's3:getObject', 'activity
    approve', or its type.
    """
    if state.function:
        return f"λ {state.function}"
    resource = state.resource
    if resource.startswith(_SERVICE_PREFIX):
        service = resource[len(_SERVICE_PREFIX):]
        return service.removeprefix("aws-sdk:")
    if ":activity:" in resource:
        return f"activity {resource.rsplit(':', 1)[-1]}"
    if resource.startswith("arn:aws:lambda:"):
        return f"λ {lambda_name(resource)}"
    return state.type


def retry_label(state: StateDef) -> str:
    """
    A state's Retry rules in a few words: 'Lambda.TooManyRequestsException ×3 (back-off 2.0)', and "Lambda's own
    errors" for the four AWS recommends retrying on every Lambda task.
    """
    parts = []
    for rule in state.retries:
        named = rule.get("ErrorEquals") or ["?"]
        errors = "Lambda's own errors" if set(named) == set(LAMBDA_SERVICE_ERRORS) else ", ".join(named)
        parts.append(f"{errors} ×{rule.get('MaxAttempts', 3)} (back-off {rule.get('BackoffRate', 2.0)})")
    return "; ".join(parts)


def catch_label(state: StateDef) -> str:
    """
    A state's Catch rules: 'States.ALL -> OrderFailed'.
    """
    return "; ".join(f"{', '.join(c.get('ErrorEquals') or ['?'])} -> {c.get('Next', '?')}" for c in state.catches)


def _retries_lambda_errors(state: StateDef) -> bool:
    covered = {error for rule in state.retries for error in rule.get("ErrorEquals") or []}
    return bool(covered & set(CATCH_ALL_ERRORS)) or bool(covered & set(LAMBDA_SERVICE_ERRORS))


def lambda_retry_block() -> str:
    """
    The Retry rule AWS recommends on every Lambda task, as JSON to paste.
    """
    rule = {"ErrorEquals": list(LAMBDA_SERVICE_ERRORS), "IntervalSeconds": 1, "MaxAttempts": 3, "BackoffRate": 2,
            "JitterStrategy": "FULL"}
    return json.dumps({"Retry": [rule]})


def unretried_lambda_states(machine: StateMachine) -> list[StateDef]:
    """
    The Lambda tasks with no Retry for Lambda's own transient errors (throttles, service errors).
    """
    return [s for s in machine.states if s.function and not _retries_lambda_errors(s)]


def unbounded_waits(machine: StateMachine) -> list[StateDef]:
    """
    The states that wait for a callback or an activity with no TimeoutSeconds or HeartbeatSeconds.
    """
    return [s for s in machine.states if s.waits_for_callback and s.timeout is None and s.heartbeat is None]


def _mermaid_id(name: str) -> str:
    return "s_" + _MERMAID_ID_RE.sub("_", name)


def _mermaid_node(state: StateDef) -> str:
    left, right = _MERMAID_SHAPES.get(state.type, ("[", "]"))
    label = state.name.replace('"', "'")
    detail = resource_label(state).replace('"', "'")
    text = label if detail == state.type else f"{label}<br/>{detail}"
    return f'    {_mermaid_id(state.name)}{left}"{text}"{right}'


def mermaid(machine: StateMachine) -> str:
    """
    The top level of a state machine as a Mermaid flowchart to paste into a Markdown cell or mermaid.live: a box per
    state, an arrow per transition, dashed arrows for Catch.
    """
    top = [s for s in machine.states if s.depth == 0]
    lines = ["flowchart TD", f"    start((start)) --> {_mermaid_id(machine.start_at)}"]
    lines += [_mermaid_node(state) for state in top]
    for state in top:
        caught = {c.get("Next") for c in state.catches}
        for target in state.next:
            arrow = "-.->|error|" if target in caught else "-->"
            lines.append(f"    {_mermaid_id(state.name)} {arrow} {_mermaid_id(target)}")
        if state.end and state.type not in ("Fail",):
            lines.append(f"    {_mermaid_id(state.name)} --> stop((end))")
    return "\n".join(lines)

