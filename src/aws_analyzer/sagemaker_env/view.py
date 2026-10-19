"""
SageMakerView: the notebook UI, which renders what SageMakerAnalyzer returns as cards and tables.
"""

from __future__ import annotations

import os
from typing import Any

from .._kit.blocks import _Cards, _Findings, _Next, _Note, _Table, _Text, _Title, _Tone
from .._kit.fmt import (
    HOURS_PER_MONTH, _as_count, _as_int, _fmt_dt, _plural, _utcnow, human_age, human_money, human_size,
)
from .._kit.render import _call
from .._kit.view import _BaseView, _friendly_errors

from .helpers import (
    _arn_name, _arn_part, _minor, _since, _tilde, app_label, describe_instance, human_runtime, new_env_call,
    shell_path,
)
from .models import Billable, EnvPlan, InstanceReport, Machine
from .analysis.common import _full
from .analysis.notebooks import _gpu_idle, idle_shutdown_commands, instance_findings, notebook_costs
from .analysis.disk import disk_findings, folder_tree
from .analysis.running import _forgotten, _unused_endpoint, running_findings
from .analysis.envs import (
    LOW_DISK, _SUGGESTED, _disks, env_failure, env_findings, kernels_lifecycle, lost_envs, plan_findings,
)
from .analyzer import SageMakerAnalyzer
from .style import _render_html


def _idle_label(state: str, minutes: int | None) -> str:
    """
    'after 60 idle min', 'off', 'not needed (free)', '?'.
    """
    if state == "on":
        return f"after {minutes} idle min" if minutes else "on"
    return {"off": "off", "n/a": "not needed (free)"}.get(state, "?")


_KIND_ICONS = {  # Billable.kind -> the icon in front of it in running(), so the kinds stand apart in a long table
    "notebook instance": "📓",
    "Studio app": "🧪",
    "endpoint": "🚀",
    "training job": "🏋️",
    "processing job": "⚙️",
}


def _kind_label(b: Billable) -> str:
    """
    '📓 notebook instance', '🧪 JupyterLab app', '🚀 endpoint': what it is, with its icon.
    """
    label = f"{app_label(b.app_type)} app" if b.kind == "Studio app" else b.kind
    icon = _KIND_ICONS.get(b.kind)
    return f"{icon} {label}" if icon else label


def _where(b: Billable) -> str:
    """
    'space analysis · d-abc123' for a Studio app.
    """
    owner = (
        f"space {b.space}"
        if b.space
        else (f"user {b.user_profile}" if b.user_profile else "")
    )
    return " · ".join(filter(None, [owner, b.domain_id]))


def _notebook_ref(b: Billable) -> str | None:
    """
    What instance() takes to describe this notebook: its name, or the space ('d-abc/analysis' form).
    """
    if b.kind == "notebook instance":
        return b.name
    if b.kind == "Studio app" and b.space:
        return f"{b.domain_id}/{b.space}" if b.domain_id else b.space
    return None


class SageMakerView(_BaseView):
    """
    Notebook UI over SageMakerAnalyzer. Each method renders a report and returns nothing; for the underlying
    data call the same-named method on `view.core` (a SageMakerAnalyzer).

    mode: 'auto' (HTML inside Jupyter, text elsewhere), 'html' or 'text'.
    max_rows: default cap for long tables (set to 0 for no cap).
    progress: 'auto' (a tqdm bar while long commands run, when tqdm is installed; else a line with the count,
    rate and time left), 'plain' (always that line) or 'off'.
    """

    _render_html = staticmethod(_render_html)  # its look: the stylesheet, root class and badge
    _GROUPS = {  # help() lists the commands in these groups, in this order
        "💻 This notebook": ("instance", "disk"),
        "🐍 Python environments": ("envs", "new_env"),
        "☁️ Your account": ("running",),
        "❓ Help": ("help",),
    }
    _START = (
        ("instance()", "this notebook: its type, cost, idle shutdown, memory and disk"),
        ("disk()", "what fills the disk, and what's safe to clear"),
        ("running()", "everything running and billing in this region"),
        ("new_env('3.11')", "a Python 3.11 environment with its own Jupyter kernel, kept when the notebook stops"),
    )

    def __init__(
        self,
        core: SageMakerAnalyzer | None = None,
        *,
        mode: str = "auto",
        max_rows: int = 50,
        progress: str = "auto",
    ):
        super().__init__(mode=mode, max_rows=max_rows, progress=progress)
        self.core: SageMakerAnalyzer = core or SageMakerAnalyzer()

    # ------------------------------------------------------------------ plumbing

    def _explain(self, code: str, message: str) -> str:
        """
        The AWS error message plus what to do about it.
        """
        lowered = message.lower()
        text = message.rstrip() + (
            "" if message.rstrip().endswith((".", "!", "?")) else "."
        )
        if code == "ResourceNotFound" or any(
            words in lowered
            for words in ("recordnotfound", "does not exist", "could not find")
        ):
            return (
                f"{text} Names are case-sensitive, and SageMaker is regional (this is {self.core.region}); "
                "running() lists what's running."
            )
        if (
            code in ("AccessDeniedException", "AccessDenied")
            or "not authorized" in lowered
        ):
            return (
                f"{text} README lists the read-only IAM permissions each command needs."
            )
        if code in ("ThrottlingException", "TooManyRequestsException"):
            return f"{text} SageMaker throttled the call: wait a few seconds and retry."
        return message

    # --------------------------------------------------------------- notebooks

    @staticmethod
    def _runtime(report: InstanceReport) -> tuple[float | None, bool]:
        """
        (seconds the notebook has been running, whether that's only approximate).
        """
        nb, m = report.notebook, report.machine
        if nb is None or not nb.billing:
            return None, False
        if nb.kind == "studio app" and nb.created:
            return _since(nb.created), False
        if report.current and m is not None and m.booted:
            return _since(m.booted), False
        return _since(
            nb.changed
        ), True  # a notebook instance's last change: about when it last started

    @_friendly_errors
    def instance(self, name: str | None = None) -> None:
        """
        The notebook you're running in: its instance type and what it costs, how long it has been running,
        whether it stops when idle, its CPU, memory, disk and GPU use right now, and its role and network.

        name: another notebook instance, or a Studio space ('analysis', or 'd-abc123/analysis' when two domains
        have one), to see its settings and cost. Its machine can only be read from inside it.
        """
        report = self.core.instance(name)
        env, nb, m = report.env, report.notebook, report.machine
        prices = self.core.prices
        if nb is None and m is None:
            blocks: list[Any] = [
                _Title(
                    "Not a SageMaker notebook", "no SageMaker metadata on this machine"
                )
            ]
            if env.error:
                blocks.append(_Note(env.error, "warn"))
            blocks.append(
                _Note(
                    "SageMaker notebook instances and Studio apps have /opt/ml/metadata/resource-metadata.json, and "
                    "this machine doesn't, so this code isn't running in one. instance('name') describes a notebook "
                    "instance or a Studio space by name; running() lists everything running in a region."
                )
            )
            blocks.append(
                _Next(
                    [
                        ("running()", "everything running and billing in the region"),
                        (
                            "instance('name')",
                            "a notebook instance or Studio space's settings and cost",
                        ),
                    ]
                )
            )
            self._show(blocks)
            return
        allowed = None
        if nb is not None:
            try:
                allowed = self.core.allowed_types(nb.kind)
            except (KeyError, AttributeError):
                allowed = None
        findings = instance_findings(report, prices, allowed)
        warned = " ".join(message for level, message in findings if level == "warn")
        subject = nb.label if nb is not None else env.label
        region = env.region or _arn_part(nb.arn if nb is not None else "", 3) or self.core.region
        blocks = [
            _Title(
                subject[0].upper() + subject[1:],
                " · ".join(
                    filter(
                        None,
                        [
                            region,
                            "the notebook this code runs in" if report.current else "",
                            f"cost at {self._price_basis()}",
                        ],
                    )
                ),
            )
        ]
        cards: list[tuple[str, ...]] = []
        costs = notebook_costs(nb, prices) if nb is not None else {}
        hourly = costs.get("hourly")
        if nb is not None:
            runtime, rough = self._runtime(report)
            cards += [
                ("Status", nb.status or "?", "bad" if nb.status == "Failed" else ""),
                ("Instance", nb.instance_type or "?"),
                ("Size", describe_instance(nb.instance_type) or "?"),
                (
                    "Price",
                    f"{human_money(hourly)}/hour" if hourly is not None else "unknown",
                ),
            ]
            if runtime is not None:
                cards.append(
                    (
                        "Running for",
                        ("about " if rough else "") + human_runtime(runtime),
                    )
                )
                if hourly is not None:
                    cards.append(
                        ("Est. cost since start", human_money(hourly * runtime / 3600))
                    )
            if hourly is not None:
                cards.append(
                    ("Est. / month if always on", human_money(costs["month_if_on"]))
                )
            if nb.idle != "n/a":
                off = nb.idle == "off" and "idle" in warned
                cards.append(
                    (
                        "Idle shutdown",
                        _idle_label(nb.idle, nb.idle_minutes),
                        "warn" if off else "",
                    )
                )
        if m is not None:
            home = m.volumes[0] if m.volumes else None
            if home is not None:
                value = (
                    "EFS, grows as needed"
                    if home.elastic
                    else f"{home.share:.0%} of {human_size(home.total)} used"
                )
                cards.append(
                    (
                        "Disk",
                        value,
                        "warn" if not home.elastic and _full(home.share) else "",
                    )
                )
            if m.memory_total:
                cards.append(
                    (
                        "Memory",
                        f"{human_size(m.memory_used)} of {human_size(m.memory_total)} used",
                        "warn" if _full(m.memory_share) else "",
                    )
                )
            if m.load and m.cpus:
                cards.append(("CPU load", f"{m.load[2]:.1f} of {m.cpus} vCPUs"))
            if m.gpus:
                busy = max(g.busy or 0 for g in m.gpus)
                cards.append(
                    (
                        "GPU",
                        "idle" if _gpu_idle(m) else f"{busy:.0f}% busy",
                        "warn" if _gpu_idle(m) and "GPU" in warned else "",
                    )
                )
        blocks.append(_Cards(cards))
        blocks.append(_Findings(findings, empty="No issues found by these checks."))
        if nb is not None and nb.idle == "off" and (nb.billing or report.current):
            commands = idle_shutdown_commands(nb)
            if commands:
                title = (
                    "Turn on auto-stop"
                    if nb.kind == "notebook instance"
                    else "Turn on idle shutdown"
                )
                blocks.append(_Text(commands, title=title, code=True))
        if m is not None:
            blocks += self._machine_blocks(m)
        if nb is not None and (hourly is not None or costs.get("storage") is not None):
            rows = []
            if hourly is not None:
                rows.append(
                    [
                        f"Instance ({nb.instance_type})",
                        f"{human_money(hourly)}",
                        human_money(costs["month_if_on"]) + " if always on",
                    ]
                )
            if costs.get("storage") is not None:
                rows.append(
                    [
                        f"Storage ({nb.volume_gb} GB volume)",
                        "",
                        f"{human_money(costs['storage'])}, running or stopped",
                    ]
                )
            blocks.append(
                _Table(
                    ["Cost", "Per hour", "Per month"],
                    rows,
                    title=f"Estimated cost ({self._price_basis()})",
                )
            )
        blocks.append(
            _Table(
                ["Setting", "Value"],
                self._settings_rows(report),
                title="Settings",
                max_rows=0,
            )
        )
        if m is not None and m.processes:
            rows = [
                [
                    str(p.pid),
                    p.command,
                    human_size(p.memory),
                    "this kernel" if p.this else "notebook kernel" if p.kernel else "",
                ]
                for p in m.processes
            ]
            blocks.append(
                _Table(
                    ["PID", "Process", "Memory", ""],
                    rows,
                    title="Biggest processes by memory",
                    collapsed=True,
                    max_rows=0,
                )
            )
        if m is not None:
            rows = [["Python", f"{m.python} ({m.executable})"]] + [
                [k, v] for k, v in m.packages.items()
            ]
            blocks.append(
                _Table(
                    ["Package", "Version"],
                    rows,
                    title="Python and packages",
                    collapsed=True,
                    max_rows=0,
                )
            )
        steps = []
        full = next(
            (v for v in (m.volumes if m else []) if not v.elastic and _full(v.share)),
            None,
        )
        if full is not None:
            steps.append(
                (
                    _call("disk", full.path),
                    "what fills that disk, and what's safe to clear",
                )
            )
        elif m is not None:
            steps.append(("disk()", "what fills the disk, and what's safe to clear"))
        if m is not None:
            steps.append(("envs()", "the Python environments and kernels here, and how to add one"))
        steps.append(("running()", f"everything running and billing in {region}"))
        if not report.current:
            steps.append(("instance()", "the notebook this code runs in"))
        blocks.append(_Next(steps))
        self._show(blocks)

    @staticmethod
    def _machine_blocks(m: Machine) -> list[Any]:
        """
        'This machine right now': CPU, memory, disks, GPUs and kernels, each with how much is used.
        """
        rows: list[list[Any]] = []
        bars: list[float] = []
        if m.cpus:
            load = (
                f"load {m.load[0]:.1f} now, {m.load[2]:.1f} over 15 min"
                if m.load
                else "?"
            )
            rows.append(["CPU", f"{m.cpus} vCPUs", load, ""])
            bars.append(min(m.cpu_share or 0.0, 1.0))
        if m.memory_total:
            rows.append(
                [
                    "Memory",
                    human_size(m.memory_total),
                    human_size(m.memory_used),
                    human_size(m.memory_available),
                ]
            )
            bars.append(m.memory_share or 0.0)
        for v in m.volumes:
            rows.append(
                [
                    f"{v.title}\n{v.path}",
                    "grows as needed" if v.elastic else human_size(v.total),
                    human_size(v.used),
                    "-" if v.elastic else human_size(v.free),
                ]
            )
            bars.append(0.0 if v.elastic else v.share)
        for i, g in enumerate(m.gpus):
            free = (
                (g.memory_total - g.memory_used)
                if g.memory_total is not None and g.memory_used is not None
                else None
            )
            busy = "?" if g.busy is None else f"{g.busy:.0f}% busy"
            rows.append(
                [
                    f"GPU {i}: {g.name}",
                    human_size(g.memory_total),
                    f"{human_size(g.memory_used)}, {busy}",
                    human_size(free),
                ]
            )
            bars.append(
                (g.memory_used or 0) / g.memory_total if g.memory_total else 0.0
            )
        if m.kernels:
            this = (
                f" (this one {human_size(m.this_memory)})"
                if m.this_memory is not None
                else ""
            )
            rows.append(
                [
                    "Jupyter kernels",
                    f"{m.kernels:,} running",
                    human_size(m.kernels_memory) + this,
                    "",
                ]
            )
            bars.append(m.kernels_memory / m.memory_total if m.memory_total else 0.0)
        if not rows:
            return []
        return [
            _Table(
                ["", "Total", "In use", "Free"],
                rows,
                title="This machine right now",
                bars=bars,
                bar_label="Used",
                max_rows=0,
            )
        ]

    def _settings_rows(self, report: InstanceReport) -> list[list[str]]:
        nb, env = report.notebook, report.env
        rows: list[list[str]] = []

        def add(label: str, value: Any) -> None:
            if value not in (None, "", []):
                rows.append([label, str(value)])

        if nb is not None:
            add("Role", _arn_name(nb.role_arn) if nb.role_arn else "")
            add("Role ARN", nb.role_arn)
        add("Signed in as", report.identity)
        if nb is not None:
            add("Internet access", nb.internet)
            add(
                "Root access",
                None if nb.root_access is None else ("on" if nb.root_access else "off"),
            )
            add("Lifecycle configuration", ", ".join(nb.lifecycle_configs) or "none")
            if nb.idle not in ("n/a",):
                where = f" (set in the {nb.idle_source})" if nb.idle_source else ""
                if nb.idle_source.startswith(("lifecycle", "Studio")):
                    where = f" ({nb.idle_source})"
                add("Idle shutdown", _idle_label(nb.idle, nb.idle_minutes) + where)
            add("Platform" if nb.kind == "notebook instance" else "Image", nb.platform)
            add(
                "Storage volume",
                f"{nb.volume_gb} GB, kept when it stops" if nb.volume_gb else "",
            )
            add("Domain", nb.domain_id)
            add(
                "Space",
                ""
                if not nb.space
                else nb.space
                + (
                    ""
                    if nb.shared is None
                    else " (shared with the domain)"
                    if nb.shared
                    else " (private)"
                ),
            )
            add("User profile", nb.user_profile)
            add(
                "App",
                f"{app_label(nb.app_type)} ({nb.name})"
                if nb.kind == "studio app" and nb.name
                else "",
            )
            add("Subnet", nb.subnet)
            add("Code repositories", "\n".join(nb.code_repositories))
            add("Created", _fmt_dt(nb.created) if nb.created else "")
            add("Last changed", _fmt_dt(nb.changed) if nb.changed else "")
            add("URL", nb.url)
            add("ARN", nb.arn or (env.arn if report.current else ""))
        return rows

    # -------------------------------------------------------------------- disk

    @_friendly_errors
    def disk(
        self, path: str | None = None, *, top: int = 20, limit: Any = "200k"
    ) -> None:
        """
        What fills the disk: how full it is, the biggest folders and files, and the caches and trash that are safe
        to clear, with the command for each (this tool never deletes anything).

        path: the folder to measure (default: where your notebooks live, ~/SageMaker on a notebook instance and
        /home/sagemaker-user in Studio). limit: stop after this many files ('200k'; None measures everything).
        """
        limit = _as_count(limit, "limit")
        top = _as_int(top, "top")
        with self._progress("Measuring", unit="files") as tick:
            report = self.core.disk(path, top=top, limit=limit, progress=tick)
        env = self.core.environment()
        findings = disk_findings(report, env, self.core.prices)
        v = report.volume
        full = v is not None and not v.elastic and _full(v.share)
        more = "+" if report.truncated else ""
        cards: list[tuple[str, ...]] = []
        if v is not None and v.elastic:
            cards.append(("Disk", "EFS, grows as needed"))
        elif v is not None:
            cards += [
                ("Disk", human_size(v.total)),
                (
                    "Used",
                    f"{human_size(v.used)} ({v.share:.0%})",
                    "warn" if full else "",
                ),
                ("Free", human_size(v.free)),
            ]
        clearable = sum(c.size for c in report.clearable)
        cards += [
            ("In this folder", human_size(report.total.size) + more),
            ("Files", f"{report.total.files:,}{more}"),
            ("Safe to clear", human_size(clearable)),
            ("Time", f"{report.seconds:.1f}s"),
        ]
        label = v.title if v is not None and v.label != "disk" else ""
        blocks: list[Any] = [
            _Title(
                f"Disk: {report.path}",
                " · ".join(
                    filter(
                        None,
                        [
                            label,
                            "sizes add up the files in each folder",
                            "this tool never deletes anything",
                        ],
                    )
                ),
            ),
            _Cards(cards),
            _Findings(findings, empty="No issues found by these checks."),
        ]
        total = report.total.size or 1
        rows, bars = [], []
        for depth, entry in folder_tree(report, top=15):
            rows.append(
                [
                    "    " * depth + entry.path.rsplit("/", 1)[-1] + "/",
                    human_size(entry.size),
                    f"{entry.files:,}",
                    human_age(entry.modified),
                ]
            )
            bars.append(entry.size / total)
        direct = report.total.size - sum(
            e.size for e in report.folders.values() if "/" not in e.path
        )
        if rows and direct > 0:
            rows.append(["(files directly in this folder)", human_size(direct), "", ""])
            bars.append(direct / total)
        if rows:
            blocks.append(
                _Table(
                    ["Folder", "Size", "Files", "Newest file"],
                    rows,
                    title="Biggest folders",
                    bars=bars,
                    tree=True,
                    max_rows=0,
                )
            )
        if report.largest:
            blocks.append(
                _Table(
                    ["File", "Size", "Last changed"],
                    [
                        [e.path, human_size(e.size), human_age(e.modified)]
                        for e in report.largest
                    ],
                    title="Largest files",
                    max_rows=0,
                )
            )
        if report.clearable:
            rows = []
            for c in report.clearable:
                shown = [os.path.relpath(p, report.path) for p in c.paths[:2]]
                where = "\n".join(shown) + (
                    f"\nand {len(c.paths) - 2:,} more" if len(c.paths) > 2 else ""
                )
                rows.append([c.what, human_size(c.size), c.why, where, c.command])
            blocks.append(
                _Table(
                    ["What", "Size", "Why it's safe", "Where", "To clear it"],
                    rows,
                    title="Caches and trash that are safe to clear",
                    code_cols=(4,),
                    max_rows=0,
                )
            )
        if not report.total.files:
            blocks.append(_Note("There are no files in this folder."))
        if report.skipped:
            blocks.append(
                _Note(
                    f"{_plural(report.skipped, 'file or folder')} couldn't be read (permissions) and "
                    "aren't counted."
                )
            )
        if report.other_disks:
            others = ", ".join(report.other_disks[:5]) + (
                " …" if len(report.other_disks) > 5 else ""
            )
            blocks.append(
                _Note(f"Not measured, because they're on another disk: {others}.")
            )
        steps = []
        biggest = next((entry for depth, entry in folder_tree(report, top=1)), None)
        if biggest is not None:
            steps.append(
                (
                    _call("disk", os.path.join(report.path, biggest.path)),
                    "what's inside the biggest folder",
                )
            )
        if env.on_sagemaker:
            steps.append(
                ("instance()", "this notebook's cost, memory and idle shutdown")
            )
        blocks.append(_Next(steps))
        self._show(blocks)

    # ----------------------------------------------------- Python environments

    @_friendly_errors
    def envs(self) -> None:
        """
        The Python environments on this machine and the Jupyter kernels that start them: each one's Python version,
        where it is and whether it's kept when the notebook stops, and kernels that fail to start.

        new_env('3.11') makes another one. Nothing here removes one; where that would help, the command is shown.
        """
        report = self.core.environments()
        home = self.core._user_home()
        findings = env_findings(report, home)
        warned = " ".join(message for level, message in findings if level == "warn")
        env = self.core.environment()
        _, _, event = _disks(report.kind)
        folder = shell_path(report.folder, home)
        lost = lost_envs(report)
        broken = sum(k.missing for k in report.kernels)
        cards: list[tuple[str, ...]] = [
            ("This kernel", f"Python {report.python}"),
            ("Environments", f"{len(report.envs):,}"),
            (
                "Jupyter kernels",
                f"{len(report.kernels):,}" + (f", {broken:,} broken" if broken else ""),
                "warn" if broken and "isn't there any more" in warned else "",
            ),
            ("Makes environments with", ", ".join(report.tools) or "nothing installed"),
            ("New ones go in", folder + (", kept" if report.folder_kept else "")),
        ]
        blocks: list[Any] = [
            _Title(
                "Python environments",
                " · ".join(
                    filter(
                        None,
                        [
                            env.label if env.on_sagemaker else "",
                            f"kept: still there after {event}" if event else "",
                            "this tool never removes one",
                        ],
                    )
                ),
            ),
            _Cards(cards),
            _Findings(findings, empty="No issues found by these checks."),
        ]
        if report.envs:
            headers = ["Environment", "Python", "Kind", "Where"] + (["Kept"] if event else [])
            rows = []
            for e in report.envs:
                kept: Any = "-" if e.kept is None else "yes" if e.kept else "no"
                if e in lost and event:
                    kept = _Tone("no", "warn")
                row: list[Any] = [
                    e.name + ("  📍 this kernel" if e.this else ""),
                    e.python or "?",
                    e.kind,
                    shell_path(e.path, home),
                ]
                row += [kept] if event else []
                row += [", ".join(e.kernels) or ("-" if e.ipykernel else "no ipykernel"), human_age(e.made)]
                rows.append(row)
            blocks.append(
                _Table(headers + ["Jupyter kernel", "Made"], rows, title="Environments", max_rows=0)
            )
        else:
            blocks.append(_Note("No Python environments found in the usual places."))
        if report.kernels:
            rows = []
            for k in report.kernels:
                if k.missing:
                    starts: Any = _Tone(f"{shell_path(k.program, home)} (not there)", "bad")
                elif k.env:
                    starts = shell_path(k.env, home)
                else:
                    starts = shell_path(k.program, home) if k.program.startswith("/") else (k.program or "?")
                kept = "-" if k.kept is None else "yes" if k.kept else "no"
                row = [k.display, k.name, starts] + ([kept] if event else [])
                rows.append(row + [shell_path(k.path, home) if k.path else "(Jupyter lists it by itself)"])
            headers = ["Kernel", "Name", "Starts"] + (["Kept"] if event else []) + ["Folder"]
            blocks.append(_Table(headers, rows, title="Jupyter kernels", max_rows=0))
        ours = [e for e in report.envs if report.folder and os.path.dirname(e.path) == report.folder]
        if report.kind == "notebook instance" and ours:
            blocks.append(
                _Text(
                    kernels_lifecycle(report.folder, env.name),
                    title="Add the kernels back at every start (lifecycle configuration)",
                    code=True,
                    collapsed=True,
                )
            )
        have = {_minor(v) for v in [report.python] + [e.python for e in report.envs if e.python]}
        suggest = next((v for v in _SUGGESTED if v not in have), _SUGGESTED[0])
        steps = [
            (new_env_call(suggest), f"a Python {suggest} environment with its own kernel, in {folder}"),
            (new_env_call(suggest, dry_run=True), "the commands that make it, without running them"),
        ]
        if ours:
            steps.append((_call("disk", _tilde(report.folder, home)), "how much disk the environments take"))
        blocks.append(_Next(steps))
        self._show(blocks)

    def _again(self, plan: EnvPlan, **extra: Any) -> str:
        """
        new_env(...) for this plan again, with only the arguments that differ from the defaults.
        """
        kwargs: dict[str, Any] = {}
        if plan.path != os.path.join(plan.folder, plan.name):
            kwargs["path"] = _tilde(plan.path, self.core._user_home())
        if plan.packages:
            kwargs["packages"] = " ".join(plan.packages)
        return new_env_call(plan.python, plan.name, **{**kwargs, **extra})

    @_friendly_errors
    def new_env(
        self,
        python: Any = "3.11",
        name: str | None = None,
        *,
        packages: Any = None,
        path: str | None = None,
        tool: str = "auto",
        dry_run: bool = False,
    ) -> None:
        """
        Makes a Python environment with the version you ask for, in a folder that's kept when the notebook stops,
        and adds it to Jupyter's kernel menu. It takes a minute or two; dry_run=True shows the commands instead.

        python: '3.11', '3.12.4' or 'py311'. name: its folder and kernel ('py311' for 3.11). packages: what to pip
        install in it ('pandas boto3', a list, or '-r requirements.txt'). path: another folder for it. tool: 'auto'
        (the first of mamba, conda, micromamba and uv that's here), or one of them; conda ones use conda-forge.
        Run it again on an environment that's there and only the packages and the kernel are set up: after a
        notebook instance restarts, new_env('3.11') adds the kernel back in seconds. It writes only the environment's
        folder and its kernel, and never removes anything.
        """
        plan = self.core.env_plan(python, name, packages=packages, path=path, tool=tool)
        home = self.core._user_home()
        where = shell_path(plan.path, home)
        findings = plan_findings(plan, home)
        warned = " ".join(message for level, message in findings if level == "warn")
        env = self.core.environment()
        _, _, event = _disks(plan.where)
        kept: list[tuple[str, ...]] = []
        if event and plan.kept is not None:
            kept = [("Kept when it stops", "yes" if plan.kept else "no", "" if plan.kept or not warned else "warn")]
        commands = "\n".join(step.command for step in plan.steps)
        lifecycle = (
            _Text(
                kernels_lifecycle(plan.on_start, env.name if env.kind == "notebook instance" else ""),
                title="Add the kernels back at every start (lifecycle configuration)",
                code=True,
                collapsed=True,
            )
            if plan.on_start
            else None
        )
        if dry_run:
            low = plan.free is not None and plan.free < LOW_DISK
            cards: list[tuple[str, ...]] = [
                ("Python", plan.python),
                ("Name", plan.name),
                ("Where", where),
                *kept,
                ("Made with", plan.tool if not plan.exists else "there already"),
                ("Kernel", plan.kernel),
            ]
            if plan.free is not None:
                cards.append(("Free there", human_size(plan.free), "warn" if low and "free" in warned else ""))
            blocks: list[Any] = [
                _Title(f"New environment {plan.name}", "what new_env() would do · nothing has run yet"),
                _Cards(cards),
                _Findings(findings),
                _Text(commands, title="Commands, to run in a terminal", code=True),
            ]
            blocks += [lifecycle] if lifecycle else []
            blocks.append(
                _Next(
                    [
                        (self._again(plan), "run them: make it and add its kernel"),
                        ("envs()", "the environments and kernels here"),
                    ]
                )
            )
            self._show(blocks)
            return
        with self._progress(f"{'Setting up' if plan.exists else 'Making'} {plan.name}", unit="steps") as tick:
            result = self.core.create_env(plan, progress=tick)
        step = result.failed
        took = human_runtime(result.seconds)
        if step is None:
            python = result.python or plan.python
            blocks = [
                _Title(
                    f"{plan.name}: Python {python}, kernel added" if plan.exists
                    else f"New environment {plan.name}: Python {python}",
                    " · ".join(
                        [
                            f"took {took}" if plan.exists else f"made with {plan.tool} in {took}",
                            f"'{plan.kernel}' in Jupyter's kernel menu",
                        ]
                    ),
                ),
                _Cards([("Python", python), ("Kernel", plan.kernel), ("Where", where), *kept, ("Took", took)]),
                _Note(
                    f"Ready: pick '{plan.kernel}' in Jupyter's kernel menu (Kernel > Change Kernel), or start a "
                    "notebook with it from the Launcher; it can take a minute to show up. %pip install in a notebook "
                    f"on that kernel installs into it, and in a terminal its Python is {where}/bin/python.",
                    "ok",
                ),
                _Findings(findings),
            ]
            steps = [
                (
                    self._again(plan, packages="-r requirements.txt"),
                    "install your project's packages in it: only those, since it's there",
                ),
                ("envs()", "every environment and kernel here"),
            ]
        else:
            failures = [
                (
                    "warn",
                    f"{step.label} failed"
                    + (f" (exit status {step.code})" if step.code not in (None, -1) else "")
                    + f" after {human_runtime(step.seconds)}. {env_failure(step, plan.python)}",
                )
            ]
            if result.leftover:
                failures.append(
                    (
                        "info",
                        f"It left {where} half made, and new_env() won't use a folder that isn't an environment. "
                        f"Remove it before trying again: rm -rf {where}",
                    )
                )
            blocks = [
                _Title(
                    f"New environment {plan.name}: not made" if not plan.exists
                    else f"{plan.name}: {step.label.lower()} failed",
                    f"Python {plan.python} with {plan.tool} · stopped after {took}",
                ),
                _Cards(
                    [("Python", plan.python), ("Where", where), ("Made with", plan.tool), ("Failed at", step.label, "bad")]
                ),
                _Findings(failures + [(level, message) for level, message in findings if level == "warn"]),
            ]
            if step.output:
                blocks.append(_Text(step.output, title="The end of what it printed"))
            steps = [(self._again(plan, dry_run=True), "the commands, without running them")]
            if "disk()" in failures[0][1]:
                steps.append((_call("disk", _tilde(plan.folder, home)), "what fills the disk"))
            steps.append(("envs()", "the environments and kernels here"))
        rows = []
        for s in plan.steps:
            outcome: Any = "not run"
            if s.code == 0:
                outcome = _Tone("done", "ok")
            elif s.code is not None:
                outcome = _Tone("stopped: too slow" if s.code == -1 else f"failed ({s.code})", "bad")
            rows.append([s.label, "-" if s.seconds is None else human_runtime(s.seconds), outcome])
        blocks.append(_Table(["Step", "Took", "Result"], rows, title="Steps", max_rows=0, sortable=False))
        blocks.append(
            _Text(
                commands,
                title="Commands, to run in a terminal" if step else "Commands it ran",
                code=True,
                collapsed=step is None,
            )
        )
        blocks += [lifecycle] if lifecycle and step is None else []
        blocks.append(_Next(steps))
        self._show(blocks)

    # ----------------------------------------------------------------- running

    @_friendly_errors
    def running(self, *, metrics: bool = True, days: int = 7) -> None:
        """
        Everything SageMaker bills by the hour in the region right now: notebook instances, Studio apps,
        endpoints and jobs, with what each costs, which ones look forgotten, and the command that stops each.

        Endpoint traffic comes from CloudWatch over the last `days` days (metrics=False skips it). Stopped notebook
        instances are listed too, because their storage is still billed.
        """
        days = _as_int(days, "days")
        with self._progress("Checking", unit="kinds of resource") as tick:
            report = self.core.running(metrics=metrics, days=days, progress=tick)
        prices = self.core.prices
        now = _utcnow()
        findings = running_findings(report, prices, now)
        resources = sorted(
            report.resources, key=lambda b: (-(b.hourly(prices) or 0), b.kind, b.name)
        )
        hourly = sum(b.hourly(prices) or 0 for b in resources)
        unknown = any(b.hourly(prices) is None for b in resources)
        storage = sum(
            (b.volume_gb or 0) * prices.get("notebook_storage", 0.0)
            for b in report.stopped
        )
        warnings = sum(level == "warn" for level, _ in findings)
        plus = "+" if unknown else ""
        cards: list[tuple[str, ...]] = [
            ("Running now", f"{len(resources):,}"),
            ("Est. cost / hour", human_money(hourly) + plus),
            ("Est. / month if left on", human_money(hourly * HOURS_PER_MONTH) + plus),
            ("Warnings", f"{warnings:,}", "warn" if warnings else "ok"),
        ]
        if report.stopped:
            cards.append(
                ("Stopped notebooks' storage", f"{human_money(storage)}/month")
            )
        traffic = (
            f"endpoint requests from CloudWatch, last {report.days} days"
            if metrics
            else ""
        )
        blocks: list[Any] = [
            _Title(
                f"Running in SageMaker, {report.region} ({len(resources)})",
                " · ".join(
                    filter(
                        None,
                        [
                            f"cost at {self._price_basis()}",
                            traffic,
                            "this tool never stops or deletes anything",
                        ],
                    )
                ),
            ),
            _Cards(cards),
            _Findings(findings, empty="Nothing looks forgotten."),
        ]
        if not resources and not report.stopped:
            blocks.append(
                _Note(
                    f"Nothing in SageMaker bills by the hour in {report.region} right now: no notebook instances, Studio "
                    "apps, endpoints or jobs are running. SageMaker is regional: "
                    "SageMakerView(SageMakerAnalyzer(region='us-west-2')) checks another region."
                )
            )
        if resources:
            has_endpoints = any(b.kind == "endpoint" for b in resources)
            has_studio = any(b.kind == "Studio app" for b in resources)
            headers = [
                "Kind",
                "Name",
                "Status",
                "Instance",
                "Est. $/hour",
                "Running for",
                "Stops when idle",
            ]
            headers += [f"Requests, {report.days}d"] if has_endpoints else []
            headers += ["Where"] if has_studio else []
            rows = []
            for b in resources:
                price = b.hourly(prices)
                if b.kind in ("notebook instance", "Studio app"):
                    idle: Any = _idle_label(b.idle, b.idle_minutes)
                    if b.idle == "off" and _forgotten(b, now):
                        idle = _Tone(idle, "warn")
                elif b.kind == "endpoint":
                    idle = "-"
                else:
                    idle = "when done"
                row: list[Any] = [
                    _kind_label(b),
                    b.name + ("  📍\u00a0this\u00a0notebook" if b.this else ""),  # the marker never wraps
                    _Tone(
                        b.status,
                        "" if b.status in ("InService", "Training") else "warn",
                    ),
                    b.instance_label + (" (spot)" if b.spot else ""),
                    "per request"
                    if b.serverless
                    else human_money(price)
                    if price is not None
                    else "?",
                    human_runtime(_since(b.since, now)),
                    idle,
                ]
                if has_endpoints:
                    if b.kind != "endpoint":
                        row.append("")
                    elif b.serverless:
                        row.append("-")
                    elif b.invocations is None:
                        row.append("?")
                    else:
                        count = f"{b.invocations:,.0f}"
                        row.append(
                            _Tone(count, "warn") if _unused_endpoint(b, now) else count
                        )
                if has_studio:
                    row.append(_where(b))
                rows.append(row)
            blocks.append(_Table(headers, rows, max_rows=0))
        if report.stopped:
            rows = [
                [
                    b.name,
                    b.instance_label,
                    f"{b.volume_gb} GB" if b.volume_gb else "?",
                    human_money(
                        (b.volume_gb or 0) * prices.get("notebook_storage", 0.0)
                    ),
                    human_age(b.since, now),
                ]
                for b in sorted(report.stopped, key=lambda b: -(b.volume_gb or 0))
            ]
            blocks.append(
                _Table(
                    [
                        "Notebook instance",
                        "Instance",
                        "Volume",
                        "Storage $/month",
                        "Stopped",
                    ],
                    rows,
                    title="Stopped notebook instances (their volumes are still billed)",
                    collapsed=len(rows) > 10,
                )
            )
        steps = []
        flagged = [b for b in resources if _forgotten(b, now) and _notebook_ref(b)]
        if flagged:
            steps.append(
                (
                    _call("instance", _notebook_ref(flagged[0])),
                    "its settings, and how to turn on idle shutdown",
                )
            )
        if self.core.environment().on_sagemaker:
            steps.append(("instance()", "the notebook this code runs in"))
        if (
            metrics
            and any(b.kind == "endpoint" and not b.serverless for b in resources)
            and report.days < 30
        ):
            steps.append(
                (_call("running", days=30), "endpoint traffic over the last 30 days")
            )
        blocks.append(_Next(steps))
        self._show(blocks)
