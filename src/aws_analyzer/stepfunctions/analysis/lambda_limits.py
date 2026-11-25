"""
How a state machine's states and what they call limit each other: the Lambda functions they run, and the state
machines they start. A Task state has rules of its own that meet the function's settings: its TimeoutSeconds against
the function's timeout, an Express workflow's five minutes against it too, a Map state's parallel items against the
function's reserved concurrency (or the region's whole pool). A task that starts another state machine waits for it
for its TimeoutSeconds, while the child runs for its own. state_limits() lines each pair up, says what they do
together and what to change. Pure: no AWS calls.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from ..helpers import machine_name
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
    One way a state and what it calls limit each other. `state` and `function` name them (`function` is the Lambda
    function it runs, or the state machine it starts), `rule` is
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


def _owner(name: str) -> str:
    """
    A name made possessive: "refund-approvals'" and "nightly-load's".
    """
    return f"{name}'" if name.endswith("s") else f"{name}'s"


def _child_runs_up_to(child: StateMachine) -> tuple[int | None, str]:
    """
    How long a child state machine's execution may run, in seconds (None: a year, Standard's longest) and in words.
    """
    if child.timeout:
        return child.timeout, f"{child.timeout:,} s (its own TimeoutSeconds)"
    if child.is_express:
        return EXPRESS_LONGEST_S, f"{EXPRESS_LONGEST_S // 60} minutes (Express)"
    return None, "up to a year (no TimeoutSeconds of its own)"


def _started_and_left(state: StateDef) -> StateLimit:
    """
    A task that starts another state machine without waiting for it (startExecution, no .sync): the parent moves
    on, and the child's failures never reach it.
    """
    name = machine_name(state.child)
    return StateLimit(state.name, name, "starts it and moves on (no .sync)", "its own execution", (
        f"{state.name} doesn't wait for {name}: the parent goes on, and succeeds, whatever the child does, so a "
        f"failed {name} shows only in its own executions (failures('{name}')). To wait for it, use "
        "arn:aws:states:::states:startExecution.sync:2."))


def _waits_without_timeout(state: StateDef, rule: str, runs: str) -> StateLimit:
    name = machine_name(state.child)
    effect = (f"Neither {state.name} nor {name} has a timeout: a {name} execution that gets stuck holds "
              f"{state.name} for up to a year, and the parent execution with it.")
    fix = (f"Give {state.name} a \"TimeoutSeconds\" a little over how long {name} should take, or give "
           f"{_owner(name)} definition one.")
    return StateLimit(state.name, name, rule, runs, effect, tone="warn", fix=fix, level="info")


def _gives_up_on_child(state: StateDef, child_limit: int | None, runs: str) -> StateLimit:
    """
    A task that waits for a child state machine with a TimeoutSeconds shorter than the child may run: the parent
    fails with States.Timeout first, and doesn't see what the child goes on to do. A Retry on the timeout starts the
    child again.
    """
    name = machine_name(state.child)
    is_retried = _catches(state, _TIMEOUT_ERRORS)
    again = (f" Its Retry catches the timeout and starts {name} again, so its work can be done twice."
             if is_retried else "")
    effect = (f"{state.name} gives up on {name} before {name} has to finish: after {state.timeout:,} s it fails with "
              f"States.Timeout, while {name} may run {runs}, and the parent doesn't see what it goes on to do."
              f"{again}")
    if child_limit is None:
        fix = (f"Give {_owner(name)} definition a \"TimeoutSeconds\" under {state.timeout:,}, so it ends first, or "
               f"give {state.name} room for how long {name} really takes.")
    else:
        room = child_limit + TIMEOUT_ROOM_S
        fix = (f"Give {state.name} room past the child's limit (\"TimeoutSeconds\": {room}), or lower {_owner(name)} "
               f"own TimeoutSeconds under {state.timeout:,}.")
    return StateLimit(state.name, name, f"waits {state.timeout:,} s for it (TimeoutSeconds)", f"runs {runs}", effect,
                      tone="warn", fix=fix, level="warn" if is_retried else "info")


def _child_limit(state: StateDef, child: StateMachine | None) -> StateLimit | None:
    """
    How a task that starts another state machine and the child limit each other; None when the child couldn't be
    read and nothing can be said without it.
    """
    if not state.waits_for_child:
        return _started_and_left(state)
    if child is None:
        return None
    child_limit, runs = _child_runs_up_to(child)
    name = machine_name(state.child)
    if state.timeout is None and child_limit is None:
        return _waits_without_timeout(state, "waits for it with no TimeoutSeconds", runs)
    if state.timeout is not None and (child_limit is None or child_limit > state.timeout):
        return _gives_up_on_child(state, child_limit, runs)
    rule = (f"waits {state.timeout:,} s for it (TimeoutSeconds)" if state.timeout is not None
            else "waits for it as long as it runs")
    return StateLimit(state.name, name, rule, f"runs {runs}", (
        f"{_owner(name)} own limit ends it before {state.name} stops waiting, so the parent sees how it ended."),
        tone="ok")


def state_limits(machine: StateMachine) -> list[StateLimit]:
    """
    How each Lambda task and its function limit each other: a TimeoutSeconds shorter than the function's timeout, an
    Express workflow's five minutes against it, and a Map that runs more of the function at once than its reserved
    concurrency, or the region's pool, allows. And how each task that starts another state machine meets it: a
    TimeoutSeconds shorter than the child may run, no timeout on either side, or a child started and left. Reads
    machine.lambda_settings and machine.children: what wasn't read is left out. Clashes first, then what's worth
    knowing, then what fits.
    """
    found: list[StateLimit] = []
    for state in machine.states:
        settings = machine.lambda_settings.get(state.function) if state.function else None
        if settings is not None:
            found += _limits_of(machine, state, settings)
        child = _child_limit(state, machine.children.get(state.child)) if state.child else None
        if child is not None:
            found.append(child)
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
