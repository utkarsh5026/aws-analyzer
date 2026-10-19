"""
Parsing and formatting: sizes, run times, ARNs, instance types and their prices, app labels, idle
shutdown in lifecycle scripts, Python versions and environment names, and paths for a shell.
"""

from __future__ import annotations

import os
import re
import shlex
from datetime import datetime
from typing import Any, Iterable

from .._kit.fmt import _plural, _utcnow

from .prices import INSTANCE_TYPES, SAGEMAKER_PRICES


KB, MB, GB, TB = 1024, 1024**2, 1024**3, 1024**4


_APP_LABELS = {
    "JupyterLab": "JupyterLab",
    "CodeEditor": "Code Editor",
    "KernelGateway": "Studio Classic kernel",
    "JupyterServer": "Studio Classic",
    "RStudioServerPro": "RStudio",
    "RSessionGateway": "RStudio session",
    "Canvas": "Canvas",
    "TensorBoard": "TensorBoard",
}

_APP_SETTINGS = {
    "JupyterLab": "JupyterLabAppSettings",
    "CodeEditor": "CodeEditorAppSettings",
}  # apps with idle shutdown


def human_runtime(seconds: float | None) -> str:
    """
    How long something has run: 42 -> '42s', 3000 -> '50m', 7500 -> '2h 05m', 273600 -> '3d 4h'.
    """
    if seconds is None:
        return "-"
    seconds = max(int(seconds), 0)
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m"
    if seconds < 86400:
        return f"{seconds // 3600}h {seconds % 3600 // 60:02d}m"
    return f"{seconds // 86400}d {seconds % 86400 // 3600}h"


def _since(when: datetime | None, now: datetime | None = None) -> float | None:
    """
    Seconds from `when` until now (None stays None).
    """
    return None if when is None else ((now or _utcnow()) - when).total_seconds()


def _arn_part(arn: str, index: int) -> str:
    """
    'arn:aws:sagemaker:us-east-1:123:notebook-instance/nb', 3 -> 'us-east-1'.
    """
    parts = (arn or "").split(":", 5)
    return parts[index] if len(parts) > index else ""


def _arn_name(arn: str) -> str:
    """
    The last part of an ARN's resource: '.../role/service-role/MyRole' -> 'MyRole'.
    """
    return (arn or "").rstrip("/").rsplit("/", 1)[-1]


def instance_spec(instance_type: str) -> tuple[int, float, int] | None:
    """
    'ml.m5.xlarge' -> (4, 16, 0): vCPUs, memory in GiB and GPUs. None for a type INSTANCE_TYPES doesn't list.
    """
    spec = INSTANCE_TYPES.get(instance_type)
    return spec[1:] if spec else None


def describe_instance(instance_type: str) -> str:
    """
    'ml.g5.xlarge' -> '4 vCPU · 16 GiB · 1 GPU'. '' for a type INSTANCE_TYPES doesn't list.
    """
    if instance_type == "system":
        return "a small shared instance, no charge"
    spec = instance_spec(instance_type)
    if spec is None:
        return ""
    vcpus, memory, gpus = spec
    return f"{vcpus} vCPU · {memory:g} GiB" + (
        f" · {_plural(gpus, 'GPU')}" if gpus else ""
    )


def hourly_price(
    instance_type: str, prices: dict[str, float] | None = None
) -> float | None:
    """
    USD per hour for one instance of this type. 0 for Studio Classic's free 'system' instance, None when the
    price table doesn't have the type (pass prices={'ml.x.y': usd_per_hour}).
    """
    if instance_type == "system":
        return 0.0
    return (SAGEMAKER_PRICES if prices is None else prices).get(instance_type)


def app_label(app_type: str) -> str:
    """
    'CodeEditor' -> 'Code Editor', 'KernelGateway' -> 'Studio Classic kernel'.
    """
    return _APP_LABELS.get(app_type, app_type)


def _image_label(spec: dict[str, Any]) -> str:
    """
    A Studio ResourceSpec's image as 'sagemaker-distribution-cpu 2.6' (or '... v15' from a version ARN).
    """
    version_arn = spec.get("SageMakerImageVersionArn") or ""
    image = _arn_name(spec.get("SageMakerImageArn") or "")
    if version_arn:
        parts = version_arn.rsplit("/", 2)
        image, version = (
            (parts[-2], "v" + parts[-1]) if len(parts) == 3 else (image, "")
        )
    else:
        version = spec.get("SageMakerImageVersionAlias") or ""
    return f"{image} {version}".strip()


_AUTOSTOP_RE = re.compile(r"auto-?stop|stop[-_]notebook[-_]instance", re.IGNORECASE)

_IDLE_TIME_RE = re.compile(r"(?:IDLE_TIME\s*=\s*|--time[ =])[\"']?(\d+)")


def lifecycle_idle(scripts: Iterable[str]) -> tuple[bool, int | None]:
    """
    Whether a notebook instance's lifecycle scripts stop it when idle (the usual way is AWS's auto-stop-idle
    sample), and after how many idle minutes when the script says -> (stops_when_idle, minutes).
    """
    text = "\n".join(scripts)
    if not _AUTOSTOP_RE.search(text):
        return False, None
    match = _IDLE_TIME_RE.search(text)
    return True, (int(match.group(1)) // 60 if match else None)


_VERSION_RE = re.compile(r"^(?:python|py)?\s*3\.?(\d{1,2})(?:\.(\d{1,3}))?$", re.IGNORECASE)

_ENV_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


def parse_python_version(value: Any) -> str:
    """
    A Python version as conda and uv take it: '3.11', 3.11, 'py311', 'python3.11' -> '3.11'; '3.11.9' stays
    '3.11.9'. The number 3.10 reads as 3.1, so a number 3.1 means 3.10.
    """
    if isinstance(value, float):
        text = "3.10" if value == 3.1 else repr(value)
    else:
        text = str(value).strip()
    match = _VERSION_RE.match(text)
    if not match:
        raise ValueError(f"python takes a version like '3.11' or '3.12.4' (got {value!r}).")
    minor, patch = match.groups()
    return f"3.{int(minor)}" + (f".{int(patch)}" if patch is not None else "")


def env_name(version: str) -> str:
    """
    The name new_env() gives an environment: '3.11' -> 'py311', '3.12.4' -> 'py312'.
    """
    return "py" + "".join(version.split(".")[:2])


def new_env_call(python: str, name: str = "", **kwargs: Any) -> str:
    """
    A new_env() call to copy, leaving out name= when it's the default: ('3.11', 'py311') -> "new_env('3.11')",
    ('3.11', 'exp', packages='pandas') -> "new_env('3.11', name='exp', packages='pandas')".
    """
    words = [repr(python)] + ([f"name={name!r}"] if name and name != env_name(python) else [])
    return f"new_env({', '.join(words + [f'{key}={value!r}' for key, value in kwargs.items()])})"


def _minor(version: str) -> str:
    """
    '3.11.9' -> '3.11' ('' -> '3.11': the version new_env() suggests when it can't tell).
    """
    return ".".join(version.split(".")[:2]) if version else "3.11"


def kernel_label(version: str, name: str) -> str:
    """
    What Jupyter's kernel menu shows for it: ('3.11.9', 'py311') -> 'Python 3.11 (py311)'.
    """
    return f"Python {'.'.join(version.split('.')[:2])} ({name})"


def parse_pyvenv_cfg(text: str) -> dict[str, str]:
    """
    A virtual environment's pyvenv.cfg -> {'home': ..., 'version': '3.11.9', ...} (uv writes version_info).
    """
    values = {}
    for line in text.splitlines():
        key, sep, value = line.partition("=")
        if sep:
            values[key.strip().lower()] = value.strip()
    return values


def _clean_version(text: str) -> str:
    """
    '3.11.9.final.0' -> '3.11.9'.
    """
    match = re.match(r"\d+\.\d+(?:\.\d+)?", text.strip())
    return match.group(0) if match else ""


def shell_path(path: str, home: str | None = None) -> str:
    """
    A path ready for a shell command, under your home folder written as ~/...: '/home/ec2-user/SageMaker/x y'
    -> "~/SageMaker/'x y'".
    """
    home = (home or os.path.expanduser("~")).rstrip("/")
    if home and path == home:
        return "~"
    if home and path.startswith(home + "/"):
        return "~/" + shlex.quote(path[len(home) + 1 :])
    return shlex.quote(path)


def _tilde(path: str, home: str | None = None) -> str:
    """
    A path for a call to copy, under your home folder written as ~/...: '/home/ec2-user/SageMaker/envs' ->
    '~/SageMaker/envs' (disk() and new_env() read ~).
    """
    home = (home or os.path.expanduser("~")).rstrip("/")
    if home and (path == home or path.startswith(home + "/")):
        return "~" + path[len(home):]
    return path
