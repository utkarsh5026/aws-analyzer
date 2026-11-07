"""
What SageMakerAnalyzer reads about the jobs people launch from a notebook: training jobs and their logs,
endpoints and their traffic, and pipelines and their runs. A mixin, so analyzer.py keeps the notebook's reads.
Never starts, stops or deletes anything.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Callable

from botocore.exceptions import BotoCoreError, ClientError

from .._kit.errors import _Hint, _error_code, _error_name
from .._kit.fmt import _utcnow, parse_time

from .models import (
    Billable, EndpointReport, EndpointUse, HostLog, PipelineInfo, PipelineRun, PipelinesReport, RunningReport,
    TrainingDetail, TrainingJob, TrainingList,
)
from .analysis.pipelines import merge_steps, parse_definition, parse_pipeline_step, parse_run_summary
from .analysis.training import (
    LOG_LINES, MOST_HOSTS, TRAINING_LOG_GROUP, host_log, parse_training_detail, parse_training_summary,
)


TRAINING_LIMIT = 100

PIPELINE_RUNS = 5

_DAY = 86400

_METRIC_QUERIES = 500

_PAGE = 100


def _not_found(exc: ClientError) -> bool:
    message = str(exc.response.get("Error", {}).get("Message", "")).lower()
    return _error_code(exc) in ("ResourceNotFound", "ValidationException") and (
        "not found" in message or "does not exist" in message)


class _JobReads:
    """
    SageMakerAnalyzer's reads of training jobs, endpoints and pipelines (it gives client, _service, _paginate,
    _map, region, and _endpoint_billables, which running() shares).
    """

    client: Any
    region: str
    _service: Callable[[str], Any]
    max_workers: int
    _paginate: Callable[..., list[dict[str, Any]]]
    _map: Callable[..., list[Any]]
    _endpoint_billables: Callable[[RunningReport], list[Billable]]

    def _training_summaries(self, since: datetime | None, status: str,
                            limit: int) -> tuple[list[dict[str, Any]], bool]:
        params: dict[str, Any] = {"SortBy": "CreationTime", "SortOrder": "Descending", "MaxResults": _PAGE}
        if since is not None:
            params["CreationTimeAfter"] = since
        if status:
            params["StatusEquals"] = status
        found: list[dict[str, Any]] = []
        for page in self.client.get_paginator("list_training_jobs").paginate(**params):
            for summary in page.get("TrainingJobSummaries", []):
                if len(found) >= limit:
                    return found, True
                found.append(summary)
        return found, False

    def training_jobs(self, *, since: str | datetime | None = "7d", status: str = "", limit: int = TRAINING_LIMIT,
                      progress: Callable[..., None] | None = None) -> TrainingList:
        """
        The region's training jobs made since `since` ('7d', '2024-05-01', None for all), newest first, each
        described (instances, billable time, failure reason), at most `limit`. status= keeps one status
        ('Failed', 'InProgress', 'Completed', 'Stopped'). progress is called with (done, total) jobs described.
        """
        start = parse_time(since) if since is not None else None
        report = TrainingList(self.region, since=start, status=status, limit=limit)
        try:
            summaries, report.cut_short = self._training_summaries(start, status, limit)
        except (ClientError, BotoCoreError) as exc:
            report.errors["training jobs"] = _error_name(exc)
            return report
        def describe(summary: dict[str, Any]) -> TrainingJob:
            try:
                return parse_training_detail(self.client.describe_training_job(
                    TrainingJobName=summary["TrainingJobName"]))
            except (ClientError, BotoCoreError) as exc:
                report.errors.setdefault("training details", _error_name(exc))
                return parse_training_summary(summary)

        batch_size = max(self.max_workers, 1)
        for begin in range(0, len(summaries), batch_size):
            report.jobs += self._map(describe, summaries[begin:begin + batch_size])
            if progress:
                progress(len(report.jobs), len(summaries))
        return report

    def _close_training_names(self, name: str) -> list[str]:
        words = [w for w in name.replace("_", "-").split("-") if len(w) >= 3][:1] or [name[:8]]
        try:
            page = self.client.list_training_jobs(NameContains=words[0], MaxResults=_PAGE, SortBy="CreationTime",
                                                  SortOrder="Descending")
        except (ClientError, BotoCoreError):
            return []
        return [s["TrainingJobName"] for s in page.get("TrainingJobSummaries", [])][:5]

    def training_logs(self, name: str, *, lines: int = LOG_LINES, most: int = MOST_HOSTS) -> list[HostLog]:
        """
        A training job's logs from CloudWatch Logs (/aws/sagemaker/TrainingJobs), one HostLog per instance (log
        stream), each its last `lines` lines with the error it ends in, at most `most` hosts. [] when the job wrote
        none (it failed before its container started).
        """
        logs = self._service("logs")
        try:
            streams = logs.describe_log_streams(logGroupName=TRAINING_LOG_GROUP, logStreamNamePrefix=f"{name}/",
                                                limit=50).get("logStreams", [])
        except ClientError as exc:
            if _error_code(exc) == "ResourceNotFoundException":
                return []
            raise
        streams.sort(key=lambda s: s.get("logStreamName", ""))

        def read(stream: dict[str, Any]) -> HostLog:
            answer = logs.get_log_events(logGroupName=TRAINING_LOG_GROUP, logStreamName=stream["logStreamName"],
                                         startFromHead=False, limit=lines)
            events = answer.get("events", [])
            return host_log(stream["logStreamName"], events, cut_short=len(events) >= lines)

        return self._map(read, streams[:most])

    def training_job(self, name: str, *, logs: bool = True, lines: int = LOG_LINES) -> TrainingDetail:
        """
        One training job: its settings, status timeline, metrics and billable time (DescribeTrainingJob), and with
        logs=True each instance's last `lines` log lines from CloudWatch Logs with the error they end in. A name
        that isn't there raises a _Hint naming the closest ones.
        """
        try:
            desc = self.client.describe_training_job(TrainingJobName=name)
        except ClientError as exc:
            if not _not_found(exc):
                raise
            close = self._close_training_names(name)
            names = f" Close names: {', '.join(close)}." if close else ""
            raise _Hint(f"There's no training job named {name} in {self.region}.{names} training_jobs() lists "
                        "the recent ones.") from None
        detail = TrainingDetail(parse_training_detail(desc))
        if logs:
            try:
                detail.hosts = self.training_logs(name, lines=lines)
                detail.logs_read = True
            except (ClientError, BotoCoreError) as exc:
                detail.errors["logs"] = _error_name(exc)
        return detail

    def _endpoint_queries(self, uses: list[EndpointUse], days: int) -> tuple[list[dict[str, Any]], list[tuple]]:
        queries: list[dict[str, Any]] = []
        owners: list[tuple[EndpointUse, str]] = []
        for use in uses:
            for variant in use.endpoint.variants:
                for metric in ("Invocations", "Invocation5XXErrors"):
                    queries.append({"Id": f"q{len(queries)}", "MetricStat": {"Metric": {
                        "Namespace": "AWS/SageMaker", "MetricName": metric,
                        "Dimensions": [{"Name": "EndpointName", "Value": use.endpoint.name},
                                       {"Name": "VariantName", "Value": variant}]},
                        "Period": _DAY, "Stat": "Sum"}})
                    owners.append((use, metric))
        return queries, owners

    def endpoint_traffic(self, uses: list[EndpointUse], days: int = 30) -> None:
        """
        Fill each endpoint's requests per UTC day, total requests and 5xx errors over the last `days` days, from
        CloudWatch (AWS/SageMaker, every variant). Needs cloudwatch:GetMetricData.
        """
        queries, owners = self._endpoint_queries(uses, days)
        for use in uses:
            use.requests, use.server_errors = (0.0, 0.0) if use.endpoint.variants else (None, None)
        if not queries:
            return
        now = _utcnow()
        end = now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
        cloudwatch = self._service("cloudwatch")
        for begin in range(0, len(queries), _METRIC_QUERIES):
            pages = cloudwatch.get_paginator("get_metric_data").paginate(
                MetricDataQueries=queries[begin:begin + _METRIC_QUERIES], StartTime=end - timedelta(days=days),
                EndTime=end)
            for page in pages:
                for series in page.get("MetricDataResults", []):
                    use, metric = owners[int(series["Id"][1:])]
                    values = series.get("Values", [])
                    if metric == "Invocations":
                        use.requests = (use.requests or 0.0) + sum(values)
                        for day, count in zip(series.get("Timestamps", []), values):
                            use.daily[day] = use.daily.get(day, 0.0) + count
                    else:
                        use.server_errors = (use.server_errors or 0.0) + sum(values)
        for use in uses:
            use.endpoint.invocations = use.requests

    def endpoints(self, *, days: int = 30, metrics: bool = True,
                  progress: Callable[..., None] | None = None) -> EndpointReport:
        """
        Every endpoint in the region that's in service (or updating), with its instances and, with metrics=True,
        its requests per day and 5xx errors over the last `days` days from CloudWatch. Sections that couldn't be
        read are in `errors`.
        """
        self.client  # noqa: B018 - made before any threads start (boto3 sessions aren't thread-safe)
        report = EndpointReport(self.region, days=days)
        scratch = RunningReport(self.region, days=days)
        try:
            billables = self._endpoint_billables(scratch)
        except (ClientError, BotoCoreError) as exc:
            report.errors["endpoints"] = _error_name(exc)
            return report
        report.errors.update(scratch.errors)
        if progress:
            progress(1, 2)
        report.endpoints = [EndpointUse(b) for b in billables]
        on_instances = [use for use in report.endpoints if not use.endpoint.serverless]
        if metrics and on_instances:
            try:
                self.endpoint_traffic(on_instances, days)
            except (ClientError, BotoCoreError) as exc:
                report.errors["metrics"] = _error_name(exc)
                for use in on_instances:
                    use.requests = use.server_errors = use.endpoint.invocations = None
                    use.daily.clear()
        if progress:
            progress(2, 2)
        return report

    def _pipeline_runs(self, name: str, most: int) -> list[Any]:
        page = self.client.list_pipeline_executions(PipelineName=name, SortBy="CreationTime",
                                                    SortOrder="Descending", MaxResults=min(max(most, 1), _PAGE))
        return [parse_run_summary(item) for item in page.get("PipelineExecutionSummaries", [])][:most]

    def pipelines(self, *, runs: int = PIPELINE_RUNS, progress: Callable[..., None] | None = None) -> PipelinesReport:
        """
        The region's pipelines, the most recently run first, each with its latest `runs` runs (status, start and
        failure reason).
        """
        report = PipelinesReport(self.region)
        try:
            summaries = self._paginate("list_pipelines", "PipelineSummaries", SortBy="CreationTime",
                                       SortOrder="Descending")
        except (ClientError, BotoCoreError) as exc:
            report.errors["pipelines"] = _error_name(exc)
            return report

        def read(summary: dict[str, Any]) -> PipelineInfo:
            info = PipelineInfo(summary.get("PipelineName", ""), summary.get("PipelineArn", ""),
                                summary.get("PipelineDescription", ""), summary.get("CreationTime"),
                                summary.get("LastExecutionTime"))
            try:
                info.runs = self._pipeline_runs(info.name, runs)
            except (ClientError, BotoCoreError) as exc:
                report.errors.setdefault("pipeline runs", _error_name(exc))
            return info

        report.pipelines = self._map(read, summaries)
        report.pipelines.sort(key=lambda p: (p.last_run is None, -(p.last_run.timestamp() if p.last_run else 0)))
        if progress:
            progress(len(summaries), len(summaries))
        return report

    def _pick_run(self, pipeline: str, run: str | int | None, history: list[Any]) -> str:
        """
        The ARN of the run asked for: the newest (None or 0), the n-th newest (1, 2, ...), an ARN, or a display
        name or ID from the pipeline's recent runs.
        """
        if isinstance(run, str) and run.startswith("arn:"):
            return run
        if not history:
            raise _Hint(f"Pipeline {pipeline} hasn't run yet in {self.region}; pipelines() lists the ones that have.")
        if run is None or isinstance(run, int):
            index = run or 0
            if index >= len(history):
                raise _Hint(f"Pipeline {pipeline} has {len(history)} recent runs; pick 0 to {len(history) - 1}.")
            return history[index].arn
        for item in history:
            if run in (item.name, item.arn.rsplit("/", 1)[-1]):
                return item.arn
        raise _Hint(f"Pipeline {pipeline} has no recent run named {run}; pipelines() shows its runs.")

    def _pipeline_history(self, pipeline: str) -> list[Any]:
        try:
            return self._pipeline_runs(pipeline, _PAGE)
        except ClientError as exc:
            if not _not_found(exc):
                raise
            raise _Hint(f"There's no pipeline named {pipeline} in {self.region}; pipelines() lists them.") from None

    def pipeline_run(self, pipeline: str, run: str | int | None = None) -> PipelineRun:
        """
        One run of a pipeline (the newest unless `run` names one: 1 is the one before, or an ARN or display name):
        its status and failure reason, and every step in the definition with how it went, the job it started and
        the steps it waited for. Sections that couldn't be read are in `errors`.
        """
        history = self._pipeline_history(pipeline)
        arn = self._pick_run(pipeline, run, history)
        result = PipelineRun(pipeline, arn, runs=history)
        try:
            desc = self.client.describe_pipeline_execution(PipelineExecutionArn=arn)
            result.name = desc.get("PipelineExecutionDisplayName") or arn.rsplit("/", 1)[-1]
            result.status = desc.get("PipelineExecutionStatus", "")
            result.started, result.modified = desc.get("CreationTime"), desc.get("LastModifiedTime")
            result.failure = desc.get("FailureReason", "")
        except (ClientError, BotoCoreError) as exc:
            result.errors["run"] = _error_name(exc)
        defined = []
        try:
            answer = self.client.describe_pipeline_definition_for_execution(PipelineExecutionArn=arn)
            defined = parse_definition(answer.get("PipelineDefinition") or "{}")
        except (ClientError, BotoCoreError) as exc:
            result.errors["definition"] = _error_name(exc)
        ran = []
        try:
            items = self._paginate("list_pipeline_execution_steps", "PipelineExecutionSteps",
                                   PipelineExecutionArn=arn)
            ran = [parse_pipeline_step(item) for item in items]
        except (ClientError, BotoCoreError) as exc:
            result.errors["steps"] = _error_name(exc)
        result.steps = merge_steps(defined, ran)
        return result
