"""
Training jobs: DescribeTrainingJob and ListTrainingJobs read into TrainingJobs, their CloudWatch log streams read
into one log per host with the error each ends in, why a job failed in plain words, and the findings of
training_jobs() and training_job().
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

from ..._kit.fmt import _plural, _utcnow, human_money
from ..._kit.text import _clip

from ..prices import INSTANCE_TYPES, SAGEMAKER_PRICES
from ..helpers import human_runtime
from ..models import HostLine, HostLog, StatusChange, TrainingDetail, TrainingJob, TrainingList
from .common import _couldnt_read


TRAINING_LOG_GROUP = "/aws/sagemaker/TrainingJobs"

LOG_LINES = 1_000

MOST_HOSTS = 8

_ERROR_LINES = 30

_REASON_CHARS = 300

_SLOW_START_SECONDS = 10 * 60

_SLOW_DOWNLOAD_SHARE = 0.3

_SPOT_WORTH_SECONDS = 3600

_SPOT_SAVING = 0.7

_SHOWN_FAILURES = 5

_TRACEBACK = "Traceback (most recent call last):"

_EXCEPTION_LINE = re.compile(r"^\s*([A-Za-z_][\w.]*(Error|Exception|Exit|Interrupt|Warning)|KeyError|Killed)\b")

_ERROR_WORDS = re.compile(r"\b(ERROR|Error:|error:|FATAL|Killed|out of memory|No space left)\b")

_HOST = re.compile(r"/(algo-\d+|[\w-]+?)(?:-\d{9,})?$")

_ERROR_MESSAGE = re.compile(r'ErrorMessage\s+"(.+?)"\s*(?:\n|Command|$)', re.S)


def _transition(item: dict[str, Any]) -> StatusChange:
    return StatusChange(item.get("Status", ""), item.get("StartTime"), item.get("EndTime"),
                        item.get("StatusMessage", ""))


def _instances(resources: dict[str, Any]) -> list[tuple[str, int]]:
    groups = resources.get("InstanceGroups") or []
    if groups:
        return [(g["InstanceType"], g.get("InstanceCount", 1)) for g in groups if g.get("InstanceType")]
    instance_type = resources.get("SelectedInstanceType") or resources.get("InstanceType") or ""
    count = resources.get("SelectedInstanceCount") or resources.get("InstanceCount", 1)
    return [(instance_type, count)] if instance_type else []


def _channel_uri(channel: dict[str, Any]) -> str:
    source = channel.get("DataSource") or {}
    return ((source.get("S3DataSource") or {}).get("S3Uri")
            or (source.get("FileSystemDataSource") or {}).get("DirectoryPath", ""))


def parse_training_detail(desc: dict[str, Any]) -> TrainingJob:
    """
    DescribeTrainingJob -> TrainingJob.
    """
    resources = desc.get("ResourceConfig") or {}
    algorithm = desc.get("AlgorithmSpecification") or {}
    stopping = desc.get("StoppingCondition") or {}
    return TrainingJob(
        desc["TrainingJobName"],
        status=desc.get("TrainingJobStatus", ""),
        secondary=desc.get("SecondaryStatus", ""),
        failure=desc.get("FailureReason", ""),
        created=desc.get("CreationTime"),
        started=desc.get("TrainingStartTime"),
        ended=desc.get("TrainingEndTime"),
        instances=_instances(resources),
        volume_gb=resources.get("VolumeSizeInGB"),
        spot=bool(desc.get("EnableManagedSpotTraining")),
        billable_seconds=desc.get("BillableTimeInSeconds"),
        training_seconds=desc.get("TrainingTimeInSeconds"),
        max_runtime=stopping.get("MaxRuntimeInSeconds"),
        image=algorithm.get("TrainingImage", ""),
        algorithm=algorithm.get("AlgorithmName", ""),
        input_mode=algorithm.get("TrainingInputMode", ""),
        inputs=[(c.get("ChannelName", ""), _channel_uri(c)) for c in desc.get("InputDataConfig") or []],
        output=(desc.get("OutputDataConfig") or {}).get("S3OutputPath", ""),
        model_artifact=(desc.get("ModelArtifacts") or {}).get("S3ModelArtifacts", ""),
        hyperparameters=dict(desc.get("HyperParameters") or {}),
        metrics=[(m.get("MetricName", ""), m.get("Value", 0.0)) for m in desc.get("FinalMetricDataList") or []],
        transitions=[_transition(t) for t in desc.get("SecondaryStatusTransitions") or []],
        checkpoints=(desc.get("CheckpointConfig") or {}).get("S3Uri", ""),
        tuning_job=(desc.get("TuningJobArn") or "").rsplit("/", 1)[-1],
        arn=desc.get("TrainingJobArn", ""),
    )


def parse_training_summary(summary: dict[str, Any]) -> TrainingJob:
    """
    A ListTrainingJobs summary -> TrainingJob, for a job that couldn't be described (`described` is False).
    """
    return TrainingJob(
        summary["TrainingJobName"],
        status=summary.get("TrainingJobStatus", ""),
        secondary=summary.get("SecondaryStatus", ""),
        created=summary.get("CreationTime"),
        ended=summary.get("TrainingEndTime"),
        arn=summary.get("TrainingJobArn", ""),
        described=False,
    )


def host_of(stream: str) -> str:
    """
    The host a training job's log stream is from: 'job/algo-1-1714569600' -> 'algo-1'.
    """
    found = _HOST.search(stream)
    return found.group(1) if found else stream.rsplit("/", 1)[-1]


def find_error(lines: list[str]) -> list[str]:
    """
    The error a log ends in: its last Python traceback up to the exception line, else its last lines that say
    ERROR / Killed / out of memory. [] when there's none.
    """
    starts = [i for i, line in enumerate(lines) if _TRACEBACK in line]
    if starts:
        begin = starts[-1]
        end = begin + 1
        while end < len(lines) and not _EXCEPTION_LINE.match(lines[end]):
            end += 1
        block = lines[begin:end + 1]
        return block[:3] + ["…"] + block[-(_ERROR_LINES - 4):] if len(block) > _ERROR_LINES else block
    flagged = [line for line in lines if _ERROR_WORDS.search(line)]
    return flagged[-5:]


def host_log(stream: str, events: list[dict[str, Any]], *, cut_short: bool = False) -> HostLog:
    """
    A log stream's events (GetLogEvents, oldest first) -> HostLog with the error it ends in.
    """
    lines = [HostLine(_from_ms(e.get("timestamp")), str(e.get("message", "")).rstrip("\n")) for e in events]
    return HostLog(stream, host_of(stream), lines, find_error([line.message for line in lines]), cut_short)


def _from_ms(ms: int | None) -> datetime | None:
    if ms is None:
        return None
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc)


def short_reason(reason: str) -> str:
    """
    What a FailureReason says first: the ErrorMessage of an AlgorithmError, else its first line, clipped.
    """
    if not reason:
        return ""
    found = _ERROR_MESSAGE.search(reason)
    text = found.group(1) if found else reason.strip().splitlines()[0]
    return _clip(" ".join(text.split()), _REASON_CHARS)


@dataclass
class _Cause:
    """
    A kind of failure: what tells it apart (in the failure reason or the log's error) and what to say about it.
    """

    kind: str
    pattern: re.Pattern
    advice: Callable[[TrainingJob, re.Match, dict[str, float]], str]


def _bigger_memory(job: TrainingJob, prices: dict[str, float]) -> str:
    """
    ' (ml.m5.2xlarge has 32 GiB for $0.46/hour)': the cheapest type with twice the memory, or ''.
    """
    if not job.instances or job.instances[0][0] not in INSTANCE_TYPES:
        return ""
    _, _, memory, gpus = INSTANCE_TYPES[job.instances[0][0]]
    fits = sorted((prices[name], name) for name, (_, _, mem, gpu) in INSTANCE_TYPES.items()
                  if name in prices and mem >= memory * 2 and gpu >= gpus and not name.startswith(("ml.t", "ml.inf")))
    if not fits:
        return ""
    price, name = fits[0]
    return f" ({name} has {INSTANCE_TYPES[name][2]:g} GiB for {human_money(price)}/hour)"


def _module_advice(job: TrainingJob, found: re.Match, prices: dict[str, float]) -> str:
    return (f"The training script imports {found.group(1)}, which the container doesn't have. Add it to a "
            "requirements.txt beside the script in source_dir (SageMaker pip-installs it before training), or use an "
            "image that has it.")


def _cpu_memory_advice(job: TrainingJob, found: re.Match, prices: dict[str, float]) -> str:
    return ("The instance ran out of memory and the process was killed (exit code 137). Load the data in "
            f"batches, or use an instance type with more memory{_bigger_memory(job, prices)}.")


def _gpu_memory_advice(job: TrainingJob, found: re.Match, prices: dict[str, float]) -> str:
    return ("The GPU ran out of memory. Lower the batch size (or use gradient accumulation or mixed precision), or a "
            "GPU with more memory.")


def _disk_advice(job: TrainingJob, found: re.Match, prices: dict[str, float]) -> str:
    size = f" from {job.volume_gb} GB" if job.volume_gb else ""
    return (f"The instance's disk filled up. Raise volume_size{size} in the estimator (ResourceConfig.VolumeSizeInGB), "
            "or stream the data with input_mode='FastFile' instead of copying it.")


def _capacity_advice(job: TrainingJob, found: re.Match, prices: dict[str, float]) -> str:
    kind = job.instances[0][0] if job.instances else "this instance type"
    return (f"AWS had no {kind} capacity free in this region when the job started; nothing in your code is wrong. "
            "Try again later, another instance type, or another region.")


def _quota_advice(job: TrainingJob, found: re.Match, prices: dict[str, float]) -> str:
    kind = job.instances[0][0] if job.instances else "the instance type"
    return (f"The account's quota for {kind} training instances is used up. Wait for other jobs to finish, or ask "
            "for more in Service Quotas: aws service-quotas list-service-quotas --service-code sagemaker")


def _runtime_advice(job: TrainingJob, found: re.Match, prices: dict[str, float]) -> str:
    limit = f" ({human_runtime(job.max_runtime)})" if job.max_runtime else ""
    return (f"The job reached its time limit{limit} and SageMaker stopped it. Raise max_run in the estimator, and "
            "save checkpoints (checkpoint_s3_uri) so a longer run can resume.")


def _spot_advice(job: TrainingJob, found: re.Match, prices: dict[str, float]) -> str:
    resume = ("Its checkpoints let a new run resume." if job.checkpoints else
              "Set checkpoint_s3_uri so the next interruption resumes instead of starting over.")
    return f"AWS took the spot capacity back before the job finished. {resume}"


def _s3_access_advice(job: TrainingJob, found: re.Match, prices: dict[str, float]) -> str:
    return ("The job's role can't read its input in S3 (or write its output). Give the role s3:GetObject and "
            "s3:ListBucket on the input path, and s3:PutObject on the output path.")


def _data_advice(job: TrainingJob, found: re.Match, prices: dict[str, float]) -> str:
    inputs = ", ".join(uri for _, uri in job.inputs if uri)
    where = f" ({inputs})" if inputs else ""
    return f"SageMaker couldn't download the input data: check the S3 path{where} exists and has files in it."


def _script_advice(job: TrainingJob, found: re.Match, prices: dict[str, float]) -> str:
    return "The training script raised an error; the log's traceback below shows where."


_CAUSES = (
    _Cause("missing package", re.compile(r"No module named '?([\w.]+)'?"), _module_advice),
    _Cause("GPU memory", re.compile(r"CUDA out of memory|OutOfMemoryError|CUDA error: out of memory"),
           _gpu_memory_advice),
    _Cause("memory", re.compile(r"ExitCode 137|exit code: 137|MemoryError|Killed\b|out of memory", re.I),
           _cpu_memory_advice),
    _Cause("disk", re.compile(r"No space left on device"), _disk_advice),
    _Cause("capacity", re.compile(r"CapacityError|InsufficientInstanceCapacity|insufficient capacity", re.I),
           _capacity_advice),
    _Cause("quota", re.compile(r"ResourceLimitExceeded|account-level service limit", re.I), _quota_advice),
    _Cause("time limit", re.compile(r"MaxRuntimeExceeded|max(imum)? run ?time", re.I), _runtime_advice),
    _Cause("spot interruption", re.compile(r"spot instance interruption|Interrupted", re.I), _spot_advice),
    _Cause("S3 access", re.compile(r"AccessDenied|403 Forbidden|not authorized to perform: s3", re.I),
           _s3_access_advice),
    _Cause("input data", re.compile(r"Data download failed|NoSuchKey|No S3 objects found|NoSuchBucket", re.I),
           _data_advice),
    _Cause("script error", re.compile(r"ExecuteUserScriptError|AlgorithmError"), _script_advice),
)


def failure_kind(job: TrainingJob, error: list[str] | None = None) -> str:
    """
    What kind of failure a job had ('missing package', 'GPU memory', 'memory', 'disk', 'capacity', 'quota',
    'time limit', 'spot interruption', 'S3 access', 'input data', 'script error'), from its failure reason and
    the log's error; '' when it didn't fail or isn't recognised.
    """
    cause = _cause(job, error)
    return cause[0].kind if cause else ""


def _cause(job: TrainingJob, error: list[str] | None) -> tuple[_Cause, re.Match] | None:
    text = "\n".join([job.failure, job.secondary if job.status == "Stopped" else "", *(error or [])])
    for cause in _CAUSES:
        found = cause.pattern.search(text)
        if found:
            return cause, found
    return None


def why_it_failed(job: TrainingJob, error: list[str] | None = None, prices: dict[str, float] | None = None) -> str:
    """
    Why a training job failed, in a sentence or two, with what to change; '' when it didn't fail.
    """
    if job.status != "Failed" and not (job.status == "Stopped" and job.secondary in ("MaxRuntimeExceeded",
                                                                                     "Interrupted")):
        return ""
    prices = SAGEMAKER_PRICES if prices is None else prices
    reason = short_reason(job.failure).rstrip(".")
    said = f" ({reason})" if reason else ""
    cause = _cause(job, error)
    advice = cause[0].advice(job, cause[1], prices) if cause else "The reason and the log below say more."
    return f"Training job {job.name} {job.status.lower()}{said}: {advice}"


def _slow_steps(job: TrainingJob, now: datetime) -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    times = {t.status: t.seconds(now) or 0 for t in job.transitions}
    waited = times.get("Starting", 0) + times.get("Pending", 0)
    if waited >= _SLOW_START_SECONDS and failure_kind(job) != "capacity":
        found.append(("info", (f"It waited {human_runtime(waited)} for its instances to start, unbilled. A warm pool "
                               "(keep_alive_period_in_seconds in the estimator) starts the next job in seconds.")))
    total = sum(times.values())
    downloading = times.get("Downloading", 0)
    if total and downloading / total >= _SLOW_DOWNLOAD_SHARE and job.input_mode != "FastFile":
        found.append(("info", (f"Downloading the data took {human_runtime(downloading)}, "
                               f"{downloading / total:.0%} of the job, billed. input_mode='FastFile' streams it from "
                               "S3 instead, so training starts at once.")))
    return found


def _spot_hint(job: TrainingJob, prices: dict[str, float], now: datetime) -> tuple[str, str] | None:
    seconds = job.seconds(now) or 0
    cost = job.cost(prices, now)
    if job.spot or seconds < _SPOT_WORTH_SECONDS or not cost:
        return None
    return ("info", (f"It ran on demand for {human_runtime(seconds)}, about {human_money(cost)}. Managed spot "
                     f"training often costs up to {_SPOT_SAVING:.0%} less (about {human_money(cost * _SPOT_SAVING)} "
                     "saved here, an estimate): use_spot_instances=True with max_wait and checkpoint_s3_uri in the "
                     "estimator."))


def training_findings(detail: TrainingDetail, prices: dict[str, float] | None = None,
                      now: datetime | None = None) -> list[tuple[str, str]]:
    """
    Plain-language notes about one training job: why it failed and what to change, how long it waited and
    downloaded, whether spot would have been cheaper, and what couldn't be read -> [(level, message)].
    """
    prices = SAGEMAKER_PRICES if prices is None else prices
    now = now or _utcnow()
    job = detail.job
    found: list[tuple[str, str]] = []
    error = next((host.error for host in detail.hosts if host.error), [])
    why = why_it_failed(job, error, prices)
    if why:
        found.append(("warn", why))
    if job.status == "InProgress" and job.max_runtime and (job.seconds(now) or 0) > job.max_runtime * 0.8:
        found.append(("warn", (f"It has run {human_runtime(job.seconds(now))} of its {human_runtime(job.max_runtime)} "
                               "limit; SageMaker stops it at the limit. Its checkpoints are what a new run resumes "
                               "from.")))
    found += _slow_steps(job, now)
    spot = _spot_hint(job, prices, now)
    if spot:
        found.append(spot)
    if detail.logs_read and not detail.hosts and job.status != "InProgress":
        found.append(("info", (f"There are no logs for it in {TRAINING_LOG_GROUP}: it failed before its container "
                               "started, so the failure reason is all there is.")))
    if detail.errors:
        found.append(("info", _couldnt_read(detail.errors, {"logs": "the logs aren't shown"})))
    return found


def training_list_findings(report: TrainingList, prices: dict[str, float] | None = None,
                           now: datetime | None = None) -> list[tuple[str, str]]:
    """
    Plain-language notes about the region's training jobs: which failed and why (the newest first), what kind
    of failure keeps happening, and jobs running close to their limit -> [(level, message)].
    """
    prices = SAGEMAKER_PRICES if prices is None else prices
    now = now or _utcnow()
    found: list[tuple[str, str]] = []
    failed = [job for job in report.jobs if job.status == "Failed"]
    for job in failed[:_SHOWN_FAILURES]:
        reason = short_reason(job.failure).rstrip(".") or "no reason given"
        kind = failure_kind(job)
        label = f" ({kind})" if kind and kind != "script error" else ""
        found.append(("warn", (f"Training job {job.name} failed{label}: {reason}. See what to change: "
                               f"training_job('{job.name}')")))
    if len(failed) > _SHOWN_FAILURES:
        found.append(("warn", f"{_plural(len(failed) - _SHOWN_FAILURES, 'more training job')} failed too."))
    kinds = Counter(failure_kind(job) for job in failed)
    kinds.pop("", None)
    kinds.pop("script error", None)
    for kind, count in kinds.items():
        if count >= 2:
            found.append(("info", (f"{count} of the failures are the same kind ({kind}), so it's likely one fix: "
                                   "training_job() on any of them says what to change.")))
    for job in report.jobs:
        if job.status == "InProgress" and job.max_runtime and (job.seconds(now) or 0) > job.max_runtime * 0.8:
            found.append(("warn", (f"Training job {job.name} has run {human_runtime(job.seconds(now))} of its "
                                   f"{human_runtime(job.max_runtime)} limit; SageMaker stops it at the limit.")))
    if report.cut_short:
        found.append(("info", (f"Only the newest {report.limit:,} jobs were read; pass limit= for more, or since= "
                               "for a shorter window.")))
    if report.errors:
        found.append(("info", _couldnt_read(report.errors, {"training details": "some jobs show no cost"})))
    return found
