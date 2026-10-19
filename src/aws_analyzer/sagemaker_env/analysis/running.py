"""
Everything running and billing: endpoints and jobs as Billables, what looks forgotten, and
running()'s findings.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from ..._kit.fmt import _plural, _utcnow, human_age, human_money

from ..prices import SAGEMAKER_PRICES
from ..helpers import _APP_SETTINGS, _since, app_label, human_runtime
from ..models import Billable, RunningReport
from .common import _couldnt_read, _money_per_hour
from .notebooks import stop_command


def parse_endpoint(
    desc: dict[str, Any], config: dict[str, Any] | None = None
) -> Billable:
    """
    DescribeEndpoint (+ DescribeEndpointConfig for the instance types) -> Billable.
    """
    b = Billable(
        "endpoint",
        desc["EndpointName"],
        desc.get("EndpointStatus", ""),
        since=desc.get("CreationTime"),
        config=desc.get("EndpointConfigName", ""),
        arn=desc.get("EndpointArn", ""),
    )
    config = config or {}
    configured = {
        v["VariantName"]: v
        for v in (config.get("ProductionVariants") or [])
        + (config.get("ShadowProductionVariants") or [])
    }
    serverless = False
    for variant in (desc.get("ProductionVariants") or []) + (
        desc.get("ShadowProductionVariants") or []
    ):
        b.variants.append(variant["VariantName"])
        setting = configured.get(variant["VariantName"], {})
        if variant.get("CurrentServerlessConfig") or setting.get("ServerlessConfig"):
            serverless = True
            continue
        count = variant.get(
            "CurrentInstanceCount", setting.get("InitialInstanceCount", 1)
        )
        pools = variant.get("InstancePools") or []
        if pools:
            b.instances += [
                (p["InstanceType"], p.get("CurrentInstanceCount", 0))
                for p in pools
                if p.get("CurrentInstanceCount")
            ]
        elif setting.get("InstanceType") and count:
            b.instances.append((setting["InstanceType"], count))
    b.serverless = serverless and not b.instances
    return b


def parse_training_job(desc: dict[str, Any]) -> Billable:
    """
    DescribeTrainingJob -> Billable.
    """
    resources = desc.get("ResourceConfig") or {}
    groups = resources.get("InstanceGroups") or []
    if groups:
        instances = [(g["InstanceType"], g.get("InstanceCount", 1)) for g in groups]
    else:
        instance_type = (
            resources.get("SelectedInstanceType") or resources.get("InstanceType") or ""
        )
        instances = [
            (
                instance_type,
                resources.get("SelectedInstanceCount")
                or resources.get("InstanceCount", 1),
            )
        ]
    return Billable(
        "training job",
        desc["TrainingJobName"],
        desc.get("SecondaryStatus") or desc.get("TrainingJobStatus", ""),
        instances=[i for i in instances if i[0]],
        since=desc.get("TrainingStartTime") or desc.get("CreationTime"),
        spot=bool(desc.get("EnableManagedSpotTraining")),
        arn=desc.get("TrainingJobArn", ""),
    )


def parse_processing_job(desc: dict[str, Any]) -> Billable:
    """
    DescribeProcessingJob -> Billable.
    """
    cluster = (desc.get("ProcessingResources") or {}).get("ClusterConfig") or {}
    instance_type = (
        cluster.get("SelectedInstanceType") or cluster.get("InstanceType") or ""
    )
    count = cluster.get("SelectedInstanceCount") or cluster.get("InstanceCount", 1)
    return Billable(
        "processing job",
        desc["ProcessingJobName"],
        desc.get("ProcessingJobStatus", ""),
        instances=[(instance_type, count)] if instance_type else [],
        since=desc.get("ProcessingStartTime") or desc.get("CreationTime"),
        arn=desc.get("ProcessingJobArn", ""),
    )


LONG_RUNNING = (
    12 * 3600
)  # a notebook running this long with nothing to stop it looks forgotten


def _forgotten(b: Billable, now: datetime | None = None) -> bool:
    """
    A notebook or app (not this one) running for LONG_RUNNING or more with nothing to stop it when idle.
    """
    return (
        b.kind in ("notebook instance", "Studio app")
        and b.idle == "off"
        and not b.this
        and (_since(b.since, now) or 0) >= LONG_RUNNING
    )


def _unused_endpoint(b: Billable, now: datetime | None = None) -> bool:
    """
    An endpoint on instances, at least a day old, that got no requests in the window counted.
    """
    return (
        b.kind == "endpoint"
        and not b.serverless
        and b.invocations == 0
        and (_since(b.since, now) or 0) >= 86400
    )


def running_findings(
    report: RunningReport,
    prices: dict[str, float] | None = None,
    now: datetime | None = None,
) -> list[tuple[str, str]]:
    """
    Plain-language notes about what's running: what looks forgotten and what it costs, with the command that
    stops it -> [(level, message)].
    """
    prices = SAGEMAKER_PRICES if prices is None else prices
    now = now or _utcnow()
    found: list[tuple[str, str]] = []
    forgotten = [b for b in report.resources if _forgotten(b, now)]
    forgotten.sort(key=lambda b: -(b.hourly(prices) or 0))
    for b in forgotten[:5]:
        hourly = b.hourly(prices)
        runtime = _since(b.since, now) or 0
        so_far = (
            f", about {human_money(hourly * runtime / 3600)} so far" if hourly else ""
        )
        if b.kind == "notebook instance":
            what = f"Notebook instance {b.name} ({b.instance_label})"
            nothing = "with nothing to stop it when idle"
        else:
            what = f"{app_label(b.app_type)} app in {'space ' + b.space if b.space else 'user ' + b.user_profile}"
            what += f" ({b.instance_label})"
            nothing = (
                f"and domain {b.domain_id} doesn't shut idle apps down"
                if b.app_type in _APP_SETTINGS
                else "and Studio Classic apps have no idle shutdown"
            )
        after = "; the files in the space stay" if b.space else ""
        found.append(
            (
                "warn",
                (
                    f"{what} has been running for {human_runtime(runtime)}{so_far}, {nothing}. It bills "
                    f"{_money_per_hour(hourly)}. If nobody's using it, stop it{after}: {stop_command(b)}"
                ),
            )
        )
    if len(forgotten) > 5:
        rest = forgotten[5:]
        cost = sum(b.hourly(prices) or 0 for b in rest)
        found.append(
            (
                "warn",
                (
                    f"{_plural(len(rest), 'more notebook')} ({', '.join(b.name for b in rest[:6])}"
                    f"{', …' if len(rest) > 6 else ''}) run with nothing to stop them when idle: "
                    f"{human_money(cost)}/hour together."
                ),
            )
        )
    for b in report.resources:
        if not _unused_endpoint(b, now):
            continue
        hourly = b.hourly(prices)
        back = (
            (
                f" (its endpoint configuration {b.config} stays, so aws sagemaker create-endpoint --endpoint-name "
                f"{b.name} --endpoint-config-name {b.config} brings it back)"
            )
            if b.config
            else ""
        )
        found.append(
            (
                "warn",
                (
                    f"Endpoint {b.name} ({b.instance_label}) got no requests in the last {report.days} days but bills "
                    f"{_money_per_hour(hourly)}. If nothing uses it, delete it{back}: {stop_command(b)}"
                ),
            )
        )
    storage = [
        (b, (b.volume_gb or 0) * prices.get("notebook_storage", 0.0))
        for b in report.stopped
    ]
    monthly = sum(cost for _, cost in storage)
    if monthly >= 1:
        oldest = min(
            ((b.since, b.name) for b, _ in storage if b.since), default=None
        )
        keeps = (
            "keeps its storage volume"
            if len(storage) == 1
            else "keep their storage volumes"
        )
        text = (
            f"{_plural(len(storage), 'stopped notebook instance')} {keeps} "
            f"({sum(b.volume_gb or 0 for b, _ in storage):,} GB), costing {human_money(monthly)}/month."
        )
        if oldest is not None:
            since, name = oldest
            text += (
                f" The one stopped longest is {name} ({human_age(since, now)}). Deleting a "
                "notebook instance deletes its volume, so copy what you need to S3 first: aws sagemaker "
                f"delete-notebook-instance --notebook-instance-name {name}"
            )
        found.append(("info", text))
    spot = [b for b in report.resources if b.spot]
    if spot:
        found.append(
            (
                "info",
                (
                    f"{_plural(len(spot), 'training job')} use{'s' if len(spot) == 1 else ''} spot capacity, which costs "
                    "less than the on-demand price shown here."
                ),
            )
        )
    if report.errors:
        consequences = {"metrics": "endpoints show unknown traffic"}
        found.append(("info", _couldnt_read(report.errors, consequences)))
    return found
