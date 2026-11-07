"""
The dataclasses SageMakerAnalyzer returns: the notebook and this machine, the disk, what's running and
billing, and the Python environments, kernels and the plan to make one.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from .._kit.deps import _require
from .._kit.fmt import HOURS_PER_MONTH, _utcnow

from .helpers import _arn_name, _arn_part, app_label, hourly_price


@dataclass
class Environment:
    """
    Where this code runs, from SageMaker's metadata file (/opt/ml/metadata/resource-metadata.json).
    """

    kind: str = "local"  # 'notebook instance' | 'studio' | 'other' | 'local' (no metadata file: not on SageMaker)
    name: str = ""  # the notebook instance's name, or the Studio app's ('default' for JupyterLab and Code Editor)
    arn: str = ""
    domain_id: str = ""
    user_profile: str = ""
    space: str = ""
    app_type: str = (
        ""  # Studio: 'JupyterLab', 'CodeEditor', 'KernelGateway' (Studio Classic), ...
    )
    role_arn: str = (
        ""  # Studio writes the execution role here; notebook instances don't
    )
    metadata: dict[str, Any] = field(default_factory=dict)
    error: str = (
        ""  # why the metadata file couldn't be read, when it's there but broken
    )

    @property
    def region(self) -> str:
        return _arn_part(self.arn, 3)

    @property
    def on_sagemaker(self) -> bool:
        return self.kind != "local"

    @property
    def label(self) -> str:
        """
        'notebook instance nb', 'JupyterLab space analysis', 'Studio Classic kernel app datascience-...'.
        """
        if self.kind == "notebook instance":
            return f"notebook instance {self.name}"
        if self.space:
            return f"{app_label(self.app_type)} space {self.space}"
        if self.kind == "studio":
            return f"{app_label(self.app_type)} app {self.name}".strip()
        return self.name or "this machine"


_BILLED = {
    "InService",
    "Pending",
    "Updating",
    "Stopping",
}  # a notebook's instance is billed in these states


@dataclass
class NotebookInfo:
    """
    One SageMaker notebook: a notebook instance, or a Studio app (JupyterLab, Code Editor, Studio Classic) with
    its space and domain settings. Parts that couldn't be read are listed in `errors` (section -> error code).
    """

    kind: str  # 'notebook instance' | 'studio app'
    name: str  # the notebook instance's name, or the app's ('default' for a space's JupyterLab / Code Editor app)
    arn: str = ""
    status: str = (
        ""  # 'InService', 'Stopped', ... ; a space with no app running shows 'Stopped'
    )
    instance_type: str = ""
    created: datetime | None = None  # a Studio app: when it started
    changed: datetime | None = (
        None  # a notebook instance: its last change, which each start and stop is
    )
    volume_gb: int | None = (
        None  # the notebook instance's or the space's storage volume
    )
    role_arn: str = ""
    lifecycle_configs: list[str] = field(default_factory=list)
    idle: str = "unknown"  # does SageMaker stop it when it's idle? 'on' | 'off' | 'unknown' | 'n/a' (free)
    idle_minutes: int | None = None  # after this many idle minutes, when known
    idle_source: str = ""  # where that's set: "lifecycle configuration 'x'", 'space', 'user profile', 'domain'
    internet: str = ""  # 'direct' | 'through your VPC only'
    root_access: bool | None = None
    platform: str = (
        ""  # notebook instance: 'notebook-al2023-v1'; Studio: the image and its version
    )
    domain_id: str = ""
    user_profile: str = ""  # Studio Classic's user, or the owner of a private space
    space: str = ""
    app_type: str = ""
    shared: bool | None = None  # a Studio space the whole domain can use
    subnet: str = ""
    url: str = ""
    code_repositories: list[str] = field(default_factory=list)
    failure: str = ""
    errors: dict[str, str] = field(default_factory=dict)

    @property
    def billing(self) -> bool:
        """
        Running (or starting / stopping), so its instance is billed by the hour.
        """
        return self.status in _BILLED

    @property
    def storage_price_key(self) -> str:
        return (
            "notebook_storage" if self.kind == "notebook instance" else "space_storage"
        )

    @property
    def label(self) -> str:
        """
        'notebook instance nb', 'JupyterLab space analysis', 'Studio Classic kernel app x (user alice)'.
        """
        if self.kind == "notebook instance":
            return f"notebook instance {self.name}"
        if self.space:
            return f"{app_label(self.app_type)} space {self.space}"
        user = f" (user {self.user_profile})" if self.user_profile else ""
        return f"{app_label(self.app_type)} app {self.name}{user}"


@dataclass
class GPUInfo:
    """
    One GPU, as nvidia-smi sees it right now.
    """

    name: str
    busy: float | None = None  # % of the last sample period a kernel ran on it
    memory_used: int | None = None  # bytes
    memory_total: int | None = None

    @property
    def idle(self) -> bool:
        """
        Nothing running on it and (almost) nothing loaded into its memory.
        """
        loaded = (
            (self.memory_used or 0) / self.memory_total if self.memory_total else 0.0
        )
        return (self.busy or 0) < 5 and loaded < 0.05


@dataclass
class Volume:
    """
    How full one disk is.
    """

    path: str
    label: str  # 'notebook volume', 'system disk', ...
    total: int
    used: int
    free: int
    note: str = ""  # what happens to it: 'kept when the instance stops', ...

    @property
    def title(self) -> str:
        """
        'notebook volume (kept when the instance stops)'.
        """
        return f"{self.label} ({self.note})" if self.note else self.label

    @property
    def share(self) -> float:
        return self.used / self.total if self.total else 0.0

    @property
    def elastic(self) -> bool:
        """
        A file system that grows as needed (EFS reports 8 EiB), so 'full' doesn't apply.
        """
        return self.total >= 2**50


@dataclass
class ProcessInfo:
    """
    One process on this machine and the memory it holds.
    """

    pid: int
    name: str
    command: str
    memory: int  # resident memory, bytes
    kernel: bool = False  # a Jupyter kernel (ipykernel)
    this: bool = False  # the kernel this code runs in


@dataclass
class Machine:
    """
    This machine right now, read locally: /proc, the disks and nvidia-smi. Parts it couldn't read are listed in
    `errors` (part -> reason).
    """

    cpus: int | None = None
    load: tuple[float, float, float] | None = None  # 1, 5 and 15-minute load averages
    memory_total: int | None = None
    memory_available: int | None = None
    booted: datetime | None = None
    volumes: list[Volume] = field(default_factory=list)
    gpus: list[GPUInfo] = field(default_factory=list)
    processes: list[ProcessInfo] = field(
        default_factory=list
    )  # the biggest by memory, and this kernel
    kernels: int = 0  # Jupyter kernels running, this one included
    kernels_memory: int = 0  # bytes they hold
    this_memory: int | None = None  # bytes this kernel holds
    python: str = ""
    executable: str = ""
    packages: dict[str, str] = field(
        default_factory=dict
    )  # installed version of the common data packages
    errors: dict[str, str] = field(default_factory=dict)

    @property
    def memory_used(self) -> int | None:
        if self.memory_total is None or self.memory_available is None:
            return None
        return self.memory_total - self.memory_available

    @property
    def memory_share(self) -> float | None:
        used = self.memory_used
        return (
            used / self.memory_total if used is not None and self.memory_total else None
        )

    @property
    def cpu_share(self) -> float | None:
        """
        The 15-minute load average as a share of the vCPUs.
        """
        return self.load[2] / self.cpus if self.load and self.cpus else None


@dataclass
class InstanceReport:
    """
    What instance() found: the notebook's settings from SageMaker, and, for the notebook this code runs in, the
    machine itself right now. Sections that couldn't be read are listed in `errors` (section -> error code).
    """

    env: Environment
    notebook: NotebookInfo | None = None
    machine: Machine | None = None
    current: bool = True  # describes the notebook this code runs in
    account: str = ""
    identity: str = ""  # the ARN AWS sees for this code: in a notebook, its execution role's session
    errors: dict[str, str] = field(default_factory=dict)

    @property
    def role_name(self) -> str:
        """
        'AmazonSageMaker-ExecutionRole-2024...' from the notebook's role, or the signed-in session.
        """
        if self.notebook and self.notebook.role_arn:
            return _arn_name(self.notebook.role_arn)
        if ":assumed-role/" in self.identity:
            return self.identity.split(":assumed-role/", 1)[1].split("/")[0]
        return _arn_name(self.identity)


@dataclass
class DiskEntry:
    """
    Files under one folder (or one file): how many, how big, and when the newest changed.
    """

    path: str  # relative to the folder measured
    size: int = 0
    files: int = 0
    modified: datetime | None = None

    def add(self, size: int, modified: datetime | None) -> None:
        self.size += size
        self.files += 1
        if modified is not None and (self.modified is None or modified > self.modified):
            self.modified = modified


@dataclass
class Clearable:
    """
    Caches or trash found while measuring: safe to empty, and the command that does it.
    """

    what: str  # 'Jupyter trash', 'pip cache', ...
    why: str  # what it holds and why emptying it is safe
    paths: list[str] = field(default_factory=list)
    size: int = 0
    files: int = 0
    command: str = ""  # shell command that empties it (never run by this tool)


@dataclass
class DiskReport:
    """
    What disk() measured under one folder.
    """

    path: str  # the folder measured
    volume: Volume | None = None  # the disk it's on
    total: DiskEntry = field(
        default_factory=lambda: DiskEntry("")
    )  # everything under path
    folders: dict[str, DiskEntry] = field(
        default_factory=dict
    )  # relative path -> totals, up to 3 levels deep
    largest: list[DiskEntry] = field(
        default_factory=list
    )  # the biggest files, biggest first
    clearable: list[Clearable] = field(default_factory=list)
    truncated: bool = False  # stopped at `limit` files, so sizes are at least these
    skipped: int = 0  # files or folders it couldn't read
    other_disks: list[str] = field(
        default_factory=list
    )  # folders on another disk inside path, not measured
    seconds: float = 0.0

    def to_df(self):
        """
        The folders measured as a pandas DataFrame: path, size in bytes, files, newest change.
        """
        pd = _require("pandas", "DiskReport.to_df()")
        return pd.DataFrame(
            [
                {
                    "path": e.path,
                    "size": e.size,
                    "files": e.files,
                    "modified": e.modified,
                }
                for e in sorted(self.folders.values(), key=lambda e: -e.size)
            ],
            columns=["path", "size", "files", "modified"],
        )


@dataclass
class Billable:
    """
    One thing SageMaker bills by the hour while it runs: a notebook instance, a Studio app, an endpoint or a
    training / processing job.
    """

    kind: str  # 'notebook instance' | 'Studio app' | 'endpoint' | 'training job' | 'processing job'
    name: str
    status: str = ""
    instances: list[tuple[str, int]] = field(
        default_factory=list
    )  # (instance type, count)
    since: datetime | None = (
        None  # when it started billing (a notebook instance: its last change, about its start)
    )
    idle: str = ""  # notebooks and apps: 'on' | 'off' | 'unknown'
    idle_minutes: int | None = None
    domain_id: str = ""  # Studio apps
    space: str = ""
    user_profile: str = ""
    app_type: str = ""
    app_name: str = ""
    volume_gb: int | None = None  # notebook instances
    serverless: bool = False  # endpoints billed per request rather than per hour
    spot: bool = (
        False  # a training job on spot capacity: cheaper than the on-demand price shown
    )
    config: str = ""  # endpoints: the endpoint configuration
    variants: list[str] = field(
        default_factory=list
    )  # endpoints: production variant names
    invocations: float | None = (
        None  # endpoints: requests in the last `days` days (None: unknown)
    )
    arn: str = ""
    this: bool = False  # the notebook this code runs in

    def hourly(self, prices: dict[str, float] | None = None) -> float | None:
        """
        USD per hour for all its instances; None when the price table lacks one of the types.
        """
        total = 0.0
        for instance_type, count in self.instances:
            price = hourly_price(instance_type, prices)
            if price is None:
                return None
            total += price * count
        return total

    @property
    def instance_label(self) -> str:
        """
        'ml.m5.xlarge', 'ml.g5.xlarge × 2', 'serverless'.
        """
        if self.serverless and not self.instances:
            return "serverless"
        return (
            " + ".join(t if n == 1 else f"{t} × {n}" for t, n in self.instances) or "?"
        )


@dataclass
class RunningReport:
    """
    What running() found: everything billing by the hour in one region, plus stopped notebook instances (their
    storage is still billed). Sections that couldn't be read are listed in `errors` (section -> error code).
    """

    region: str
    resources: list[Billable] = field(default_factory=list)
    stopped: list[Billable] = field(default_factory=list)
    days: int = 7  # the window endpoint traffic was counted over
    errors: dict[str, str] = field(default_factory=dict)

    def to_df(self, prices: dict[str, float] | None = None):
        """
        Everything running (and the stopped notebook instances) as a pandas DataFrame, one row each.
        """
        pd = _require("pandas", "RunningReport.to_df()")
        rows = []
        for b in self.resources + self.stopped:
            rows.append(
                {
                    "kind": b.kind,
                    "name": b.name,
                    "status": b.status,
                    "instances": b.instance_label,
                    "usd_per_hour": b.hourly(prices) if b in self.resources else 0.0,
                    "since": b.since,
                    "idle_shutdown": b.idle,
                    "idle_minutes": b.idle_minutes,
                    "volume_gb": b.volume_gb,
                    "invocations": b.invocations,
                    "domain": b.domain_id,
                    "space": b.space,
                    "user_profile": b.user_profile,
                    "this_notebook": b.this,
                }
            )
        return pd.DataFrame(
            rows,
            columns=[
                "kind",
                "name",
                "status",
                "instances",
                "usd_per_hour",
                "since",
                "idle_shutdown",
                "idle_minutes",
                "volume_gb",
                "invocations",
                "domain",
                "space",
                "user_profile",
                "this_notebook",
            ],
        )


@dataclass
class PythonEnv:
    """
    One Python environment on this machine: a conda environment, or a virtual environment (venv, uv).
    """

    name: str  # the folder's name ('base' for a conda installation's own)
    path: str  # as this machine sees it
    python: str = ""  # '3.11.9', or '' when it can't tell
    kind: str = "conda"  # 'conda' | 'venv'
    kernels: list[str] = field(default_factory=list)  # the Jupyter kernels that start it, by the name Jupyter shows
    ipykernel: bool = False  # has ipykernel, so it can be a Jupyter kernel
    kept: bool | None = None  # on the disk that's kept when the notebook stops (None: not on SageMaker)
    made: datetime | None = None  # when it was made or last changed (conda-meta/history, pyvenv.cfg)
    this: bool = False  # the environment this kernel runs in


@dataclass
class KernelSpec:
    """
    One Jupyter kernel: what Jupyter's kernel menu shows, and the Python it starts.
    """

    name: str  # its folder: 'py311'
    display: str  # 'Python 3.11 (py311)'
    path: str  # its folder, as this machine sees it
    program: str = ""  # what it starts: argv[0] of kernel.json
    language: str = ""
    env: str = ""  # the environment it starts (its path), when known
    kept: bool | None = None  # on the disk that's kept when the notebook stops
    missing: bool = False  # what it starts isn't there any more, so the kernel fails to start


@dataclass
class EnvReport:
    """
    What environments() found: the Python environments and Jupyter kernels on this machine, the programs that
    make new environments, and where new_env() puts them. Parts it couldn't read are in `errors` (part -> reason).
    """

    kind: str  # Environment.kind: 'notebook instance' | 'studio' | 'other' | 'local'
    envs: list[PythonEnv] = field(default_factory=list)
    kernels: list[KernelSpec] = field(default_factory=list)
    tools: dict[str, str] = field(default_factory=dict)  # 'conda' -> its path, in the order new_env() prefers them
    folder: str = ""  # where new_env() puts environments
    folder_kept: bool | None = None
    python: str = ""  # this kernel's Python
    executable: str = ""
    booted: datetime | None = None  # when this machine started (environments made since then on the system disk go)
    errors: dict[str, str] = field(default_factory=dict)

    def to_df(self):
        """
        The environments as a pandas DataFrame: name, Python version, kind, path, kernels, kept, made.
        """
        pd = _require("pandas", "EnvReport.to_df()")
        return pd.DataFrame(
            [
                {
                    "name": e.name,
                    "python": e.python,
                    "kind": e.kind,
                    "path": e.path,
                    "kernels": ", ".join(e.kernels),
                    "kept": e.kept,
                    "made": e.made,
                    "this_kernel": e.this,
                }
                for e in self.envs
            ],
            columns=["name", "python", "kind", "path", "kernels", "kept", "made", "this_kernel"],
        )


@dataclass
class EnvStep:
    """
    One command new_env() runs, and once run, how it went.
    """

    label: str  # 'Make the environment', 'Install packages', 'Add the Jupyter kernel'
    argv: list[str]
    env: dict[str, str] = field(default_factory=dict)  # environment variables it needs set
    command: str = ""  # the same as you'd type it in a terminal
    seconds: float | None = None
    code: int | None = None  # its exit status, once run
    output: str = ""  # the end of what it printed


@dataclass
class EnvPlan:
    """
    What new_env() does: the environment it makes (or finds), and the commands that make it.
    """

    name: str  # 'py311': the environment's folder and its kernel's name
    python: str  # '3.11'
    path: str  # where it goes, as this machine sees it
    tool: str  # 'mamba' | 'conda' | 'micromamba' | 'uv'
    kernel: str  # 'Python 3.11 (py311)'
    packages: list[str] = field(default_factory=list)
    steps: list[EnvStep] = field(default_factory=list)
    exists: bool = False  # it's there already with this Python: only packages and the kernel are set up
    found: str = ""  # the Python version of the one that's there
    kept: bool | None = None  # on the disk that's kept when the notebook stops
    free: int | None = None  # bytes free on that disk
    where: str = "local"  # Environment.kind
    folder: str = ""  # where new_env() puts environments when there's no path=
    kernel_kept: bool | None = None  # the kernel's folder (~/.local/share/jupyter) is kept when the notebook stops
    on_start: str = ""  # a notebook instance's: the folder whose environments' kernels to add back at each start


@dataclass
class EnvResult:
    """
    What new_env() did: the plan, each step's exit status and output, and the Python the environment got.
    """

    plan: EnvPlan
    ok: bool = False
    python: str = ""  # the version it got: '3.11.10'
    seconds: float = 0.0
    failed: EnvStep | None = None
    leftover: bool = False  # a failed make left a folder that isn't an environment (new_env() won't reuse it)


@dataclass
class AccessEvent:
    """
    One CloudTrail record of someone opening, starting or stopping a notebook: who, when, from where, and
    whether AWS let them.
    """

    when: datetime
    event_name: str
    action: str
    notebook: str
    who: str
    who_arn: str = ""
    identity_type: str = ""
    ip: str = ""
    user_agent: str = ""
    refused: str = ""
    domain_id: str = ""
    username: str = ""

    @property
    def is_sign_in(self) -> bool:
        """
        Whether this is someone opening a notebook (a sign-in link), rather than starting or stopping one.
        """
        return self.action == "opened"


@dataclass
class AccessReport:
    """
    What access() found in CloudTrail: the sign-ins, starts and stops of notebooks in one region over the last
    `days` days, newest first. `cut_short` says the read stopped at `limit` events; sections that couldn't be
    read are in `errors` (section -> error code).
    """

    region: str
    days: int
    events: list[AccessEvent] = field(default_factory=list)
    name: str = ""
    asked_days: int = 0
    limit: int = 0
    cut_short: bool = False
    errors: dict[str, str] = field(default_factory=dict)

    @property
    def sign_ins(self) -> list[AccessEvent]:
        """
        The events that opened a notebook and weren't refused.
        """
        return [e for e in self.events if e.is_sign_in and not e.refused]

    @property
    def refused(self) -> list[AccessEvent]:
        """
        The events AWS refused (AccessDenied and the like).
        """
        return [e for e in self.events if e.refused]

    def to_df(self):
        """
        Every event as a pandas DataFrame, one row each, newest first.
        """
        pd = _require("pandas", "AccessReport.to_df()")
        columns = ["when", "action", "notebook", "who", "who_arn", "identity_type", "ip", "user_agent", "refused",
                   "event_name", "domain_id", "username"]
        rows = [{column: getattr(e, column) for column in columns} for e in self.events]
        return pd.DataFrame(rows, columns=columns)


@dataclass
class PersonAccess:
    """
    One identity's sign-ins in an AccessReport: how often it opened notebooks, which, from where, and when last.
    """

    who: str
    sign_ins: int = 0
    notebooks: list[str] = field(default_factory=list)
    ips: list[str] = field(default_factory=list)
    refused: int = 0
    last: datetime | None = None


@dataclass
class StatusChange:
    """
    One of a training job's secondary statuses (Starting, Downloading, Training, Uploading, ...): when it began and
    ended, and what SageMaker said about it.
    """

    status: str
    start: datetime | None = None
    end: datetime | None = None
    message: str = ""

    def seconds(self, now: datetime | None = None) -> float | None:
        """
        How long it lasted (until now when it hasn't ended).
        """
        if self.start is None:
            return None
        return ((self.end or now or _utcnow()) - self.start).total_seconds()


@dataclass
class TrainingJob:
    """
    One training job, from DescribeTrainingJob (or ListTrainingJobs' summary alone when it couldn't be described:
    `described` is False). `instances` are (instance type, count); `billable_seconds` is what SageMaker billed
    (less than the run time on spot capacity).
    """

    name: str
    status: str = ""
    secondary: str = ""
    failure: str = ""
    created: datetime | None = None
    started: datetime | None = None
    ended: datetime | None = None
    instances: list[tuple[str, int]] = field(default_factory=list)
    volume_gb: int | None = None
    spot: bool = False
    billable_seconds: int | None = None
    training_seconds: int | None = None
    max_runtime: int | None = None
    image: str = ""
    algorithm: str = ""
    input_mode: str = ""
    inputs: list[tuple[str, str]] = field(default_factory=list)
    output: str = ""
    model_artifact: str = ""
    hyperparameters: dict[str, str] = field(default_factory=dict)
    metrics: list[tuple[str, float]] = field(default_factory=list)
    transitions: list[StatusChange] = field(default_factory=list)
    checkpoints: str = ""
    tuning_job: str = ""
    arn: str = ""
    described: bool = True

    @property
    def instance_label(self) -> str:
        """
        'ml.g5.xlarge', 'ml.p3.2xlarge × 2'.
        """
        return " + ".join(t if n == 1 else f"{t} × {n}" for t, n in self.instances) or "?"

    @property
    def is_finished(self) -> bool:
        return self.status in ("Completed", "Failed", "Stopped")

    def seconds(self, now: datetime | None = None) -> float | None:
        """
        How long it ran (from when its instances started, else from when it was made; until now while it runs).
        """
        begin = self.started or self.created
        if begin is None:
            return None
        if self.is_finished and self.ended is None:
            return float(self.training_seconds) if self.training_seconds is not None else None
        return ((self.ended or now or _utcnow()) - begin).total_seconds()

    def hourly(self, prices: dict[str, float] | None = None) -> float | None:
        """
        USD per hour for all its instances on demand; None when the price table lacks one of the types.
        """
        total = 0.0
        for instance_type, count in self.instances:
            price = hourly_price(instance_type, prices)
            if price is None:
                return None
            total += price * count
        return total if self.instances else None

    def cost(self, prices: dict[str, float] | None = None, now: datetime | None = None) -> float | None:
        """
        What it cost, estimated: its billable time (else its run time) at the on-demand price of its instances.
        Billable seconds already count spot's savings.
        """
        hourly = self.hourly(prices)
        seconds = self.billable_seconds if self.billable_seconds is not None else self.seconds(now)
        if hourly is None or seconds is None:
            return None
        return hourly * seconds / 3600


@dataclass
class TrainingList:
    """
    What training_jobs() found: the region's training jobs made since `since`, newest first, at most `limit`
    (`cut_short` when there were more). Sections that couldn't be read are in `errors`.
    """

    region: str
    since: datetime | None = None
    status: str = ""
    jobs: list[TrainingJob] = field(default_factory=list)
    limit: int = 0
    cut_short: bool = False
    errors: dict[str, str] = field(default_factory=dict)

    def to_df(self, prices: dict[str, float] | None = None):
        """
        One row per training job, as a pandas DataFrame.
        """
        pd = _require("pandas", "TrainingList.to_df()")
        rows = [
            {
                "name": job.name,
                "status": job.status,
                "secondary_status": job.secondary,
                "created": job.created,
                "seconds": job.seconds(),
                "billable_seconds": job.billable_seconds,
                "instances": job.instance_label,
                "spot": job.spot,
                "usd": job.cost(prices),
                "failure": job.failure,
            }
            for job in self.jobs
        ]
        return pd.DataFrame(rows)


@dataclass
class HostLine:
    """
    A line a training job's container wrote, with when CloudWatch Logs got it.
    """

    when: datetime | None
    message: str


@dataclass
class HostLog:
    """
    One instance's log of a training job (a log stream: '<job>/algo-1-1714569600'): its last lines, newest last,
    and the error they end in, when there is one (`error`: the traceback or the error lines). `cut_short` when
    older lines weren't read.
    """

    stream: str
    host: str
    lines: list[HostLine] = field(default_factory=list)
    error: list[str] = field(default_factory=list)
    cut_short: bool = False

    @property
    def first(self) -> datetime | None:
        return next((line.when for line in self.lines if line.when), None)

    @property
    def last(self) -> datetime | None:
        return next((line.when for line in reversed(self.lines) if line.when), None)


@dataclass
class TrainingDetail:
    """
    What training_job() found: the job, its hosts' logs (when read) and what couldn't be read (`errors`: 'logs').
    """

    job: TrainingJob
    hosts: list[HostLog] = field(default_factory=list)
    logs_read: bool = False
    errors: dict[str, str] = field(default_factory=dict)


@dataclass
class EndpointUse:
    """
    One endpoint (a Billable) with its traffic from CloudWatch: requests per UTC day over the window, and the
    requests that failed with a 5xx error. None when unknown.
    """

    endpoint: Billable
    daily: dict[datetime, float] = field(default_factory=dict)
    requests: float | None = None
    server_errors: float | None = None

    @property
    def last_called(self) -> datetime | None:
        """
        The start of the last UTC day it got a request in the window.
        """
        days = [day for day, count in self.daily.items() if count > 0]
        return max(days) if days else None


@dataclass
class EndpointReport:
    """
    What endpoints() found: every endpoint in the region with its traffic over the last `days` days.
    """

    region: str
    days: int = 30
    endpoints: list[EndpointUse] = field(default_factory=list)
    errors: dict[str, str] = field(default_factory=dict)

    def to_df(self, prices: dict[str, float] | None = None):
        """
        One row per endpoint, as a pandas DataFrame.
        """
        pd = _require("pandas", "EndpointReport.to_df()")
        rows = []
        for use in self.endpoints:
            b = use.endpoint
            hourly = b.hourly(prices)
            rows.append(
                {
                    "name": b.name,
                    "status": b.status,
                    "instances": b.instance_label,
                    "usd_per_month": None if hourly is None else hourly * HOURS_PER_MONTH,
                    "requests": use.requests,
                    "server_errors": use.server_errors,
                    "last_called": use.last_called,
                    "created": b.since,
                }
            )
        return pd.DataFrame(rows)


@dataclass
class PipelineRunSummary:
    """
    One run of a pipeline, as ListPipelineExecutions lists it.
    """

    arn: str
    name: str = ""
    status: str = ""
    started: datetime | None = None
    failure: str = ""


@dataclass
class PipelineInfo:
    """
    A pipeline and its latest runs, newest first.
    """

    name: str
    arn: str = ""
    description: str = ""
    created: datetime | None = None
    last_run: datetime | None = None
    runs: list[PipelineRunSummary] = field(default_factory=list)


@dataclass
class PipelinesReport:
    """
    What pipelines() found: the region's pipelines with their latest runs.
    """

    region: str
    pipelines: list[PipelineInfo] = field(default_factory=list)
    errors: dict[str, str] = field(default_factory=dict)


@dataclass
class PipelineStep:
    """
    One step of a pipeline run: what it is (from the definition: its type and the steps it waits for) and how it
    went (from ListPipelineExecutionSteps). `job` is the training, processing or other job it started, as
    (kind, name); `cached` when it reused an earlier run's result. `branch` is the condition step and the side
    ('if' or 'else') that a step runs on.
    """

    name: str
    type: str = ""
    status: str = ""
    start: datetime | None = None
    end: datetime | None = None
    failure: str = ""
    attempts: int = 1
    cached: bool = False
    job: tuple[str, str] = ("", "")
    depends_on: list[str] = field(default_factory=list)
    branch: tuple[str, str] = ("", "")

    def seconds(self, now: datetime | None = None) -> float | None:
        if self.start is None:
            return None
        return ((self.end or now or _utcnow()) - self.start).total_seconds()


@dataclass
class PipelineRun:
    """
    What pipeline_run() found: one run of a pipeline, its steps in the definition's order (steps that never ran
    have no status), and what couldn't be read (`errors`: 'definition', 'steps').
    """

    pipeline: str
    arn: str = ""
    name: str = ""
    status: str = ""
    started: datetime | None = None
    modified: datetime | None = None
    failure: str = ""
    steps: list[PipelineStep] = field(default_factory=list)
    runs: list[PipelineRunSummary] = field(default_factory=list)
    errors: dict[str, str] = field(default_factory=dict)

    @property
    def is_finished(self) -> bool:
        return self.status in ("Succeeded", "Failed", "Stopped", "CompletedWithFailures")

    def seconds(self, now: datetime | None = None) -> float | None:
        if self.started is None:
            return None
        end = self.modified if self.is_finished else None
        return ((end or now or _utcnow()) - self.started).total_seconds()
