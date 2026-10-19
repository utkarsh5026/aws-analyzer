"""
SageMakerAnalyzer: talks to SageMaker, CloudWatch and STS, and returns the dataclasses in models.
Never prints, and never stops, deletes or changes anything in AWS.
"""

from __future__ import annotations

import base64
import difflib
import os
from datetime import timedelta
from typing import Any, Callable

from botocore.exceptions import BotoCoreError, ClientError, NoRegionError

from .._kit.analyzer import _BaseAnalyzer
from .._kit.errors import _error_code, _error_name
from .._kit.fmt import _utcnow

from .prices import SAGEMAKER_PRICES
from .helpers import _arn_name, lifecycle_idle
from .models import _BILLED, Billable, Environment, InstanceReport, NotebookInfo, RunningReport
from .analysis.machine import parse_metadata
from .analysis.notebooks import apply_studio_settings, parse_app, parse_notebook_instance, studio_idle
from .analysis.running import parse_endpoint, parse_processing_job, parse_training_job
from .machine import _MachineReads
from .envs import _EnvMaking, _EnvReads


def _missing(exc: ClientError) -> bool:
    """
    A 'no such notebook' error (DescribeNotebookInstance answers ValidationException: RecordNotFound).
    """
    message = str(exc.response.get("Error", {}).get("Message", "")).lower()
    return _error_code(exc) in ("ResourceNotFound", "ValidationException") and any(
        words in message
        for words in ("recordnotfound", "not found", "does not exist", "could not find")
    )


class SageMakerAnalyzer(_MachineReads, _EnvReads, _EnvMaking, _BaseAnalyzer):
    """
    Pure-logic SageMaker analysis: every method returns data; nothing is printed, stopped or deleted. The one
    method that makes anything is create_env(), which runs the commands of an env_plan() (a Python environment and its
    Jupyter kernel).

    instance() and disk() read this machine (SageMaker's metadata file, /proc, the disks, nvidia-smi) as well as
    AWS, so they describe the notebook the code runs in. `root` is where that machine's files are ("/"; tests
    point it at a folder of fake files). `prices` overrides SAGEMAKER_PRICES (storage, and USD per hour by
    instance type). `clients` pre-fills the boto3 clients by service name ('sagemaker', 'cloudwatch', 'sts'),
    e.g. to use stubbed ones.
    """

    _LIST_PRICES = SAGEMAKER_PRICES

    def __init__(
        self,
        session: Any = None,
        *,
        region: str | None = None,
        profile: str | None = None,
        client: Any = None,
        clients: dict[str, Any] | None = None,
        prices: dict[str, float] | None = None,
        root: str = "/",
    ):
        super().__init__(session, region=region, profile=profile, prices=prices)
        self._clients: dict[str, Any] = dict(clients or {})
        if client is not None:
            self._clients["sagemaker"] = client
        self.root = root
        self.pid = os.getpid()  # this kernel, to tell it apart from the others
        self.max_workers = (
            8  # notebooks, endpoints and jobs described in parallel by running()
        )
        self._env: Environment | None = None
        self._lifecycle: dict[str, tuple[bool, int | None]] = {}
        self._domains: dict[str, dict[str, Any]] = {}

    # ------------------------------------------------------------------ clients

    def _service(self, name: str) -> Any:
        """
        A boto3 client, made on first use. Without a region set, the notebook's own region is used (from its
        metadata), so a missing region only matters outside SageMaker.
        """
        if name not in self._clients:
            region = self.session.region_name or self.environment().region or None
            makers = {  # named one by one so the project's read-only check can see which services are used
                "sagemaker": lambda: self.session.client(
                    "sagemaker", region_name=region, config=self._config
                ),
                "cloudwatch": lambda: self.session.client(
                    "cloudwatch", region_name=region, config=self._config
                ),
                "sts": lambda: self.session.client(
                    "sts", region_name=region, config=self._config
                ),
            }
            try:
                self._clients[name] = makers[name]()
            except NoRegionError:
                raise ValueError(
                    "No AWS region is set, and SageMaker is regional. Pass one: "
                    "SageMakerView(SageMakerAnalyzer(region='us-east-1')), or set AWS_DEFAULT_REGION."
                ) from None
        return self._clients[name]

    @property
    def client(self) -> Any:
        """
        The sagemaker client.
        """
        return self._service("sagemaker")

    def allowed_types(self, kind: str) -> set[str]:
        """
        The instance types a notebook instance ('notebook instance') or a Studio app ('studio app') can use,
        from botocore's service model.
        """
        model = self.client.meta.service_model
        shape = (
            model.operation_model("CreateNotebookInstance").input_shape.members[
                "InstanceType"
            ]
            if kind == "notebook instance"
            else model.shape_for("AppInstanceType")
        )
        return set(shape.enum or []) - {"system"}

    # --------------------------------------------------------------- notebooks

    def identity(self) -> dict[str, str]:
        """
        {'Account': ..., 'Arn': ...}: who AWS sees (in a notebook, its execution role). Needs no permission.
        """
        resp = self._service("sts").get_caller_identity()
        return {"Account": resp.get("Account", ""), "Arn": resp.get("Arn", "")}

    def _lifecycle_idle(self, name: str) -> tuple[bool, int | None]:
        """
        Does lifecycle configuration `name` stop the notebook when idle? (cached)
        """
        if name not in self._lifecycle:
            desc = self.client.describe_notebook_instance_lifecycle_config(
                NotebookInstanceLifecycleConfigName=name
            )
            scripts = []
            for hook in (desc.get("OnStart") or []) + (desc.get("OnCreate") or []):
                try:
                    scripts.append(
                        base64.b64decode(hook.get("Content", "")).decode(
                            "utf-8", "replace"
                        )
                    )
                except ValueError:
                    continue
            self._lifecycle[name] = lifecycle_idle(scripts)
        return self._lifecycle[name]

    def notebook_instance(self, name: str, *, lifecycle: bool = True) -> NotebookInfo:
        """
        A notebook instance's settings, and (lifecycle=True) whether its lifecycle configuration stops it when
        idle. Raises ClientError when there's no such notebook instance.
        """
        nb = parse_notebook_instance(
            self.client.describe_notebook_instance(NotebookInstanceName=name)
        )
        nb.idle = "off"
        if lifecycle and nb.lifecycle_configs:
            config = nb.lifecycle_configs[0]
            try:
                stops, minutes = self._lifecycle_idle(config)
                nb.idle, nb.idle_minutes = ("on", minutes) if stops else ("off", None)
                nb.idle_source = f"lifecycle configuration {config!r}"
            except (ClientError, BotoCoreError) as exc:
                nb.idle = "unknown"
                nb.errors["lifecycle"] = _error_name(exc)
        elif not lifecycle:
            nb.idle = "unknown"
        return nb

    def _describe_domain(self, domain_id: str) -> dict[str, Any]:
        if domain_id not in self._domains:
            self._domains[domain_id] = self.client.describe_domain(DomainId=domain_id)
        return self._domains[domain_id]

    def _with_studio_settings(self, nb: NotebookInfo) -> NotebookInfo:
        """
        Add the domain's, the user profile's and the space's settings; unreadable ones go in nb.errors.
        """

        def get(section: str, call: Callable[[], Any]) -> Any:
            try:
                return call()
            except (ClientError, BotoCoreError) as exc:
                nb.errors[section] = _error_name(exc)
            return None

        domain = (
            get("domain", lambda: self._describe_domain(nb.domain_id))
            if nb.domain_id
            else None
        )
        space = None
        if nb.space:
            space = get(
                "space",
                lambda: self.client.describe_space(
                    DomainId=nb.domain_id, SpaceName=nb.space
                ),
            )
        owner = nb.user_profile or ((space or {}).get("OwnershipSettings") or {}).get(
            "OwnerUserProfileName", ""
        )
        profile = None
        if (
            owner
            and not (space or {}).get("SpaceSharingSettings", {}).get("SharingType")
            == "Shared"
        ):
            profile = get(
                "profile",
                lambda: self.client.describe_user_profile(
                    DomainId=nb.domain_id, UserProfileName=owner
                ),
            )
        return apply_studio_settings(nb, domain=domain, profile=profile, space=space)

    def studio_app(
        self,
        domain_id: str,
        app_type: str,
        app_name: str,
        *,
        space: str = "",
        user_profile: str = "",
    ) -> NotebookInfo:
        """
        A Studio app with its space's, user profile's and domain's settings. Raises ClientError when the app
        can't be described.
        """
        owner = {"SpaceName": space} if space else {"UserProfileName": user_profile}
        desc = self.client.describe_app(
            DomainId=domain_id, AppType=app_type, AppName=app_name, **owner
        )
        return self._with_studio_settings(parse_app(desc))

    def _space_app(
        self, domain_id: str, space: str, summary: dict[str, Any] | None = None
    ) -> NotebookInfo:
        """
        The app running in a space (or, when none is, the space with the app it would start).
        """
        apps = [
            a
            for a in self._paginate(
                "list_apps", "Apps", DomainIdEquals=domain_id, SpaceNameEquals=space
            )
            if a.get("Status") in ("InService", "Pending")
        ]
        if apps:
            app = apps[0]
            return self.studio_app(
                domain_id, app["AppType"], app["AppName"], space=space
            )
        app_type = ((summary or {}).get("SpaceSettingsSummary") or {}).get(
            "AppType"
        ) or "JupyterLab"
        nb = NotebookInfo(
            "studio app",
            "",
            status="Stopped",
            domain_id=domain_id,
            space=space,
            app_type=app_type,
        )
        return self._with_studio_settings(nb)

    def notebook(self, name: str) -> NotebookInfo:
        """
        A notebook by name: a notebook instance, a Studio space ('analysis', or 'd-abc123/analysis' when spaces
        in two domains share the name), or an app or notebook instance ARN.
        """
        name = str(name).strip()
        if ":notebook-instance/" in name:
            return self.notebook_instance(_arn_name(name))
        if ":app/" in name:
            env = parse_metadata({"ResourceArn": name})
            return self.studio_app(
                env.domain_id,
                env.app_type,
                env.name,
                space=env.space,
                user_profile=env.user_profile,
            )
        domain_id, space = (
            name.split("/", 1) if name.startswith("d-") and "/" in name else ("", name)
        )
        if not domain_id:
            try:
                return self.notebook_instance(name)
            except ClientError as exc:
                if not _missing(exc):
                    raise
        domains = (
            [domain_id]
            if domain_id
            else [d["DomainId"] for d in self._paginate("list_domains", "Domains")]
        )
        matches: list[dict[str, Any]] = []
        known: list[str] = []
        for d in domains:
            for summary in self._paginate("list_spaces", "Spaces", DomainIdEquals=d):
                known.append(summary["SpaceName"])
                if summary["SpaceName"] == space:
                    matches.append(summary)
        if len(matches) == 1:
            return self._space_app(matches[0]["DomainId"], space, matches[0])
        if matches:
            ids = ", ".join(m["DomainId"] for m in matches)
            raise ValueError(
                f"Spaces named {space!r} are in {len(matches)} domains ({ids}): pass "
                f"'{matches[0]['DomainId']}/{space}'."
            )
        if not domain_id:
            known += [
                n["NotebookInstanceName"]
                for n in self._paginate("list_notebook_instances", "NotebookInstances")
            ]
        close = [
            k for k in known if k.lower() == space.lower()
        ] or difflib.get_close_matches(space, known, n=3)
        hint = f" Did you mean {' or '.join(map(repr, close))}?" if close else ""
        raise ValueError(
            f"No notebook instance or Studio space named {space!r} in {self.region}.{hint} "
            "running() lists what's running."
        )

    def current_notebook(self) -> NotebookInfo | None:
        """
        The notebook this code runs in, with its settings (None outside SageMaker). What can't be read is in
        its errors, so this doesn't raise for a missing permission.
        """
        env = self.environment()
        if env.kind == "notebook instance":
            try:
                return self.notebook_instance(env.name)
            except (ClientError, BotoCoreError) as exc:
                return NotebookInfo(
                    "notebook instance",
                    env.name,
                    env.arn,
                    errors={"notebook": _error_name(exc)},
                )
        if env.kind != "studio":
            return None
        try:
            nb = self.studio_app(
                env.domain_id,
                env.app_type,
                env.name,
                space=env.space,
                user_profile=env.user_profile,
            )
        except (ClientError, BotoCoreError) as exc:
            nb = NotebookInfo(
                "studio app",
                env.name,
                env.arn,
                domain_id=env.domain_id,
                space=env.space,
                user_profile=env.user_profile,
                app_type=env.app_type,
                errors={"app": _error_name(exc)},
            )
            nb = self._with_studio_settings(nb)
        nb.role_arn = nb.role_arn or env.role_arn
        return nb

    def instance(
        self, name: str | None = None, *, machine: bool = True
    ) -> InstanceReport:
        """
        The notebook this code runs in (or the one named: see notebook()): its settings and, for this one and
        machine=True, the machine right now.
        """
        env = self.environment()
        report = InstanceReport(env=env, current=name is None)
        try:
            ident = self.identity()
            report.account, report.identity = ident["Account"], ident["Arn"]
        except (ClientError, BotoCoreError) as exc:
            report.errors["identity"] = _error_name(exc)
        if name is not None:
            report.notebook = self.notebook(name)
            return report
        report.notebook = self.current_notebook()
        if machine and env.on_sagemaker:
            report.machine = self.machine()
        return report

    # ----------------------------------------------------------------- running

    def _notebook_billables(self, report: RunningReport) -> list[Billable]:
        summaries = [
            s
            for s in self._paginate("list_notebook_instances", "NotebookInstances")
            if s.get("NotebookInstanceStatus") in _BILLED | {"Stopped"}
        ]

        def detail(summary: dict[str, Any]) -> NotebookInfo:
            try:
                return self.notebook_instance(
                    summary["NotebookInstanceName"],
                    lifecycle=summary.get("NotebookInstanceStatus") != "Stopped",
                )
            except (ClientError, BotoCoreError) as exc:
                report.errors.setdefault("notebook details", _error_name(exc))
                nb = parse_notebook_instance(summary)
                nb.idle = "unknown"
                return nb

        running: list[Billable] = []
        for nb in self._map(detail, summaries):
            b = Billable(
                "notebook instance",
                nb.name,
                nb.status,
                [(nb.instance_type, 1)],
                since=nb.changed,
                idle=nb.idle,
                idle_minutes=nb.idle_minutes,
                volume_gb=nb.volume_gb,
                arn=nb.arn,
            )
            (report.stopped if nb.status == "Stopped" else running).append(b)
        return running

    def _app_billables(self, report: RunningReport) -> list[Billable]:
        apps = [
            a
            for a in self._paginate("list_apps", "Apps")
            if a.get("Status") in ("InService", "Pending")
            and (a.get("ResourceSpec") or {}).get("InstanceType") != "system"
        ]  # 'system' is free
        domains: dict[str, dict[str, Any] | None] = {}
        for domain_id in sorted({a.get("DomainId", "") for a in apps}):
            try:
                domains[domain_id] = self._describe_domain(domain_id)
            except (ClientError, BotoCoreError) as exc:
                report.errors.setdefault("domain", _error_name(exc))
                domains[domain_id] = None
        billables = []
        for app in apps:
            nb = parse_app(app)
            idle, minutes, _source = studio_idle(nb.app_type, domains.get(nb.domain_id))
            billables.append(
                Billable(
                    "Studio app",
                    nb.space or nb.name,
                    nb.status,
                    [(nb.instance_type, 1)] if nb.instance_type else [],
                    since=nb.created,
                    idle=idle,
                    idle_minutes=minutes,
                    domain_id=nb.domain_id,
                    space=nb.space,
                    user_profile=nb.user_profile,
                    app_type=nb.app_type,
                    app_name=nb.name,
                )
            )
        return billables

    def _endpoint_billables(self, report: RunningReport) -> list[Billable]:
        summaries = [
            e
            for e in self._paginate("list_endpoints", "Endpoints")
            if e.get("EndpointStatus")
            in ("InService", "Updating", "SystemUpdating", "RollingBack")
        ]
        configs: dict[str, dict[str, Any]] = {}

        def detail(summary: dict[str, Any]) -> Billable:
            try:
                desc = self.client.describe_endpoint(
                    EndpointName=summary["EndpointName"]
                )
                name = desc.get("EndpointConfigName", "")
                if name and name not in configs:
                    configs[name] = self.client.describe_endpoint_config(
                        EndpointConfigName=name
                    )
                return parse_endpoint(desc, configs.get(name))
            except (ClientError, BotoCoreError) as exc:
                report.errors.setdefault("endpoint details", _error_name(exc))
                return Billable(
                    "endpoint",
                    summary["EndpointName"],
                    summary.get("EndpointStatus", ""),
                    since=summary.get("CreationTime"),
                    arn=summary.get("EndpointArn", ""),
                )

        return self._map(detail, summaries)

    def _job_billables(self, report: RunningReport, kind: str) -> list[Billable]:
        if kind == "training":
            summaries = self._paginate(
                "list_training_jobs", "TrainingJobSummaries", StatusEquals="InProgress"
            )
            key, describe, parse = (
                "TrainingJobName",
                self.client.describe_training_job,
                parse_training_job,
            )
            fallback = "training job"
        else:
            summaries = self._paginate(
                "list_processing_jobs",
                "ProcessingJobSummaries",
                StatusEquals="InProgress",
            )
            key, describe, parse = (
                "ProcessingJobName",
                self.client.describe_processing_job,
                parse_processing_job,
            )
            fallback = "processing job"

        def detail(summary: dict[str, Any]) -> Billable:
            try:
                return parse(describe(**{key: summary[key]}))
            except (ClientError, BotoCoreError) as exc:
                report.errors.setdefault(f"{kind} details", _error_name(exc))
                return Billable(
                    fallback,
                    summary[key],
                    "InProgress",
                    since=summary.get("CreationTime"),
                )

        return self._map(detail, summaries)

    def endpoint_invocations(self, endpoints: list[Billable], days: int = 7) -> None:
        """
        Set each endpoint's `invocations`: requests over the last `days` days, from CloudWatch (every variant).
        Needs cloudwatch:GetMetricData.
        """
        queries: list[dict[str, Any]] = []
        owners: list[Billable] = []
        for b in endpoints:
            b.invocations = 0.0 if b.variants else None
            for variant in b.variants:
                queries.append(
                    {
                        "Id": f"q{len(queries)}",
                        "MetricStat": {
                            "Metric": {
                                "Namespace": "AWS/SageMaker",
                                "MetricName": "Invocations",
                                "Dimensions": [
                                    {"Name": "EndpointName", "Value": b.name},
                                    {"Name": "VariantName", "Value": variant},
                                ],
                            },
                            "Period": days * 86400,
                            "Stat": "Sum",
                        },
                    }
                )
                owners.append(b)
        if not queries:
            return
        cloudwatch = self._service("cloudwatch")
        now = _utcnow()
        for start in range(
            0, len(queries), 500
        ):  # GetMetricData takes up to 500 queries
            pages = cloudwatch.get_paginator("get_metric_data").paginate(
                MetricDataQueries=queries[start : start + 500],
                StartTime=now - timedelta(days=days),
                EndTime=now,
            )
            for page in pages:
                for series in page.get("MetricDataResults", []):
                    owner = owners[int(series["Id"][1:])]
                    owner.invocations = (owner.invocations or 0.0) + sum(
                        series.get("Values", [])
                    )

    def running(
        self,
        *,
        metrics: bool = True,
        days: int = 7,
        progress: Callable[..., None] | None = None,
    ) -> RunningReport:
        """
        Everything SageMaker bills by the hour in the region right now: notebook instances, Studio apps (not the
        free 'system' ones), endpoints and training / processing jobs in progress, plus stopped notebook instances
        (their volumes are still billed). metrics=True counts each endpoint's requests over the last `days` days.
        A section that can't be read is recorded in `errors`; progress is called with (done, total) sections.
        """
        self.client  # noqa: B018 - made before any threads start (boto3 sessions aren't thread-safe)
        report = RunningReport(self.region, days=days)
        steps: list[tuple[str, Callable[[RunningReport], list[Billable]]]] = [
            ("notebook instances", self._notebook_billables),
            ("apps", self._app_billables),
            ("endpoints", self._endpoint_billables),
            ("training jobs", lambda r: self._job_billables(r, "training")),
            ("processing jobs", lambda r: self._job_billables(r, "processing")),
        ]
        for i, (section, read) in enumerate(steps):
            try:
                report.resources += read(report)
            except (ClientError, BotoCoreError) as exc:
                report.errors[section] = _error_name(exc)
            if progress:
                progress(i + 1, len(steps))
        endpoints = [
            b for b in report.resources if b.kind == "endpoint" and not b.serverless
        ]
        if metrics and endpoints:
            try:
                self.endpoint_invocations(endpoints, days)
            except (ClientError, BotoCoreError) as exc:
                report.errors["metrics"] = _error_name(exc)
                for b in endpoints:
                    b.invocations = None
        env = self.environment()
        for b in report.resources:
            b.this = _is_current(b, env)
        return report


def _is_current(b: Billable, env: Environment) -> bool:
    """
    Is this the notebook the code runs in?
    """
    if b.kind == "notebook instance":
        return env.kind == "notebook instance" and b.name == env.name
    if b.kind == "Studio app":
        return (
            env.kind == "studio"
            and b.domain_id == env.domain_id
            and b.app_type == env.app_type
            and b.app_name == env.name
            and (b.space or b.user_profile) == (env.space or env.user_profile)
        )
    return False
