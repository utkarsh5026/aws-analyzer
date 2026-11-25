"""
How a state machine's states and the Lambda functions they call limit each other. A Task state has rules of its own
that meet the function's settings: its TimeoutSeconds against the function's timeout, an Express workflow's five
minutes against it too, a Map state's parallel items against the function's reserved concurrency (or the region's
whole pool). state_limits() lines each pair up, says what they do together and what to change. Pure: no AWS calls.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from ..models import LambdaSettings, StateDef, StateMachine
from .definition import CATCH_ALL_ERRORS

EXPRESS_LONGEST_S = 300
INLINE_MAP_MOST = 40
DISTRIBUTED_MAP_MOST = 10_000
REGION_CONCURRENCY = 1_000
TIMEOUT_ROOM_S = 5

_TIMEOUT_ERRORS = ("States.Timeout", "States.ALL")
_THROTTLE_ERRORS = ("Lambda.TooManyRequestsException", *CATCH_ALL_ERRORS)


@dataclass
class StateLimit:
    """
    One way a state and the Lambda function it calls limit each other. `state` and `function` name them, `rule` is
    the state's side in a few words ('gives up after 30 s'), `setting` the function's ('60 s timeout'), `effect`
    what the two do together, written as a finding, and `fix` what lines them up ('' when they already do). `tone` is
    'warn' when they clash, 'ok' when they fit, '' when it's only worth knowing; `level` the finding it makes
    ('warn', 'info' or '' for none).
    """

    state: str
    function: str
    rule: str
    setting: str
    effect: str
    tone: str = ""
    fix: str = ""
    level: str = ""

    @property
    def finding(self) -> tuple[str, str] | None:
        """
        The (level, message) pair findings show, or None when it fits.
        """
        if not self.level:
            return None
        return self.level, f"{self.effect} {self.fix}".strip()


def _catches(state: StateDef, errors: tuple[str, ...]) -> bool:
    named = {error for rule in state.retries for error in rule.get("ErrorEquals") or []}
    return bool(named & set(errors))


def _task_timeout(state: StateDef, settings: LambdaSettings) -> StateLimit | None:
    """
    A Task's TimeoutSeconds shorter than its function's timeout: Step Functions fails the state with States.Timeout
    and moves on, but the Lambda run it started isn't stopped, so it goes on, billed, and whatever it writes lands
    after the workflow has given up on it. A Retry on States.Timeout then starts a second run beside the first.
    """
    if state.timeout is None:
        return None
    rule = f"gives up after {state.timeout:,} s (TimeoutSeconds)"
    setting = f"{settings.timeout:,} s timeout"
    if state.timeout >= settings.timeout:
        return StateLimit(state.name, state.function, rule, setting, (
            "The function's own timeout ends a run before the state gives up on it, so the state sees the "
            "function's error."), tone="ok")
    is_retried = _catches(state, _TIMEOUT_ERRORS)
    twice = (" Its Retry catches the timeout, so it starts a second run beside the first and the work is done "
             "twice." if is_retried else "")
    effect = (f"{state.name} gives up on {state.function} before the function does: after {state.timeout:,} s it "
              f"fails with States.Timeout, while the run goes on for up to {settings.timeout:,} s, billed, and what "
              f"it writes lands after the workflow has moved on.{twice}")
    fix = (f"Give the state room past the function's timeout (\"TimeoutSeconds\": "
           f"{settings.timeout + TIMEOUT_ROOM_S}), or lower the function's timeout to {state.timeout} s "
           f"(aws lambda update-function-configuration --function-name {state.function} --timeout {state.timeout}).")
    return StateLimit(state.name, state.function, rule, setting, effect, tone="warn", fix=fix,
                      level="warn" if is_retried else "info")


def _express_timeout(machine: StateMachine, state: StateDef, settings: LambdaSettings) -> StateLimit | None:
    """
    An Express workflow stops at five minutes, whatever its states and functions are set to. A function allowed
    longer keeps running after the execution has timed out.
    """
    if not machine.is_express or settings.timeout <= EXPRESS_LONGEST_S:
        return None
    effect = (f"{machine.name} is an Express workflow, which stops at {EXPRESS_LONGEST_S // 60} minutes, but "
              f"{state.function} may run {settings.timeout:,} s: a long run outlives the execution that started it, "
              "billed, with nobody to hand its answer to.")
    fix = (f"Lower the function's timeout to fit (aws lambda update-function-configuration --function-name "
           f"{state.function} --timeout {EXPRESS_LONGEST_S}), or make it a Standard workflow if the work needs longer.")
    return StateLimit(state.name, state.function, f"its execution stops at {EXPRESS_LONGEST_S // 60} minutes "
                      "(Express)", f"{settings.timeout:,} s timeout", effect, tone="warn", fix=fix, level="info")


def _map_parallel(map_state: StateDef) -> tuple[int, str]:
    """
    How many items a Map runs at once, and how that's set: its MaxConcurrency, else the most Step Functions runs.
    """
    if map_state.max_concurrency:
        return map_state.max_concurrency, f"runs {map_state.max_concurrency:,} items at once (MaxConcurrency)"
    if map_state.distributed:
        return DISTRIBUTED_MAP_MOST, (f"runs up to {DISTRIBUTED_MAP_MOST:,} items at once (Distributed, no "
                                      "MaxConcurrency)")
    return INLINE_MAP_MOST, f"runs up to {INLINE_MAP_MOST} items at once (no MaxConcurrency)"


def _max_concurrency_fix(map_state: StateDef, most: int) -> str:
    return f"Cap the Map: {json.dumps({'MaxConcurrency': most})} on {map_state.name}."


def _reserved_clash(map_state: StateDef, state: StateDef, settings: LambdaSettings, parallel: int,
                    rule: str) -> StateLimit:
    """
    A Map that runs more of a function at once than its reserved concurrency allows: the rest are throttled, and
    fail the execution unless the task retries Lambda's throttles.
    """
    reserved = settings.reserved_concurrency or 0
    setting = f"reserved concurrency {reserved:,}"
    if parallel <= reserved:
        return StateLimit(state.name, state.function, rule, setting, (
            "The Map runs no more of it at once than the function may run, so none is throttled."), tone="ok")
    is_retried = _catches(state, _THROTTLE_ERRORS)
    then = ("They wait and retry, so the Map is held to the function's limit and spends its retries on throttles."
            if is_retried else "Nothing retries them, so a throttle fails the item, and with it the execution.")
    effect = (f"{map_state.name} runs more of {state.function} at once than it may run: up to {parallel:,} items "
              f"against its reserved concurrency of {reserved:,}, so the rest are throttled. {then}")
    return StateLimit(state.name, state.function, rule, setting, effect, tone="warn",
                      fix=_max_concurrency_fix(map_state, reserved), level="info" if is_retried else "warn")


def _pool_clash(map_state: StateDef, state: StateDef, settings: LambdaSettings, parallel: int,
                rule: str) -> StateLimit | None:
    """
    A Map that can start more runs at once than the region's whole Lambda pool: with no reserved concurrency to
    hold it, the function takes the pool, and every other function in the region is throttled meanwhile.
    """
    pool = settings.region_concurrency or REGION_CONCURRENCY
    if parallel < pool:
        return None
    effect = (f"{map_state.name} can start up to {parallel:,} runs of {state.function} at once, as many as the "
              f"region's whole Lambda pool ({pool:,}): while it runs, every other function in the region can be "
              "throttled.")
    fix = (f"{_max_concurrency_fix(map_state, pool // 10)} Or give the function reserved concurrency, which keeps "
           "the rest of the pool for the others.")
    return StateLimit(state.name, state.function, rule, "no reserved concurrency (shares the region's pool)",
                      effect, tone="warn", fix=fix, level="warn")


def _map_concurrency(map_state: StateDef, state: StateDef, settings: LambdaSettings) -> StateLimit | None:
    parallel, rule = _map_parallel(map_state)
    if settings.reserved_concurrency:
        return _reserved_clash(map_state, state, settings, parallel, rule)
    return _pool_clash(map_state, state, settings, parallel, rule)


def _enclosing_map(machine: StateMachine, state: StateDef) -> StateDef | None:
    """
    The innermost Map a state runs inside, by its path ('ProcessItems > ResizeImage' is inside ProcessItems).
    """
    found = None
    for candidate in machine.states:
        if candidate.type == "Map" and state.path.startswith(f"{candidate.path} > "):
            if found is None or len(candidate.path) > len(found.path):
                found = candidate
    return found


def _limits_of(machine: StateMachine, state: StateDef, settings: LambdaSettings) -> list[StateLimit]:
    found = [_task_timeout(state, settings), _express_timeout(machine, state, settings)]
    map_state = _enclosing_map(machine, state)
    if map_state is not None:
        found.append(_map_concurrency(map_state, state, settings))
    return [limit for limit in found if limit is not None]


def state_limits(machine: StateMachine) -> list[StateLimit]:
    """
    How each Lambda task and its function limit each other: a TimeoutSeconds shorter than the function's timeout, an
    Express workflow's five minutes against it, and a Map that runs more of the function at once than its reserved
    concurrency, or the region's pool, allows. Reads machine.lambda_settings: a function whose settings weren't read
    is left out. Clashes first, then what's worth knowing, then what fits.
    """
    found: list[StateLimit] = []
    for state in machine.states:
        settings = machine.lambda_settings.get(state.function) if state.function else None
        if settings is not None:
            found += _limits_of(machine, state, settings)
    order = {"warn": 0, "": 1, "ok": 2}
    return sorted(found, key=lambda limit: order.get(limit.tone, 1))


def state_limit_findings(machine: StateMachine) -> list[tuple[str, str]]:
    """
    The findings state_limits() makes: each clash, with what to change.
    """
    findings = []
    for limit in state_limits(machine):
        finding = limit.finding
        if finding is not None:
            findings.append(finding)
    return findings
