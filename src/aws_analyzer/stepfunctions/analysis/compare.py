"""
One execution beside another, usually a failed one beside the last that succeeded: state by state (did it run,
did it fail, how many tries, how long) and its input field by field (missing, extra, empty or of another type), and
what the differences say, a missing field named in the error first. Pure: run it on two ExecutionDetails of your own.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from ..._kit.fmt import _duration, _plural
from ..._kit.text import _clip

from ..helpers import STATUS_WORDS
from ..models import ExecutionDetail, StepRun


Finding = tuple[str, str]

SLOWER_RATIO = 2.0

SLOWER_SECONDS = 1.0

INPUT_DEPTH = 3

_VALUE_CHARS = 40

_EMPTY_VALUES: tuple[Any, ...] = (None, "", [], {})


@dataclass
class StateRuns:
    """
    How one state ran in an execution: how many times, how long in all, how many retries, and the error it failed
    with ('' when it didn't).
    """

    count: int = 0
    seconds: float = 0.0
    retries: int = 0
    error: str = ""

    @property
    def failed(self) -> bool:
        return bool(self.error)


@dataclass
class StepDifference:
    """
    One state in both executions, and how they differ: kind is 'failed' (only here), 'retried', 'slower', 'faster',
    'not_reached' (ran only in the other), 'only_here' (ran only in this one), 'after_failure' (ran only in this one,
    after it failed: where a Catch sent the error) or 'same'.
    """

    state: str
    depth: int
    this: StateRuns | None
    other: StateRuns | None
    kind: str
    words: str


@dataclass
class InputDifference:
    """
    One input field that differs: kind is 'missing' (in the other's input, not this one's), 'extra', 'empty' (empty
    here, set there) or 'type'; path is dotted ('customer.email'), the values shown short.
    """

    path: str
    kind: str
    this: str
    other: str


@dataclass
class ExecutionComparison:
    """
    Two executions side by side: this (the one asked about) and other (what it's compared with, other_is_before when
    it started first), their states and input fields that differ.
    """

    this: ExecutionDetail
    other: ExecutionDetail
    steps: list[StepDifference] = field(default_factory=list)
    inputs: list[InputDifference] = field(default_factory=list)
    other_is_before: bool = True

    @property
    def differing_steps(self) -> list[StepDifference]:
        """
        The states that differ, without what only followed from a failure: once this execution failed, the states
        it never reached and where its Catch sent the error are the failure's consequences, not differences.
        """
        has_failed = any(step.kind == "failed" for step in self.steps)
        consequences = ("not_reached", "after_failure") if has_failed else ()
        return [step for step in self.steps if step.kind != "same" and step.kind not in consequences]


def _state_runs(steps: list[StepRun]) -> tuple[dict[str, StateRuns], dict[str, int]]:
    """
    Each state's runs in an execution (a state inside a Map ran once per item), and its depth, in the order the
    execution entered them.
    """
    runs: dict[str, StateRuns] = {}
    depths: dict[str, int] = {}
    for step in steps:
        seen = runs.setdefault(step.name, StateRuns())
        depths.setdefault(step.name, step.depth)
        seen.count += 1
        seen.seconds += step.seconds or 0.0
        seen.retries += step.retries
        if step.status in ("failed", "timed out") and not seen.error:
            seen.error = step.error or step.status
    return runs, depths


def _runs_text(runs: StateRuns) -> str:
    """
    How a state ran, short: 'failed · 1.73s', '4.00s · 3 tries', '0.21s ×3'.
    """
    took = _duration(runs.seconds)
    times = f" ×{runs.count}" if runs.count > 1 else ""
    tries = f" · {runs.retries + runs.count} tries" if runs.retries else ""
    return f"failed · {took}" if runs.failed else f"{took}{times}{tries}"


def _difference(this: StateRuns | None, other: StateRuns | None, other_name: str) -> tuple[str, str]:
    """
    How a state differs between the two executions, as (kind, words).
    """
    if this is None:
        return "not_reached", f"not reached here; ran in {other_name}"
    if other is None:
        return ("failed", f"failed here with {this.error}") if this.failed else ("only_here", "ran only here")
    if this.failed and not other.failed:
        return "failed", f"failed here with {this.error}; went through in {other_name}"
    if this.retries > other.retries:
        return "retried", f"{this.retries + 1} tries here, {other.retries + 1} there"
    is_slower = this.seconds >= SLOWER_RATIO * other.seconds and this.seconds - other.seconds >= SLOWER_SECONDS
    if is_slower:
        return "slower", f"{_duration(this.seconds)} here, {_duration(other.seconds)} there"
    is_faster = other.seconds >= SLOWER_RATIO * this.seconds and other.seconds - this.seconds >= SLOWER_SECONDS
    if is_faster:
        return "faster", f"{_duration(this.seconds)} here, {_duration(other.seconds)} there"
    return "same", "same"


def compare_steps(this: ExecutionDetail, other: ExecutionDetail) -> list[StepDifference]:
    """
    Every state either execution entered, in the order this one entered them (then the other's), with how it differs.
    """
    mine, depths = _state_runs(this.steps)
    theirs, other_depths = _state_runs(other.steps)
    names = list(mine) + [name for name in theirs if name not in mine]
    found = []
    has_failed = False
    for name in names:
        kind, words = _difference(mine.get(name), theirs.get(name), other.execution.name)
        if kind == "only_here" and has_failed:
            kind, words = "after_failure", "ran only here, after the failure (where its Catch sent the error)"
        has_failed = has_failed or kind == "failed"
        depth = depths.get(name, other_depths.get(name, 0))
        found.append(StepDifference(name, depth, mine.get(name), theirs.get(name), kind, words))
    return found


def _flatten(value: Any, prefix: str = "", depth: int = 0) -> dict[str, Any]:
    """
    A JSON object's fields by dotted path, INPUT_DEPTH deep (an array or a deeper object is one value).
    """
    if not isinstance(value, dict) or depth >= INPUT_DEPTH:
        return {prefix: value} if prefix else {}
    fields: dict[str, Any] = {}
    for key, inner in value.items():
        path = f"{prefix}.{key}" if prefix else str(key)
        if isinstance(inner, dict) and inner:
            fields.update(_flatten(inner, path, depth + 1))
        else:
            fields[path] = inner
    return fields


def _shown(value: Any) -> str:
    return _clip(json.dumps(value, default=str), _VALUE_CHARS)


def _parsed(text: str) -> Any:
    try:
        return json.loads(text or "null")
    except (TypeError, ValueError):
        return None


def compare_inputs(this_input: str, other_input: str) -> list[InputDifference]:
    """
    The input fields that differ between two executions (JSON objects, compared by field, not by value: values
    differ from run to run): missing here, extra here, empty here but set there, or of another type.
    """
    mine, theirs = _flatten(_parsed(this_input)), _flatten(_parsed(other_input))
    found = []
    for path in sorted(set(mine) | set(theirs)):
        if path not in mine:
            found.append(InputDifference(path, "missing", "", _shown(theirs[path])))
        elif path not in theirs:
            found.append(InputDifference(path, "extra", _shown(mine[path]), ""))
        elif mine[path] in _EMPTY_VALUES and theirs[path] not in _EMPTY_VALUES:
            found.append(InputDifference(path, "empty", _shown(mine[path]), _shown(theirs[path])))
        elif theirs[path] not in _EMPTY_VALUES and type(mine[path]) is not type(theirs[path]):
            found.append(InputDifference(path, "type", _shown(mine[path]), _shown(theirs[path])))
    return found


def compare_executions(this: ExecutionDetail, other: ExecutionDetail) -> ExecutionComparison:
    """
    Two executions side by side: their states and their inputs' fields, and whether the other started first.
    """
    is_before = this.execution.start is None or other.execution.start is None or (
        other.execution.start <= this.execution.start)
    return ExecutionComparison(this, other, compare_steps(this, other),
                               compare_inputs(this.execution.input, other.execution.input), is_before)


def _error_text(comparison: ExecutionComparison) -> str:
    """
    Everything this execution said went wrong, to look for a field's name in.
    """
    detail = comparison.this
    step = detail.failed_step
    parts = [detail.execution.error, detail.execution.cause]
    if step is not None:
        parts += [step.error, step.cause]
    return " ".join(part for part in parts if part)


def _named_in_error(comparison: ExecutionComparison) -> list[InputDifference]:
    """
    The fields missing or empty in this execution's input whose name its error mentions: the likely cause.
    """
    said = _error_text(comparison)
    absent = [d for d in comparison.inputs if d.kind in ("missing", "empty")]
    return [d for d in absent if d.path.rsplit(".", 1)[-1] in said]


def _cause_finding(comparison: ExecutionComparison, difference: InputDifference) -> Finding:
    this, other = comparison.this.execution.name, comparison.other.execution.name
    step = comparison.this.failed_step
    where = f"{step.name} failed with {step.error}" if step is not None and step.error else "it failed"
    how = "has no" if difference.kind == "missing" else "has an empty"
    return ("warn", f"{this}'s input {how} {difference.path}, which {other}'s had ({difference.other}): that's the "
                    f"likely cause, since {where}, naming it. Find what sent this input without it, or make the state "
                    f"handle a missing {difference.path.rsplit('.', 1)[-1]}.")


def _failed_finding(comparison: ExecutionComparison, step: StepDifference) -> Finding:
    other = comparison.other
    there = f" in {_duration(step.other.seconds)}" if step.other is not None else ""
    return ("warn", f"{step.state} failed in {comparison.this.execution.name} with {step.this.error}, and went "
                    f"through in {other.execution.name}{there}: compare what each was given there.")


def _ran_differently(comparison: ExecutionComparison, step: StepDifference) -> str:
    """
    A state that took more tries or much longer here, as a sentence naming both executions.
    """
    this, other = comparison.this.execution.name, comparison.other.execution.name
    if step.kind == "retried":
        return (f"{step.state} took {step.this.retries + 1} tries in {this} and {step.other.retries + 1} in {other}: "
                "it was retried more, so its errors were the kind a Retry catches.")
    return (f"{step.state} took {_duration(step.this.seconds)} in {this} and {_duration(step.other.seconds)} in "
            f"{other}: the slowdown may be part of what went wrong.")


def comparison_findings(comparison: ExecutionComparison) -> list[Finding]:
    """
    What the differences say: a missing field the error names (the likely cause), the state that failed only here,
    states only this one went through (another branch), more retries, much slower states, and which execution it was
    compared with.
    """
    found: list[Finding] = [_cause_finding(comparison, d) for d in _named_in_error(comparison)]
    if not found:
        found += [_failed_finding(comparison, step) for step in comparison.steps if step.kind == "failed"]
    only_here = [step.state for step in comparison.steps if step.kind == "only_here"]
    if only_here:
        found.append(("info", f"Only {comparison.this.execution.name} went through {', '.join(only_here)}: the two "
                              "took different branches, so their inputs led somewhere else."))
    for step in comparison.steps:
        if step.kind in ("retried", "slower"):
            found.append(("info", _ran_differently(comparison, step)))
    other = comparison.other.execution
    if not comparison.other_is_before:
        found.append(("info", f"No execution succeeded before {comparison.this.execution.name} in the time read, so "
                              f"it's compared with {other.name}, which started after it."))
    elif not comparison.differing_steps and not comparison.inputs:
        word = STATUS_WORDS.get(other.status, other.status.lower())
        found.append(("info", f"{comparison.this.execution.name} and {other.name} ({word}) ran the same states with "
                              f"the same input fields: the difference is in the values, or in a service they call. "
                              f"{_plural(len(comparison.steps), 'state')} compared."))
    return found
