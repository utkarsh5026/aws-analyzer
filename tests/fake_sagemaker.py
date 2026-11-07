"""
An in-memory SageMaker for the tests and the demo: acme's training jobs (two failed in different ways, one done,
one running, one that never got capacity), their CloudWatch log streams, endpoints with a month of traffic (busy,
idle, forgotten, barely used, failing), and pipelines with runs and their steps. Every request and answer is
checked against the service model, as a real client's would be (fake_stepfunctions' _Checked).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from botocore.exceptions import ClientError

from fake_stepfunctions import _Checked


ACCOUNT = "123456789012"

REGION = "us-east-1"

ROLE = f"arn:aws:iam::{ACCOUNT}:role/service-role/AmazonSageMaker-ExecutionRole"


def arn(kind: str, name: str) -> str:
    return f"arn:aws:sagemaker:{REGION}:{ACCOUNT}:{kind}/{name}"


def _ms(moment: datetime) -> int:
    return int(moment.timestamp() * 1000)


@dataclass
class Job:
    """
    A training job: its settings, how it went (status changes as (status, minutes, message)), and its log lines
    per host as (seconds after it started, line).
    """

    name: str
    status: str
    created: datetime
    instance: str = "ml.m5.xlarge"
    count: int = 1
    spot: bool = False
    failure: str = ""
    phases: list[tuple[str, float, str]] = field(default_factory=list)
    logs: dict[str, list[tuple[float, str]]] = field(default_factory=dict)
    metrics: list[tuple[str, float]] = field(default_factory=list)
    input_mode: str = "File"
    volume: int = 30
    max_runtime: int = 86400

    @property
    def started(self) -> datetime:
        return self.created + timedelta(minutes=self.phases[0][1] if self.phases else 0)

    @property
    def ended(self) -> datetime | None:
        if self.status == "InProgress":
            return None
        return self.created + timedelta(minutes=sum(minutes for _, minutes, _ in self.phases))

    def transitions(self) -> list[dict[str, Any]]:
        moment, items = self.created, []
        for i, (status, minutes, message) in enumerate(self.phases):
            item = {"Status": status, "StartTime": moment, "StatusMessage": message}
            moment += timedelta(minutes=minutes)
            if i < len(self.phases) - 1 or self.status != "InProgress":
                item["EndTime"] = moment
            items.append(item)
        return items

    def describe(self) -> dict[str, Any]:
        secondary = self.phases[-1][0] if self.phases else "Starting"
        answer: dict[str, Any] = {
            "TrainingJobName": self.name, "TrainingJobArn": arn("training-job", self.name),
            "TrainingJobStatus": self.status, "SecondaryStatus": secondary, "CreationTime": self.created,
            "LastModifiedTime": self.ended or self.created,
            "ModelArtifacts": {"S3ModelArtifacts": f"s3://acme-ml/models/{self.name}/output/model.tar.gz"},
            "AlgorithmSpecification": {"TrainingImage": "683313688378.dkr.ecr.us-east-1.amazonaws.com/"
                                                        "sagemaker-xgboost:1.7-1", "TrainingInputMode": self.input_mode},
            "RoleArn": ROLE,
            "InputDataConfig": [{"ChannelName": "train", "DataSource": {"S3DataSource": {
                "S3DataType": "S3Prefix", "S3Uri": f"s3://acme-ml/data/{self.name.rsplit('-', 1)[0]}/train/"}}}],
            "OutputDataConfig": {"S3OutputPath": "s3://acme-ml/models/"},
            "ResourceConfig": {"InstanceType": self.instance, "InstanceCount": self.count,
                               "VolumeSizeInGB": self.volume},
            "StoppingCondition": {"MaxRuntimeInSeconds": self.max_runtime},
            "HyperParameters": {"max_depth": "6", "eta": "0.2", "num_round": "300",
                                "sagemaker_program": "train.py"},
            "SecondaryStatusTransitions": self.transitions(),
            "EnableManagedSpotTraining": self.spot,
        }
        if any(status in ("Downloading", "Training") for status, _, _ in self.phases):
            answer["TrainingStartTime"] = self.started
        if self.ended and "TrainingStartTime" in answer:
            answer["TrainingEndTime"] = self.ended
            seconds = max(int((self.ended - self.started).total_seconds()), 1)
            answer["TrainingTimeInSeconds"] = seconds
            answer["BillableTimeInSeconds"] = int(seconds * (0.35 if self.spot else 1))
        if self.failure:
            answer["FailureReason"] = self.failure
        if self.metrics:
            answer["FinalMetricDataList"] = [{"MetricName": name, "Value": value, "Timestamp": self.ended}
                                             for name, value in self.metrics]
        return answer

    def summary(self) -> dict[str, Any]:
        item = {"TrainingJobName": self.name, "TrainingJobArn": arn("training-job", self.name),
                "CreationTime": self.created, "TrainingJobStatus": self.status}
        if self.ended:
            item["TrainingEndTime"] = self.ended
        return item

    def streams(self) -> dict[str, list[dict[str, Any]]]:
        epoch = int(self.started.timestamp())
        return {f"{self.name}/{host}-{epoch}": [{"timestamp": _ms(self.started + timedelta(seconds=at)),
                                                 "message": line, "ingestionTime": _ms(self.started)}
                                                for at, line in lines]
                for host, lines in self.logs.items()}


@dataclass
class Endpoint:
    """
    An endpoint on instances (or serverless), made `age` days ago, with its requests and 5xx errors per day, the
    newest day last.
    """

    name: str
    instance: str = "ml.m5.large"
    count: int = 1
    age: int = 60
    daily: list[float] = field(default_factory=list)
    errors: list[float] = field(default_factory=list)
    serverless: bool = False

    @property
    def config(self) -> str:
        return f"{self.name}-config"


@dataclass
class Step:
    """
    A pipeline step's definition and how it went in a run: (minutes after the run started, minutes it took).
    """

    name: str
    type: str
    depends: list[str] = field(default_factory=list)
    status: str = "Succeeded"
    at: float = 0.0
    minutes: float = 1.0
    failure: str = ""
    job: str = ""
    cached: bool = False
    gets: list[str] = field(default_factory=list)
    if_steps: list[Step] = field(default_factory=list)
    else_steps: list[Step] = field(default_factory=list)

    def definition(self) -> dict[str, Any]:
        arguments: dict[str, Any] = {"Inputs": [{"Get": f"Steps.{name}.Properties.ModelArtifacts"}
                                                for name in self.gets]}
        if self.type == "Condition":
            arguments = {"Conditions": [{"Type": "GreaterThanOrEqualTo", "LeftValue": {
                "Std:JsonGet": {"PropertyFile": {"Get": "Steps.Evaluate.PropertyFiles.Metrics"}}}, "RightValue": 0.8}],
                         "IfSteps": [s.definition() for s in self.if_steps],
                         "ElseSteps": [s.definition() for s in self.else_steps]}
        spec: dict[str, Any] = {"Name": self.name, "Type": self.type, "Arguments": arguments}
        if self.depends:
            spec["DependsOn"] = self.depends
        return spec


@dataclass
class Run:
    """
    One pipeline run: its steps (those that ran have a status), when it started and how it ended.
    """

    pipeline: str
    name: str
    started: datetime
    status: str
    steps: list[Step]
    failure: str = ""

    @property
    def arn(self) -> str:
        return f"{arn('pipeline', self.pipeline)}/execution/{self.name}"

    def flat(self) -> list[Step]:
        found = []
        for step in self.steps:
            found += [step, *step.if_steps, *step.else_steps]
        return found

    @property
    def modified(self) -> datetime:
        ran = [s for s in self.flat() if s.status]
        return self.started + timedelta(minutes=max((s.at + s.minutes for s in ran), default=0))


def _not_found(message: str, operation: str) -> ClientError:
    return ClientError({"Error": {"Code": "ValidationException", "Message": message}}, operation)


class FakeSageMaker(_Checked):
    """
    SageMaker's read operations over training jobs, endpoints and pipeline runs.
    """

    service = "sagemaker"

    def __init__(self, jobs: list[Job], endpoints: list[Endpoint], runs: list[Run], now: datetime):
        super().__init__(REGION)
        self.jobs, self.endpoints, self.runs, self.now = jobs, endpoints, runs, now

    def _list_training_jobs(self, CreationTimeAfter: datetime | None = None, StatusEquals: str | None = None,
                            NameContains: str | None = None, MaxResults: int = 100, **params: Any) -> dict[str, Any]:
        jobs = [j for j in self.jobs if (CreationTimeAfter is None or j.created > CreationTimeAfter)
                and StatusEquals in (None, j.status) and (NameContains or "") in j.name]
        jobs.sort(key=lambda j: j.created, reverse=True)
        return {"TrainingJobSummaries": [j.summary() for j in jobs[:MaxResults]]}

    def _describe_training_job(self, TrainingJobName: str) -> dict[str, Any]:
        for job in self.jobs:
            if job.name == TrainingJobName:
                return job.describe()
        raise _not_found(f"Requested resource not found: {TrainingJobName}", "DescribeTrainingJob")

    def _list_endpoints(self, **params: Any) -> dict[str, Any]:
        return {"Endpoints": [{"EndpointName": e.name, "EndpointArn": arn("endpoint", e.name),
                               "CreationTime": self.now - timedelta(days=e.age), "LastModifiedTime": self.now,
                               "EndpointStatus": "InService"} for e in self.endpoints]}

    def _endpoint(self, name: str, operation: str) -> Endpoint:
        for endpoint in self.endpoints:
            if name in (endpoint.name, endpoint.config):
                return endpoint
        raise _not_found(f"Could not find endpoint {name}.", operation)

    def _describe_endpoint(self, EndpointName: str) -> dict[str, Any]:
        e = self._endpoint(EndpointName, "DescribeEndpoint")
        variant: dict[str, Any] = {"VariantName": "AllTraffic"}
        if e.serverless:
            variant["CurrentServerlessConfig"] = {"MemorySizeInMB": 2048, "MaxConcurrency": 5}
        else:
            variant["CurrentInstanceCount"] = e.count
        return {"EndpointName": e.name, "EndpointArn": arn("endpoint", e.name), "EndpointConfigName": e.config,
                "ProductionVariants": [variant], "EndpointStatus": "InService",
                "CreationTime": self.now - timedelta(days=e.age), "LastModifiedTime": self.now}

    def _describe_endpoint_config(self, EndpointConfigName: str) -> dict[str, Any]:
        e = self._endpoint(EndpointConfigName, "DescribeEndpointConfig")
        variant: dict[str, Any] = {"VariantName": "AllTraffic", "ModelName": f"{e.name}-model"}
        if e.serverless:
            variant["ServerlessConfig"] = {"MemorySizeInMB": 2048, "MaxConcurrency": 5}
        else:
            variant.update(InstanceType=e.instance, InitialInstanceCount=e.count)
        return {"EndpointConfigName": e.config, "EndpointConfigArn": arn("endpoint-config", e.config),
                "ProductionVariants": [variant], "CreationTime": self.now - timedelta(days=e.age)}

    def _pipelines(self) -> list[str]:
        return list(dict.fromkeys(run.pipeline for run in self.runs))

    def _list_pipelines(self, **params: Any) -> dict[str, Any]:
        items = []
        for name in self._pipelines():
            runs = [r for r in self.runs if r.pipeline == name]
            items.append({"PipelineArn": arn("pipeline", name), "PipelineName": name, "PipelineDisplayName": name,
                          "CreationTime": min(r.started for r in runs) - timedelta(days=30),
                          "LastExecutionTime": max(r.started for r in runs)})
        return {"PipelineSummaries": items}

    def _list_pipeline_executions(self, PipelineName: str, MaxResults: int = 100, **params: Any) -> dict[str, Any]:
        if PipelineName not in self._pipelines():
            raise ClientError({"Error": {"Code": "ResourceNotFound", "Message": f"Pipeline '{PipelineName}' does not "
                                         "exist."}}, "ListPipelineExecutions")
        runs = sorted((r for r in self.runs if r.pipeline == PipelineName), key=lambda r: r.started, reverse=True)
        items = []
        for run in runs[:MaxResults]:
            item = {"PipelineExecutionArn": run.arn, "StartTime": run.started, "PipelineExecutionStatus": run.status,
                    "PipelineExecutionDisplayName": run.name}
            if run.failure:
                item["PipelineExecutionFailureReason"] = run.failure
            items.append(item)
        return {"PipelineExecutionSummaries": items}

    def _run(self, execution_arn: str, operation: str) -> Run:
        for run in self.runs:
            if run.arn == execution_arn:
                return run
        raise _not_found(f"Pipeline execution {execution_arn} does not exist.", operation)

    def _describe_pipeline_execution(self, PipelineExecutionArn: str) -> dict[str, Any]:
        run = self._run(PipelineExecutionArn, "DescribePipelineExecution")
        answer = {"PipelineArn": arn("pipeline", run.pipeline), "PipelineExecutionArn": run.arn,
                  "PipelineExecutionDisplayName": run.name, "PipelineExecutionStatus": run.status,
                  "CreationTime": run.started, "LastModifiedTime": run.modified}
        if run.failure:
            answer["FailureReason"] = run.failure
        return answer

    def _describe_pipeline_definition_for_execution(self, PipelineExecutionArn: str) -> dict[str, Any]:
        run = self._run(PipelineExecutionArn, "DescribePipelineDefinitionForExecution")
        definition = {"Version": "2020-12-01", "Parameters": [], "Steps": [s.definition() for s in run.steps]}
        return {"PipelineDefinition": json.dumps(definition), "CreationTime": run.started}

    def _list_pipeline_execution_steps(self, PipelineExecutionArn: str, **params: Any) -> dict[str, Any]:
        run = self._run(PipelineExecutionArn, "ListPipelineExecutionSteps")
        items = []
        for step in sorted((s for s in run.flat() if s.status), key=lambda s: s.at, reverse=True):
            start = run.started + timedelta(minutes=step.at)
            item: dict[str, Any] = {"StepName": step.name, "StepStatus": step.status, "StartTime": start,
                                    "AttemptCount": 1, "Metadata": {}}
            if step.status != "Executing":
                item["EndTime"] = start + timedelta(minutes=step.minutes)
            if step.failure:
                item["FailureReason"] = step.failure
            if step.job:
                key = {"Training": "TrainingJob", "Processing": "ProcessingJob"}.get(step.type, "")
                kind = {"Training": "training-job", "Processing": "processing-job"}.get(step.type, "")
                if key:
                    item["Metadata"] = {key: {"Arn": arn(kind, step.job)}}
            if step.cached:
                item["CacheHitResult"] = {"SourcePipelineExecutionArn": run.arn.replace(run.name, "earlier")}
            items.append(item)
        return {"PipelineExecutionSteps": items}


class FakeLogs(_Checked):
    """
    CloudWatch Logs' DescribeLogStreams and GetLogEvents over the training jobs' streams.
    """

    service = "logs"

    def __init__(self, jobs: list[Job]):
        super().__init__(REGION)
        self.streams: dict[str, list[dict[str, Any]]] = {}
        for job in jobs:
            self.streams.update(job.streams())

    def _describe_log_streams(self, logGroupName: str, logStreamNamePrefix: str = "", **params: Any) -> dict[str, Any]:
        names = sorted(name for name in self.streams if name.startswith(logStreamNamePrefix))
        return {"logStreams": [{"logStreamName": name, "firstEventTimestamp": self.streams[name][0]["timestamp"],
                                "lastEventTimestamp": self.streams[name][-1]["timestamp"]} for name in names]}

    def _get_log_events(self, logGroupName: str, logStreamName: str, limit: int = 10_000, startFromHead: bool = False,
                        **params: Any) -> dict[str, Any]:
        events = self.streams.get(logStreamName, [])
        shown = events[:limit] if startFromHead else events[-limit:]
        return {"events": shown, "nextForwardToken": "f/1", "nextBackwardToken": "b/1"}


class FakeCloudWatch(_Checked):
    """
    GetMetricData over the endpoints' AWS/SageMaker Invocations and Invocation5XXErrors, a value per UTC day.
    """

    service = "cloudwatch"

    def __init__(self, endpoints: list[Endpoint], now: datetime):
        super().__init__(REGION)
        self.endpoints = {e.name: e for e in endpoints}
        self.today = now.replace(hour=0, minute=0, second=0, microsecond=0)

    def _get_metric_data(self, MetricDataQueries: list[dict[str, Any]], StartTime: datetime, EndTime: datetime,
                         **params: Any) -> dict[str, Any]:
        results = []
        for query in MetricDataQueries:
            metric = query["MetricStat"]["Metric"]
            endpoint = self.endpoints.get(metric["Dimensions"][0]["Value"])
            series = [] if endpoint is None else (endpoint.daily if metric["MetricName"] == "Invocations"
                                                  else endpoint.errors)
            days = [self.today - timedelta(days=len(series) - 1 - i) for i in range(len(series))]
            kept = [(day, value) for day, value in zip(days, series) if StartTime <= day < EndTime and value]
            results.append({"Id": query["Id"], "Label": metric["MetricName"], "Timestamps": [d for d, _ in kept],
                            "Values": [v for _, v in kept], "StatusCode": "Complete"})
        return {"MetricDataResults": results}


MISSING_MODULE = ('AlgorithmError: ExecuteUserScriptError:\nExitCode 1\nErrorMessage "ModuleNotFoundError: No module '
                  "named 'lightgbm'\"\nCommand \"/miniconda3/bin/python3 train.py --max_depth 6\", exit code: 1")

GPU_MEMORY = ('AlgorithmError: ExecuteUserScriptError:\nExitCode 1\nErrorMessage "torch.OutOfMemoryError: CUDA out of '
              'memory. Tried to allocate 2.00 GiB. GPU 0 has a total capacity of 22.05 GiB"\nCommand '
              '"/opt/conda/bin/python3.10 finetune.py --batch_size 64", exit code: 1')

NO_CAPACITY = ("CapacityError: Unable to provision requested ML compute capacity. Please retry using a different ML "
               "instance type.")


def _startup(host: str) -> list[tuple[float, str]]:
    return [(1, f"sagemaker-training-toolkit INFO     Imported framework "
                f"sagemaker_xgboost_container.training ({host})"),
            (2, "sagemaker-training-toolkit INFO     No GPUs detected (normal if no gpus "
                "installed)"),
            (3, "sagemaker-training-toolkit INFO     Invoking user script")]


def _churn_logs() -> list[tuple[float, str]]:
    return _startup("algo-1") + [
        (5, "Training Env: {\"current_host\": \"algo-1\", \"hosts\": [\"algo-1\"]}"),
        (9, "Traceback (most recent call last):"),
        (9, '  File "/opt/ml/code/train.py", line 4, in <module>'),
        (9, "    import lightgbm as lgb"),
        (9, "ModuleNotFoundError: No module named 'lightgbm'"),
        (10, "sagemaker-training-toolkit ERROR    Reporting training FAILURE"),
        (10, "sagemaker-training-toolkit ERROR    ExecuteUserScriptError:"),
    ]


def _bert_logs(host: str, *, fails: bool) -> list[tuple[float, str]]:
    lines = _startup(host) + [(30 + i * 60, f"[{host}] epoch 1 step {i * 50}/2000 loss={2.3 - i * 0.04:.3f}")
                              for i in range(12)]
    if fails:
        lines += [(760, "Traceback (most recent call last):"),
                  (760, '  File "/opt/ml/code/finetune.py", line 88, in <module>'),
                  (760, "    outputs = model(**batch)"),
                  (760, '  File "/opt/conda/lib/python3.10/site-packages/torch/nn/modules/module.py", line 1553, in '
                        "_wrapped_call_impl"),
                  (760, "    return self._call_impl(*args, **kwargs)"),
                  (760, "torch.OutOfMemoryError: CUDA out of memory. Tried to allocate 2.00 GiB. GPU 0 has a total "
                        "capacity of 22.05 GiB of which 1.12 GiB is free.")]
    return lines


def scene(now: datetime) -> tuple[list[Job], list[Endpoint], list[Run]]:
    """
    acme's training jobs, endpoints and pipeline runs, timed back from `now`.
    """
    hours = lambda n: now - timedelta(hours=n)  # noqa: E731
    jobs = [
        Job("churn-xgb-0517", "Failed", hours(3), failure=MISSING_MODULE,
            phases=[("Starting", 3, "Preparing the instances for training"),
                    ("Downloading", 1, "Downloading input data"), ("Training", 0.2, "Training image download "
                                                                     "completed. Training in progress."),
                    ("Failed", 0, "Training job failed")],
            logs={"algo-1": _churn_logs()}),
        Job("bert-finetune-0516", "Failed", hours(20), instance="ml.g5.2xlarge", count=2, failure=GPU_MEMORY,
            phases=[("Starting", 6, "Preparing the instances for training"),
                    ("Downloading", 4, "Downloading input data"), ("Training", 13, "Training in progress."),
                    ("Failed", 0, "Training job failed")],
            logs={"algo-1": _bert_logs("algo-1", fails=True), "algo-2": _bert_logs("algo-2", fails=False)}),
        Job("churn-xgb-0515", "Completed", hours(50), instance="ml.m5.2xlarge",
            phases=[("Starting", 4, "Preparing the instances for training"),
                    ("Downloading", 41, "Downloading input data"), ("Training", 58, "Training in progress."),
                    ("Uploading", 1, "Uploading generated training model"),
                    ("Completed", 0, "Training job completed")],
            logs={"algo-1": _startup("algo-1") + [(3000, "[299]#011train-auc:0.94120#011validation-auc:0.91873")]},
            metrics=[("validation:auc", 0.91873), ("train:auc", 0.9412)]),
        Job("forecast-deepar-0517", "InProgress", hours(2), instance="ml.c5.4xlarge", spot=True,
            phases=[("Starting", 2, "Preparing the instances for training"),
                    ("Downloading", 3, "Downloading input data"), ("Training", 0, "Training in progress.")],
            logs={"algo-1": _startup("algo-1") + [(600, "#metrics {\"epoch\": 12, \"train_loss\": 1.82}")]}),
        Job("resnet-capacity-0514", "Failed", hours(80), instance="ml.p4d.24xlarge", failure=NO_CAPACITY,
            phases=[("Starting", 11, "Preparing the instances for training"),
                    ("Failed", 0, "Training job failed")]),
    ]
    month = 30
    endpoints = [
        Endpoint("churn-endpoint", "ml.m5.large", age=120, daily=[5200.0 + i * 13 for i in range(month)],
                 errors=[2.0] * month),
        Endpoint("bert-demo", "ml.g5.xlarge", age=41, daily=[0.0] * month),
        Endpoint("fraud-staging", "ml.m5.xlarge", count=2, age=90,
                 daily=[300.0] * (month - 12) + [0.0] * 12),
        Endpoint("recs-endpoint", "ml.c5.large", age=200, daily=[11.0] * month, errors=[0.4] * month),
        Endpoint("faq-serverless", serverless=True, age=30),
    ]
    preprocess = lambda status="Succeeded", at=0.0, minutes=6.0, job="", cached=False: Step(  # noqa: E731
        "Preprocess", "Processing", status=status, at=at, minutes=minutes, job=job, cached=cached)

    def churn(name: str, started: datetime, broken: bool) -> Run:
        train = Step("TrainModel", "Training", gets=["Preprocess"], at=6.5, minutes=0.3 if broken else 38,
                     status="Failed" if broken else "Succeeded", job="churn-xgb-0517" if broken else "churn-xgb-0515",
                     failure=MISSING_MODULE if broken else "")
        evaluate = Step("Evaluate", "Processing", gets=["TrainModel"], status="" if broken else "Succeeded", at=45,
                        minutes=4, job="" if broken else "churn-eval-0515")
        register = Step("RegisterModel", "RegisterModel", status="" if broken else "Succeeded", at=50, minutes=0.2)
        stop = Step("AccuracyTooLow", "Fail", status="")
        check = Step("CheckAccuracy", "Condition", depends=["Evaluate"], status="" if broken else "Succeeded", at=49,
                     minutes=0.1, if_steps=[register], else_steps=[stop])
        steps = [preprocess(job=f"churn-prep-{name}"), train, evaluate, check]
        reason = f"Step failure: One or multiple steps failed. TrainModel: {MISSING_MODULE.splitlines()[0]}"
        return Run("churn-pipeline", name, started, "Failed" if broken else "Succeeded", steps,
                   failure=reason if broken else "")

    runs = [
        churn("run-0517", hours(3.2), True),
        churn("run-0516", hours(27), True),
        churn("run-0515", hours(51), False),
        Run("forecast-pipeline", "run-0517", hours(2.1), "Executing", [
            Step("BuildFeatures", "Processing", at=0, minutes=5, job="forecast-features-0517"),
            Step("TrainDeepAR", "Training", gets=["BuildFeatures"], status="Executing", at=6, minutes=0,
                 job="forecast-deepar-0517")]),
        Run("nightly-features", "run-1107", hours(9), "Succeeded", [
            preprocess(minutes=0.1, cached=True),
            Step("Aggregate", "Processing", depends=["Preprocess"], at=0.2, minutes=22, job="nightly-agg-1107"),
            Step("Publish", "Lambda", depends=["Aggregate"], at=22.5, minutes=0.3)]),
    ]
    return jobs, endpoints, runs


def fake_clients(now: datetime) -> dict[str, Any]:
    """
    {'sagemaker', 'logs', 'cloudwatch'} fakes over scene(now), for SageMakerAnalyzer(clients=...).
    """
    jobs, endpoints, runs = scene(now)
    return {"sagemaker": FakeSageMaker(jobs, endpoints, runs, now), "logs": FakeLogs(jobs),
            "cloudwatch": FakeCloudWatch(endpoints, now)}
