"""
SageMakerView's commands about what people launch from a notebook: training jobs and why they failed, endpoints
and which are idle, and pipelines and where a run broke. A mixin, so view.py keeps the notebook's commands.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from ..._kit.blocks import _Cards, _Findings, _Next, _Note, _Table, _Text, _Title, _Tone
from ..._kit.fmt import _as_int, _duration, _fmt_dt, _tz_label, _utcnow, human_age, human_money
from ..._kit.render import _call
from ..._kit.view import _friendly_errors

from ..blocks import _PipelineGraph, _StepWaterfall
from ..helpers import human_runtime
from ..models import EndpointReport, HostLog, PipelineRun, TrainingDetail, TrainingJob, TrainingList
from ..analysis.endpoints import endpoint_findings, endpoint_monthly, is_idle, is_stale
from ..analysis.pipelines import (
    failed_steps, pipeline_findings, pipeline_layout, pipelines_findings, run_reason, step_next, step_waterfall,
)
from ..analysis.training import (
    LOG_LINES, TRAINING_LOG_GROUP, failure_kind, short_reason, training_findings, training_list_findings,
)


_SHOWN_LINES = 60

_RECENT_DAYS = 7

_REASON_CHARS = 90

_GOOD = ("Completed", "Succeeded", "InService")

_BAD = ("Failed",)

_RUN_MARKS = {"Succeeded": "✓", "Failed": "✗", "Executing": "▶", "Stopped": "■", "Stopping": "■",
              "CompletedWithFailures": "!"}


def _status_cell(status: str) -> Any:
    if status in _GOOD:
        return _Tone(status, "ok")
    if status in _BAD:
        return _Tone(status, "bad")
    if status in ("Stopped", "Stopping", "CompletedWithFailures"):
        return _Tone(status, "warn")
    return status or "-"


def _short(text: str, chars: int = _REASON_CHARS) -> str:
    return text if len(text) <= chars else text[:chars - 1].rstrip() + "…"


def _job_row(job: TrainingJob, prices: dict[str, float], now: datetime) -> list[Any]:
    cost = job.cost(prices, now)
    kind = failure_kind(job)
    reason = short_reason(job.failure)
    why = f"{kind}: {reason}" if kind and kind != "script error" else reason
    return [job.name, _status_cell(job.status), _fmt_dt(job.created), human_runtime(job.seconds(now)),
            job.instance_label + (" (spot)" if job.spot else ""), human_money(cost) if cost is not None else "?",
            _short(why) or "-"]


def _log_text(host: HostLog) -> str:
    shown = host.lines[-_SHOWN_LINES:]
    return "\n".join(f"{_fmt_dt(line.when, seconds=True)}  {line.message}" for line in shown)


def _host_title(host: HostLog) -> str:
    span = f"{_fmt_dt(host.first)} to {_fmt_dt(host.last)}" if host.first else "no lines"
    more = f" (its last {min(len(host.lines), _SHOWN_LINES):,} lines)" if len(host.lines) > _SHOWN_LINES else ""
    return f"{host.host}'s log, {span}{more}"


class _JobCommands:
    """
    SageMakerView's commands about training jobs, endpoints and pipelines (it gives core, _show, _progress and
    _price_basis).
    """

    core: Any
    _show: Any
    _progress: Any
    _price_basis: Any

    @_friendly_errors
    def training_jobs(self, since: str | None = "7d", *, status: str | None = None, limit: int = 100) -> None:
        """
        The region's training jobs made in the last 7 days, newest first: how each ended, how long it ran, what it
        cost, and why each failed one failed in a few words.

        since= takes '24h', '30d', a date or None for all; status= keeps one status ('Failed', 'InProgress',
        'Completed', 'Stopped'); the read stops at `limit` jobs.
        """
        limit = _as_int(limit, "limit")
        with self._progress("Reading", unit="training jobs") as tick:
            report: TrainingList = self.core.training_jobs(since=since, status=status or "", limit=limit,
                                                           progress=tick)
        prices, now = self.core.prices, _utcnow()
        findings = training_list_findings(report, prices, now)
        jobs = report.jobs
        failed = [job for job in jobs if job.status == "Failed"]
        running = [job for job in jobs if job.status == "InProgress"]
        costs = [job.cost(prices, now) for job in jobs]
        known = sum(cost for cost in costs if cost is not None)
        plus = "+" if any(cost is None for cost in costs) else ""
        window = f"since {_fmt_dt(report.since, zone=True)}" if report.since else "all time"
        blocks: list[Any] = [
            _Title(f"Training jobs in {report.region} ({len(jobs):,}{'+' if report.cut_short else ''})",
                   f"made {window} · cost at {self._price_basis()}, an estimate from billable time"),
            _Cards([("Jobs", f"{len(jobs):,}"), ("Failed", f"{len(failed):,}", "bad" if failed else "ok"),
                    ("Running now", f"{len(running):,}"), ("Est. cost", human_money(known) + plus)]),
            _Findings(findings, empty="No job failed."),
        ]
        if not jobs and not report.errors:
            blocks.append(_Note(f"No training jobs were made in {report.region} {window}. training_jobs(since=None) "
                                "looks at every job; SageMaker is regional."))
        if jobs:
            headers = ["Training job", "Status", f"Created ({_tz_label()})", "Ran for", "Instances", "Est. cost",
                       "Why it failed"]
            blocks.append(_Table(headers, [_job_row(job, prices, now) for job in jobs]))
        steps = []
        pick = (failed or jobs or [None])[0]
        if pick is not None:
            steps.append((_call("training_job", pick.name), "why it failed and its log" if pick in failed
                          else "its settings, timeline and log"))
        if since is not None and not status:
            steps.append((_call("training_jobs", "30d", status="Failed"), "every failure in the last 30 days"))
        steps.append(("pipelines()", "the pipelines that run these jobs, and where their runs broke"))
        blocks.append(_Next(steps))
        self._show(blocks)

    def _training_cards(self, job: TrainingJob, now: datetime) -> list[tuple[str, ...]]:
        prices = self.core.prices
        cost = job.cost(prices, now)
        billed = human_runtime(job.billable_seconds) if job.billable_seconds is not None else "-"
        tone = "bad" if job.status == "Failed" else "ok" if job.status == "Completed" else ""
        status = ("Status", job.status + (f" ({job.secondary})" if job.secondary not in ("", job.status) else ""))
        return [status + ((tone,) if tone else ()), ("Ran for", human_runtime(job.seconds(now))),
                ("Billable time", billed + (" (spot)" if job.spot else "")),
                ("Est. cost", human_money(cost) if cost is not None else "?"), ("Instances", job.instance_label)]

    @staticmethod
    def _timeline(job: TrainingJob, now: datetime) -> list[Any]:
        if not job.transitions:
            return []
        rows = [[t.status, _fmt_dt(t.start, seconds=True), human_runtime(t.seconds(now)), t.message or "-"]
                for t in job.transitions]
        return [_Table(["Step", f"Started ({_tz_label()})", "Took", "What SageMaker said"], rows,
                       title="What the job went through", sortable=False)]

    @staticmethod
    def _host_blocks(detail: TrainingDetail) -> list[Any]:
        blocks: list[Any] = []
        hosts = detail.hosts
        failing = next((host for host in hosts if host.error), None)
        if failing is not None:
            blocks.append(_Text("\n".join(failing.error), title=f"The error in {failing.host}'s log"))
        if len(hosts) > 1:
            rows = [[host.host, f"{len(host.lines):,}{'+' if host.cut_short else ''}", _fmt_dt(host.first),
                     _fmt_dt(host.last), _short(host.error[-1]) if host.error else "-"] for host in hosts]
            blocks.append(_Table(["Host", "Lines read", f"From ({_tz_label()})", f"To ({_tz_label()})", "Error"],
                                 rows, title="Each instance's log"))
        for host in hosts:
            blocks.append(_Text(_log_text(host), title=_host_title(host), collapsed=host is not failing))
        return blocks

    @staticmethod
    def _settings_blocks(job: TrainingJob) -> list[Any]:
        blocks: list[Any] = []
        if job.metrics:
            blocks.append(_Table(["Metric", "Final value"], [[name, f"{value:,.4g}"] for name, value in job.metrics],
                                 title="Final metrics"))
        rows = [[f"input: {channel}", uri] for channel, uri in job.inputs]
        rows += [[label, value] for label, value in (
            ("output", job.output), ("model", job.model_artifact), ("checkpoints", job.checkpoints),
            ("image", job.image or job.algorithm), ("input mode", job.input_mode),
            ("disk", f"{job.volume_gb} GB" if job.volume_gb else ""),
            ("time limit", human_runtime(job.max_runtime) if job.max_runtime else ""),
            ("tuning job", job.tuning_job)) if value]
        if rows:
            blocks.append(_Table(["Setting", "Value"], rows, title="Data and settings", collapsed=True,
                                 sortable=False))
        if job.hyperparameters:
            rows = [[key, value] for key, value in sorted(job.hyperparameters.items())]
            blocks.append(_Table(["Hyperparameter", "Value"], rows, title="Hyperparameters", collapsed=True))
        return blocks

    @_friendly_errors
    def training_job(self, name: str, *, lines: int = LOG_LINES) -> None:
        """
        One training job: why it failed in plain words and what to change, the error in its log, what it went
        through (starting, downloading, training, uploading) and how long each took, what it cost, and each
        instance's log.

        The log is the last `lines` lines of each instance's stream in CloudWatch Logs.
        """
        lines = _as_int(lines, "lines")
        with self._progress("Reading", unit="parts") as tick:
            detail: TrainingDetail = self.core.training_job(name, lines=lines)
            tick(1, 1)
        job, now = detail.job, _utcnow()
        findings = training_findings(detail, self.core.prices, now)
        blocks: list[Any] = [
            _Title(f"Training job {job.name}", f"made {_fmt_dt(job.created, zone=True)} · cost at "
                   f"{self._price_basis()} · logs from {TRAINING_LOG_GROUP}"),
            _Cards(self._training_cards(job, now)),
            _Findings(findings, empty="It finished without a problem."),
        ]
        blocks += self._host_blocks(detail)
        blocks += self._timeline(job, now)
        blocks += self._settings_blocks(job)
        steps = []
        if any(host.cut_short for host in detail.hosts):
            steps.append((_call("training_job", job.name, lines=lines * 5), "more of each log"))
        steps.append((_call("training_jobs", "30d", status="Failed"), "the other failures in the last 30 days"))
        if job.status == "Completed":
            steps.append(("endpoints()", "the endpoints in the region, and which ones nobody calls"))
        blocks.append(_Next(steps))
        self._show(blocks)

    def _endpoint_row(self, use: Any, report: EndpointReport, now: datetime) -> list[Any]:
        b = use.endpoint
        monthly = endpoint_monthly(use, self.core.prices)
        price = "per request" if b.serverless else human_money(monthly) if monthly is not None else "?"
        if b.serverless:
            requests: Any = "-"
        elif use.requests is None:
            requests = "?"
        else:
            requests = f"{use.requests:,.0f}"
            requests = _Tone(requests, "warn") if is_idle(use, now) else requests
        last = human_age(use.last_called, now) if use.last_called else "-"
        last = _Tone(last, "warn") if is_stale(use, now) else last
        errors = "-" if use.server_errors is None else f"{use.server_errors:,.0f}"
        return [b.name, _status_cell(b.status), b.instance_label, price, requests, last, errors, human_age(b.since, now)]

    @_friendly_errors
    def endpoints(self, *, days: int = 30) -> None:
        """
        Every endpoint in the region with what it costs a month and how often it was called in the last 30 days,
        the idle ones first: the endpoints nobody calls, what each costs, and the command that deletes it.

        Traffic is CloudWatch's Invocations and 5xx errors, summed per UTC day; days= sets the window.
        """
        days = _as_int(days, "days")
        with self._progress("Reading", unit="steps") as tick:
            report: EndpointReport = self.core.endpoints(days=days, progress=tick)
        prices, now = self.core.prices, _utcnow()
        findings = endpoint_findings(report, prices, now)
        uses = sorted(report.endpoints, key=lambda u: (not is_idle(u, now), -(endpoint_monthly(u, prices) or 0)))
        idle = [use for use in uses if is_idle(use, now)]
        total = sum(endpoint_monthly(use, prices) or 0 for use in uses)
        wasted = sum(endpoint_monthly(use, prices) or 0 for use in idle)
        blocks: list[Any] = [
            _Title(f"Endpoints in {report.region} ({len(uses):,})",
                   f"requests from CloudWatch, the last {days} days in UTC days · cost at {self._price_basis()}, "
                   "if left on · this tool never deletes anything"),
            _Cards([("Endpoints", f"{len(uses):,}"), ("Idle", f"{len(idle):,}", "warn" if idle else "ok"),
                    ("Est. / month", human_money(total)),
                    ("Idle, / month", human_money(wasted), "warn" if wasted else "ok")]),
            _Findings(findings, empty="Every endpoint is being called."),
        ]
        if not uses and not report.errors:
            blocks.append(_Note(f"There are no endpoints in service in {report.region}. SageMaker is regional: "
                                "SageMakerView(SageMakerAnalyzer(region='us-west-2')) checks another region."))
        if uses:
            headers = ["Endpoint", "Status", "Instances", "Est. $/month", f"Requests, {days}d", "Last request",
                       f"5xx, {days}d", "Made"]
            blocks.append(_Table(headers, [self._endpoint_row(use, report, now) for use in uses]))
        steps = []
        if days < 90:
            steps.append((_call("endpoints", days=90), "traffic over the last 90 days"))
        steps.append(("running()", "everything else billing by the hour: notebooks, apps and jobs"))
        blocks.append(_Next(steps))
        self._show(blocks)

    @_friendly_errors
    def pipelines(self, *, runs: int = 5) -> None:
        """
        The region's pipelines, the most recently run first: how their latest runs went, and which ones are
        failing, with the call that shows where a run broke.
        """
        runs = _as_int(runs, "runs")
        with self._progress("Reading", unit="pipelines") as tick:
            report = self.core.pipelines(runs=runs, progress=tick)
        findings = pipelines_findings(report)
        failing = [p for p in report.pipelines if p.runs and p.runs[0].status == "Failed"]
        now = _utcnow()
        recent = [p for p in report.pipelines if p.last_run and (now - p.last_run).days < _RECENT_DAYS]
        blocks: list[Any] = [
            _Title(f"Pipelines in {report.region} ({len(report.pipelines):,})", f"each with its last {runs} runs"),
            _Cards([("Pipelines", f"{len(report.pipelines):,}"),
                    ("Latest run failed", f"{len(failing):,}", "bad" if failing else "ok"),
                    ("Ran in the last 7 days", f"{len(recent):,}")]),
            _Findings(findings, empty="Every pipeline's latest run went through."),
        ]
        if not report.pipelines and not report.errors:
            blocks.append(_Note(f"There are no SageMaker pipelines in {report.region}."))
        rows = []
        for p in report.pipelines:
            latest = p.runs[0] if p.runs else None
            marks = " ".join(_RUN_MARKS.get(r.status, "?") for r in p.runs) or "-"
            rows.append([p.name, latest.name if latest else "-", _status_cell(latest.status) if latest else "never ran",
                         human_age(latest.started, now) if latest else "-", marks,
                         _short(run_reason(latest.failure)) if latest and latest.failure else "-"])
        if rows:
            blocks.append(_Table(["Pipeline", "Latest run", "Status", "Started", "Recent runs (newest first)",
                                  "Why it failed"], rows))
        pick = (failing or report.pipelines or [None])[0]
        steps = [(_call("pipeline_run", pick.name), "where its latest run broke" if pick in failing
                  else "its latest run, step by step")] if pick is not None else []
        steps.append(("training_jobs()", "the training jobs of the last 7 days, and why any failed"))
        blocks.append(_Next(steps))
        self._show(blocks)

    @staticmethod
    def _step_rows(run: PipelineRun, now: datetime) -> list[list[Any]]:
        rows = []
        for step in run.steps:
            seconds = step.seconds(now)
            took = "cached" if step.cached else _duration(seconds) if seconds is not None else "-"
            job = f"{step.job[0]} {step.job[1]}" if step.job[1] else "-"
            rows.append([step.name, step.type or "?", _status_cell(step.status) if step.status else "never ran",
                         _fmt_dt(step.start, seconds=True), took, job, _short(short_reason(step.failure)) or "-"])
        return rows

    def _run_cards(self, run: PipelineRun, now: datetime) -> list[tuple[str, ...]]:
        broken = failed_steps(run)
        tone = "bad" if run.status == "Failed" else "ok" if run.status == "Succeeded" else ""
        status = ("Status", run.status or "?") + ((tone,) if tone else ())
        ran = sum(1 for step in run.steps if step.status)
        took = "Took" if run.is_finished else "Running for"
        cards = [status, (took, _duration(run.seconds(now) or 0) if run.started else "-"),
                 ("Steps run", f"{ran:,} of {len(run.steps):,}")]
        if broken:
            cards.append(("Failed step", broken[0].name, "bad"))
        return cards

    @_friendly_errors
    def pipeline_run(self, pipeline: str, run: str | int | None = None) -> None:
        """
        One run of a pipeline (the latest unless run= names one): the step that broke and why, with the call that
        shows its job's log, the steps drawn as a graph coloured by how each went, and a waterfall of when each ran.

        run= takes 1 for the run before the latest, 2 for the one before that, or a run's ARN or display name.
        """
        with self._progress("Reading", unit="parts") as tick:
            result: PipelineRun = self.core.pipeline_run(pipeline, run)
            tick(1, 1)
        now = _utcnow()
        findings = pipeline_findings(result, now)
        bars, seconds = step_waterfall(result, now)
        notes = {step.name: f"{step.attempts} tries" for step in result.steps if step.attempts > 1}
        blocks: list[Any] = [
            _Title(f"Pipeline {result.pipeline}: run {result.name or result.arn.rsplit('/', 1)[-1]}",
                   f"started {_fmt_dt(result.started, zone=True)}"),
            _Cards(self._run_cards(result, now)),
            _Findings(findings, empty="Every step went through." if result.is_finished
                      else "Nothing has gone wrong so far."),
        ]
        if result.steps:
            blocks.append(_PipelineGraph(pipeline_layout(result.steps), title="Its steps", notes=notes))
            blocks.append(_StepWaterfall(bars, seconds, title="When each step ran"))
            blocks.append(_Table(["Step", "Type", "Status", f"Started ({_tz_label()})", "Took", "Job",
                                  "Why it failed"], self._step_rows(result, now), sortable=False))
        others = [[i, r.name, _status_cell(r.status), _fmt_dt(r.started)] for i, r in enumerate(result.runs[:10])]
        if len(others) > 1:
            blocks.append(_Table(["run=", "Run", "Status", f"Started ({_tz_label()})"], others,
                                 title="Its recent runs", collapsed=True, sortable=False))
        steps = []
        broken = failed_steps(result)
        look = step_next(broken[0]) if broken else ""
        if look.startswith("training_job("):
            steps.append((look, "why that training job failed, and its log"))
        if len(result.runs) > 1 and result.arn == result.runs[0].arn:
            steps.append((_call("pipeline_run", result.pipeline, 1), "the run before this one"))
        steps.append(("pipelines()", "every pipeline in the region, and which are failing"))
        blocks.append(_Next(steps))
        self._show(blocks)

