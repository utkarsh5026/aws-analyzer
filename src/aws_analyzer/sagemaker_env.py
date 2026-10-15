"""
sagemaker_env.py - SageMaker toolkit for SageMaker / Jupyter notebooks: the notebook you're running in, what fills
its disk, its Python environments, and everything else in SageMaker that's running and billing.

Part of aws-analyzer: ``pip install aws-analyzer``, then ``from aws_analyzer import SageMakerView``. It isn't called
sagemaker.py, so it's never mixed up with the SageMaker Python SDK (``import sagemaker``).

Requirements: boto3 (required). pandas only for DataFrames, IPython only for rich
HTML output. All are preinstalled on SageMaker.

The file has two layers:

    SageMakerAnalyzer  Pure logic. Reads this machine (SageMaker's metadata file, memory,
                       disks, GPUs) and talks to AWS, and returns plain Python data
                       (dataclasses, dicts, lists, DataFrames). Never prints.
    SageMakerView      Notebook UI. Calls SageMakerAnalyzer and renders readable
                       cards and tables (HTML in Jupyter, plain text in a terminal).

Nothing in this file changes AWS: it never stops a notebook or deletes an app or an endpoint,
and it never removes a file. Where one of those would help, it shows the command to run. The
one thing it makes is what new_env() is asked for: a Python environment, in a folder that's
kept when the notebook stops, and its Jupyter kernel.

Quick start
-----------
    ui = SageMakerView()                              # or SageMakerView(SageMakerAnalyzer(region="eu-west-1"))
    ui.help()                                         # list every command
    ui.instance()                                     # this notebook: type, cost, idle shutdown, CPU / memory / disk / GPU
    ui.instance("team-notebook")                      # another notebook instance, or a Studio space by name
    ui.disk()                                         # what fills the disk, and caches or trash you can clear
    ui.disk("~/SageMaker/data")                       # one folder
    ui.envs()                                         # the Python environments and Jupyter kernels here
    ui.new_env("3.11")                                # a Python 3.11 environment with its own kernel, kept when it stops
    ui.running()                                      # everything running and billing in the region, and what looks forgotten

    sm = ui.core                                      # same analyzer, raw data
    report = sm.instance()                            # InstanceReport: .notebook (settings), .machine (right now)
    df = sm.running().to_df()                         # one row per notebook, app, endpoint and job
    folders = sm.disk().to_df()                       # folder sizes
    envs = sm.environments().to_df()                  # one row per Python environment
"""

from __future__ import annotations

import base64
import difflib
import heapq
import importlib
import importlib.metadata
import json
import math
import os
import platform
import re
import shlex
import shutil
import stat
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

from botocore.exceptions import BotoCoreError, ClientError, NoRegionError

from ._kit.analyzer import _BaseAnalyzer
from ._kit.blocks import _Cards, _Findings, _Next, _Note, _Table, _Text, _Title, _Tone
from ._kit.deps import _require
from ._kit.errors import _error_code, _error_name, _Hint, _why
from ._kit.fmt import (
    HOURS_PER_MONTH, _as_count, _as_int, _fmt_dt, _plural, _utcnow, human_age, human_money, human_size,
)
from ._kit.render import _base_css, _call, _report_html
from ._kit.text import _clip
from ._kit.view import _BaseView, _friendly_errors

# =============================================================================
# 1. Helpers: parsing and formatting
# =============================================================================

KB, MB, GB, TB = 1024, 1024**2, 1024**3, 1024**4

# Instance type -> (USD per hour, vCPUs, memory in GiB, GPUs). us-east-1 on-demand list prices, read from the AWS
# Price List API (the AmazonSageMaker offer file) on 2026-09-27. A type costs the same per hour whether it runs a
# notebook instance, a Studio app (JupyterLab, Code Editor, Studio Classic), an endpoint or a training or
# processing job, to within a cent. Other regions differ; pass SageMakerAnalyzer(prices={"ml.g5.xlarge": 1.21})
# to use your own.
INSTANCE_TYPES: dict[str, tuple[float, int, float, int]] = {
    # t2 / t3: small, burstable
    "ml.t2.medium": (0.0464, 2, 4, 0),
    "ml.t2.large": (0.111, 2, 8, 0),
    "ml.t2.xlarge": (0.223, 4, 16, 0),
    "ml.t2.2xlarge": (0.445, 8, 32, 0),
    "ml.t3.medium": (0.05, 2, 4, 0),
    "ml.t3.large": (0.1, 2, 8, 0),
    "ml.t3.xlarge": (0.2, 4, 16, 0),
    "ml.t3.2xlarge": (0.399, 8, 32, 0),
    # m: general purpose
    "ml.m5.large": (0.115, 2, 8, 0),
    "ml.m5.xlarge": (0.23, 4, 16, 0),
    "ml.m5.2xlarge": (0.461, 8, 32, 0),
    "ml.m5.4xlarge": (0.922, 16, 64, 0),
    "ml.m5.8xlarge": (1.843, 32, 128, 0),
    "ml.m5.12xlarge": (2.765, 48, 192, 0),
    "ml.m5.16xlarge": (3.686, 64, 256, 0),
    "ml.m5.24xlarge": (5.53, 96, 384, 0),
    "ml.m5d.large": (0.136, 2, 8, 0),
    "ml.m5d.xlarge": (0.271, 4, 16, 0),
    "ml.m5d.2xlarge": (0.542, 8, 32, 0),
    "ml.m5d.4xlarge": (1.085, 16, 64, 0),
    "ml.m5d.8xlarge": (2.17, 32, 128, 0),
    "ml.m5d.12xlarge": (3.254, 48, 192, 0),
    "ml.m5d.16xlarge": (4.339, 64, 256, 0),
    "ml.m5d.24xlarge": (6.509, 96, 384, 0),
    "ml.m6i.large": (0.115, 2, 8, 0),
    "ml.m6i.xlarge": (0.23, 4, 16, 0),
    "ml.m6i.2xlarge": (0.461, 8, 32, 0),
    "ml.m6i.4xlarge": (0.922, 16, 64, 0),
    "ml.m6i.8xlarge": (1.843, 32, 128, 0),
    "ml.m6i.12xlarge": (2.765, 48, 192, 0),
    "ml.m6i.16xlarge": (3.686, 64, 256, 0),
    "ml.m6i.24xlarge": (5.53, 96, 384, 0),
    "ml.m6i.32xlarge": (7.373, 128, 512, 0),
    "ml.m7i.large": (0.121, 2, 8, 0),
    "ml.m7i.xlarge": (0.242, 4, 16, 0),
    "ml.m7i.2xlarge": (0.484, 8, 32, 0),
    "ml.m7i.4xlarge": (0.968, 16, 64, 0),
    "ml.m7i.8xlarge": (1.935, 32, 128, 0),
    "ml.m7i.12xlarge": (2.903, 48, 192, 0),
    "ml.m7i.16xlarge": (3.871, 64, 256, 0),
    "ml.m7i.24xlarge": (5.806, 96, 384, 0),
    "ml.m7i.48xlarge": (11.612, 192, 768, 0),
    # c: compute optimized
    "ml.c5.large": (0.102, 2, 4, 0),
    "ml.c5.xlarge": (0.204, 4, 8, 0),
    "ml.c5.2xlarge": (0.408, 8, 16, 0),
    "ml.c5.4xlarge": (0.816, 16, 32, 0),
    "ml.c5.9xlarge": (1.836, 36, 72, 0),
    "ml.c5.12xlarge": (2.448, 48, 96, 0),
    "ml.c5.18xlarge": (3.672, 72, 144, 0),
    "ml.c5.24xlarge": (4.896, 96, 192, 0),
    "ml.c6i.large": (0.102, 2, 4, 0),
    "ml.c6i.xlarge": (0.204, 4, 8, 0),
    "ml.c6i.2xlarge": (0.408, 8, 16, 0),
    "ml.c6i.4xlarge": (0.816, 16, 32, 0),
    "ml.c6i.8xlarge": (1.632, 32, 64, 0),
    "ml.c6i.12xlarge": (2.448, 48, 96, 0),
    "ml.c6i.16xlarge": (3.264, 64, 128, 0),
    "ml.c6i.24xlarge": (4.896, 96, 192, 0),
    "ml.c6i.32xlarge": (6.528, 128, 256, 0),
    "ml.c7i.large": (0.107, 2, 4, 0),
    "ml.c7i.xlarge": (0.214, 4, 8, 0),
    "ml.c7i.2xlarge": (0.428, 8, 16, 0),
    "ml.c7i.4xlarge": (0.857, 16, 32, 0),
    "ml.c7i.8xlarge": (1.714, 32, 64, 0),
    "ml.c7i.12xlarge": (2.57, 48, 96, 0),
    "ml.c7i.16xlarge": (3.427, 64, 128, 0),
    "ml.c7i.24xlarge": (5.141, 96, 192, 0),
    "ml.c7i.48xlarge": (10.282, 192, 384, 0),
    # r: memory optimized
    "ml.r5.large": (0.151, 2, 16, 0),
    "ml.r5.xlarge": (0.302, 4, 32, 0),
    "ml.r5.2xlarge": (0.605, 8, 64, 0),
    "ml.r5.4xlarge": (1.21, 16, 128, 0),
    "ml.r5.8xlarge": (2.419, 32, 256, 0),
    "ml.r5.12xlarge": (3.629, 48, 384, 0),
    "ml.r5.16xlarge": (4.838, 64, 512, 0),
    "ml.r5.24xlarge": (7.258, 96, 768, 0),
    "ml.r6i.large": (0.151, 2, 16, 0),
    "ml.r6i.xlarge": (0.302, 4, 32, 0),
    "ml.r6i.2xlarge": (0.605, 8, 64, 0),
    "ml.r6i.4xlarge": (1.21, 16, 128, 0),
    "ml.r6i.8xlarge": (2.419, 32, 256, 0),
    "ml.r6i.12xlarge": (3.629, 48, 384, 0),
    "ml.r6i.16xlarge": (4.838, 64, 512, 0),
    "ml.r6i.24xlarge": (7.258, 96, 768, 0),
    "ml.r6i.32xlarge": (9.677, 128, 1024, 0),
    "ml.r7i.large": (0.159, 2, 16, 0),
    "ml.r7i.xlarge": (0.318, 4, 32, 0),
    "ml.r7i.2xlarge": (0.635, 8, 64, 0),
    "ml.r7i.4xlarge": (1.27, 16, 128, 0),
    "ml.r7i.8xlarge": (2.54, 32, 256, 0),
    "ml.r7i.12xlarge": (3.81, 48, 384, 0),
    "ml.r7i.16xlarge": (5.08, 64, 512, 0),
    "ml.r7i.24xlarge": (7.62, 96, 768, 0),
    "ml.r7i.48xlarge": (15.241, 192, 1536, 0),
    # g / p: NVIDIA GPUs
    "ml.g4dn.xlarge": (0.7364, 4, 16, 1),
    "ml.g4dn.2xlarge": (0.94, 8, 32, 1),
    "ml.g4dn.4xlarge": (1.505, 16, 64, 1),
    "ml.g4dn.8xlarge": (2.72, 32, 128, 1),
    "ml.g4dn.12xlarge": (4.89, 48, 192, 4),
    "ml.g4dn.16xlarge": (5.44, 64, 256, 1),
    "ml.g5.xlarge": (1.41, 4, 16, 1),
    "ml.g5.2xlarge": (1.52, 8, 32, 1),
    "ml.g5.4xlarge": (2.03, 16, 64, 1),
    "ml.g5.8xlarge": (3.06, 32, 128, 1),
    "ml.g5.12xlarge": (7.09, 48, 192, 4),
    "ml.g5.16xlarge": (5.12, 64, 256, 1),
    "ml.g5.24xlarge": (10.18, 96, 384, 4),
    "ml.g5.48xlarge": (20.36, 192, 768, 8),
    "ml.g6.xlarge": (1.127, 4, 16, 1),
    "ml.g6.2xlarge": (1.222, 8, 32, 1),
    "ml.g6.4xlarge": (1.654, 16, 64, 1),
    "ml.g6.8xlarge": (2.518, 32, 128, 1),
    "ml.g6.12xlarge": (5.752, 48, 192, 4),
    "ml.g6.16xlarge": (4.246, 64, 256, 1),
    "ml.g6.24xlarge": (8.344, 96, 384, 4),
    "ml.g6.48xlarge": (16.688, 192, 768, 8),
    "ml.g6e.xlarge": (2.61, 4, 32, 1),
    "ml.g6e.2xlarge": (2.8, 8, 64, 1),
    "ml.g6e.4xlarge": (3.76, 16, 128, 1),
    "ml.g6e.8xlarge": (5.66, 32, 256, 1),
    "ml.g6e.12xlarge": (13.12, 48, 384, 4),
    "ml.g6e.16xlarge": (9.47, 64, 512, 1),
    "ml.g6e.24xlarge": (18.83, 96, 768, 4),
    "ml.g6e.48xlarge": (37.66, 192, 1536, 8),
    "ml.p4d.24xlarge": (25.2513, 96, 1152, 8),
    "ml.p4de.24xlarge": (31.5641, 96, 1152, 8),
    "ml.p5.4xlarge": (7.912, 16, 256, 1),
    "ml.p5.48xlarge": (63.296, 192, 2048, 8),
    # AWS Trainium and Inferentia accelerators (not GPUs)
    "ml.trn1.2xlarge": (1.54, 8, 32, 0),
    "ml.trn1.32xlarge": (24.73, 128, 512, 0),
    "ml.inf2.xlarge": (0.99, 4, 16, 0),
    "ml.inf2.8xlarge": (2.36, 32, 128, 0),
    "ml.inf2.24xlarge": (7.79, 96, 384, 0),
    "ml.inf2.48xlarge": (15.58, 192, 768, 0),
}

# USD, us-east-1 list prices read on 2026-09-27: storage per GB-month, plus every instance type's price per hour
# from INSTANCE_TYPES. Pass SageMakerAnalyzer(prices={...}) to override any of them.
SAGEMAKER_PRICES: dict[str, float] = {
    "notebook_storage": 0.14,  # per GB-month of a notebook instance's ML storage volume, billed while stopped too
    "space_storage": 0.112,  # per GB-month of a Studio space's EBS volume (gp3), billed while its app is stopped too
    "s3_storage": 0.023,  # per GB-month in S3 Standard, to compare with keeping files on a notebook's volume
    **{name: spec[0] for name, spec in INSTANCE_TYPES.items()},
}

METADATA_FILE = "opt/ml/metadata/resource-metadata.json"  # under the analyzer's root, "/" on SageMaker
_HOMES = (
    "home/ec2-user/SageMaker",
    "home/sagemaker-user",
)  # a notebook instance's volume, a Studio space's home
_PACKAGES = (
    "boto3",
    "sagemaker",
    "pandas",
    "numpy",
    "pyarrow",
    "scikit-learn",
    "xgboost",
    "torch",
    "tensorflow",
    "transformers",
    "jupyterlab",
    "ipykernel",
)
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
    """How long something has run: 42 -> '42s', 3000 -> '50m', 7500 -> '2h 05m', 273600 -> '3d 4h'."""
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
    """Seconds from `when` until now (None stays None)."""
    return None if when is None else ((now or _utcnow()) - when).total_seconds()


def _arn_part(arn: str, index: int) -> str:
    """'arn:aws:sagemaker:us-east-1:123:notebook-instance/nb', 3 -> 'us-east-1'."""
    parts = (arn or "").split(":", 5)
    return parts[index] if len(parts) > index else ""


def _arn_name(arn: str) -> str:
    """The last part of an ARN's resource: '.../role/service-role/MyRole' -> 'MyRole'."""
    return (arn or "").rstrip("/").rsplit("/", 1)[-1]


def instance_spec(instance_type: str) -> tuple[int, float, int] | None:
    """'ml.m5.xlarge' -> (4, 16, 0): vCPUs, memory in GiB and GPUs. None for a type INSTANCE_TYPES doesn't list."""
    spec = INSTANCE_TYPES.get(instance_type)
    return spec[1:] if spec else None


def describe_instance(instance_type: str) -> str:
    """'ml.g5.xlarge' -> '4 vCPU · 16 GiB · 1 GPU'. '' for a type INSTANCE_TYPES doesn't list."""
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
    """USD per hour for one instance of this type. 0 for Studio Classic's free 'system' instance, None when the
    price table doesn't have the type (pass prices={'ml.x.y': usd_per_hour})."""
    if instance_type == "system":
        return 0.0
    return (SAGEMAKER_PRICES if prices is None else prices).get(instance_type)


def app_label(app_type: str) -> str:
    """'CodeEditor' -> 'Code Editor', 'KernelGateway' -> 'Studio Classic kernel'."""
    return _APP_LABELS.get(app_type, app_type)


def _image_label(spec: dict[str, Any]) -> str:
    """A Studio ResourceSpec's image as 'sagemaker-distribution-cpu 2.6' (or '... v15' from a version ARN)."""
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


def parse_meminfo(text: str) -> dict[str, int]:
    """/proc/meminfo -> {'MemTotal': bytes, 'MemAvailable': bytes, ...}."""
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
    """/proc/loadavg -> the 1, 5 and 15-minute load averages (processes running or waiting to run)."""
    one, five, fifteen = (float(value) for value in text.split()[:3])
    return one, five, fifteen


def parse_gpus(text: str) -> list[GPUInfo]:
    """nvidia-smi --query-gpu=name,utilization.gpu,memory.used,memory.total --format=csv,noheader,nounits
    -> [GPUInfo]. '[N/A]' values become None."""

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
    """One process from /proc/<pid>/status and /proc/<pid>/cmdline. None for kernel threads (no memory)."""
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


_AUTOSTOP_RE = re.compile(r"auto-?stop|stop[-_]notebook[-_]instance", re.IGNORECASE)
_IDLE_TIME_RE = re.compile(r"(?:IDLE_TIME\s*=\s*|--time[ =])[\"']?(\d+)")


def lifecycle_idle(scripts: Iterable[str]) -> tuple[bool, int | None]:
    """Whether a notebook instance's lifecycle scripts stop it when idle (the usual way is AWS's auto-stop-idle
    sample), and after how many idle minutes when the script says -> (stops_when_idle, minutes)."""
    text = "\n".join(scripts)
    if not _AUTOSTOP_RE.search(text):
        return False, None
    match = _IDLE_TIME_RE.search(text)
    return True, (int(match.group(1)) // 60 if match else None)


_VERSION_RE = re.compile(r"^(?:python|py)?\s*3\.?(\d{1,2})(?:\.(\d{1,3}))?$", re.IGNORECASE)
_ENV_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


def parse_python_version(value: Any) -> str:
    """A Python version as conda and uv take it: '3.11', 3.11, 'py311', 'python3.11' -> '3.11'; '3.11.9' stays
    '3.11.9'. The number 3.10 reads as 3.1, so a number 3.1 means 3.10."""
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
    """The name new_env() gives an environment: '3.11' -> 'py311', '3.12.4' -> 'py312'."""
    return "py" + "".join(version.split(".")[:2])


def new_env_call(python: str, name: str = "", **kwargs: Any) -> str:
    """A new_env() call to copy, leaving out name= when it's the default: ('3.11', 'py311') -> "new_env('3.11')",
    ('3.11', 'exp', packages='pandas') -> "new_env('3.11', name='exp', packages='pandas')"."""
    words = [repr(python)] + ([f"name={name!r}"] if name and name != env_name(python) else [])
    return f"new_env({', '.join(words + [f'{key}={value!r}' for key, value in kwargs.items()])})"


def _minor(version: str) -> str:
    """'3.11.9' -> '3.11' ('' -> '3.11': the version new_env() suggests when it can't tell)."""
    return ".".join(version.split(".")[:2]) if version else "3.11"


def kernel_label(version: str, name: str) -> str:
    """What Jupyter's kernel menu shows for it: ('3.11.9', 'py311') -> 'Python 3.11 (py311)'."""
    return f"Python {'.'.join(version.split('.')[:2])} ({name})"


def parse_pyvenv_cfg(text: str) -> dict[str, str]:
    """A virtual environment's pyvenv.cfg -> {'home': ..., 'version': '3.11.9', ...} (uv writes version_info)."""
    values = {}
    for line in text.splitlines():
        key, sep, value = line.partition("=")
        if sep:
            values[key.strip().lower()] = value.strip()
    return values


def _clean_version(text: str) -> str:
    """'3.11.9.final.0' -> '3.11.9'."""
    match = re.match(r"\d+\.\d+(?:\.\d+)?", text.strip())
    return match.group(0) if match else ""


# =============================================================================
# 2. Data models (what SageMakerAnalyzer returns)
# =============================================================================


@dataclass
class Environment:
    """Where this code runs, from SageMaker's metadata file (/opt/ml/metadata/resource-metadata.json)."""

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
        """'notebook instance nb', 'JupyterLab space analysis', 'Studio Classic kernel app datascience-...'."""
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
    """One SageMaker notebook: a notebook instance, or a Studio app (JupyterLab, Code Editor, Studio Classic) with
    its space and domain settings. Parts that couldn't be read are listed in `errors` (section -> error code)."""

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
        """Running (or starting / stopping), so its instance is billed by the hour."""
        return self.status in _BILLED

    @property
    def storage_price_key(self) -> str:
        return (
            "notebook_storage" if self.kind == "notebook instance" else "space_storage"
        )

    @property
    def label(self) -> str:
        """'notebook instance nb', 'JupyterLab space analysis', 'Studio Classic kernel app x (user alice)'."""
        if self.kind == "notebook instance":
            return f"notebook instance {self.name}"
        if self.space:
            return f"{app_label(self.app_type)} space {self.space}"
        user = f" (user {self.user_profile})" if self.user_profile else ""
        return f"{app_label(self.app_type)} app {self.name}{user}"


@dataclass
class GPUInfo:
    """One GPU, as nvidia-smi sees it right now."""

    name: str
    busy: float | None = None  # % of the last sample period a kernel ran on it
    memory_used: int | None = None  # bytes
    memory_total: int | None = None

    @property
    def idle(self) -> bool:
        """Nothing running on it and (almost) nothing loaded into its memory."""
        loaded = (
            (self.memory_used or 0) / self.memory_total if self.memory_total else 0.0
        )
        return (self.busy or 0) < 5 and loaded < 0.05


@dataclass
class Volume:
    """How full one disk is."""

    path: str
    label: str  # 'notebook volume', 'system disk', ...
    total: int
    used: int
    free: int
    note: str = ""  # what happens to it: 'kept when the instance stops', ...

    @property
    def title(self) -> str:
        """'notebook volume (kept when the instance stops)'."""
        return f"{self.label} ({self.note})" if self.note else self.label

    @property
    def share(self) -> float:
        return self.used / self.total if self.total else 0.0

    @property
    def elastic(self) -> bool:
        """A file system that grows as needed (EFS reports 8 EiB), so 'full' doesn't apply."""
        return self.total >= 2**50


@dataclass
class ProcessInfo:
    """One process on this machine and the memory it holds."""

    pid: int
    name: str
    command: str
    memory: int  # resident memory, bytes
    kernel: bool = False  # a Jupyter kernel (ipykernel)
    this: bool = False  # the kernel this code runs in


@dataclass
class Machine:
    """This machine right now, read locally: /proc, the disks and nvidia-smi. Parts it couldn't read are listed in
    `errors` (part -> reason)."""

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
        """The 15-minute load average as a share of the vCPUs."""
        return self.load[2] / self.cpus if self.load and self.cpus else None


@dataclass
class InstanceReport:
    """What instance() found: the notebook's settings from SageMaker, and, for the notebook this code runs in, the
    machine itself right now. Sections that couldn't be read are listed in `errors` (section -> error code)."""

    env: Environment
    notebook: NotebookInfo | None = None
    machine: Machine | None = None
    current: bool = True  # describes the notebook this code runs in
    account: str = ""
    identity: str = ""  # the ARN AWS sees for this code: in a notebook, its execution role's session
    errors: dict[str, str] = field(default_factory=dict)

    @property
    def role_name(self) -> str:
        """'AmazonSageMaker-ExecutionRole-2024...' from the notebook's role, or the signed-in session."""
        if self.notebook and self.notebook.role_arn:
            return _arn_name(self.notebook.role_arn)
        if ":assumed-role/" in self.identity:
            return self.identity.split(":assumed-role/", 1)[1].split("/")[0]
        return _arn_name(self.identity)


@dataclass
class DiskEntry:
    """Files under one folder (or one file): how many, how big, and when the newest changed."""

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
    """Caches or trash found while measuring: safe to empty, and the command that does it."""

    what: str  # 'Jupyter trash', 'pip cache', ...
    why: str  # what it holds and why emptying it is safe
    paths: list[str] = field(default_factory=list)
    size: int = 0
    files: int = 0
    command: str = ""  # shell command that empties it (never run by this tool)


@dataclass
class DiskReport:
    """What disk() measured under one folder."""

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
        """The folders measured as a pandas DataFrame: path, size in bytes, files, newest change."""
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
    """One thing SageMaker bills by the hour while it runs: a notebook instance, a Studio app, an endpoint or a
    training / processing job."""

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
        """USD per hour for all its instances; None when the price table lacks one of the types."""
        total = 0.0
        for instance_type, count in self.instances:
            price = hourly_price(instance_type, prices)
            if price is None:
                return None
            total += price * count
        return total

    @property
    def instance_label(self) -> str:
        """'ml.m5.xlarge', 'ml.g5.xlarge × 2', 'serverless'."""
        if self.serverless and not self.instances:
            return "serverless"
        return (
            " + ".join(t if n == 1 else f"{t} × {n}" for t, n in self.instances) or "?"
        )


@dataclass
class RunningReport:
    """What running() found: everything billing by the hour in one region, plus stopped notebook instances (their
    storage is still billed). Sections that couldn't be read are listed in `errors` (section -> error code)."""

    region: str
    resources: list[Billable] = field(default_factory=list)
    stopped: list[Billable] = field(default_factory=list)
    days: int = 7  # the window endpoint traffic was counted over
    errors: dict[str, str] = field(default_factory=dict)

    def to_df(self, prices: dict[str, float] | None = None):
        """Everything running (and the stopped notebook instances) as a pandas DataFrame, one row each."""
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
    """One Python environment on this machine: a conda environment, or a virtual environment (venv, uv)."""

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
    """One Jupyter kernel: what Jupyter's kernel menu shows, and the Python it starts."""

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
    """What environments() found: the Python environments and Jupyter kernels on this machine, the programs that
    make new environments, and where new_env() puts them. Parts it couldn't read are in `errors` (part -> reason)."""

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
        """The environments as a pandas DataFrame: name, Python version, kind, path, kernels, kept, made."""
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
    """One command new_env() runs, and once run, how it went."""

    label: str  # 'Make the environment', 'Install packages', 'Add the Jupyter kernel'
    argv: list[str]
    env: dict[str, str] = field(default_factory=dict)  # environment variables it needs set
    command: str = ""  # the same as you'd type it in a terminal
    seconds: float | None = None
    code: int | None = None  # its exit status, once run
    output: str = ""  # the end of what it printed


@dataclass
class EnvPlan:
    """What new_env() does: the environment it makes (or finds), and the commands that make it."""

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
    """What new_env() did: the plan, each step's exit status and output, and the Python the environment got."""

    plan: EnvPlan
    ok: bool = False
    python: str = ""  # the version it got: '3.11.10'
    seconds: float = 0.0
    failed: EnvStep | None = None
    leftover: bool = False  # a failed make left a folder that isn't an environment (new_env() won't reuse it)


# =============================================================================
# 3. Pure analysis (no AWS calls - works on the dicts AWS returns and the text /proc holds)
# =============================================================================


def parse_metadata(data: dict[str, Any]) -> Environment:
    """SageMaker's resource-metadata.json -> Environment. Notebook instances write ResourceArn and ResourceName;
    Studio apps also write DomainId, AppType, and SpaceName or UserProfileName."""
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


def parse_notebook_instance(desc: dict[str, Any]) -> NotebookInfo:
    """A DescribeNotebookInstance response (or a ListNotebookInstances summary) -> NotebookInfo."""
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
    """A DescribeApp response (or a ListApps item) -> NotebookInfo, without the space and domain settings
    (apply_studio_settings adds those)."""
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
    """Whether SageMaker shuts a Studio app down when it sits idle -> (state, minutes, where it's set).
    state is 'on', 'off', 'unknown' (the domain couldn't be read) or 'n/a' (a free app). The domain (or the
    user profile) turns idle shutdown on; the space, the user profile or the domain sets the timeout."""
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
    """Fill in a Studio app's settings from DescribeDomain, DescribeUserProfile and DescribeSpace responses:
    idle shutdown, role, network, storage volume, sharing and code repositories."""
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


def parse_endpoint(
    desc: dict[str, Any], config: dict[str, Any] | None = None
) -> Billable:
    """DescribeEndpoint (+ DescribeEndpointConfig for the instance types) -> Billable."""
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
    """DescribeTrainingJob -> Billable."""
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
    """DescribeProcessingJob -> Billable."""
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


def notebook_costs(
    nb: NotebookInfo, prices: dict[str, float] | None = None
) -> dict[str, float | None]:
    """Estimated USD for a notebook: 'hourly' (its instance), 'month_if_on' (running around the clock) and
    'storage' (its volume per month, billed whether it runs or not; None for Studio Classic's EFS home)."""
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
    """The cheapest type with at least `vcpus`, `memory_gib` and `gpus` (and no GPUs when gpus=0) that costs at least
    a quarter less than `instance_type`. allowed: the types this kind of notebook can use; burstable=False leaves out
    the t3 types, which slow down under steady load. None when nothing fits."""
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


_SECTIONS = {  # section -> (what it is, the permission that reads it)
    "identity": ("who you're signed in as", "sts:GetCallerIdentity"),
    "notebook": ("the notebook instance", "sagemaker:DescribeNotebookInstance"),
    "lifecycle": (
        "its lifecycle configuration",
        "sagemaker:DescribeNotebookInstanceLifecycleConfig",
    ),
    "app": ("the Studio app", "sagemaker:DescribeApp"),
    "space": ("the space", "sagemaker:DescribeSpace"),
    "profile": ("the user profile", "sagemaker:DescribeUserProfile"),
    "domain": ("the domain", "sagemaker:DescribeDomain"),
    "notebook instances": ("notebook instances", "sagemaker:ListNotebookInstances"),
    "notebook details": (
        "some notebook instances' settings",
        "sagemaker:DescribeNotebookInstance",
    ),
    "apps": ("Studio apps", "sagemaker:ListApps"),
    "endpoints": ("endpoints", "sagemaker:ListEndpoints"),
    "endpoint details": ("some endpoints' instances", "sagemaker:DescribeEndpoint"),
    "training jobs": ("training jobs", "sagemaker:ListTrainingJobs"),
    "training details": (
        "some training jobs' instances",
        "sagemaker:DescribeTrainingJob",
    ),
    "processing jobs": ("processing jobs", "sagemaker:ListProcessingJobs"),
    "processing details": (
        "some processing jobs' instances",
        "sagemaker:DescribeProcessingJob",
    ),
    "metrics": ("endpoint traffic", "cloudwatch:GetMetricData"),
}
FULL = 0.85  # a disk or memory this full gets a warning
LONG_RUNNING = (
    12 * 3600
)  # a notebook running this long with nothing to stop it looks forgotten


def _couldnt_read(
    errors: dict[str, str], consequences: dict[str, str] | None = None
) -> str:
    """ "Couldn't read the domain (AccessDeniedException; needs sagemaker:DescribeDomain), so ..."."""
    parts = [
        f"{_SECTIONS.get(k, (k, ''))[0]} ({_why(v, _SECTIONS[k][1]) if k in _SECTIONS else v})"
        for k, v in errors.items()
    ]
    after = [consequences[k] for k in errors if consequences and k in consequences]
    return (
        "Couldn't read "
        + ", ".join(parts)
        + (f", so {' and '.join(after)}" if after else "")
        + "."
    )


def _money_per_hour(hourly: float | None) -> str:
    """'$0.23/hour, up to $168/month' (or 'an unknown price per hour')."""
    if hourly is None:
        return "an unknown price per hour"
    return f"{human_money(hourly)}/hour, up to {human_money(hourly * HOURS_PER_MONTH)}/month"


def stop_command(b: Billable | NotebookInfo) -> str:
    """The AWS CLI command that stops a notebook instance or a Studio app, or deletes an endpoint."""
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
    """The commands that turn on idle shutdown for this notebook (shown, never run), or '' when there's no setting
    for it."""
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


def _full(share: float | None) -> bool:
    return share is not None and share >= FULL


def _other_kernels(machine: Machine) -> tuple[int, int]:
    """(count, bytes) of the Jupyter kernels other than this one."""
    others = machine.kernels - (1 if machine.this_memory is not None else 0)
    return max(others, 0), max(machine.kernels_memory - (machine.this_memory or 0), 0)


def instance_findings(
    report: InstanceReport,
    prices: dict[str, float] | None = None,
    allowed: Iterable[str] | None = None,
    now: datetime | None = None,
) -> list[tuple[str, str]]:
    """Plain-language risks and cost notes for a notebook (and, when it's this one, its machine), each with what to
    do about it -> [(level, message)]. allowed: the instance types this kind of notebook can switch to."""
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


# Caches and trash a disk() walk recognizes: (pattern on the folder's path, what, why it's safe, command).
# {path} is the folder, {root} the folder measured; the tool shows the command and never runs it.
_CLEARABLE = [
    (
        re.compile(r"/\.Trash-\d+$|/\.local/share/Trash$"),
        "Jupyter trash",
        "Files you deleted in Jupyter. They take up the disk until the trash is emptied.",
        "rm -rf {path}/*",
    ),
    (
        re.compile(r"/\.cache/pip$"),
        "pip cache",
        "Package downloads pip keeps to reinstall faster.",
        "pip cache purge",
    ),
    (
        re.compile(r"/\.conda/pkgs$|/(?:ana|mini)conda3?/pkgs$"),
        "conda package cache",
        "Package files conda keeps after installing them.",
        "conda clean --all --yes",
    ),
    (
        re.compile(r"/\.cache/huggingface$"),
        "Hugging Face cache",
        "Models and datasets transformers and datasets downloaded; they download again when needed.",
        "huggingface-cli delete-cache",
    ),
    (
        re.compile(r"/\.cache/torch$"),
        "PyTorch cache",
        "Pretrained weights torch.hub and torchvision downloaded; they download again when needed.",
        "rm -rf {path}",
    ),
    (
        re.compile(r"/\.ipynb_checkpoints$"),
        "notebook checkpoints",
        "Jupyter's autosaved copies of notebooks, big when notebooks hold large outputs.",
        "find {root} -name .ipynb_checkpoints -type d -prune -exec rm -rf {{}} +",
    ),
]


def clearable_kind(path: str) -> int | None:
    """Index into _CLEARABLE of the cache or trash folder at `path`, or None."""
    for i, (pattern, *_rest) in enumerate(_CLEARABLE):
        if pattern.search(path.replace(os.sep, "/")):
            return i
    return None


def shell_path(path: str, home: str | None = None) -> str:
    """A path ready for a shell command, under your home folder written as ~/...: '/home/ec2-user/SageMaker/x y'
    -> "~/SageMaker/'x y'"."""
    home = (home or os.path.expanduser("~")).rstrip("/")
    if home and path == home:
        return "~"
    if home and path.startswith(home + "/"):
        return "~/" + shlex.quote(path[len(home) + 1 :])
    return shlex.quote(path)


def _tilde(path: str, home: str | None = None) -> str:
    """A path for a call to copy, under your home folder written as ~/...: '/home/ec2-user/SageMaker/envs' ->
    '~/SageMaker/envs' (disk() and new_env() read ~)."""
    home = (home or os.path.expanduser("~")).rstrip("/")
    if home and (path == home or path.startswith(home + "/")):
        return "~" + path[len(home):]
    return path


def clear_command(item: Clearable, root: str, home: str | None = None) -> str:
    """The shell command that empties one kind of cache or trash (shown, never run)."""
    template = next((t for _p, what, _w, t in _CLEARABLE if what == item.what), "")
    where = shell_path(root, home)
    if "{path}" not in template:
        return template.format(root=where)
    return "; ".join(
        template.format(path=shell_path(p, home), root=where) for p in item.paths[:3]
    )


def folder_tree(
    report: DiskReport, *, top: int = 15, min_share: float = 0.1
) -> list[tuple[int, DiskEntry]]:
    """The folders worth showing, as (depth, entry) in tree order: the `top` biggest top-level folders, and inside
    any folder holding at least `min_share` of the total, its biggest few (5, then 3)."""
    total = report.total.size or 1
    children: dict[str, list[DiskEntry]] = {}
    for entry in report.folders.values():
        parent = entry.path.rsplit("/", 1)[0] if "/" in entry.path else ""
        children.setdefault(parent, []).append(entry)
    rows: list[tuple[int, DiskEntry]] = []

    def walk(parent: str, depth: int, limit: int) -> None:
        for entry in sorted(children.get(parent, []), key=lambda e: -e.size)[:limit]:
            rows.append((depth, entry))
            if depth < 2 and entry.size / total >= min_share:
                walk(entry.path, depth + 1, 5 if depth == 0 else 3)

    walk("", 0, top)
    return rows


def disk_findings(
    report: DiskReport,
    env: Environment | None = None,
    prices: dict[str, float] | None = None,
    now: datetime | None = None,
) -> list[tuple[str, str]]:
    """Plain-language notes about what fills a folder and its disk, each with what to do -> [(level, message)].
    env: where this runs, to show the right command for a bigger volume."""
    prices = SAGEMAKER_PRICES if prices is None else prices
    found: list[tuple[str, str]] = []
    volume = report.volume
    full = volume is not None and not volume.elastic and _full(volume.share)
    if full and volume is not None:  # full implies a volume; type checkers need it spelled out
        biggest = max(
            (e for e in report.folders.values() if "/" not in e.path),
            key=lambda e: e.size,
            default=None,
        )
        text = (
            f"The disk is {volume.share:.0%} full: {human_size(volume.free)} free of {human_size(volume.total)}. "
            "When it fills, saving notebooks and installing packages fail."
        )
        if biggest is not None and biggest.size:
            where = os.path.join(report.path, biggest.path)
            text += f" The biggest folder here is {biggest.path} ({human_size(biggest.size)}): disk({where!r})."
        size = max(
            math.ceil(volume.total / GB * 2 / 10) * 10, 10
        )  # twice the size, in whole tens of GB
        if env is not None and env.kind == "notebook instance" and env.name:
            extra = (size - volume.total / GB) * prices.get("notebook_storage", 0.0)
            text += (
                f" To make it {size} GB (about {human_money(extra)}/month more; it can't shrink later), stop the "
                f"notebook instance, then: aws sagemaker update-notebook-instance --notebook-instance-name "
                f"{env.name} --volume-size-in-gb {size}"
            )
        elif env is not None and env.kind == "studio" and env.space and env.domain_id:
            extra = (size - volume.total / GB) * prices.get("space_storage", 0.0)
            text += (
                f" To make it {size} GB (about {human_money(extra)}/month more, up to the domain's maximum), "
                f"stop the app, then: aws sagemaker update-space --domain-id {env.domain_id} --space-name "
                f"{env.space} --space-settings SpaceStorageSettings={{EbsStorageSettings={{EbsVolumeSizeInGb="
                f"{size}}}}}"
            )
        found.append(("warn", text))
    clearable = sum(c.size for c in report.clearable)
    if clearable >= 100 * MB:
        parts = ", ".join(
            f"{c.what} {human_size(c.size)}"
            for c in sorted(report.clearable, key=lambda c: -c.size)
        )
        found.append(
            (
                "warn" if full else "info",
                (
                    f"{human_size(clearable)} is caches and trash that are safe to empty ({parts}). The command for each "
                    "is in the table below."
                ),
            )
        )
    now = now or _utcnow()
    stale = [
        e
        for e in report.largest
        if e.size >= GB and e.modified and (now - e.modified).days >= 90
    ]
    if stale:
        size = sum(e.size for e in stale)
        on_disk = prices.get(
            (
                "notebook_storage"
                if env is not None and env.kind == "notebook instance"
                else "space_storage"
            ),
            0.0,
        )
        in_s3 = prices.get("s3_storage", 0.0)
        example = max(stale, key=lambda e: e.size)
        found.append(
            (
                "info",
                (
                    f"{_plural(len(stale), 'file')} over 1 GB {'hasn' if len(stale) == 1 else 'haven'}'t changed in 90 days "
                    f"({human_size(size)}; the biggest is {example.path}, {human_size(example.size)}). Moving them to S3 "
                    f"(aws s3 cp, then delete the copy here) frees {human_size(size)} on this disk, and S3 keeps them for "
                    f"about {human_money(size / GB * in_s3)}/month rather than {human_money(size / GB * on_disk)}/month "
                    "here."
                ),
            )
        )
    if report.truncated:
        found.append(
            (
                "info",
                (
                    f"Stopped after {report.total.files:,} files, so the sizes are at least these. "
                    f"disk({report.path!r}, limit=None) measures everything."
                ),
            )
        )
    return found


def _forgotten(b: Billable, now: datetime | None = None) -> bool:
    """A notebook or app (not this one) running for LONG_RUNNING or more with nothing to stop it when idle."""
    return (
        b.kind in ("notebook instance", "Studio app")
        and b.idle == "off"
        and not b.this
        and (_since(b.since, now) or 0) >= LONG_RUNNING
    )


def _unused_endpoint(b: Billable, now: datetime | None = None) -> bool:
    """An endpoint on instances, at least a day old, that got no requests in the window counted."""
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
    """Plain-language notes about what's running: what looks forgotten and what it costs, with the command that
    stops it -> [(level, message)]."""
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


TOOLS = ("mamba", "conda", "micromamba", "uv")  # what new_env() makes environments with, in the order it prefers
CHANNEL = "conda-forge"  # conda's defaults channel needs its Terms of Service accepted (and, at bigger companies, a license)
LOW_DISK = 3 * GB  # less free than this where an environment goes gets a warning
_SUGGESTED = ("3.11", "3.12", "3.13", "3.10")  # what envs() suggests making, the first one there isn't one of
KERNELS_FOLDER = ".local/share/jupyter/kernels"  # under your home folder: where `ipykernel install --user` puts a kernel
LIFECYCLE_NAME = "python-envs"  # the lifecycle configuration that adds a notebook instance's kernels back at each start
_ANACONDA = "/home/ec2-user/anaconda3"  # a notebook instance's conda: its environments are on the system disk
_CONDA_ROOTS = ("/opt/conda", _ANACONDA)  # Studio's SageMaker Distribution image, a notebook instance
_HOME_CONDA_ROOTS = ("miniforge3", "miniconda3", "anaconda3", "micromamba")  # under your home folder
_TOOL_FOLDERS = (*(f"{root}/{sub}" for root in _CONDA_ROOTS for sub in ("bin", "condabin")), "/usr/local/bin", "/usr/bin")
_HOME_TOOL_FOLDERS = (".local/bin", ".cargo/bin", *(f"{root}/bin" for root in _HOME_CONDA_ROOTS))
_JUPYTER_SERVERS = ("/opt/conda", f"{_ANACONDA}/envs/JupyterSystemEnv")  # the environment Jupyter itself runs in
_HISTORY_RE = re.compile(r"^==> (\d{4}-\d\d-\d\d \d\d:\d\d:\d\d) <==", re.MULTILINE)


def parse_conda_history(text: str) -> datetime | None:
    """When a conda environment was made: the first '==> 2025-10-14 09:12:33 <==' in its conda-meta/history,
    which conda writes in the machine's local time."""
    match = _HISTORY_RE.search(text)
    if not match:
        return None
    return datetime.strptime(match.group(1), "%Y-%m-%d %H:%M:%S").astimezone(timezone.utc)


def package_list(packages: Any) -> list[str]:
    """What to pip install: 'pandas boto3>=1.34', 'pandas, boto3', ['pandas'] or '-r requirements.txt' -> a list."""
    if packages is None:
        return []
    words = shlex.split(packages) if isinstance(packages, str) else [str(p) for p in packages]
    return [w.strip().rstrip(",") for w in words if w.strip().rstrip(",")]


def env_steps(
    tool: str,
    exe: str,
    prefix: str | Path,
    python: str,
    name: str,
    packages: Iterable[str] = (),
    *,
    exists: bool = False,
    ipykernel: bool = False,
    python_dir: str = "",
) -> list[EnvStep]:
    """The commands that make an environment at `prefix` with this Python (unless it `exists`), pip install
    `packages` in it, and add it to Jupyter as kernel `name` -> [EnvStep]. Nothing is run. tool: 'mamba', 'conda',
    'micromamba' (conda-forge only) or 'uv' (a virtual environment with pip in it; python_dir: where uv keeps the
    Pythons it downloads)."""
    python_exe = str(Path(prefix, "bin", "python"))
    install = list(packages)
    steps = []
    if not exists:
        if tool == "uv":
            steps.append(
                EnvStep(
                    "Make the environment",
                    [exe, "venv", "--seed", "--python", python, str(prefix)],
                    {"UV_PYTHON_INSTALL_DIR": python_dir} if python_dir else {},
                )
            )
            install = ["ipykernel", *install]
        else:
            steps.append(
                EnvStep(
                    "Make the environment",
                    [exe, "create", "--yes", "--quiet", "--prefix", str(prefix), "--override-channels",
                     "--channel", CHANNEL, f"python={python}", "ipykernel"],
                )
            )
    elif not ipykernel:
        install = ["ipykernel", *install]
    if install:
        steps.append(
            EnvStep(
                "Install packages" if packages else "Install ipykernel",
                [exe, "pip", "install", "--python", python_exe, *install]
                if tool == "uv"
                else [python_exe, "-m", "pip", "install", "--disable-pip-version-check", *install],
            )
        )
    steps.append(
        EnvStep(
            "Add the Jupyter kernel",
            [python_exe, "-m", "ipykernel", "install", "--user", "--name", name,
             "--display-name", kernel_label(python, name)],
        )
    )
    return steps


def step_command(step: EnvStep, shown: Callable[[str], str] | None = None, home: str | None = None) -> str:
    """A step as you'd type it in a terminal, with your home folder as ~: 'conda create --yes ... --prefix
    ~/SageMaker/envs/py311 ...'. shown: turns a path into the one this machine sees (tests use a fake root)."""
    shown = shown or (lambda text: text)

    def word(text: str) -> str:
        text = shown(text)
        return shell_path(text, home) if text.startswith("/") else shlex.quote(text)

    variables = [f"{key}={word(value)}" for key, value in step.env.items()]
    return " ".join(variables + [word(arg) for arg in step.argv])


def output_tail(text: str, lines: int = 30) -> str:
    """The end of what a command printed: its last `lines` lines, each as it was left after progress updates."""
    kept = [line.rstrip("\r").rsplit("\r", 1)[-1].rstrip() for line in text.split("\n")]
    kept = [line for line in kept if line]
    return "\n".join(kept[-lines:])[-4000:]


def on_start_script(folder: str) -> str:
    """A notebook instance's on-start lifecycle script that adds the Jupyter kernel of each environment in `folder`
    back at every start: the environments are on the notebook volume, which is kept, but kernels are in ~/.local,
    which is reset when the instance stops."""
    return "\n".join(
        [
            "#!/bin/bash",
            f"# Adds back the Jupyter kernel of each environment in {folder} at every start.",
            "sudo -u ec2-user -i <<'EOF'",
            f"for env in {shlex.quote(folder)}/*/; do",
            '    env="${env%/}"; name="$(basename "$env")"',
            '    "$env/bin/python" -c "import ipykernel" 2>/dev/null || continue',
            "    version=\"$(\"$env/bin/python\" -c 'import sys; print(\"%d.%d\" % sys.version_info[:2])')\"",
            '    "$env/bin/python" -m ipykernel install --user --name "$name" --display-name "Python $version ($name)"',
            "done",
            "EOF",
        ]
    )


def kernels_lifecycle(folder: str, notebook: str = "") -> str:
    """The commands that save on_start_script() and attach it to notebook instance `notebook` as a lifecycle
    configuration, so its kernels come back by themselves after a restart (shown, never run)."""
    lines = [
        "# 1. In a terminal here (File > New > Terminal), save the script:",
        "cat > on-start.sh <<'SCRIPT'",
        on_start_script(folder),
        "SCRIPT",
        "# 2. Make it a lifecycle configuration. A notebook instance has one: if it has one already (auto-stop,",
        "#    say), add the script's lines to that one's on-start script instead.",
        "aws sagemaker create-notebook-instance-lifecycle-config \\",
        f"    --notebook-instance-lifecycle-config-name {LIFECYCLE_NAME} \\",
        '    --on-start Content="$(base64 -w0 on-start.sh)"',
    ]
    if notebook:
        lines += [
            "# 3. Attach it while the notebook instance is stopped. Stopping it ends this terminal, so run these",
            "#    from AWS CloudShell or your laptop:",
            f"aws sagemaker stop-notebook-instance --notebook-instance-name {notebook}",
            f"aws sagemaker wait notebook-instance-stopped --notebook-instance-name {notebook}",
            f"aws sagemaker update-notebook-instance --notebook-instance-name {notebook} "
            f"--lifecycle-config-name {LIFECYCLE_NAME}",
            f"aws sagemaker start-notebook-instance --notebook-instance-name {notebook}",
        ]
    return "\n".join(lines)


def _disks(kind: str) -> tuple[str, str, str]:
    """(the disk that's kept, the one that's reset, when it's reset) for where this runs."""
    if kind == "notebook instance":
        return "notebook volume", "system disk", "the notebook instance stops"
    if kind == "studio":
        return "home folder", "app disk", "the app stops"
    return "", "", ""


def _same_python(found: str, wanted: str) -> bool:
    """Does the Python an environment has ('3.11.9') match the one asked for ('3.11')?"""
    return found.split(".")[: len(wanted.split("."))] == wanted.split(".")


def plan_findings(plan: EnvPlan, home: str | None = None) -> list[tuple[str, str]]:
    """What to know before new_env() runs a plan: an environment that's there already, a disk too full, a folder
    that's reset when the notebook stops, a kernel that is -> [(level, message)]."""
    found: list[tuple[str, str]] = []
    _, reset_disk, event = _disks(plan.where)
    where = shell_path(plan.path, home)
    if plan.exists:
        rest = "its packages are installed and its" if plan.packages else "its"
        found.append(
            (
                "info",
                f"{where} is there already with Python {plan.found or plan.python}, so it isn't made again: only "
                f"{rest} Jupyter kernel is added.",
            )
        )
    if plan.free is not None and plan.free < LOW_DISK:
        found.append(
            (
                "warn",
                f"Only {human_size(plan.free)} is free where it goes, and an environment takes from about 300 MB to "
                "several GB (PyTorch alone is about 4 GB). When the disk fills, the install fails. disk() shows what "
                "fills it and what's safe to clear.",
            )
        )
    if plan.kept is False and event:
        found.append(
            (
                "warn",
                f"{where} is on the {reset_disk}, which is reset when {event}: the environment goes with it. Leave "
                f"out path= to put it in {shell_path(plan.folder, home)}, which is kept.",
            )
        )
    if plan.kept and plan.kernel_kept is False:
        again = new_env_call(plan.python, plan.name)
        found.append(
            (
                "info",
                f"Its kernel goes in ~/{KERNELS_FOLDER}, which is reset when {event}, while the environment is kept. "
                f"After a restart, {again} adds the kernel back in seconds, or the lifecycle configuration below "
                "does it at every start.",
            )
        )
    return found


def lost_envs(report: EnvReport) -> list[PythonEnv]:
    """The environments made since this machine started on the disk that's reset when the notebook stops: they
    go then. (The image's own come back; a conda installation's base isn't made, only changed.)"""
    return [
        e
        for e in report.envs
        if e.kept is False and e.name != "base" and e.made and report.booted and e.made > report.booted
    ]


def env_findings(report: EnvReport, home: str | None = None) -> list[tuple[str, str]]:
    """What to know about the Python environments and kernels here: environments that go when the notebook stops,
    kernels that fail to start, environments missing from the kernel menu -> [(level, message)]."""
    found: list[tuple[str, str]] = []
    _, reset_disk, event = _disks(report.kind)
    folder = shell_path(report.folder, home) if report.folder else ""
    lost = lost_envs(report)
    if lost and event:
        e = lost[0]
        names = ", ".join(x.name for x in lost[:5]) + (" …" if len(lost) > 5 else "")
        again = new_env_call(_minor(e.python), e.name)
        found.append(
            (
                "warn",
                f"{_plural(len(lost), 'environment')} made since this machine started "
                f"{'is' if len(lost) == 1 else 'are'} on the {reset_disk}, which is reset when {event} ({names}): "
                f"{'it goes' if len(lost) == 1 else 'they go'}, with {'its' if len(lost) == 1 else 'their'} packages "
                f"and kernels. {again} makes {e.name} again in {folder}, which is kept; then install its packages "
                "there.",
            )
        )
    broken = [k for k in report.kernels if k.missing]
    for k in broken[:3]:
        found.append(
            (
                "warn",
                f"Kernel '{k.display}' starts {shell_path(k.program, home)}, which isn't there any more: picking it "
                f"in Jupyter fails. To take it off the kernel menu: rm -rf {shell_path(k.path, home)}",
            )
        )
    if len(broken) > 3:
        found.append(("warn", f"{_plural(len(broken) - 3, 'more kernel')} start a Python that isn't there any more."))
    unregistered = [
        e for e in report.envs if not e.kernels and report.folder and Path(e.path).parent == Path(report.folder)
    ]
    if unregistered:
        e = unregistered[0]
        names = ", ".join(x.name for x in unregistered[:5]) + (" …" if len(unregistered) > 5 else "")
        again = new_env_call(_minor(e.python), e.name)
        why = (
            f" Kernels are in ~/.local, which is reset when {event}, while the environments are kept."
            if report.kind == "notebook instance"
            else ""
        )
        found.append(
            (
                "info",
                f"{names} in {folder} {'has' if len(unregistered) == 1 else 'have'} no Jupyter kernel, so "
                f"{'it isn' if len(unregistered) == 1 else 'they aren'}'t in the kernel menu.{why} {again} adds "
                f"{e.name}'s back in seconds"
                + (", and the lifecycle configuration below adds them all at every start." if why else "."),
            )
        )
    if not report.tools:
        found.append(
            (
                "info",
                "There's no conda, mamba, micromamba or uv here, so new_env() can't make an environment. "
                "%pip install uv adds uv; new_env('3.11') then uses it.",
            )
        )
    if report.errors:
        found.append(
            ("info", "Couldn't read " + ", ".join(f"{part} ({why})" for part, why in report.errors.items()) + ".")
        )
    return found


_FAILURES = [  # (what the output says, what it means and what to do); {python} is the version asked for
    (
        re.compile(r"No space left on device", re.IGNORECASE),
        "The disk filled up. disk() shows what fills it and what's safe to clear.",
    ),
    (
        re.compile(r"No matching distribution found for (\S+)", re.IGNORECASE),
        "pip can't find {match}: check its name and version on pypi.org.",
    ),
    (
        re.compile(
            r"PackagesNotFound|nothing provides (?:requested )?python|No interpreter found|No download found|"
            r"not available from current channels",
            re.IGNORECASE,
        ),
        "Python {python} isn't there to download: ask for a release that exists, like '3.12' or '3.11'.",
    ),
    (
        re.compile(
            r"resolve host|Name or service not known|name resolution|Failed to establish|Connection (?:refused|reset)|"
            r"timed? ?out|CondaHTTPError|Failed to download|error sending request|dns error|ProxyError|"
            r"Network is unreachable",
            re.IGNORECASE,
        ),
        "It couldn't download what it needs. A notebook without direct internet access needs a NAT gateway or a "
        "package mirror such as AWS CodeArtifact; instance() shows its internet access.",
    ),
]


def env_failure(step: EnvStep, python: str = "") -> str:
    """What a failed step's output means and what to do about it."""
    if step.code == 127:
        return f"{step.argv[0]} couldn't be run: it isn't there any more. envs() shows what's installed."
    if step.code == -1:
        return (
            "It took too long and was stopped. Run the commands below in a terminal (File > New > Terminal) to see "
            "where it's stuck."
        )
    for pattern, meaning in _FAILURES:
        match = pattern.search(step.output)
        if match:
            return meaning.format(python=python, match=match.group(1) if match.groups() else "")
    return (
        "The end of what it printed is below. The commands are below too, to run in a terminal (File > New > "
        "Terminal) and see all of it."
    )


# =============================================================================
# 4. SageMakerAnalyzer - pure logic layer (reads this machine, talks to AWS, returns data)
# =============================================================================


def _missing(exc: ClientError) -> bool:
    """A 'no such notebook' error (DescribeNotebookInstance answers ValidationException: RecordNotFound)."""
    message = str(exc.response.get("Error", {}).get("Message", "")).lower()
    return _error_code(exc) in ("ResourceNotFound", "ValidationException") and any(
        words in message
        for words in ("recordnotfound", "not found", "does not exist", "could not find")
    )


class SageMakerAnalyzer(_BaseAnalyzer):
    """Pure-logic SageMaker analysis: every method returns data; nothing is printed, stopped or deleted. The one
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
        """A boto3 client, made on first use. Without a region set, the notebook's own region is used (from its
        metadata), so a missing region only matters outside SageMaker."""
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
        """The sagemaker client."""
        return self._service("sagemaker")

    def allowed_types(self, kind: str) -> set[str]:
        """The instance types a notebook instance ('notebook instance') or a Studio app ('studio app') can use,
        from botocore's service model."""
        model = self.client.meta.service_model
        shape = (
            model.operation_model("CreateNotebookInstance").input_shape.members[
                "InstanceType"
            ]
            if kind == "notebook instance"
            else model.shape_for("AppInstanceType")
        )
        return set(shape.enum or []) - {"system"}

    # ------------------------------------------------------------ this machine

    def _path(self, relative: str) -> Path:
        return Path(self.root, relative)

    def _shown(self, path: str | Path) -> str:
        """A path as this machine sees it: under a fake root (tests, demos), without the root's prefix."""
        text = str(path)
        if self.root in ("", "/"):
            return text
        root = str(Path(self.root))
        if text == root:
            return "/"
        return text[len(root) :] if text.startswith(root + os.sep) else text

    def _user_home(self) -> str:
        """Your home folder (~) as this machine sees it."""
        if self.root in ("", "/"):
            return os.path.expanduser("~")
        home = self._shown(self.home())
        return home[: -len("/SageMaker")] if home.endswith("/SageMaker") else home

    def _real(self, path: str | Path) -> Path:
        """A path you give (~ is your home folder) -> the folder to read, under the fake root when there is one."""
        text = str(path)
        if text == "~" or text.startswith("~/"):
            text = self._user_home() + text[1:]
        real = Path(text)
        root = Path(self.root)
        if (
            self.root not in ("", "/")
            and real.is_absolute()
            and real != root
            and root not in real.parents
        ):
            real = root / text.lstrip("/")
        return real

    def environment(self, *, refresh: bool = False) -> Environment:
        """Where this code runs, from SageMaker's metadata file. kind='local' when there isn't one."""
        if self._env is None or refresh:
            path = self._path(METADATA_FILE)
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                self._env = parse_metadata(data if isinstance(data, dict) else {})
            except FileNotFoundError:
                self._env = Environment()
            except (OSError, ValueError) as exc:
                self._env = Environment(
                    error=f"{path} couldn't be read ({type(exc).__name__}: {exc})"
                )
        return self._env

    def home(self) -> Path:
        """The folder your notebooks and data live in: a notebook instance's volume (/home/ec2-user/SageMaker), a
        Studio space's home (/home/sagemaker-user), otherwise your home folder."""
        for relative in _HOMES:
            path = self._path(relative)
            if path.is_dir():
                return path
        return Path.home() if self.root == "/" else self._path("")

    @staticmethod
    def _volume_labels(env: Environment) -> tuple[tuple[str, str], tuple[str, str]]:
        """(label, note) for the home disk and the system disk, saying what survives a stop."""
        if env.kind == "notebook instance":
            return ("notebook volume", "kept when the instance stops"), (
                "system disk",
                "reset when the instance stops",
            )
        if env.kind == "studio" and env.space:
            return ("space volume", "kept when the app stops"), (
                "app disk",
                "reset when the app stops",
            )
        if env.kind == "studio":
            return ("home folder", "EFS, shared by your apps"), (
                "app disk",
                "reset when the app stops",
            )
        return ("home folder", ""), ("system disk", "")

    @staticmethod
    def _cpu_count() -> int | None:
        try:
            return len(
                os.sched_getaffinity(0)
            )  # the CPUs this process may use (a container's, not the host's)
        except (AttributeError, OSError):
            return os.cpu_count()

    @staticmethod
    def _disk_usage(path: str) -> tuple[int, int, int]:
        usage = shutil.disk_usage(path)
        return usage.total, usage.used, usage.free

    def volume(self, path: str | Path, label: str = "disk", note: str = "") -> Volume:
        """How full the disk holding `path` is."""
        total, used, free = self._disk_usage(str(path))
        return Volume(self._shown(path), label, total, used, free, note)

    def volumes(self) -> list[Volume]:
        """The disks that matter here: where notebooks live, and the system disk (once each)."""
        home_label, system_label = self._volume_labels(self.environment())
        seen: set[int] = set()
        found = []
        for path, (label, note) in (
            (self.home(), home_label),
            (self._path(""), system_label),
        ):
            try:
                device = os.stat(path).st_dev
                if device in seen:
                    continue
                seen.add(device)
                found.append(self.volume(path, label, note))
            except OSError:
                continue
        return found

    def _gpu_query(self) -> str | None:
        """nvidia-smi's CSV line per GPU, or None when there's no NVIDIA driver."""
        exe = shutil.which("nvidia-smi")
        if not exe:
            return None
        result = subprocess.run(
            [
                exe,
                "--query-gpu=name,utilization.gpu,memory.used,memory.total",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            raise OSError(
                (result.stderr or result.stdout).strip()[:200]
                or f"nvidia-smi exited {result.returncode}"
            )
        return result.stdout

    def _processes(self) -> list[ProcessInfo]:
        processes = []
        with os.scandir(self._path("proc")) as entries:
            for entry in entries:
                if not entry.name.isdigit():
                    continue
                try:
                    status = Path(entry.path, "status").read_text(
                        encoding="utf-8", errors="replace"
                    )
                    cmdline = Path(entry.path, "cmdline").read_bytes()
                except OSError:  # it exited, or isn't ours to read
                    continue
                process = parse_process(int(entry.name), status, cmdline)
                if process is not None:
                    process.this = process.pid == self.pid
                    processes.append(process)
        return processes

    def machine(self, *, top: int = 8) -> Machine:
        """This machine right now: vCPUs and load, memory, disks, GPUs, the biggest processes (and how much memory
        the Jupyter kernels hold), Python and the common data packages. Reads local files only."""
        m = Machine(
            cpus=self._cpu_count(),
            python=platform.python_version(),
            executable=sys.executable,
        )

        def read(part: str, fn: Callable[[], Any]) -> Any:
            try:
                return fn()
            except (OSError, ValueError, IndexError, subprocess.SubprocessError) as exc:
                m.errors[part] = str(exc).strip() or type(exc).__name__
            return None

        if (
            text := read("load", lambda: self._path("proc/loadavg").read_text())
        ) is not None:
            m.load = read("load", lambda: parse_loadavg(text))
        if (
            text := read("memory", lambda: self._path("proc/meminfo").read_text())
        ) is not None:
            info = parse_meminfo(text)
            m.memory_total, m.memory_available = (
                info.get("MemTotal"),
                info.get("MemAvailable"),
            )
        if (
            text := read("uptime", lambda: self._path("proc/uptime").read_text())
        ) is not None:
            seconds = read("uptime", lambda: float(text.split()[0]))
            m.booted = (
                None if seconds is None else _utcnow() - timedelta(seconds=seconds)
            )
        m.volumes = read("disks", self.volumes) or []
        if (text := read("GPUs", self._gpu_query)) is not None:
            m.gpus = parse_gpus(text)
        processes = read("processes", self._processes) or []
        kernels = [p for p in processes if p.kernel]
        m.kernels, m.kernels_memory = len(kernels), sum(p.memory for p in kernels)
        this = next((p for p in processes if p.this), None)
        m.this_memory = this.memory if this else None
        m.processes = sorted(processes, key=lambda p: -p.memory)[:top]
        if this is not None and this not in m.processes:
            m.processes.append(this)
        for name in _PACKAGES:
            try:
                m.packages[name] = importlib.metadata.version(name)
            except importlib.metadata.PackageNotFoundError:
                continue
        return m

    # --------------------------------------------------------------- notebooks

    def identity(self) -> dict[str, str]:
        """{'Account': ..., 'Arn': ...}: who AWS sees (in a notebook, its execution role). Needs no permission."""
        resp = self._service("sts").get_caller_identity()
        return {"Account": resp.get("Account", ""), "Arn": resp.get("Arn", "")}

    def _lifecycle_idle(self, name: str) -> tuple[bool, int | None]:
        """Does lifecycle configuration `name` stop the notebook when idle? (cached)"""
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
        """A notebook instance's settings, and (lifecycle=True) whether its lifecycle configuration stops it when
        idle. Raises ClientError when there's no such notebook instance."""
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
        """Add the domain's, the user profile's and the space's settings; unreadable ones go in nb.errors."""

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
        """A Studio app with its space's, user profile's and domain's settings. Raises ClientError when the app
        can't be described."""
        owner = {"SpaceName": space} if space else {"UserProfileName": user_profile}
        desc = self.client.describe_app(
            DomainId=domain_id, AppType=app_type, AppName=app_name, **owner
        )
        return self._with_studio_settings(parse_app(desc))

    def _space_app(
        self, domain_id: str, space: str, summary: dict[str, Any] | None = None
    ) -> NotebookInfo:
        """The app running in a space (or, when none is, the space with the app it would start)."""
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
        """A notebook by name: a notebook instance, a Studio space ('analysis', or 'd-abc123/analysis' when spaces
        in two domains share the name), or an app or notebook instance ARN."""
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
        """The notebook this code runs in, with its settings (None outside SageMaker). What can't be read is in
        its errors, so this doesn't raise for a missing permission."""
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
        """The notebook this code runs in (or the one named: see notebook()): its settings and, for this one and
        machine=True, the machine right now."""
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

    # -------------------------------------------------------------------- disk

    def disk(
        self,
        path: str | Path | None = None,
        *,
        top: int = 20,
        limit: int | None = 200_000,
        progress: Callable[[int], None] | None = None,
    ) -> DiskReport:
        """Sizes under a folder (default: home()): per folder up to 3 levels deep, the `top` biggest files, and the
        caches and trash in it. Stays on the folder's disk and doesn't follow links. Stops after `limit` files
        (None: no limit); progress is called with the running count of files."""
        started = time.monotonic()
        root = self._real(path) if path else self.home()
        if not root.exists():
            raise ValueError(
                f"There's no folder {self._shown(root)!r} here. disk() with no path measures "
                f"{self._shown(self.home())}."
            )
        if not root.is_dir():
            raise ValueError(
                f"{self._shown(root)!r} is a file ({human_size(root.stat().st_size)}). disk() measures "
                f"a folder: disk({self._shown(root.parent)!r})."
            )
        env = self.environment()
        home = self.home()
        on_home = (
            root.resolve() == home.resolve() or home.resolve() in root.resolve().parents
        )
        label, note = self._volume_labels(env)[0] if on_home else ("disk", "")
        report = DiskReport(self._shown(root))
        try:
            report.volume = self.volume(root, label, note)
        except OSError:
            pass
        device = root.stat().st_dev
        found: dict[int, Clearable] = {}
        largest: list[tuple[int, str, float]] = []  # min-heap of (size, path, mtime)
        stack: list[tuple[str, tuple[str, ...], int | None]] = [(str(root), (), None)]
        count = 0
        while stack and not report.truncated:
            folder, parts, tag = stack.pop()
            try:
                with os.scandir(folder) as scan:
                    entries = list(scan)
            except OSError:
                report.skipped += 1
                continue
            for entry in entries:
                try:
                    if entry.is_symlink():
                        continue
                    info = entry.stat(follow_symlinks=False)
                except OSError:
                    report.skipped += 1
                    continue
                if stat.S_ISDIR(info.st_mode):
                    if info.st_dev != device:
                        report.other_disks.append(self._shown(entry.path))
                        continue
                    kind = tag if tag is not None else clearable_kind(entry.path)
                    if kind is not None and kind not in found:
                        _pattern, what, why, _command = _CLEARABLE[kind]
                        found[kind] = Clearable(what, why)
                    if kind is not None and tag is None:
                        found[kind].paths.append(self._shown(entry.path))
                    stack.append((entry.path, parts + (entry.name,), kind))
                    continue
                if not stat.S_ISREG(info.st_mode):
                    continue
                size, modified = (
                    info.st_size,
                    datetime.fromtimestamp(info.st_mtime, timezone.utc),
                )
                count += 1
                report.total.add(size, modified)
                for depth in range(1, min(len(parts), 3) + 1):
                    key = "/".join(parts[:depth])
                    report.folders.setdefault(key, DiskEntry(key)).add(size, modified)
                if tag is not None:
                    found[tag].size += size
                    found[tag].files += 1
                relative = "/".join(parts + (entry.name,))
                if len(largest) < top:
                    heapq.heappush(largest, (size, relative, info.st_mtime))
                elif size > largest[0][0]:
                    heapq.heapreplace(largest, (size, relative, info.st_mtime))
                if progress and count % 1000 == 0:
                    progress(count)
                if limit is not None and count >= limit:
                    report.truncated = True
                    break
        report.largest = [
            DiskEntry(p, s, 1, datetime.fromtimestamp(t, timezone.utc))
            for s, p, t in sorted(largest, reverse=True)
        ]
        report.clearable = sorted(
            (c for c in found.values() if c.size), key=lambda c: -c.size
        )
        for item in report.clearable:
            item.command = clear_command(item, report.path, self._user_home())
        report.seconds = time.monotonic() - started
        return report

    # ----------------------------------------------------- Python environments

    def tools(self) -> dict[str, str]:
        """The programs here that make Python environments, in the order new_env() prefers them: mamba, conda,
        micromamba, uv -> the path of each one installed."""
        home = self._user_home()
        folders = [*_TOOL_FOLDERS, *(f"{home}/{folder}" for folder in _HOME_TOOL_FOLDERS)]
        found: dict[str, str] = {}
        for name in TOOLS:
            candidates: list[str | None] = []
            if self.root in ("", "/"):
                candidates += [
                    shutil.which(name),
                    os.environ.get("CONDA_EXE") if name == "conda" else None,
                    str(Path(sys.executable).parent / name),  # %pip install uv puts it next to this kernel's Python
                ]
            candidates += [str(self._real(f"{folder}/{name}")) for folder in folders]
            path = next((c for c in candidates if c and os.path.isfile(c) and os.access(c, os.X_OK)), None)
            if path:
                found[name] = path
        return found

    def envs_folder(self) -> Path:
        """Where new_env() puts environments: ~/SageMaker/envs on a notebook instance (the notebook volume is the
        only disk it keeps when it stops), ~/.conda/envs elsewhere (a Studio home folder is kept)."""
        if self.environment().kind == "notebook instance":
            return self.home() / "envs"
        return self._real("~/.conda/envs")

    def _kept(self, path: str | Path) -> bool | None:
        """Is `path` (a real path) on the disk that's kept when the notebook stops? None off SageMaker."""
        if self.environment().kind not in ("notebook instance", "studio"):
            return None
        home, path = Path(os.path.normpath(self.home())), Path(os.path.normpath(path))
        return path == home or home in path.parents

    def _booted(self) -> datetime | None:
        """When this machine started, from /proc/uptime."""
        try:
            seconds = float(self._path("proc/uptime").read_text().split()[0])
        except (OSError, ValueError, IndexError):
            return None
        return _utcnow() - timedelta(seconds=seconds)

    @staticmethod
    def _env_python(prefix: Path) -> str:
        """The Python version of the environment at `prefix`: '3.11.9', or '' when it can't tell. Reads its files
        (conda-meta, pyvenv.cfg, lib/python3.x), never runs it."""
        for meta in sorted((prefix / "conda-meta").glob("python-[0-9]*.json")):
            version = _clean_version(meta.name[len("python-"):])
            if version:
                return version
        try:
            values = parse_pyvenv_cfg((prefix / "pyvenv.cfg").read_text(encoding="utf-8", errors="replace"))
            version = _clean_version(values.get("version_info") or values.get("version") or "")
            if version:
                return version
        except OSError:
            pass
        libs = [p.name[len("python"):] for p in prefix.glob("lib/python3.*")]
        return max(libs, key=lambda v: tuple(int(n) for n in v.split(".") if n.isdigit()), default="")

    @staticmethod
    def _env_made(prefix: Path) -> datetime | None:
        """When the environment at `prefix` was made: conda-meta/history's first entry, else when its pyvenv.cfg was
        written."""
        history = prefix / "conda-meta" / "history"
        try:
            with open(history, encoding="utf-8", errors="replace") as f:
                made = parse_conda_history(f.read(4096))
            if made is not None:
                return made
        except OSError:
            pass
        for path in (history, prefix / "pyvenv.cfg"):
            try:
                return datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
            except OSError:
                continue
        return None

    def _env_at(self, prefix: Path, name: str = "") -> PythonEnv | None:
        """The environment at `prefix` (a real path), or None when it isn't one: no conda-meta or pyvenv.cfg, or no
        bin/python (a link to a Python that's gone still counts)."""
        conda = (prefix / "conda-meta").is_dir()
        if not (conda or (prefix / "pyvenv.cfg").is_file()) or not os.path.lexists(prefix / "bin" / "python"):
            return None
        return PythonEnv(
            name or prefix.name,
            self._shown(prefix),
            self._env_python(prefix),
            "conda" if conda else "venv",
            ipykernel=any(prefix.glob("lib/python3*/site-packages/ipykernel")),
            kept=self._kept(prefix),
            made=self._env_made(prefix),
        )

    def _env_prefixes(self, tools: dict[str, str], folder: Path) -> dict[str, str]:
        """Where environments may be: each conda installation and its envs/, ~/.conda/envs, new_env()'s folder, what
        conda noted in ~/.conda/environments.txt, and this kernel's -> {real path: name}."""
        found: dict[str, str] = {}

        def add(path: Path, name: str = "") -> None:
            found.setdefault(os.path.normpath(path), name)

        def children(path: Path) -> None:
            try:
                for child in sorted(path.iterdir()):
                    if child.is_dir() and not child.name.startswith("."):
                        add(child)
            except OSError:
                pass

        roots = [Path(tools[t]).parent.parent for t in ("mamba", "conda") if t in tools]
        roots += [self._real(r) for r in (*_CONDA_ROOTS, *(f"~/{r}" for r in _HOME_CONDA_ROOTS))]
        for root in roots:
            if root.is_dir():
                add(root, "base")
                children(root / "envs")
        for path in (folder, self._real("~/.conda/envs")):
            children(path)
        try:
            noted = self._real("~/.conda/environments.txt").read_text(encoding="utf-8", errors="replace")
            for line in noted.splitlines():
                if line.strip().startswith("/"):
                    add(self._real(line.strip()))
        except OSError:
            pass
        if self.root in ("", "/"):
            add(Path(sys.prefix))
        return found

    def _kernel_folders(self) -> list[Path]:
        """The folders Jupyter finds kernels in: yours (~/.local/share/jupyter/kernels), JUPYTER_PATH's, the Jupyter
        server's environment's, and the system's."""
        folders = []
        if self.root in ("", "/"):
            data = os.environ.get("JUPYTER_DATA_DIR")
            folders += [Path(data, "kernels")] if data else []
            folders += [Path(p, "kernels") for p in os.environ.get("JUPYTER_PATH", "").split(os.pathsep) if p]
        folders.append(self._real(f"~/{KERNELS_FOLDER}"))
        servers = [self._real(p) for p in _JUPYTER_SERVERS]
        if self.environment().kind == "local" and self.root in ("", "/"):
            servers.append(Path(sys.prefix))  # off SageMaker, Jupyter usually runs from this kernel's environment
        folders += [p / "share/jupyter/kernels" for p in servers]
        folders += [self._real(p) for p in ("/usr/local/share/jupyter/kernels", "/usr/share/jupyter/kernels")]
        return folders

    def _kernel_at(self, spec: Path, envs: dict[str, PythonEnv]) -> KernelSpec | None:
        """The kernel described by `spec` (a kernel.json), or None when it can't be read."""
        try:
            data = json.loads(spec.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        argv = data.get("argv") if isinstance(data, dict) else None
        program = str(argv[0]) if isinstance(argv, list) and argv else ""
        kernel = KernelSpec(
            spec.parent.name,
            str(data.get("display_name") or spec.parent.name),
            self._shown(spec.parent),
            program,
            str(data.get("language") or ""),
            kept=self._kept(spec.parent),
        )
        if program.startswith("/"):
            real = self._real(program)
            kernel.missing = not real.exists()
            env = envs.get(os.path.normpath(real.parent.parent))
        else:  # 'python': the Python of the environment the kernel's folder is in
            env = envs.get(os.path.normpath(spec.parents[4])) if len(spec.parents) > 4 else None
        if env is not None:
            kernel.env = env.path
            env.kernels.append(kernel.display)
        return kernel

    def environments(self) -> EnvReport:
        """The Python environments on this machine (conda environments and virtual environments) with their Python
        versions, the Jupyter kernels and the environment each starts, the programs here that make environments,
        and where new_env() puts them. Reads local files only."""
        env = self.environment()
        report = EnvReport(env.kind, python=platform.python_version(), executable=sys.executable)
        report.tools = self.tools()
        folder = self.envs_folder()
        report.folder, report.folder_kept = self._shown(folder), self._kept(folder)
        report.booted = self._booted()
        here = os.path.normpath(sys.prefix) if self.root in ("", "/") else ""
        envs: dict[str, PythonEnv] = {}
        for path, name in self._env_prefixes(report.tools, folder).items():
            found = self._env_at(Path(path), name)
            if found is not None:
                found.this = path == here
                envs[path] = found
        seen: set[str] = set()
        for kernels in self._kernel_folders():
            try:
                specs = sorted(kernels.glob("*/kernel.json"))
            except OSError as exc:
                report.errors[self._shown(kernels)] = str(exc)
                continue
            for spec in specs:
                if spec.parent.name.lower() in seen:
                    continue  # Jupyter uses the first kernel of a name it finds
                kernel = self._kernel_at(spec, envs)
                if kernel is not None:
                    seen.add(kernel.name.lower())
                    report.kernels.append(kernel)
        if env.kind == "notebook instance":
            # A notebook instance's Jupyter lists each conda environment in ~/anaconda3/envs that has ipykernel as
            # conda_<name> by itself (its EnvironmentKernelSpecManager); there's no kernel.json for those.
            anaconda = os.path.normpath(self._real(f"{_ANACONDA}/envs"))
            for path, e in envs.items():
                if os.path.dirname(path) == anaconda and e.ipykernel and e.name != "JupyterSystemEnv":
                    label = f"conda_{e.name}"
                    e.kernels.append(label)
                    report.kernels.append(
                        KernelSpec(label, label, "", self._shown(Path(path, "bin", "python")), "python", e.path,
                                   e.kept)
                    )
        report.envs = sorted(envs.values(), key=lambda e: (not e.this, e.path))
        return report

    def env_plan(
        self,
        python: Any = "3.11",
        name: str | None = None,
        *,
        packages: Any = None,
        path: str | None = None,
        tool: str = "auto",
    ) -> EnvPlan:
        """What new_env() does, without doing it: where the environment goes, what makes it, and the commands.
        Raises ValueError for a version, name, folder or tool it can't use."""
        version = parse_python_version(python)
        prefix = self._real(path) if path else None
        if prefix is not None and not prefix.is_absolute():
            prefix = Path.cwd() / prefix  # from the notebook's folder; the kernel needs the whole path
        name = str(name).strip() if name is not None else (prefix.name if prefix else env_name(version))
        if not _ENV_NAME_RE.match(name):
            raise ValueError(f"name takes letters, digits, '.', '_' and '-', like 'py311' (got {name!r}).")
        folder = self.envs_folder()
        prefix = prefix or folder / name
        tools = self.tools()
        tool = str(tool or "auto").strip().lower()
        if tool == "auto":
            if not tools:
                raise _Hint(
                    "There's no conda, mamba, micromamba or uv on this machine to make an environment with. "
                    "%pip install uv adds uv; then run new_env() again."
                )
            tool = next(iter(tools))
        elif tool not in TOOLS:
            raise ValueError(f"tool takes 'auto', {', '.join(map(repr, TOOLS))} (got {tool!r}).")
        elif tool not in tools:
            others = f" ({', '.join(tools)} {'is' if len(tools) == 1 else 'are'}: leave out tool=)" if tools else ""
            how = " %pip install uv adds it." if tool == "uv" else ""
            raise _Hint(f"There's no {tool} on this machine{others}.{how}")
        home = self._user_home()
        existing = self._env_at(prefix) if prefix.exists() else None
        if existing is None and prefix.exists() and (not prefix.is_dir() or any(prefix.iterdir())):
            raise ValueError(
                f"{shell_path(self._shown(prefix), home)} is there already and isn't a Python environment. "
                f"Pick another name=, or remove it first: rm -rf {shell_path(self._shown(prefix), home)}"
            )
        if existing is not None and existing.python and not _same_python(existing.python, version):
            raise ValueError(
                f"{shell_path(existing.path, home)} is there already with Python {existing.python}. Pick another "
                f"name ({new_env_call(version, name + '-new')}), or remove it first: "
                f"rm -rf {shell_path(existing.path, home)}"
            )
        python_dir = ""
        if tool == "uv" and self.environment().kind == "notebook instance":
            python_dir = str(folder / ".python")  # uv's own (~/.local/share/uv) is reset when the instance stops
        steps = env_steps(
            tool, tools[tool], prefix, version, name, package_list(packages), exists=existing is not None,
            ipykernel=bool(existing and existing.ipykernel), python_dir=python_dir,
        )
        for step in steps:
            step.command = step_command(step, self._shown, home)
        plan = EnvPlan(
            name, version, self._shown(prefix), tool, kernel_label(version, name), package_list(packages), steps,
            exists=existing is not None, found=existing.python if existing else "", kept=self._kept(prefix),
            where=self.environment().kind, folder=self._shown(folder),
            kernel_kept=self._kept(self._real(f"~/{KERNELS_FOLDER}")),
        )
        there = next((p for p in (prefix, *prefix.parents) if p.exists()), None)
        if there is not None:
            try:
                plan.free = self._disk_usage(str(there))[2]
            except OSError:
                pass
        if plan.where == "notebook instance" and plan.kept and plan.kernel_kept is False:
            plan.on_start = self._shown(prefix.parent)
        return plan

    def _run(self, argv: list[str], env: dict[str, str], timeout: float) -> tuple[int, str]:
        """Runs one command -> (its exit status, what it printed). -1 when it ran past `timeout` seconds and was
        stopped, 127 when it couldn't be started. Tests and the demo replace this."""
        variables = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "PYTHONHOME")}
        try:
            done = subprocess.run(
                argv,
                env={**variables, **env},
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                errors="replace",
                timeout=timeout,
            )
        except subprocess.TimeoutExpired as exc:
            output = exc.output or ""
            text = output.decode("utf-8", "replace") if isinstance(output, bytes) else output
            return -1, text + f"\nStopped after {human_runtime(timeout)}."
        except OSError as exc:
            return 127, f"{argv[0]}: {exc.strerror or exc}"
        return done.returncode, done.stdout or ""

    def create_env(
        self,
        plan: EnvPlan,
        *,
        progress: Callable[..., None] | None = None,
        timeout: float = 1800,
    ) -> EnvResult:
        """Runs a plan's commands in order and stops at the first that fails -> EnvResult. Makes the folder the
        environment goes in, and never removes anything. Each command may run `timeout` seconds; progress is called
        with (steps done, steps)."""
        result = EnvResult(plan)
        started = time.monotonic()
        prefix = self._real(plan.path)
        prefix.parent.mkdir(parents=True, exist_ok=True)
        total = len(plan.steps)
        for done, step in enumerate(plan.steps):
            if progress:
                progress(done, total)
            began = time.monotonic()
            code, output = self._run(step.argv, step.env, timeout)
            step.seconds, step.code, step.output = time.monotonic() - began, code, output_tail(output)
            if code != 0:
                result.failed = step
                break
        else:
            if progress:
                progress(total, total)
        result.ok = result.failed is None
        result.python = self._env_python(prefix) if prefix.is_dir() else ""
        result.leftover = (
            not result.ok and not plan.exists and prefix.exists() and self._env_at(prefix) is None
            and (not prefix.is_dir() or any(prefix.iterdir()))
        )
        result.seconds = time.monotonic() - started
        return result

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
        """Set each endpoint's `invocations`: requests over the last `days` days, from CloudWatch (every variant).
        Needs cloudwatch:GetMetricData."""
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
        """Everything SageMaker bills by the hour in the region right now: notebook instances, Studio apps (not the
        free 'system' ones), endpoints and training / processing jobs in progress, plus stopped notebook instances
        (their volumes are still billed). metrics=True counts each endpoint's requests over the last `days` days.
        A section that can't be read is recorded in `errors`; progress is called with (done, total) sections."""
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
    """Is this the notebook the code runs in?"""
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


# =============================================================================
# 5. SageMakerView - notebook UI layer (renders what SageMakerAnalyzer returns)
# =============================================================================


_ROOT = "smk"  # the class around each report's HTML: its stylesheet's rules are scoped to it
_CSS = _base_css(_ROOT)
_BADGE = "🧪 SageMaker"  # the chip before each report's title, so reports from different analyzers are easy to tell apart


def _render_html(blocks: list[Any], max_rows: int) -> str:
    """A report as HTML: this service's stylesheet, root class and badge around the blocks _kit's renderer draws."""
    return _report_html(blocks, max_rows, _CSS, _ROOT, _BADGE)


def _idle_label(state: str, minutes: int | None) -> str:
    """'after 60 idle min', 'off', 'not needed (free)', '?'."""
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
    """'📓 notebook instance', '🧪 JupyterLab app', '🚀 endpoint': what it is, with its icon."""
    label = f"{app_label(b.app_type)} app" if b.kind == "Studio app" else b.kind
    icon = _KIND_ICONS.get(b.kind)
    return f"{icon} {label}" if icon else label


def _where(b: Billable) -> str:
    """'space analysis · d-abc123' for a Studio app."""
    owner = (
        f"space {b.space}"
        if b.space
        else (f"user {b.user_profile}" if b.user_profile else "")
    )
    return " · ".join(filter(None, [owner, b.domain_id]))


def _notebook_ref(b: Billable) -> str | None:
    """What instance() takes to describe this notebook: its name, or the space ('d-abc/analysis' form)."""
    if b.kind == "notebook instance":
        return b.name
    if b.kind == "Studio app" and b.space:
        return f"{b.domain_id}/{b.space}" if b.domain_id else b.space
    return None


class SageMakerView(_BaseView):
    """Notebook UI over SageMakerAnalyzer. Each method renders a report and returns nothing; for the underlying
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
        """The AWS error message plus what to do about it."""
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
        """(seconds the notebook has been running, whether that's only approximate)."""
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
        """The notebook you're running in: its instance type and what it costs, how long it has been running,
        whether it stops when idle, its CPU, memory, disk and GPU use right now, and its role and network.

        name: another notebook instance, or a Studio space ('analysis', or 'd-abc123/analysis' when two domains
        have one), to see its settings and cost. Its machine can only be read from inside it."""
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
        """'This machine right now': CPU, memory, disks, GPUs and kernels, each with how much is used."""
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
        """What fills the disk: how full it is, the biggest folders and files, and the caches and trash that are safe
        to clear, with the command for each (this tool never deletes anything).

        path: the folder to measure (default: where your notebooks live, ~/SageMaker on a notebook instance and
        /home/sagemaker-user in Studio). limit: stop after this many files ('200k'; None measures everything)."""
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
        """The Python environments on this machine and the Jupyter kernels that start them: each one's Python version,
        where it is and whether it's kept when the notebook stops, and kernels that fail to start.

        new_env('3.11') makes another one. Nothing here removes one; where that would help, the command is shown."""
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
        """new_env(...) for this plan again, with only the arguments that differ from the defaults."""
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
        """Makes a Python environment with the version you ask for, in a folder that's kept when the notebook stops,
        and adds it to Jupyter's kernel menu. It takes a minute or two; dry_run=True shows the commands instead.

        python: '3.11', '3.12.4' or 'py311'. name: its folder and kernel ('py311' for 3.11). packages: what to pip
        install in it ('pandas boto3', a list, or '-r requirements.txt'). path: another folder for it. tool: 'auto'
        (the first of mamba, conda, micromamba and uv that's here), or one of them; conda ones use conda-forge.
        Run it again on an environment that's there and only the packages and the kernel are set up: after a
        notebook instance restarts, new_env('3.11') adds the kernel back in seconds. It writes only the environment's
        folder and its kernel, and never removes anything."""
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
        """Everything SageMaker bills by the hour in the region right now: notebook instances, Studio apps,
        endpoints and jobs, with what each costs, which ones look forgotten, and the command that stops each.

        Endpoint traffic comes from CloudWatch over the last `days` days (metrics=False skips it). Stopped notebook
        instances are listed too, because their storage is still billed."""
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
