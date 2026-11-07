"""
SageMaker Pipelines: a run's definition read into its steps and what each waits for, its steps' outcomes merged
in, the run drawn as a graph (steps in layers by the longest way from a first step) and as a waterfall on one time
axis, and the findings of pipelines() and pipeline_run().
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from ..._kit.fmt import _duration, _plural, _utcnow

from ..models import PipelineRun, PipelineRunSummary, PipelinesReport, PipelineStep
from .common import _couldnt_read
from .training import TRAINING_LOG_GROUP, short_reason


STEP_WIDTH = 188

STEP_HEIGHT = 46

STEP_GAP_X = 30

STEP_GAP_Y = 46

GRAPH_MARGIN = 20

_FAILED = ("Failed",)

_SLOW_SHARE = 0.5

_STEP_FAILURE = re.compile(r"Step failure: One or multiple steps failed\.\s*([\w-]+):\s*(.*)", re.S)

_JOB_KINDS = {
    "TrainingJob": "training job",
    "ProcessingJob": "processing job",
    "TransformJob": "transform job",
    "TuningJob": "tuning job",
    "AutoMLJob": "AutoML job",
    "Lambda": "Lambda function",
    "EMR": "EMR step",
    "Model": "model",
    "RegisterModel": "model package",
}

_LOG_GROUPS = {
    "processing job": "/aws/sagemaker/ProcessingJobs",
    "transform job": "/aws/sagemaker/TransformJobs",
    "training job": TRAINING_LOG_GROUP,
}


def _references(value: Any) -> set[str]:
    """
    The steps a definition's value reads from: every {"Get": "Steps.<name>...."} in it, however deep.
    """
    found: set[str] = set()
    if isinstance(value, dict):
        target = value.get("Get")
        if isinstance(target, str) and target.startswith("Steps."):
            found.add(target.split(".")[1])
        for inner in value.values():
            found |= _references(inner)
    elif isinstance(value, list):
        for inner in value:
            found |= _references(inner)
    return found


def _defined(spec: dict[str, Any], branch: tuple[str, str], steps: list[PipelineStep]) -> None:
    arguments = spec.get("Arguments") or {}
    branches = {side: arguments.get(key) or [] for side, key in (("if", "IfSteps"), ("else", "ElseSteps"))}
    plain = {key: value for key, value in arguments.items() if key not in ("IfSteps", "ElseSteps")}
    name = spec.get("Name", "")
    waits = list(dict.fromkeys([*(spec.get("DependsOn") or []), *sorted(_references(plain) - {name})]))
    steps.append(PipelineStep(name, type=spec.get("Type", ""), depends_on=waits, branch=branch))
    for side, inner in branches.items():
        for child in inner:
            _defined(child, (name, side), steps)


def parse_definition(definition: str | dict[str, Any]) -> list[PipelineStep]:
    """
    A pipeline definition (its JSON) -> its steps in order, each with its type, the steps it waits for (DependsOn
    and the steps whose properties it reads) and, for a condition's if or else steps, the branch it's on.
    """
    spec = json.loads(definition) if isinstance(definition, str) else definition
    steps: list[PipelineStep] = []
    for item in spec.get("Steps") or []:
        _defined(item, ("", ""), steps)
    return steps


def _job_of(metadata: dict[str, Any]) -> tuple[str, str]:
    """
    (kind, name) of the job a step started, from its metadata's ARN: ('training job', 'train-0517').
    """
    for key, kind in _JOB_KINDS.items():
        arn = (metadata.get(key) or {}).get("Arn", "")
        if arn:
            return kind, arn.rsplit("/", 1)[-1].rsplit(":", 1)[-1]
    return "", ""


def parse_pipeline_step(item: dict[str, Any]) -> PipelineStep:
    """
    A ListPipelineExecutionSteps item -> PipelineStep (its type unknown until merged with the definition).
    """
    return PipelineStep(
        item.get("StepName", ""),
        status=item.get("StepStatus", ""),
        start=item.get("StartTime"),
        end=item.get("EndTime"),
        failure=item.get("FailureReason", ""),
        attempts=item.get("AttemptCount") or 1,
        cached=bool(item.get("CacheHitResult")),
        job=_job_of(item.get("Metadata") or {}),
    )


def parse_run_summary(item: dict[str, Any]) -> PipelineRunSummary:
    """
    A ListPipelineExecutions item -> PipelineRunSummary.
    """
    arn = item.get("PipelineExecutionArn", "")
    return PipelineRunSummary(arn, item.get("PipelineExecutionDisplayName") or arn.rsplit("/", 1)[-1],
                              item.get("PipelineExecutionStatus", ""), item.get("StartTime"),
                              item.get("PipelineExecutionFailureReason", ""))


def _start_order(step: PipelineStep) -> tuple[bool, float]:
    return step.start is None, step.start.timestamp() if step.start else 0.0


def merge_steps(defined: list[PipelineStep], ran: list[PipelineStep]) -> list[PipelineStep]:
    """
    The definition's steps with how each went (a step that ran more than once keeps its latest try); steps that
    ran but aren't in the definition go at the end.
    """
    latest: dict[str, PipelineStep] = {}
    for step in ran:
        known = latest.get(step.name)
        if known is None or (step.start and known.start and step.start > known.start):
            latest[step.name] = step
    merged = []
    for step in defined:
        outcome = latest.pop(step.name, None)
        if outcome is not None:
            outcome.type, outcome.depends_on, outcome.branch = step.type, step.depends_on, step.branch
            merged.append(outcome)
        else:
            merged.append(step)
    return merged + sorted(latest.values(), key=_start_order)


@dataclass
class StepNode:
    """
    A step in the drawing: its box's top-left corner and size, its layer and place in it.
    """

    step: PipelineStep
    layer: int
    column: int = 0
    x: float = 0.0
    y: float = 0.0

    @property
    def middle(self) -> float:
        return self.x + STEP_WIDTH / 2


@dataclass
class StepEdge:
    """
    An arrow from the step that has to finish first to the step that waits for it; `label` is 'if' / 'else' for
    a condition's branches.
    """

    source: str
    target: str
    label: str = ""


@dataclass
class StepLayout:
    """
    A pipeline run as a graph: its boxes, its arrows and its size.
    """

    nodes: list[StepNode] = field(default_factory=list)
    edges: list[StepEdge] = field(default_factory=list)
    width: float = 0.0
    height: float = 0.0


def _edges(steps: list[PipelineStep]) -> list[StepEdge]:
    names = {step.name for step in steps}
    edges = [StepEdge(parent, step.name) for step in steps for parent in step.depends_on if parent in names]
    edges += [StepEdge(step.branch[0], step.name, step.branch[1]) for step in steps
              if step.branch[0] in names and not step.depends_on]
    return edges


def _layers(steps: list[PipelineStep], edges: list[StepEdge]) -> dict[str, int]:
    """
    Each step's layer: one more than the deepest step it waits for (a pipeline has no loops; a cycle is cut).
    """
    parents: dict[str, list[str]] = {step.name: [] for step in steps}
    for edge in edges:
        parents[edge.target].append(edge.source)
    layer: dict[str, int] = {}

    def depth(name: str, seen: frozenset[str]) -> int:
        if name in layer:
            return layer[name]
        above = [depth(p, seen | {name}) for p in parents.get(name, []) if p not in seen]
        layer[name] = 1 + max(above) if above else 0
        return layer[name]

    for step in steps:
        depth(step.name, frozenset())
    return layer


def pipeline_layout(steps: list[PipelineStep]) -> StepLayout:
    """
    Steps -> StepLayout: in layers from the first steps down, each layer ordered under the steps it waits for and
    centred, an arrow from each step to the ones waiting for it.
    """
    if not steps:
        return StepLayout()
    edges = _edges(steps)
    layer = _layers(steps, edges)
    rows: dict[int, list[StepNode]] = {}
    for step in steps:
        rows.setdefault(layer[step.name], []).append(StepNode(step, layer[step.name]))
    placed: dict[str, StepNode] = {}
    for depth in sorted(rows):
        row = rows[depth]
        for node in row:
            above = [placed[e.source].column for e in edges if e.target == node.step.name and e.source in placed]
            node.column = round(sum(above) / len(above) * 100) if above else 0
        row.sort(key=lambda n: n.column)
        for i, node in enumerate(row):
            node.column = i
            placed[node.step.name] = node
    widest = max(len(row) for row in rows.values())
    width = 2 * GRAPH_MARGIN + widest * STEP_WIDTH + (widest - 1) * STEP_GAP_X
    for depth, row in rows.items():
        used = len(row) * STEP_WIDTH + (len(row) - 1) * STEP_GAP_X
        left = (width - used) / 2
        for node in row:
            node.x = left + node.column * (STEP_WIDTH + STEP_GAP_X)
            node.y = GRAPH_MARGIN + depth * (STEP_HEIGHT + STEP_GAP_Y)
    height = 2 * GRAPH_MARGIN + len(rows) * STEP_HEIGHT + (len(rows) - 1) * STEP_GAP_Y
    nodes = [placed[step.name] for step in steps]
    return StepLayout(nodes, edges, width, height)


@dataclass
class StepBar:
    """
    A step's bar on the run's time axis: where it starts and how wide it is (0..1 of the run), its status, and
    its label.
    """

    name: str
    type: str
    status: str
    start: float
    width: float
    label: str = ""


def step_waterfall(run: PipelineRun, now: datetime | None = None) -> tuple[list[StepBar], float]:
    """
    The steps that ran, in the order they started, as bars on the run's time axis -> (bars, the axis's seconds).
    """
    now = now or _utcnow()
    ran = sorted((s for s in run.steps if s.start), key=lambda s: s.start)
    if not ran:
        return [], 0.0
    begin = min([run.started or ran[0].start, *[s.start for s in ran]])
    finish = max([(s.end or now) for s in ran])
    seconds = max((finish - begin).total_seconds(), 1.0)
    bars = []
    for step in ran:
        offset = (step.start - begin).total_seconds() / seconds
        span = (step.seconds(now) or 0) / seconds
        label = "cached" if step.cached else _duration(step.seconds(now) or 0)
        status = "cached" if step.cached else step.status.lower()
        bars.append(StepBar(step.name, step.type, status, offset, span, label))
    return bars, seconds


def failed_steps(run: PipelineRun) -> list[PipelineStep]:
    return [step for step in run.steps if step.status in _FAILED]


def step_next(step: PipelineStep) -> str:
    """
    What to run to see why a step's job failed: training_job(...) for a training step, the AWS CLI command that
    tails its log for a processing or transform job; '' for other steps.
    """
    kind, name = step.job
    if not name:
        return ""
    if kind == "training job":
        return f"training_job('{name}')"
    group = _LOG_GROUPS.get(kind)
    if group:
        return f"aws logs tail {group} --log-stream-name-prefix {name} --since 30d"
    if kind == "Lambda function":
        return f"aws logs tail /aws/lambda/{name} --since 1d"
    return ""


def run_reason(reason: str) -> str:
    """
    What a pipeline run's FailureReason says, without SageMaker's lead-in: 'Step failure: One or multiple steps
    failed. TrainModel: AlgorithmError: ...' -> 'step TrainModel failed (AlgorithmError: ...)'.
    """
    found = _STEP_FAILURE.match(reason or "")
    if not found:
        return short_reason(reason).rstrip(".")
    step, said = found.group(1), short_reason(found.group(2)).rstrip(".:")
    return f"step {step} failed ({said})" if said else f"step {step} failed"


def _failed_finding(step: PipelineStep) -> tuple[str, str]:
    reason = short_reason(step.failure).rstrip(".") or "no reason given"
    kind, name = step.job
    started = f" ({kind} {name})" if name else ""
    look = step_next(step)
    after = f" See why: {look}" if look else ""
    tries = f" after {step.attempts} tries" if step.attempts > 1 else ""
    return ("warn", f"Step {step.name}{started} failed{tries}: {reason}.{after}")


def pipeline_findings(run: PipelineRun, now: datetime | None = None) -> list[tuple[str, str]]:
    """
    Plain-language notes about one pipeline run: the step that broke and why, with the call that shows its job's
    log; the steps it never reached; steps retried; the step that took most of the time; what couldn't be read
    -> [(level, message)].
    """
    now = now or _utcnow()
    found: list[tuple[str, str]] = []
    broken = failed_steps(run)
    found += [_failed_finding(step) for step in broken]
    if run.status == "Failed" and not broken and run.failure:
        found.append(("warn", f"The run failed: {short_reason(run.failure).rstrip('.')}."))
    never = [step.name for step in run.steps if not step.status]
    if broken and never:
        found.append(("info", (f"{_plural(len(never), 'step')} never ran because of it ({', '.join(never[:6])}"
                               f"{', …' if len(never) > 6 else ''}). Once it's fixed, retry the run from the failed "
                               "step instead of from the start: aws sagemaker retry-pipeline-execution "
                               f"--pipeline-execution-arn {run.arn}")))
    for step in run.steps:
        if step.attempts > 1 and step.status not in _FAILED:
            found.append(("info", f"Step {step.name} needed {step.attempts} tries; its retry policy covered it."))
    total = (run.seconds(now) or 0) if run.is_finished else 0
    slowest = max((s for s in run.steps if s.start and not s.cached), key=lambda s: s.seconds(now) or 0, default=None)
    if slowest is not None and total and (slowest.seconds(now) or 0) / total >= _SLOW_SHARE and len(run.steps) > 1:
        found.append(("info", (f"Step {slowest.name} took {_duration(slowest.seconds(now) or 0)}, "
                               f"{(slowest.seconds(now) or 0) / total:.0%} of the run: it's where a faster run "
                               "would come from.")))
    cached = [step.name for step in run.steps if step.cached]
    if cached:
        found.append(("info", (f"{_plural(len(cached), 'step')} reused an earlier run's result (step caching): "
                               f"{', '.join(cached[:6])}.")))
    if run.errors:
        found.append(("info", _couldnt_read(run.errors, {"definition": "the graph shows no arrows"})))
    return found


def pipelines_findings(report: PipelinesReport) -> list[tuple[str, str]]:
    """
    Plain-language notes about the region's pipelines: those whose latest run failed, and those that failed
    several times in a row, each with the call that shows the run -> [(level, message)].
    """
    found: list[tuple[str, str]] = []
    for pipeline in report.pipelines:
        if not pipeline.runs:
            continue
        latest = pipeline.runs[0]
        if latest.status != "Failed":
            continue
        streak = 0
        for run in pipeline.runs:
            if run.status != "Failed":
                break
            streak += 1
        again = f" (its last {streak} runs all failed)" if streak > 1 else ""
        reason = run_reason(latest.failure)
        said = f": {reason}" if reason else ""
        found.append(("warn", (f"Pipeline {pipeline.name}'s latest run failed{again}{said}. See which step broke: "
                               f"pipeline_run('{pipeline.name}')")))
    if report.errors:
        found.append(("info", _couldnt_read(report.errors)))
    return found
