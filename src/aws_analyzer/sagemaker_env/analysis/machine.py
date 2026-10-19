"""
What this machine's files say: /proc's memory, load and processes, nvidia-smi's GPUs and
SageMaker's metadata file.
"""

from __future__ import annotations

from typing import Any

from ..._kit.text import _clip

from ..helpers import KB, MB, _APP_SETTINGS, _arn_name, _arn_part
from ..models import Environment, GPUInfo, ProcessInfo


def parse_meminfo(text: str) -> dict[str, int]:
    """
    /proc/meminfo -> {'MemTotal': bytes, 'MemAvailable': bytes, ...}.
    """
    values: dict[str, int] = {}
    for line in text.splitlines():
        name, _, rest = line.partition(":")
        parts = rest.split()
        if parts and parts[0].isdigit():
            values[name.strip()] = int(parts[0]) * (
                KB if len(parts) > 1 and parts[1].lower() == "kb" else 1
            )
    return values


def parse_loadavg(text: str) -> tuple[float, float, float]:
    """
    /proc/loadavg -> the 1, 5 and 15-minute load averages (processes running or waiting to run).
    """
    one, five, fifteen = (float(value) for value in text.split()[:3])
    return one, five, fifteen


def parse_gpus(text: str) -> list[GPUInfo]:
    """
    nvidia-smi --query-gpu=name,utilization.gpu,memory.used,memory.total --format=csv,noheader,nounits
    -> [GPUInfo]. '[N/A]' values become None.
    """

    def number(value: str) -> float | None:
        try:
            return float(value.strip())
        except ValueError:
            return None

    gpus = []
    for line in text.strip().splitlines():
        parts = line.rsplit(",", 3)
        if len(parts) != 4:
            continue
        busy, used, total = (number(value) for value in parts[1:])
        gpus.append(
            GPUInfo(
                parts[0].strip(),
                busy,
                None if used is None else int(used * MB),
                None if total is None else int(total * MB),
            )
        )
    return gpus


def parse_process(pid: int, status: str, cmdline: bytes) -> ProcessInfo | None:
    """
    One process from /proc/<pid>/status and /proc/<pid>/cmdline. None for kernel threads (no memory).
    """
    fields = dict(line.split(":", 1) for line in status.splitlines() if ":" in line)
    rss = fields.get("VmRSS", "").split()
    if not rss or not rss[0].isdigit():
        return None
    args = [arg.decode("utf-8", "replace") for arg in cmdline.split(b"\0") if arg]
    name = fields.get("Name", "").strip()
    return ProcessInfo(
        pid,
        name,
        _clip(" ".join(args) or name, 120),
        int(rss[0]) * KB,
        kernel=any("ipykernel" in arg for arg in args),
    )


def parse_metadata(data: dict[str, Any]) -> Environment:
    """
    SageMaker's resource-metadata.json -> Environment. Notebook instances write ResourceArn and ResourceName;
    Studio apps also write DomainId, AppType, and SpaceName or UserProfileName.
    """
    arn = data.get("ResourceArn") or ""
    resource = _arn_part(arn, 5)
    env = Environment(
        name=data.get("ResourceName") or _arn_name(arn),
        arn=arn,
        domain_id=data.get("DomainId") or "",
        user_profile=data.get("UserProfileName") or "",
        space=data.get("SpaceName") or "",
        app_type=data.get("AppType") or "",
        role_arn=data.get("ExecutionRoleArn") or "",
        metadata=dict(data),
    )
    if env.domain_id or resource.startswith("app/"):
        env.kind = "studio"
        parts = resource.split(
            "/"
        )  # app/<domain>/<space or user>/<app type>/<app name>
        if len(parts) == 5:
            env.domain_id = env.domain_id or parts[1]
            env.app_type = env.app_type or parts[3]
            env.name = parts[4] or env.name
            if not (env.space or env.user_profile):
                if env.app_type in _APP_SETTINGS:
                    env.space = parts[
                        2
                    ]  # JupyterLab and Code Editor apps always run in a space
                else:
                    env.user_profile = parts[2]
    elif resource.startswith("notebook-instance/"):
        env.kind = "notebook instance"
    else:
        env.kind = "other" if arn else "local"
    return env
