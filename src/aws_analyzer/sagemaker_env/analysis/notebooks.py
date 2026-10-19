"""
Notebook instances and Studio apps: their settings and idle shutdown, what they cost, a
smaller type, the commands to stop them, and instance()'s findings.
"""

from __future__ import annotations

import math
from datetime import datetime
from typing import Any, Iterable

from ..._kit.fmt import HOURS_PER_MONTH, _plural, human_money, human_size

from ..prices import INSTANCE_TYPES, SAGEMAKER_PRICES
from ..helpers import (
    GB, _APP_SETTINGS, _arn_name, _image_label, app_label, describe_instance, hourly_price, instance_spec,
)
from ..models import Billable, InstanceReport, Machine, NotebookInfo
from .common import _couldnt_read, _full, _money_per_hour


def parse_notebook_instance(desc: dict[str, Any]) -> NotebookInfo:
    """
    A DescribeNotebookInstance response (or a ListNotebookInstances summary) -> NotebookInfo.
    """
    repos = (
        [desc["DefaultCodeRepository"]] if desc.get("DefaultCodeRepository") else []
    ) + list(desc.get("AdditionalCodeRepositories") or [])
    return NotebookInfo(
        kind="notebook instance",
        name=desc["NotebookInstanceName"],
        arn=desc.get("NotebookInstanceArn", ""),
        status=desc.get("NotebookInstanceStatus", ""),
        instance_type=desc.get("InstanceType", ""),
        created=desc.get("CreationTime"),
        changed=desc.get("LastModifiedTime"),
        volume_gb=desc.get("VolumeSizeInGB"),
        role_arn=desc.get("RoleArn", ""),
        lifecycle_configs=[desc["NotebookInstanceLifecycleConfigName"]]
        if desc.get("NotebookInstanceLifecycleConfigName")
        else [],
        internet={"Enabled": "direct", "Disabled": "through your VPC only"}.get(
            desc.get("DirectInternetAccess", ""), ""
        ),
        root_access=None
        if "RootAccess" not in desc
        else desc["RootAccess"] == "Enabled",
        platform=desc.get("PlatformIdentifier", ""),
        subnet=desc.get("SubnetId", ""),
        url=desc.get("Url", ""),
        code_repositories=repos,
        failure=desc.get("FailureReason", ""),
    )


def parse_app(desc: dict[str, Any]) -> NotebookInfo:
    """
    A DescribeApp response (or a ListApps item) -> NotebookInfo, without the space and domain settings
    (apply_studio_settings adds those).
    """
    spec = desc.get("ResourceSpec") or {}
    return NotebookInfo(
        kind="studio app",
        name=desc.get("AppName", ""),
        arn=desc.get("AppArn", ""),
        status=desc.get("Status", ""),
        instance_type=spec.get("InstanceType", ""),
        created=desc.get("CreationTime"),
        lifecycle_configs=[_arn_name(spec["LifecycleConfigArn"])]
        if spec.get("LifecycleConfigArn")
        else [],
        platform=_image_label(spec),
        domain_id=desc.get("DomainId", ""),
        user_profile=desc.get("UserProfileName", ""),
        space=desc.get("SpaceName", ""),
        app_type=desc.get("AppType", ""),
        failure=desc.get("FailureReason", ""),
    )


def _idle_settings(settings: dict[str, Any] | None, key: str) -> dict[str, Any]:
    return (((settings or {}).get(key) or {}).get("AppLifecycleManagement") or {}).get(
        "IdleSettings"
    ) or {}


def studio_idle(
    app_type: str,
    domain: dict[str, Any] | None,
    profile: dict[str, Any] | None = None,
    space: dict[str, Any] | None = None,
) -> tuple[str, int | None, str]:
    """
    Whether SageMaker shuts a Studio app down when it sits idle -> (state, minutes, where it's set).
    state is 'on', 'off', 'unknown' (the domain couldn't be read) or 'n/a' (a free app). The domain (or the
    user profile) turns idle shutdown on; the space, the user profile or the domain sets the timeout.
    """
    if app_type == "JupyterServer":
        return "n/a", None, ""  # Studio Classic's own server runs on a free instance
    key = _APP_SETTINGS.get(app_type)
    if key is None:
        return (
            ("off", None, "Studio Classic")
            if app_type == "KernelGateway"
            else ("unknown", None, "")
        )
    if domain is None:
        return "unknown", None, ""
    shared = ((space or {}).get("SpaceSharingSettings") or {}).get(
        "SharingType"
    ) == "Shared"
    domain_idle = (
        _idle_settings(domain.get("DefaultSpaceSettings"), key) if shared else {}
    ) or _idle_settings(domain.get("DefaultUserSettings"), key)
    profile_idle = _idle_settings((profile or {}).get("UserSettings"), key)
    space_idle = _idle_settings((space or {}).get("SpaceSettings"), key)
    switch = profile_idle.get("LifecycleManagement") or domain_idle.get(
        "LifecycleManagement"
    )
    if switch != "ENABLED":
        return (
            "off",
            None,
            "user profile" if profile_idle.get("LifecycleManagement") else "domain",
        )
    for source, settings in (
        ("space", space_idle),
        ("user profile", profile_idle),
        ("domain", domain_idle),
    ):
        if settings.get("IdleTimeoutInMinutes"):
            return "on", settings["IdleTimeoutInMinutes"], source
    return "on", None, "domain"


def apply_studio_settings(
    nb: NotebookInfo,
    *,
    domain: dict[str, Any] | None = None,
    profile: dict[str, Any] | None = None,
    space: dict[str, Any] | None = None,
) -> NotebookInfo:
    """
    Fill in a Studio app's settings from DescribeDomain, DescribeUserProfile and DescribeSpace responses:
    idle shutdown, role, network, storage volume, sharing and code repositories.
    """
    if space:
        settings = space.get("SpaceSettings") or {}
        nb.volume_gb = (
            (settings.get("SpaceStorageSettings") or {}).get("EbsStorageSettings") or {}
        ).get("EbsVolumeSizeInGb", nb.volume_gb)
        nb.shared = (space.get("SpaceSharingSettings") or {}).get(
            "SharingType"
        ) == "Shared"
        nb.user_profile = nb.user_profile or (space.get("OwnershipSettings") or {}).get(
            "OwnerUserProfileName", ""
        )
        nb.url = space.get("Url") or nb.url
        app_settings = settings.get(_APP_SETTINGS.get(nb.app_type, "")) or {}
        nb.code_repositories = [
            r["RepositoryUrl"]
            for r in app_settings.get("CodeRepositories") or []
            if r.get("RepositoryUrl")
        ] or nb.code_repositories
        if not nb.instance_type:  # no app running: the type it starts with
            nb.instance_type = (app_settings.get("DefaultResourceSpec") or {}).get(
                "InstanceType", ""
            )
    nb.idle, nb.idle_minutes, nb.idle_source = studio_idle(
        nb.app_type, domain, profile, space
    )
    user_settings = (profile or {}).get("UserSettings") or {}
    domain = domain or {}
    space_defaults = domain.get("DefaultSpaceSettings") or {}
    user_defaults = domain.get("DefaultUserSettings") or {}
    nb.role_arn = nb.role_arn or (
        (space_defaults.get("ExecutionRole") if nb.shared else "")
        or user_settings.get("ExecutionRole")
        or user_defaults.get("ExecutionRole")
        or ""
    )
    nb.internet = {
        "PublicInternetOnly": "direct",
        "VpcOnly": "through your VPC only",
    }.get(domain.get("AppNetworkAccessType", ""), nb.internet)
    return nb


def notebook_costs(
    nb: NotebookInfo, prices: dict[str, float] | None = None
) -> dict[str, float | None]:
    """
    Estimated USD for a notebook: 'hourly' (its instance), 'month_if_on' (running around the clock) and
    'storage' (its volume per month, billed whether it runs or not; None for Studio Classic's EFS home).
    """
    prices = SAGEMAKER_PRICES if prices is None else prices
    hourly = hourly_price(nb.instance_type, prices) if nb.instance_type else None
    return {
        "hourly": hourly,
        "month_if_on": None if hourly is None else hourly * HOURS_PER_MONTH,
        "storage": None
        if nb.volume_gb is None
        else nb.volume_gb * prices.get(nb.storage_price_key, 0.0),
    }


def smaller_type(
    instance_type: str,
    *,
    vcpus: float,
    memory_gib: float,
    gpus: int = 0,
    prices: dict[str, float] | None = None,
    allowed: Iterable[str] | None = None,
    burstable: bool = True,
) -> str | None:
    """
    The cheapest type with at least `vcpus`, `memory_gib` and `gpus` (and no GPUs when gpus=0) that costs at least
    a quarter less than `instance_type`. allowed: the types this kind of notebook can use; burstable=False leaves out
    the t3 types, which slow down under steady load. None when nothing fits.
    """
    prices = SAGEMAKER_PRICES if prices is None else prices
    current = hourly_price(instance_type, prices)
    if not current:
        return None
    allowed = set(allowed) if allowed is not None else None
    fits = [
        (prices[name], name)
        for name, (_, cpu, memory, gpu) in INSTANCE_TYPES.items()
        if cpu >= vcpus
        and memory >= memory_gib
        and (gpu >= gpus if gpus else gpu == 0)
        and name in prices
        and (allowed is None or name in allowed)
        and name != instance_type
        and not name.startswith(
            ("ml.trn", "ml.inf", "ml.t2.")
        )  # accelerators, and t2 (a previous generation)
        and (burstable or not name.startswith("ml.t"))
    ]
    if not fits:
        return None
    price, name = min(fits)
    return name if price <= current * 0.75 else None


def stop_command(b: Billable | NotebookInfo) -> str:
    """
    The AWS CLI command that stops a notebook instance or a Studio app, or deletes an endpoint.
    """
    kind = b.kind
    if kind == "notebook instance":
        return f"aws sagemaker stop-notebook-instance --notebook-instance-name {b.name}"
    if kind in ("studio app", "Studio app"):
        name = b.name if isinstance(b, NotebookInfo) else (b.app_name or b.name)
        owner = (
            f"--space-name {b.space}"
            if b.space
            else f"--user-profile-name {b.user_profile}"
        )
        return (
            f"aws sagemaker delete-app --domain-id {b.domain_id} {owner} --app-type {b.app_type} "
            f"--app-name {name}"
        )
    if kind == "endpoint":
        return f"aws sagemaker delete-endpoint --endpoint-name {b.name}"
    if kind == "training job":
        return f"aws sagemaker stop-training-job --training-job-name {b.name}"
    return f"aws sagemaker stop-processing-job --processing-job-name {b.name}"


AUTOSTOP_SAMPLE = (
    "https://github.com/aws-samples/amazon-sagemaker-notebook-instance-lifecycle-config-samples"
    "/tree/master/scripts/auto-stop-idle"
)


def idle_shutdown_commands(nb: NotebookInfo, minutes: int = 60) -> str:
    """
    The commands that turn on idle shutdown for this notebook (shown, never run), or '' when there's no setting
    for it.
    """
    if nb.kind == "notebook instance":
        return "\n".join(
            [
                f"# 1. Save on-start.sh from {AUTOSTOP_SAMPLE}",
                f"#    and set IDLE_TIME in it (seconds idle before it stops: {minutes * 60} is {minutes} minutes).",
                "# 2. Make it a lifecycle configuration:",
                "aws sagemaker create-notebook-instance-lifecycle-config \\",
                "    --notebook-instance-lifecycle-config-name auto-stop-idle \\",
                '    --on-start Content="$(base64 -w0 on-start.sh)"',
                "# 3. Attach it while the notebook instance is stopped, then start it again:",
                f"aws sagemaker stop-notebook-instance --notebook-instance-name {nb.name}",
                f"aws sagemaker update-notebook-instance --notebook-instance-name {nb.name} "
                "--lifecycle-config-name auto-stop-idle",
                f"aws sagemaker start-notebook-instance --notebook-instance-name {nb.name}",
            ]
        )
    key = _APP_SETTINGS.get(nb.app_type)
    if key is None or not nb.domain_id:
        return ""
    return "\n".join(
        [
            f"# An admin turns idle shutdown on for every {app_label(nb.app_type)} app in the domain. The settings",
            f"# replace the domain's current {key}: check them first with",
            f"#   aws sagemaker describe-domain --domain-id {nb.domain_id}",
            "# and add the ones you want to keep to this JSON.",
            f"aws sagemaker update-domain --domain-id {nb.domain_id} \\",
            "    --default-user-settings '{",
            f'      "{key}": {{"AppLifecycleManagement": {{"IdleSettings": {{',
            f'        "LifecycleManagement": "ENABLED", "IdleTimeoutInMinutes": {minutes}}}}}}}}}\'',
        ]
    )


def _gpu_idle(machine: Machine | None) -> bool:
    return bool(machine and machine.gpus and all(g.idle for g in machine.gpus))


def _other_kernels(machine: Machine) -> tuple[int, int]:
    """
    (count, bytes) of the Jupyter kernels other than this one.
    """
    others = machine.kernels - (1 if machine.this_memory is not None else 0)
    return max(others, 0), max(machine.kernels_memory - (machine.this_memory or 0), 0)


def instance_findings(
    report: InstanceReport,
    prices: dict[str, float] | None = None,
    allowed: Iterable[str] | None = None,
    now: datetime | None = None,
) -> list[tuple[str, str]]:
    """
    Plain-language risks and cost notes for a notebook (and, when it's this one, its machine), each with what to
    do about it -> [(level, message)]. allowed: the instance types this kind of notebook can switch to.
    """
    prices = SAGEMAKER_PRICES if prices is None else prices
    found: list[tuple[str, str]] = []
    nb, m = report.notebook, report.machine
    hourly = hourly_price(nb.instance_type, prices) if nb and nb.instance_type else None
    if nb is not None:
        if nb.status and nb.status not in ("InService", "Stopped"):
            why = f": {nb.failure}" if nb.failure else ""
            found.append(
                ("warn" if nb.status == "Failed" else "info", f"It's {nb.status}{why}.")
            )
        if nb.idle == "off" and (nb.billing or report.current):
            cost = _money_per_hour(hourly)
            if nb.kind == "notebook instance":
                found.append(
                    (
                        "warn",
                        (
                            f"Nothing stops this notebook instance when it sits idle, so it bills {cost} until someone "
                            "stops it. A lifecycle configuration with AWS's auto-stop-idle script stops it after an idle "
                            "hour (the steps are under 'Turn on auto-stop' below). Until then, stop it when you're done: "
                            f"{stop_command(nb)}"
                        ),
                    )
                )
            elif nb.app_type in _APP_SETTINGS:
                found.append(
                    (
                        "warn",
                        (
                            f"Domain {nb.domain_id} doesn't shut idle {app_label(nb.app_type)} apps down, so this one bills "
                            f"{cost} until someone stops it. An admin can turn idle shutdown on for the domain (the command "
                            "is under 'Turn on idle shutdown' below). Until then, stop the app when you're done; the files "
                            f"in the space stay: {stop_command(nb)}"
                        ),
                    )
                )
            else:
                found.append(
                    (
                        "warn",
                        (
                            f"Studio Classic apps have no idle shutdown, so this one bills {cost} until it's shut down. "
                            "Shut it down from the Running Terminals and Kernels panel when you're done, or: "
                            f"{stop_command(nb)}"
                        ),
                    )
                )
        if nb.platform == "notebook-al1-v1":
            found.append(
                (
                    "warn",
                    (
                        "This notebook instance runs Amazon Linux 1, which gets no security fixes and can't install "
                        "current packages. New notebook instances use notebook-al2023-v1; to move this one, stop it, then: "
                        f"aws sagemaker update-notebook-instance --notebook-instance-name {nb.name} "
                        "--platform-identifier notebook-al2023-v1 (copy anything outside ~/SageMaker to S3 first)."
                    ),
                )
            )
        if nb.instance_type and hourly is None:
            found.append(
                (
                    "info",
                    (
                        f"{nb.instance_type} isn't in the price table, so its cost shows as unknown. "
                        f"SageMakerAnalyzer(prices={{'{nb.instance_type}': 1.23}}) adds its price per hour."
                    ),
                )
            )
        consequences = {
            "domain": "idle shutdown shows as unknown",
            "lifecycle": "auto-stop shows as unknown",
        }
        if nb.errors:
            found.append(("info", _couldnt_read(nb.errors, consequences)))
    if m is not None:
        for volume in m.volumes:
            if not volume.elastic and _full(volume.share):
                found.append(
                    (
                        "warn",
                        (
                            f"The {volume.label} ({volume.path}) is {volume.share:.0%} full: {human_size(volume.free)} free "
                            f"of {human_size(volume.total)}. When it fills, saving notebooks and installing packages fail. "
                            f"disk({volume.path!r}) shows what fills it and what you can clear."
                        ),
                    )
                )
        share = m.memory_share
        others, others_memory = _other_kernels(m)
        if _full(share):
            text = (
                f"Memory is {share:.0%} used ({human_size(m.memory_used)} of {human_size(m.memory_total)}): the "
                "next big DataFrame or model can crash the kernel."
            )
            if others and others_memory:
                text += (
                    f" {_plural(others, 'other notebook kernel')} {'holds' if others == 1 else 'hold'} "
                    f"{human_size(others_memory)}; shutting "
                    "down the ones you don't need frees it (Running Terminals and Kernels panel)."
                )
            found.append(("warn", text + " The biggest processes are listed below."))
        elif others >= 3 and m.memory_total and others_memory >= 0.25 * m.memory_total:
            found.append(
                (
                    "info",
                    (
                        f"{_plural(others, 'other notebook kernel')} {'holds' if others == 1 else 'hold'} "
                        f"{human_size(others_memory)} "
                        f"({others_memory / m.memory_total:.0%} of memory). Shutting down the ones you don't need "
                        "(Running Terminals and Kernels panel) leaves room for this one."
                    ),
                )
            )
        spec = instance_spec(nb.instance_type) if nb else None
        if _gpu_idle(m) and nb is not None and hourly:
            used = sum(g.memory_used or 0 for g in m.gpus)
            total = sum(g.memory_total or 0 for g in m.gpus)
            text = (
                f"This {nb.instance_type} costs {human_money(hourly)}/hour, and its "
                f"{'GPU is' if len(m.gpus) == 1 else 'GPUs are'} idle right now (under 5% busy, "
                f"{human_size(used)} of {human_size(total)} memory in use)."
            )
            cheaper = (
                smaller_type(
                    nb.instance_type,
                    vcpus=spec[0],
                    memory_gib=spec[1],
                    prices=prices,
                    allowed=allowed,
                    burstable=False,
                )
                if spec
                else None
            )
            if cheaper:
                saving = hourly - prices[cheaper]
                text += (
                    f" If this notebook doesn't use the GPU, {cheaper} ({describe_instance(cheaper)}) has as "
                    f"many vCPUs and as much memory without one, for {human_money(prices[cheaper])}/hour: "
                    f"{human_money(saving)}/hour less, up to {human_money(saving * HOURS_PER_MONTH)}/month. "
                    "Switch when the notebook is stopped."
                )
            found.append(("warn", text))
        cpu = m.cpu_share
        if (
            spec
            and not spec[2]
            and nb is not None
            and hourly
            and cpu is not None
            and cpu < 0.25
            and share is not None
            and share < 0.3
            and m.memory_used is not None
            and m.load is not None
        ):
            need_memory = max(4.0, m.memory_used / GB * 1.5)
            need_cpus = max(2, math.ceil(m.load[2] * 2))
            cheaper = smaller_type(
                nb.instance_type,
                vcpus=need_cpus,
                memory_gib=need_memory,
                prices=prices,
                allowed=allowed,
            )
            if cheaper:
                saving = hourly - prices[cheaper]
                found.append(
                    (
                        "info",
                        (
                            f"This {nb.instance_type} ({describe_instance(nb.instance_type)}) is mostly idle: the CPU load "
                            f"averaged {m.load[2]:.1f} of {m.cpus} vCPUs over 15 minutes, and "
                            f"{human_size(m.memory_used)} of memory is in use. If that's typical, {cheaper} "
                            f"({describe_instance(cheaper)}) costs {human_money(prices[cheaper])}/hour: "
                            f"{human_money(saving)}/hour less. Switch when the notebook is stopped."
                        ),
                    )
                )
        if m.errors:
            found.append(
                (
                    "info",
                    "Couldn't read this machine's "
                    + ", ".join(
                        f"{part} ({reason})" for part, reason in m.errors.items()
                    )
                    + ".",
                )
            )
    if report.errors:
        found.append(("info", _couldnt_read(report.errors)))
    return found
