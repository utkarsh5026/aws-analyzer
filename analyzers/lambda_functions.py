"""
lambda_functions.py - self-contained AWS Lambda toolkit for SageMaker / Jupyter notebooks.

Copy this one file into a notebook cell (or upload it next to your notebook and
``import lambda_functions``). Nothing else from this repo is needed. It isn't called
lambda.py because `lambda` is a Python keyword, so `import lambda` can't work.

Requirements: boto3 (required). pandas only for DataFrames, IPython only for rich
HTML output. All are preinstalled on SageMaker.

The file has two layers:

    LambdaAnalyzer   Pure logic. Talks to AWS and returns plain Python data
                     (dataclasses, dicts, lists, DataFrames). Never prints.
    LambdaView       Notebook UI. Calls LambdaAnalyzer and renders readable
                     cards and tables (HTML in Jupyter, plain text in a terminal).

Functions and their settings come from Lambda, how often they ran, failed and were
throttled from CloudWatch, and what they logged from CloudWatch Logs. Nothing in this
file invokes, changes or deletes a function: where a change would help, the report
shows the command to run instead. Environment variable values are never shown.

Quick start
-----------
    ui = LambdaView()                                 # or LambdaView(LambdaAnalyzer(region="eu-west-1"))
    ui.help()                                         # list every command
    ui.functions()                                    # every function: runtime, triggers, use, errors, cost, warnings
    ui.functions(regions="all")                       # ...in every region your account has turned on
    ui.function_info("etl-nightly")                   # one function in plain English: triggers, access, cost, activity
    ui.errors("etl-nightly")                          # its errors in the last 24 hours, grouped by cause
    ui.logs("etl-nightly", search="KeyError")         # the newest lines it logged
    ui.performance("etl-nightly")                     # run times, memory used, cold starts, and the memory it needs
    ui.code("etl-nightly")                            # the files in its package, and the handler's source

    lam = ui.core                                     # same analyzer, raw data
    df = lam.overview(regions="all").to_df()          # one row per function
    detail = lam.describe("etl-nightly")              # FunctionDetail: .function, .triggers, .aliases, .metrics
"""

from __future__ import annotations

import difflib
import fnmatch
import functools
import html
import importlib
import inspect
import io
import json
import math
import posixpath
import re
import sys
import threading
import time
import unicodedata
import urllib.request
import zipfile
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Generator, Iterable
from urllib.parse import unquote

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError, NoRegionError

# =============================================================================
# 1. Helpers: parsing and formatting
# =============================================================================

KB, MB, GB, TB = 1024, 1024**2, 1024**3, 1024**4
HOURS_PER_MONTH = 730
DAYS_PER_MONTH = HOURS_PER_MONTH / 24  # 30.4 days: what a month's estimate scales a daily rate by

# USD, us-east-1 list prices from the AWS Price List API (the AWSLambda and AmazonCloudWatch offer files) on
# 2026-10-05, before the free tier (1M requests and 400,000 GB-seconds a month for the whole account). Compute is the
# first pricing tier: the first 6 billion GB-seconds a month on x86_64, 7.5 billion on arm64. Other regions differ;
# pass LambdaAnalyzer(prices={...}) to use your own.
LAMBDA_PRICES: dict[str, float] = {
    "request": 0.20,  # per million requests, x86_64 and arm64 alike
    "gb_second": 0.0000166667,  # per GB-second of compute (memory x run time), x86_64
    "gb_second_arm": 0.0000133334,  # the same on arm64 (Graviton): 20% less
    "provisioned": 0.0000041667,  # per GB-second of provisioned concurrency kept ready, used or not, x86_64
    "provisioned_arm": 0.0000033334,  # the same on arm64
    "ephemeral_storage": 0.0000000309,  # per GB-second of /tmp above the 512 MB every function gets free
    "log_ingestion": 0.50,  # per GB a function logs to CloudWatch Logs (the first 10 TB a month)
    "log_storage": 0.03,  # per GB-month of logs CloudWatch Logs keeps
    "metric_request": 0.01,  # per 1,000 metrics read with CloudWatch GetMetricData (what these reports read)
}

# Lambda's managed runtimes: the day AWS stops supporting each one (no more security patches), then the days it
# blocks creating functions on it and updating them, as AWS published them on 2026-09-29 (the Lambda runtimes page,
# https://docs.aws.amazon.com/lambda/latest/dg/lambda-runtimes.html). None: no date set. A runtime that isn't here
# is reported as unknown rather than guessed.
RUNTIMES: dict[str, tuple[str | None, str | None, str | None]] = {
    "python3.14": ("2029-06-30", "2029-07-31", "2029-08-31"),
    "python3.13": ("2029-06-30", "2029-07-31", "2029-08-31"),
    "python3.12": ("2028-10-31", "2028-11-30", "2029-01-10"),
    "python3.11": ("2027-06-30", "2027-07-31", "2027-08-31"),
    "python3.10": ("2026-10-31", "2027-02-01", "2027-03-03"),
    "python3.9": ("2025-12-15", "2027-02-01", "2027-03-03"),
    "python3.8": ("2024-10-14", "2027-02-01", "2027-03-03"),
    "python3.7": ("2023-12-04", "2024-01-09", "2027-03-03"),
    "python3.6": ("2022-07-18", "2022-07-18", "2022-08-29"),
    "python2.7": ("2021-07-15", "2021-07-15", "2022-05-30"),
    "nodejs24.x": ("2028-04-30", "2028-06-01", "2028-07-01"),
    "nodejs22.x": ("2027-04-30", "2027-06-01", "2027-07-01"),
    "nodejs20.x": ("2026-04-30", "2027-02-01", "2027-03-03"),
    "nodejs18.x": ("2025-09-01", "2027-02-01", "2027-03-03"),
    "nodejs16.x": ("2024-06-12", "2027-02-01", "2027-03-03"),
    "nodejs14.x": ("2023-12-04", "2024-01-09", "2027-03-03"),
    "nodejs12.x": ("2023-03-31", "2023-03-31", "2023-04-30"),
    "nodejs10.x": ("2021-07-30", "2021-07-30", "2022-02-14"),
    "nodejs8.10": ("2020-03-06", "2020-02-04", "2020-03-06"),
    "nodejs6.10": ("2019-08-12", "2019-07-12", "2019-08-12"),
    "nodejs4.3": ("2020-03-05", "2020-02-03", "2020-03-05"),
    "nodejs4.3-edge": ("2020-03-05", "2019-03-31", "2019-04-30"),
    "nodejs": ("2016-08-30", "2016-09-30", "2016-10-31"),
    "java25": ("2029-06-30", "2029-07-31", "2029-08-31"),
    "java21": ("2029-06-30", "2029-07-31", "2029-08-31"),
    "java17": ("2027-06-30", "2027-07-31", "2027-08-31"),
    "java11": ("2027-06-30", "2027-07-31", "2027-08-31"),
    "java8.al2": ("2027-06-30", "2027-07-31", "2027-08-31"),
    "java8": ("2024-01-08", "2024-02-08", "2027-03-03"),
    "dotnet10": ("2028-11-14", "2028-12-14", "2029-01-15"),
    "dotnet9": ("2026-11-10", None, None),  # container images only
    "dotnet8": ("2026-11-10", "2027-02-01", "2027-03-03"),
    "dotnet6": ("2024-12-20", "2027-02-01", "2027-03-03"),
    "dotnetcore3.1": ("2023-04-03", "2023-04-03", "2023-05-03"),
    "dotnetcore2.1": ("2022-01-05", "2022-01-05", "2022-04-13"),
    "dotnetcore2.0": ("2019-05-30", "2019-04-30", "2019-05-30"),
    "dotnetcore1.0": ("2019-06-27", "2019-06-30", "2019-07-30"),
    "ruby4.0": ("2029-03-31", "2029-04-30", "2029-05-31"),
    "ruby3.4": ("2028-03-31", "2028-04-30", "2028-05-31"),
    "ruby3.3": ("2027-03-31", "2027-04-30", "2027-05-31"),
    "ruby3.2": ("2026-03-31", "2027-02-01", "2027-03-03"),
    "ruby2.7": ("2023-12-07", "2024-01-09", "2027-03-03"),
    "ruby2.5": ("2021-07-30", "2021-07-30", "2022-03-31"),
    "provided.al2023": ("2029-06-30", "2029-07-31", "2029-08-31"),
    "provided.al2": ("2026-07-31", "2027-02-01", "2027-03-03"),
    "provided": ("2024-01-08", "2024-02-08", "2027-03-03"),
    "go1.x": ("2024-01-08", "2024-02-08", "2027-03-03"),
}
# The runtime to move each language to: the newest one AWS supports. Go runs on the OS-only runtime now.
LATEST_RUNTIMES = {
    "python": "python3.14",
    "nodejs": "nodejs24.x",
    "java": "java25",
    "dotnet": "dotnet10",
    "ruby": "ruby4.0",
    "provided": "provided.al2023",
    "go": "provided.al2023",
}
ENDING_SOON_DAYS = 180  # a runtime that loses support within this many days gets a warning

MAX_TIMEOUT = 900  # seconds: the longest a Lambda function can run
CODE_STORAGE_LIMIT = 75 * GB  # code storage per region by default; GetAccountSettings has the account's own
UNZIPPED_LIMIT = 250 * MB  # the most a function's code and layers can take, unzipped
MEMORY_PER_VCPU = 1769  # MB: Lambda gives a function one full vCPU at this much memory, in proportion below it
MEMORY_STEPS = (128, 256, 512, 768, 1024, 1536, 2048, 3008, 4096, 6144, 8192, 10240)  # sizes performance() suggests


def human_size(num_bytes: float | None) -> str:
    """1536 -> '1.5 KB' (binary units, like the AWS console)."""
    if num_bytes is None:
        return "-"
    value = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(value) < 1024:
            return f"{int(value)} B" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} PB"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def human_age(when: datetime | None, now: datetime | None = None) -> str:
    """datetime -> '3d ago' / '5mo ago' / 'just now'."""
    if when is None:
        return "-"
    seconds = ((now or _utcnow()) - when).total_seconds()
    for unit, size in (
        ("y", 365 * 86400),
        ("mo", 30 * 86400),
        ("d", 86400),
        ("h", 3600),
        ("m", 60),
    ):
        if seconds >= size:
            return f"{int(seconds // size)}{unit} ago"
    return "just now"


def human_money(usd: float | None) -> str:
    """12.345 -> '$12.35', 0.004 -> '<$0.01', 12345.6 -> '$12,346', -3 -> '-$3.00'."""
    if usd is None:
        return "-"
    sign, usd = ("-" if usd < 0 else ""), abs(usd)
    if 0 < usd < 0.01:
        return f"{sign}<$0.01"
    return f"{sign}${usd:,.0f}" if usd >= 1000 else f"{sign}${usd:,.2f}"


def _plural(count: int, word: str) -> str:
    return f"{count:,} {word}{'' if count == 1 else 's'}"


def _require(module: str, purpose: str, package: str | None = None) -> Any:
    """Import an optional package, or say what to pip install. package: its pip name when that differs (pillow)."""
    try:
        return importlib.import_module(module)
    except ImportError as exc:
        package = package or module.split(".")[0]
        raise ImportError(
            f"{purpose} needs `{package}` (pip install {package})"
        ) from exc


def _error_code(exc: ClientError) -> str:
    return exc.response.get("Error", {}).get("Code", "Unknown")


def _error_name(exc: ClientError | BotoCoreError) -> str:
    return _error_code(exc) if isinstance(exc, ClientError) else type(exc).__name__


def _why(code: str, permission: str) -> str:
    """'AccessDeniedException' -> 'AccessDeniedException; needs dynamodb:Scan'. Other codes stay as they are."""
    return (
        f"{code}; needs {permission}"
        if "denied" in code.lower() or code == "UnauthorizedOperation"
        else code
    )


_COUNT_RE = re.compile(r"^\s*(\d[\d,_]*(?:\.\d+)?)\s*([km]?)\s*$", re.IGNORECASE)


def _as_int(value: Any, name: str, *, hint: str = "") -> int:
    """A number-of-items argument: 1000, '10,000', '10k' or '2m' -> int."""
    if (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and float(value).is_integer()
    ):
        return int(value)
    match = _COUNT_RE.match(value) if isinstance(value, str) else None
    if match:
        number = (
            float(match.group(1).replace(",", "").replace("_", ""))
            * {"": 1, "k": 1000, "m": 10**6}[match.group(2).lower()]
        )
        if number.is_integer():
            return int(number)
    raise ValueError(
        f"{name} takes a number of items, like 1000 or '10k'{hint}; got {value!r}"
    )


def _as_count(value: Any, name: str) -> int | None:
    """Like _as_int, for limits where None means no limit."""
    return (
        None if value is None else _as_int(value, name, hint=", or None for no limit")
    )


def _clip(text: str, width: int = 90) -> str:
    return text if len(text) <= width else text[: width - 1] + "…"


def _width(text: str) -> int:
    """How many columns a terminal gives `text`, so text tables line up when a cell holds an emoji (📁) or CJK:
    2 for a wide character, 1 more for a symbol U+FE0F turns into an emoji (⚙️), 0 for combining marks and joiners."""
    width, wide = 0, False
    for ch in text:
        if ch == "\ufe0f":
            width, wide = width + (not wide), True
        elif not unicodedata.combining(ch) and unicodedata.category(ch) not in ("Mn", "Me", "Cf"):
            wide = unicodedata.east_asian_width(ch) in ("W", "F")
            width += 2 if wide else 1
    return width


def _pad(text: str, width: int, right: bool = False) -> str:
    """ljust / rjust by the columns the text takes on screen (_width), not its length."""
    fill = " " * max(0, width - _width(text))
    return fill + text if right else text + fill


_RELATIVE_TIME_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*([smhdw])\s*$", re.IGNORECASE)
_UNIT_SECONDS = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 7 * 86400}


def parse_time(
    value: datetime | date | timedelta | str | None, now: datetime | None = None
) -> datetime | None:
    """datetime/date, ISO string ('2024-05-01', '2024-05-01T10:00Z'), or a relative
    age like '7d', '12h', '30m', '2w' meaning "that long ago". Naive values are UTC."""
    if value is None:
        return None
    if isinstance(value, timedelta):
        return (now or _utcnow()) - value
    if isinstance(value, datetime):
        moment = value
    elif isinstance(value, date):
        moment = datetime(value.year, value.month, value.day)
    else:
        relative = _RELATIVE_TIME_RE.match(str(value))
        if relative:
            seconds = (
                float(relative.group(1)) * _UNIT_SECONDS[relative.group(2).lower()]
            )
            return (now or _utcnow()) - timedelta(seconds=seconds)
        moment = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


_SIZE_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*([kmgtp]?)i?b?\s*$", re.IGNORECASE)


def parse_size(value: int | float | str | None) -> int | None:
    """'10MB', '1.5 GiB', '512k', 1024 -> bytes. Units are binary (1 KB = 1024 B)."""
    if value is None or isinstance(value, (int, float)):
        return None if value is None else int(value)
    match = _SIZE_RE.match(value)
    if not match:
        raise ValueError(f"Can't parse size {value!r}; try 1024, '10MB' or '1.5GB'")
    number, unit = match.groups()
    return int(float(number) * 1024 ** " kmgtp".index(unit.lower() or " "))


_FUNCTION_ARN_RE = re.compile(
    r"^arn:aws[\w-]*:lambda:([a-z0-9-]+):(\d{12}):function:([A-Za-z0-9_-]{1,64})(?::([\w$-]+))?$"
)
_PARTIAL_ARN_RE = re.compile(r"^(\d{12}):function:([A-Za-z0-9_-]{1,64})(?::([\w$-]+))?$")
_CONSOLE_RE = re.compile(r"console\.aws\.amazon\.com/lambda/.*#/functions/([A-Za-z0-9_%-]+)", re.IGNORECASE)
_CONSOLE_REGION_RE = re.compile(r"[?&]region=([a-z0-9-]+)|^https?://([a-z]{2}(?:-[a-z]+)+-\d)\.console\.")
_NAME_RE = re.compile(r"^([A-Za-z0-9_-]{1,64})(?::([\w$-]+))?$")


def parse_function_ref(text: str) -> tuple[str, str | None, str | None]:
    """A function the way you'd paste it -> (name, qualifier, region): 'etl', 'etl:prod' (an alias or a version),
    an ARN ('arn:aws:lambda:eu-west-1:123456789012:function:etl'), a partial ARN ('123456789012:function:etl') or a
    link to it in the Lambda console. qualifier and region are None when the text doesn't say."""
    value = str(text).strip()
    if match := _FUNCTION_ARN_RE.match(value):
        return match.group(3), match.group(4), match.group(1)
    if match := _PARTIAL_ARN_RE.match(value):
        return match.group(2), match.group(3), None
    if match := _CONSOLE_RE.search(value):
        region = _CONSOLE_REGION_RE.search(value)
        return unquote(match.group(1)), None, (region.group(1) or region.group(2)) if region else None
    if match := _NAME_RE.match(value):
        return match.group(1), match.group(2), None
    raise ValueError(
        f"{text!r} isn't a Lambda function name, ARN or console link (names are letters, digits, - and _); "
        "functions() lists them"
    )


def runtime_family(runtime: str | None) -> str | None:
    """'python3.12' -> 'python', 'nodejs20.x' -> 'nodejs', 'dotnetcore3.1' -> 'dotnet', 'provided.al2' ->
    'provided', 'go1.x' -> 'go'; None for a container image (no runtime)."""
    if not runtime:
        return None
    match = re.match(r"[a-z]+", runtime)
    family = match.group(0) if match else runtime
    return "dotnet" if family.startswith("dotnet") else family


def _lambda_time(value: Any) -> datetime | None:
    """Lambda's '2024-05-01T10:00:00.000+0000' (or a datetime) -> an aware datetime; None when missing or unreadable."""
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    match = re.match(
        r"^(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2}:\d{2})(\.\d+)?(Z|[+-]\d{2}:?\d{2})?$", str(value or "").strip()
    )
    if not match:
        return None
    fraction = (match.group(3) or ".0")[1:7].ljust(6, "0")  # Python 3.10 reads exactly 3 or 6 digits
    zone = match.group(4) or "Z"
    zone = "+00:00" if zone == "Z" else zone if ":" in zone else f"{zone[:3]}:{zone[3:]}"
    try:
        return datetime.fromisoformat(f"{match.group(1)}T{match.group(2)}.{fraction}{zone}")
    except ValueError:
        return None


def _midnight(moment: datetime) -> datetime:
    """The start of the moment's UTC day."""
    return moment.astimezone(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)


def _day(text: str | None) -> date | None:
    return date.fromisoformat(text) if text else None


def human_ms(ms: float | None) -> str:
    """A run time: 0.42 -> '0.42 ms', 102.4 -> '102 ms', 1530 -> '1.53 s', 75000 -> '75.0 s', 185000 -> '3m 05s'."""
    if ms is None:
        return "-"
    if ms < 1:
        return f"{ms:.2f} ms"
    if ms < 1000:
        return f"{ms:.0f} ms"
    seconds = ms / 1000
    if seconds < 10:
        return f"{seconds:.2f} s"
    if seconds < 120:
        return f"{seconds:.1f} s"
    return f"{int(seconds // 60)}m {int(seconds % 60):02d}s"


def _ms(ms: float | None) -> str:
    """A run time in a table: always in ms ('1,530 ms'), so a column of them lines up."""
    if ms is None:
        return "-"
    return f"{ms:.2f} ms" if 0 < ms < 1 else f"{ms:,.0f} ms"


def _mb(value: float | None) -> str:
    return "-" if value is None else f"{value:,.0f} MB"


def _pct(fraction: float | None) -> str:
    """0.024 -> '2.4%', 0.0004 -> '<0.1%', 0 -> '0%'."""
    if fraction is None:
        return "-"
    if fraction == 0:
        return "0%"
    return "<0.1%" if fraction < 0.001 else f"{fraction * 100:.1f}%"


def _in_days(days: int) -> str:
    """26 -> 'in 26 days', 1 -> 'tomorrow', 0 -> 'today', -40 -> '40 days ago'."""
    if days == 0:
        return "today"
    if days == 1:
        return "tomorrow"
    return f"in {days:,} days" if days > 0 else f"{-days:,} days ago"


# =============================================================================
# 2. Data models (what LambdaAnalyzer returns)
# =============================================================================


@dataclass
class RuntimeStatus:
    """Where a runtime stands in AWS's support schedule."""

    runtime: str | None
    state: str  # 'supported' | 'ending' (within ENDING_SOON_DAYS) | 'deprecated' | 'blocked' | 'image' | 'unknown'
    deprecated: date | None = None  # end of support: no more security patches
    block_create: date | None = None  # no new functions on it from this day
    block_update: date | None = None  # no updates to functions on it from this day
    days_left: int | None = None  # until end of support (negative: since)
    upgrade: str | None = None  # the newest runtime for the same language

    @property
    def label(self) -> str:
        """'python3.9 (ended)', 'python3.10 (ends in 26 days)', 'python3.12', 'container image'."""
        if self.state == "image":
            return "container image"
        name = self.runtime or "-"
        if self.state in ("deprecated", "blocked"):
            return f"{name} (ended)"
        if self.state == "ending" and self.days_left is not None:
            return f"{name} (ends {_in_days(self.days_left)})"
        return name

    @property
    def tone(self) -> str:
        return {"deprecated": "bad", "blocked": "bad", "ending": "warn"}.get(self.state, "")


@dataclass
class Layer:
    arn: str
    code_size: int | None = None

    @property
    def name(self) -> str:
        """'arn:aws:lambda:us-east-1:123456789012:layer:pandas:7' -> 'pandas:7'."""
        parts = self.arn.split(":")
        return ":".join(parts[-2:]) if len(parts) >= 8 else self.arn


@dataclass
class Function:
    """One function's configuration (ListFunctions / GetFunction), in plain Python."""

    name: str
    arn: str = ""
    region: str = ""
    runtime: str | None = None  # None for a container image
    handler: str | None = None
    description: str = ""
    memory: int = 128  # MB; it also sets the CPU
    timeout: int = 3  # seconds
    architecture: str = "x86_64"  # or 'arm64'
    package_type: str = "Zip"  # or 'Image'
    code_size: int = 0  # bytes of the deployment package (zipped)
    last_modified: datetime | None = None
    ephemeral_storage: int = 512  # MB of /tmp
    layers: list[Layer] = field(default_factory=list)
    env_names: list[str] = field(default_factory=list)  # environment variable names; values are never read in
    env_error: str | None = None  # why Lambda couldn't decrypt them, if it couldn't
    role: str | None = None  # the execution role: what the code is allowed to do
    vpc_id: str | None = None
    subnets: list[str] = field(default_factory=list)
    security_groups: list[str] = field(default_factory=list)
    tracing: str | None = None  # 'Active' (X-Ray) or 'PassThrough'
    snapstart: bool = False
    state: str | None = None  # 'Active', 'Pending', 'Inactive', 'Failed'
    state_reason: str | None = None
    last_update: str | None = None  # 'Successful', 'InProgress', 'Failed'
    last_update_reason: str | None = None
    log_group: str = ""
    log_format: str | None = None  # 'Text' or 'JSON'
    log_level: str | None = None  # the application log level (JSON logs only)
    dead_letter: str | None = None  # SQS queue or SNS topic for failed asynchronous events
    kms_key: str | None = None  # a customer key for its environment variables
    file_systems: list[str] = field(default_factory=list)  # 'EFS access point -> /mnt/data'
    image_uri: str | None = None
    version: str = "$LATEST"
    reserved_concurrency: int | None = None  # GetFunction only: None means it shares the region's pool
    tags: dict[str, str] = field(default_factory=dict)  # GetFunction only
    code_location: str | None = field(default=None, repr=False)  # GetFunction only: a link to download the package
    raw: dict[str, Any] = field(default_factory=dict, repr=False)  # the configuration, environment values hidden
    errors: dict[str, str] = field(default_factory=dict)

    @property
    def gb(self) -> float:
        return self.memory / 1024

    @property
    def arm(self) -> bool:
        return self.architecture == "arm64"

    @property
    def vcpus(self) -> float:
        """The share of CPU its memory buys (Lambda gives a full vCPU at 1,769 MB)."""
        return self.memory / MEMORY_PER_VCPU

    @property
    def layer_size(self) -> int:
        return sum(layer.code_size or 0 for layer in self.layers)


@dataclass
class Trigger:
    """Something that invokes a function: an event source mapping (a queue or stream Lambda reads), a resource
    policy statement (a service or account allowed to call it), or its function URL."""

    kind: str  # 'SQS queue', 'S3 bucket', 'EventBridge rule', 'API Gateway', 'Function URL', 'AWS account', ...
    source: str = ""  # its name: a queue, bucket, rule or account
    arn: str | None = None
    via: str = "event source mapping"  # or 'resource policy' / 'function URL'
    state: str | None = None  # event source mappings: 'Enabled', 'Disabled', ...
    detail: str = ""  # batch size, API route, auth type...
    last_result: str | None = None  # event source mappings over streams: 'OK' or 'PROBLEM: ...'
    public: bool = False  # anyone on the internet can invoke it this way
    statement: str | None = None  # the policy statement's ID (to remove it)
    principal: str | None = None
    unscoped: bool = False  # a service allowed without saying which of its resources (any account's)
    uuid: str | None = None  # the event source mapping's ID

    @property
    def short(self) -> str:
        """A few characters for a table cell: 'SQS', 'S3', 'URL (public)'."""
        label = _SHORT_KINDS.get(self.kind, self.kind)
        return f"{label} (public)" if self.public else label

    @property
    def asynchronous(self) -> bool:
        """Whether the source invokes the function asynchronously, so failed events go to its on-failure destination."""
        return self.kind in _ASYNC_KINDS


@dataclass
class DailyUsage:
    """One period of CloudWatch numbers for a function (a day, or an hour in short reports)."""

    start: datetime
    invocations: float = 0.0
    errors: float = 0.0
    throttles: float = 0.0
    duration_sum: float = 0.0  # ms, all runs together
    duration_max: float | None = None  # ms
    concurrency: float | None = None  # the most running at once

    @property
    def avg_duration(self) -> float | None:
        return self.duration_sum / self.invocations if self.invocations else None


@dataclass
class FunctionMetrics:
    """What CloudWatch counted for a function over a window: runs, failures, throttles, run time, log volume."""

    name: str
    days: float  # the window, in days
    period: int = 86400  # seconds per row of `daily`
    invocations: float = 0.0
    errors: float = 0.0
    throttles: float = 0.0
    duration_sum: float = 0.0  # ms, all runs together
    duration_max: float | None = None  # ms, the longest run
    concurrency_max: float | None = None  # the most running at once
    log_bytes: float | None = None  # bytes it sent to CloudWatch Logs (None: not read)
    daily: list[DailyUsage] = field(default_factory=list)  # oldest first; periods with no data are left out

    @property
    def avg_duration(self) -> float | None:
        return self.duration_sum / self.invocations if self.invocations else None

    @property
    def error_rate(self) -> float | None:
        return self.errors / self.invocations if self.invocations else None

    def _last(self, attribute: str) -> datetime | None:
        return max((d.start for d in self.daily if getattr(d, attribute)), default=None)

    @property
    def last_invoked(self) -> datetime | None:
        """The start of the latest period with a run (a day, in daily numbers)."""
        return self._last("invocations")

    @property
    def last_error(self) -> datetime | None:
        return self._last("errors")

    @property
    def last_throttle(self) -> datetime | None:
        return self._last("throttles")


@dataclass
class LogGroup:
    name: str
    retention_days: int | None = None  # None: logs are kept forever
    stored_bytes: int | None = None
    created: datetime | None = None
    log_class: str | None = None  # 'STANDARD' or 'INFREQUENT_ACCESS'


@dataclass
class ProvisionedConcurrency:
    """Copies of a version or alias kept started and ready, billed whether they're used or not."""

    qualifier: str  # the alias or version
    requested: int
    allocated: int = 0
    available: int = 0
    status: str = ""  # 'READY', 'IN_PROGRESS', 'FAILED'
    reason: str | None = None

    @property
    def billed(self) -> int:
        """The copies being paid for: what's allocated, or what was asked for while it's starting."""
        return self.allocated or (self.requested if self.status == "IN_PROGRESS" else 0)


@dataclass
class Alias:
    name: str
    version: str
    weights: dict[str, float] = field(default_factory=dict)  # other version -> its share of the traffic
    description: str = ""


@dataclass
class Version:
    version: str
    code_size: int = 0
    last_modified: datetime | None = None
    description: str = ""
    runtime: str | None = None


@dataclass
class FunctionUrl:
    url: str
    auth_type: str  # 'NONE' (anyone with the URL) or 'AWS_IAM'
    cors_origins: list[str] = field(default_factory=list)
    invoke_mode: str | None = None  # 'BUFFERED' or 'RESPONSE_STREAM'


@dataclass
class AsyncConfig:
    """What happens to an event an asynchronous caller (S3, SNS, EventBridge) sent when the function fails it."""

    retries: int = 2  # Lambda's default
    max_age: int = 6 * 3600  # seconds an event may wait; Lambda's default
    on_success: str | None = None
    on_failure: str | None = None


@dataclass
class FunctionDetail:
    """Everything function_info() shows about one function. Sections that couldn't be read are in errors."""

    function: Function
    triggers: list[Trigger] = field(default_factory=list)
    url: FunctionUrl | None = None
    async_config: AsyncConfig | None = None  # None: not read
    versions: list[Version] = field(default_factory=list)  # published versions, oldest first
    aliases: list[Alias] = field(default_factory=list)
    provisioned: list[ProvisionedConcurrency] = field(default_factory=list)
    policy: dict[str, Any] | None = None  # the resource-based policy document
    log_group: LogGroup | None = None
    runtime_updates: str | None = None  # 'Auto', 'FunctionUpdate' or 'Manual'
    runtime_version: str | None = None  # the runtime version 'Manual' pins
    metrics: FunctionMetrics | None = None
    errors: dict[str, str] = field(default_factory=dict)  # section -> error code


@dataclass
class AccountLimits:
    """A region's Lambda limits and usage (GetAccountSettings), with the most functions CloudWatch saw running."""

    region: str
    concurrency: int | None = None  # how many runs may be in progress at once, across the region
    unreserved: int | None = None  # what's left for functions without reserved concurrency
    code_storage: int | None = None  # bytes of code stored, every version and layer
    code_storage_limit: int | None = None
    function_count: int | None = None
    peak_concurrency: float | None = None  # the most running at once in the window (CloudWatch)


@dataclass
class Overview:
    """Every function in one or more regions, with what CloudWatch counted for each and what invokes it."""

    regions: list[str]
    days: int
    functions: list[Function] = field(default_factory=list)
    metrics: dict[str, FunctionMetrics] = field(default_factory=dict)  # by function ARN
    triggers: dict[str, list[Trigger]] = field(default_factory=dict)  # by function ARN
    provisioned: dict[str, list[ProvisionedConcurrency]] = field(default_factory=dict)  # by function ARN
    log_groups: dict[str, LogGroup] = field(default_factory=dict)  # by function ARN
    accounts: dict[str, AccountLimits] = field(default_factory=dict)  # by region
    errors: dict[str, str] = field(default_factory=dict)  # 'region:section' -> error code
    skipped: dict[str, str] = field(default_factory=dict)  # regions that aren't turned on -> why
    details: bool = True  # whether each function's resource policy and provisioned concurrency were read
    metrics_read: int = 0  # metrics requested from CloudWatch, for the cost of this report
    prices: dict[str, float] = field(default_factory=lambda: dict(LAMBDA_PRICES), repr=False)

    def detail(self, fn: Function) -> FunctionDetail:
        """What the overview knows about one function, shaped like describe()'s answer (fewer sections). Its errors
        name what wasn't read, so findings don't claim, say, that nothing triggers a function whose policy is unread."""
        errors = {section: code for section, code in fn.errors.items() if section in ("policy", "provisioned")}
        if not self.details:
            errors.update(policy="not read", provisioned="not read")
        if f"{fn.region}:triggers" in self.errors:
            errors["triggers"] = self.errors[f"{fn.region}:triggers"]
        return FunctionDetail(
            fn,
            triggers=self.triggers.get(fn.arn, []),
            provisioned=self.provisioned.get(fn.arn, []),
            log_group=self.log_groups.get(fn.arn),
            metrics=self.metrics.get(fn.arn),
            errors=errors,
        )

    def to_df(self):
        """One row per function: its settings, what CloudWatch counted and the estimated monthly cost."""
        pd = _require("pandas", "Overview.to_df()")
        rows = []
        for fn in self.functions:
            m = self.metrics.get(fn.arn)
            cost = function_monthly_cost(fn, m, self.provisioned.get(fn.arn, ()), self.log_groups.get(fn.arn),
                                         prices=self.prices)
            rows.append({
                "region": fn.region,
                "function": fn.name,
                "runtime": fn.runtime or "container image",
                "runtime_status": runtime_status(fn.runtime, package_type=fn.package_type).state,
                "memory_mb": fn.memory,
                "timeout_s": fn.timeout,
                "architecture": fn.architecture,
                "package": fn.package_type,
                "code_bytes": fn.code_size,
                "last_modified": fn.last_modified,
                "triggers": ", ".join(t.short for t in self.triggers.get(fn.arn, [])),
                "invocations": m.invocations if m else None,
                "errors": m.errors if m else None,
                "throttles": m.throttles if m else None,
                "avg_duration_ms": m.avg_duration if m else None,
                "max_duration_ms": m.duration_max if m else None,
                "last_invoked": m.last_invoked if m else None,
                "est_monthly_usd": _total(cost),
                "description": fn.description,
            })
        return pd.DataFrame(rows)


@dataclass
class LogEvent:
    time: datetime
    message: str
    stream: str = ""
    request_id: str | None = None


@dataclass
class LogPage:
    """Log events read from a function's log group, oldest first."""

    function: Function
    log_group: str
    since: datetime
    until: datetime
    events: list[LogEvent] = field(default_factory=list)
    pattern: str | None = None  # the CloudWatch Logs filter pattern used
    truncated: bool = False  # stopped at the limit before reaching `since`
    covered_from: datetime | None = None  # how far back the events read reach (when truncated)
    latest: datetime | None = None  # the newest event in the log group, when the window had none
    errors: dict[str, str] = field(default_factory=dict)

    def to_df(self):
        pd = _require("pandas", "LogPage.to_df()")
        return pd.DataFrame(
            [{"time": e.time, "request_id": e.request_id, "message": e.message, "stream": e.stream}
             for e in self.events]
        )


@dataclass
class ErrorGroup:
    """Errors that share a cause: the same kind and message, once numbers and IDs are blanked out."""

    kind: str  # 'Timeout', 'Out of memory', 'KeyError', 'Runtime.ImportModuleError', 'Logged error', ...
    message: str  # the message with numbers and IDs blanked out
    count: int
    first: datetime
    last: datetime
    example: str  # one full message
    request_ids: list[str] = field(default_factory=list)  # a few, newest first


@dataclass
class ErrorReport:
    function: Function
    log_group: str
    since: datetime
    until: datetime
    groups: list[ErrorGroup] = field(default_factory=list)  # most frequent first
    newest: list[LogEvent] = field(default_factory=list)  # the newest error events, newest first
    errors_found: int = 0  # log events that look like errors
    truncated: bool = False
    covered_from: datetime | None = None
    metrics: FunctionMetrics | None = None  # CloudWatch over the same window
    errors: dict[str, str] = field(default_factory=dict)


@dataclass
class Invocation:
    """One run, from the REPORT line Lambda logs at the end of it."""

    request_id: str
    time: datetime | None = None
    duration: float = 0.0  # ms
    billed: float = 0.0  # ms
    memory: int = 0  # MB it had
    max_memory: int = 0  # MB it used
    init: float | None = None  # ms of start-up before it: a cold start
    restore: float | None = None  # ms to restore a SnapStart snapshot
    status: str | None = None  # 'timeout' or 'error' when it didn't succeed (newer runtimes say)
    error_type: str | None = None  # 'Runtime.OutOfMemory', ...


@dataclass
class Performance:
    """Run times and memory from the REPORT lines in a window."""

    function: Function
    log_group: str
    since: datetime
    until: datetime
    invocations: list[Invocation] = field(default_factory=list)  # newest first
    truncated: bool = False
    covered_from: datetime | None = None
    errors: dict[str, str] = field(default_factory=dict)

    @property
    def cold_starts(self) -> list[Invocation]:
        return [i for i in self.invocations if i.init is not None]

    @property
    def window_days(self) -> float:
        """The days the invocations read cover (less than asked when the read stopped at the limit)."""
        start = self.covered_from if self.truncated and self.covered_from else self.since
        return max((self.until - start).total_seconds() / 86400, 1 / 24)

    def to_df(self):
        pd = _require("pandas", "Performance.to_df()")
        return pd.DataFrame([vars(i) for i in self.invocations])


@dataclass
class CodeFile:
    path: str
    size: int  # unzipped
    compressed: int


@dataclass
class CodePackage:
    """A function's deployment package: the files in it, and the text of one of them."""

    function: Function
    size: int = 0  # bytes downloaded (zipped)
    files: list[CodeFile] = field(default_factory=list)
    handler_file: str | None = None  # the file Lambda loads the handler from
    shown_file: str | None = None
    source: str | None = None  # the shown file's text
    source_truncated: bool = False
    note: str | None = None  # why there's no code to show (a container image, a binary or secrets file)

    @property
    def unzipped(self) -> int:
        return sum(f.size for f in self.files)


# =============================================================================
# 3. Pure analysis (no AWS calls - works on ListFunctions / GetFunction answers, policies and log lines)
# =============================================================================

_SHORT_KINDS = {
    "SQS queue": "SQS",
    "S3 bucket": "S3",
    "SNS topic": "SNS",
    "EventBridge rule": "EventBridge",
    "Function URL": "URL",
    "Kinesis stream": "Kinesis",
    "DynamoDB stream": "DynamoDB",
    "Kafka cluster": "Kafka",
    "Kafka (self-managed)": "Kafka",
    "MQ broker": "MQ",
    "DocumentDB stream": "DocumentDB",
    "CloudWatch Logs": "Logs",
    "CloudWatch alarm": "Alarm",
    "Cognito user pool": "Cognito",
    "IoT rule": "IoT",
    "Load balancer": "ALB",
    "Lex bot": "Lex",
    "Alexa skill": "Alexa",
    "AWS Config rule": "Config",
    "AWS account": "Account",
    "Any caller meeting its conditions": "Conditional",
}
# Services that call a function asynchronously: Lambda queues the event, retries it twice when it fails, and then
# drops it unless an on-failure destination or a dead-letter queue catches it.
_ASYNC_KINDS = {"S3 bucket", "SNS topic", "EventBridge rule", "CloudWatch Logs", "CloudWatch alarm", "IoT rule", "SES",
                "AWS Config rule", "CodeCommit"}
_SERVICE_KINDS = {  # service principal in a resource policy -> what it is
    "s3.amazonaws.com": "S3 bucket",
    "sns.amazonaws.com": "SNS topic",
    "events.amazonaws.com": "EventBridge rule",
    "apigateway.amazonaws.com": "API Gateway",
    "logs.amazonaws.com": "CloudWatch Logs",
    "lambda.alarms.cloudwatch.amazonaws.com": "CloudWatch alarm",
    "cognito-idp.amazonaws.com": "Cognito user pool",
    "iot.amazonaws.com": "IoT rule",
    "elasticloadbalancing.amazonaws.com": "Load balancer",
    "bedrock.amazonaws.com": "Bedrock agent",
    "secretsmanager.amazonaws.com": "Secrets Manager rotation",
    "lex.amazonaws.com": "Lex bot",
    "lexv2.amazonaws.com": "Lex bot",
    "alexa-appkit.amazon.com": "Alexa skill",
    "alexa-connectedhome.amazon.com": "Alexa skill",
    "ses.amazonaws.com": "SES",
    "config.amazonaws.com": "AWS Config rule",
    "connect.amazonaws.com": "Amazon Connect",
    "codecommit.amazonaws.com": "CodeCommit",
}
_STREAM_KINDS = {  # event source mapping: the source ARN's service -> what it is
    "sqs": "SQS queue",
    "kinesis": "Kinesis stream",
    "dynamodb": "DynamoDB stream",
    "kafka": "Kafka cluster",
    "mq": "MQ broker",
    "rds": "DocumentDB stream",
}
_INVOKE_ACTIONS = ("lambda:invokefunction", "lambda:invokefunctionurl", "lambda:invoke*", "lambda:*", "*")


def masked_config(config: dict[str, Any]) -> dict[str, Any]:
    """A copy of a function's configuration with environment variable values replaced by '(hidden)', so it can be
    shown or shared: the values often hold passwords and keys."""
    shown = json.loads(json.dumps(config, default=str))
    shown.pop("ResponseMetadata", None)
    variables = (shown.get("Environment") or {}).get("Variables")
    if isinstance(variables, dict):
        shown["Environment"]["Variables"] = dict.fromkeys(variables, "(hidden)")
    return shown


def _unqualified(arn: str) -> str:
    """'arn:aws:lambda:r:a:function:etl:prod' -> 'arn:aws:lambda:r:a:function:etl'."""
    return ":".join(arn.split(":")[:7]) if arn.startswith("arn:") else arn


def parse_function(config: dict[str, Any], region: str = "") -> Function:
    """One function's configuration (an entry of ListFunctions, or GetFunction's Configuration) -> Function.
    Environment variable values are left out (only their names are kept), and hidden in .raw."""
    arn = config.get("FunctionArn") or ""
    name = config.get("FunctionName") or (arn.split(":")[6] if arn.count(":") >= 6 else "")
    environment = config.get("Environment") or {}
    env_error = environment.get("Error") or {}
    vpc = config.get("VpcConfig") or {}
    logging = config.get("LoggingConfig") or {}
    return Function(
        name=name,
        arn=_unqualified(arn),
        region=region or (arn.split(":")[3] if arn.count(":") >= 6 else ""),
        runtime=config.get("Runtime") or None,
        handler=config.get("Handler") or None,
        description=config.get("Description") or "",
        memory=int(config.get("MemorySize") or 128),
        timeout=int(config.get("Timeout") or 3),
        architecture=(config.get("Architectures") or ["x86_64"])[0],
        package_type=config.get("PackageType") or "Zip",
        code_size=int(config.get("CodeSize") or 0),
        last_modified=_lambda_time(config.get("LastModified")),
        ephemeral_storage=int((config.get("EphemeralStorage") or {}).get("Size") or 512),
        layers=[Layer(layer.get("Arn", ""), layer.get("CodeSize")) for layer in config.get("Layers") or []],
        env_names=sorted(environment.get("Variables") or {}),
        env_error=env_error.get("Message") or env_error.get("ErrorCode") or None,
        role=config.get("Role") or None,
        vpc_id=vpc.get("VpcId") or None,
        subnets=list(vpc.get("SubnetIds") or []),
        security_groups=list(vpc.get("SecurityGroupIds") or []),
        tracing=(config.get("TracingConfig") or {}).get("Mode"),
        snapstart=(config.get("SnapStart") or {}).get("ApplyOn") == "PublishedVersions",
        state=config.get("State"),
        state_reason=config.get("StateReason"),
        last_update=config.get("LastUpdateStatus"),
        last_update_reason=config.get("LastUpdateStatusReason"),
        log_group=logging.get("LogGroup") or f"/aws/lambda/{name}",
        log_format=logging.get("LogFormat"),
        log_level=logging.get("ApplicationLogLevel"),
        dead_letter=(config.get("DeadLetterConfig") or {}).get("TargetArn"),
        kms_key=config.get("KMSKeyArn"),
        file_systems=[
            f"{fs.get('Arn', '?').split('/')[-1]} at {fs.get('LocalMountPath', '?')}"
            for fs in config.get("FileSystemConfigs") or []
        ],
        version=config.get("Version") or "$LATEST",
        raw=masked_config(config),
    )


def runtime_status(runtime: str | None, *, package_type: str = "Zip", today: date | None = None) -> RuntimeStatus:
    """Where a runtime stands in AWS's support schedule (RUNTIMES) on `today`: supported, ending within
    ENDING_SOON_DAYS, deprecated (no more patches), or blocked (functions on it can't be updated)."""
    if not runtime:
        return RuntimeStatus(None, "image" if package_type == "Image" else "unknown")
    upgrade = LATEST_RUNTIMES.get(runtime_family(runtime) or "")
    upgrade = upgrade if upgrade != runtime else None
    dates = RUNTIMES.get(runtime)
    if dates is None:
        return RuntimeStatus(runtime, "unknown", upgrade=upgrade)
    deprecated, block_create, block_update = (_day(d) for d in dates)
    today = today or _utcnow().date()
    days_left = (deprecated - today).days if deprecated else None
    if block_update and today >= block_update:
        state = "blocked"
    elif deprecated and today >= deprecated:
        state = "deprecated"
    elif days_left is not None and days_left <= ENDING_SOON_DAYS:
        state = "ending"
    else:
        state = "supported"
    return RuntimeStatus(runtime, state, deprecated, block_create, block_update, days_left, upgrade)


def _policy_condition(conditions: Any, key: str) -> str | None:
    """The first value a policy statement's Condition gives `key` (any operator, keys compared without case)."""
    if not isinstance(conditions, dict):
        return None
    for block in conditions.values():
        if not isinstance(block, dict):
            continue
        for name, value in block.items():
            if name.lower() == key.lower():
                values = value if isinstance(value, list) else [value]
                return str(values[0]) if values else None
    return None


def _source_name(arn: str | None) -> tuple[str, str]:
    """(name, detail) from a trigger's source ARN: a bucket, queue, topic, rule, stream, API (with its route) or
    log group."""
    if not arn:
        return "", ""
    parts = arn.split(":", 5)
    if len(parts) < 6:
        return arn, ""
    service, resource = parts[2], parts[5]
    if service == "execute-api":  # a1b2c3/prod/GET/orders
        api, _, route = resource.partition("/")
        stage, _, rest = route.partition("/")
        method, _, path = rest.partition("/")
        if not route:
            return api, ""
        stage_text = "any stage" if stage in ("*", "") else f"stage {stage}"
        call = f"{'any method' if method in ('*', '') else method} /{path}".rstrip("/") if rest else ""
        return api, ", ".join(filter(None, [stage_text, call.replace("/*", "/…") if call else ""]))
    if service == "logs" and resource.startswith("log-group:"):
        return resource.split(":")[1], ""
    if service == "dynamodb" and resource.startswith("table/"):
        return resource.split("/")[1], ""
    if service == "kafka" and resource.startswith("cluster/"):
        return resource.split("/")[1], ""
    if service == "mq":
        return resource.split(":")[1] if resource.startswith("broker:") else resource, ""
    if service == "rds":
        return resource.split(":")[-1], ""
    return resource.rstrip("/").split("/")[-1] or resource, ""


def parse_policy(policy: dict[str, Any] | str | None) -> list[Trigger]:
    """A function's resource-based policy (GetPolicy) as the callers it allows: services (S3, EventBridge, API
    Gateway...) with the bucket, rule or API they're limited to, other accounts, and anyone (public)."""
    if not policy:
        return []
    document = json.loads(policy) if isinstance(policy, str) else policy
    statements = document.get("Statement") or []
    statements = [statements] if isinstance(statements, dict) else statements
    triggers: list[Trigger] = []
    for st in statements:
        if not isinstance(st, dict) or st.get("Effect", "Allow") != "Allow":
            continue
        actions = st.get("Action") or []
        actions = [actions] if isinstance(actions, str) else actions
        actions = [str(a).lower() for a in actions]
        if not any(a in _INVOKE_ACTIONS for a in actions):
            continue
        conditions = st.get("Condition") or {}
        source_arn = _policy_condition(conditions, "AWS:SourceArn")
        source_account = _policy_condition(conditions, "AWS:SourceAccount")
        org = _policy_condition(conditions, "aws:PrincipalOrgID")
        token = _policy_condition(conditions, "lambda:EventSourceToken")  # Alexa skills
        auth_type = _policy_condition(conditions, "lambda:FunctionUrlAuthType")
        via_url = _policy_condition(conditions, "lambda:InvokedViaFunctionUrl")
        sid = st.get("Sid")
        principal = st.get("Principal")
        if isinstance(principal, dict):
            services = principal.get("Service") or []
            services = [services] if isinstance(services, str) else list(services)
            accounts = principal.get("AWS") or []
            accounts = [accounts] if isinstance(accounts, str) else list(accounts)
        else:
            services, accounts = [], [principal] if principal else []
        if "*" in accounts:
            if auth_type or via_url or all(a == "lambda:invokefunctionurl" for a in actions):
                public = (auth_type or "").upper() == "NONE" or (via_url or "").lower() == "true"
                detail = "no sign-in (auth type NONE)" if public else "callers sign with IAM (auth type AWS_IAM)"
                triggers.append(Trigger("Function URL", "", via="function URL", detail=detail, public=public,
                                        statement=sid, principal="*"))
            elif source_arn or source_account or org:
                kind = _SERVICE_KINDS.get(f"{source_arn.split(':')[2]}.amazonaws.com", "AWS service") if (
                    source_arn and source_arn.count(":") >= 5) else ("AWS organization" if org else "AWS account")
                name, detail = _source_name(source_arn)
                triggers.append(Trigger(kind, name or org or source_account or "", arn=source_arn,
                                        via="resource policy", detail=detail, statement=sid, principal="*"))
            elif conditions:  # limited some other way (a VPC endpoint, an IP range...): not open to everyone
                keys = sorted({k for block in conditions.values() if isinstance(block, dict) for k in block})
                triggers.append(Trigger("Any caller meeting its conditions", "", via="resource policy",
                                        statement=sid, principal="*", detail="when " + ", ".join(keys)))
            else:
                triggers.append(Trigger("Anyone", "any AWS account", via="resource policy", public=True,
                                        statement=sid, principal="*",
                                        detail="Principal \"*\" with no condition"))
        for service in services:
            name, detail = _source_name(source_arn)
            kind = _SERVICE_KINDS.get(service, service.split(".")[0])
            triggers.append(Trigger(kind, name or (f"account {source_account}" if source_account else ""),
                                    arn=source_arn, via="resource policy", detail=detail, statement=sid,
                                    principal=service, unscoped=not (source_arn or source_account or token or org)))
        for account in accounts:
            if account == "*":
                continue
            who = account.split(":")[4] if account.startswith("arn:") and account.count(":") >= 5 else account
            role = account.split("/")[-1] if ":role/" in account or ":user/" in account else ""
            triggers.append(Trigger("AWS account", who, via="resource policy", detail=role and f"only {role}",
                                    statement=sid, principal=account))
    unique: dict[tuple[Any, ...], Trigger] = {}
    for t in triggers:  # a public URL takes two statements (InvokeFunctionUrl and InvokeFunction): show it once
        unique.setdefault((t.kind, t.source, t.detail, t.public), t)
    return list(unique.values())


def parse_event_source_mapping(mapping: dict[str, Any]) -> Trigger:
    """One event source mapping (ListEventSourceMappings): the queue or stream Lambda polls for the function."""
    arn = mapping.get("EventSourceArn")
    service = arn.split(":")[2] if arn and arn.count(":") >= 5 else ""
    kind = _STREAM_KINDS.get(service, service or "event source")
    name, _ = _source_name(arn)
    if not arn and mapping.get("SelfManagedEventSource"):
        kind = "Kafka (self-managed)"
        servers = (mapping["SelfManagedEventSource"].get("Endpoints") or {}).get("KAFKA_BOOTSTRAP_SERVERS") or []
        name = servers[0] if servers else ""
    if mapping.get("Topics"):
        name = f"{name} ({', '.join(mapping['Topics'])})" if name else ", ".join(mapping["Topics"])
    parts = []
    if mapping.get("BatchSize"):
        parts.append(f"batches of up to {mapping['BatchSize']:,}")
    if mapping.get("MaximumBatchingWindowInSeconds"):
        parts.append(f"waits up to {mapping['MaximumBatchingWindowInSeconds']} s to fill one")
    filters = (mapping.get("FilterCriteria") or {}).get("Filters") or []
    if filters:
        parts.append(_plural(len(filters), "filter"))
    most = (mapping.get("ScalingConfig") or {}).get("MaximumConcurrency")
    if most:
        parts.append(f"at most {most} at once")
    failure = ((mapping.get("DestinationConfig") or {}).get("OnFailure") or {}).get("Destination")
    if failure:
        parts.append(f"failed batches go to {_source_name(failure)[0]}")
    return Trigger(kind, name, arn=arn, via="event source mapping", state=mapping.get("State"),
                   detail=", ".join(parts), last_result=mapping.get("LastProcessingResult"),
                   uuid=mapping.get("UUID"))


def _window(seconds: float) -> str:
    """How long a window is, for 'in the last ...': 900 -> '15 minutes', 3600 -> 'hour', 86400 -> '24 hours',
    2592000 -> '30 days'."""
    minutes = round(seconds / 60)
    if minutes < 60:
        return _plural(max(minutes, 1), "minute")
    hours = round(seconds / 3600)
    if hours == 1:
        return "hour"
    if hours <= 48:
        return _plural(hours, "hour")
    return _plural(round(seconds / 86400), "day")


def _cli(fn: Function) -> str:
    """The options that name a function in an AWS CLI command, region included so it works from any terminal."""
    return f"--function-name {fn.name}" + (f" --region {fn.region}" if fn.region else "")


def _region_flag(fn: Function) -> str:
    return f" --region {fn.region}" if fn.region else ""


def _reason(text: str | None) -> str:
    return f" ({text.rstrip('.')})" if text else ""


def provisioned_monthly_cost(fn: Function, copies: int, prices: dict[str, float] | None = None) -> float:
    """What keeping `copies` copies of the function ready (provisioned concurrency) costs a month, used or not."""
    prices = {**LAMBDA_PRICES, **(prices or {})}
    return copies * fn.gb * HOURS_PER_MONTH * 3600 * prices["provisioned_arm" if fn.arm else "provisioned"]


def log_storage_monthly_cost(group: LogGroup | None, prices: dict[str, float] | None = None) -> float | None:
    """What keeping a log group's logs costs a month (CloudWatch Logs storage), at its size now."""
    if group is None or group.stored_bytes is None:
        return None
    prices = {**LAMBDA_PRICES, **(prices or {})}
    return group.stored_bytes / GB * prices["log_storage"]


def function_monthly_cost(
    fn: Function,
    metrics: FunctionMetrics | None = None,
    provisioned: Iterable[ProvisionedConcurrency] = (),
    log_group: LogGroup | None = None,
    *,
    prices: dict[str, float] | None = None,
) -> dict[str, float]:
    """Estimated USD a month, by part: 'requests', 'compute' (memory x run time), 'storage' (/tmp above 512 MB),
    'logs' (what it sends to CloudWatch Logs), 'log_storage' (what the log group keeps) and 'provisioned'
    (copies kept ready). Usage parts scale what CloudWatch counted over its window (metrics) to a month; a part
    without the numbers it needs is left out. Before the free tier; compute that runs on provisioned concurrency is
    billed a little less than this."""
    prices = {**LAMBDA_PRICES, **(prices or {})}
    cost: dict[str, float] = {}
    if metrics is not None and metrics.days:
        per_month = DAYS_PER_MONTH / metrics.days
        seconds = metrics.duration_sum / 1000 * per_month
        cost["requests"] = metrics.invocations * per_month * prices["request"] / 1e6
        cost["compute"] = seconds * fn.gb * prices["gb_second_arm" if fn.arm else "gb_second"]
        if fn.ephemeral_storage > 512:
            cost["storage"] = seconds * (fn.ephemeral_storage - 512) / 1024 * prices["ephemeral_storage"]
        if metrics.log_bytes is not None:
            cost["logs"] = metrics.log_bytes / GB * per_month * prices["log_ingestion"]
    stored = log_storage_monthly_cost(log_group, prices)
    if stored is not None:
        cost["log_storage"] = stored
    copies = sum(p.billed for p in provisioned)
    if copies:
        cost["provisioned"] = provisioned_monthly_cost(fn, copies, prices)
    return cost


def _total(cost: dict[str, float | None]) -> float | None:
    """The sum of a cost breakdown, or None when no part of it is known."""
    known = [v for v in cost.values() if v is not None]
    return sum(known) if known else None


_SECRET_NAME_RE = re.compile(r"PASSWORD|PASSWD|SECRET|TOKEN|API_?KEY|PRIVATE_?KEY|ACCESS_?KEY|CREDENTIAL", re.I)
_POINTER_NAME_RE = re.compile(
    r"_(ARN|NAME|ID|PATH|URL|URI|PARAM|PARAMETER|REGION|TTL|HEADER|FILE|LENGTH|EXPIRY|SECONDS|ENDPOINT|TYPE)$", re.I
)


def secret_like(names: Iterable[str]) -> list[str]:
    """Environment variable names that look like they hold a secret (DB_PASSWORD, API_KEY), leaving out the ones
    that point at a secret instead (DB_SECRET_ARN, TOKEN_PARAMETER_NAME)."""
    return [n for n in names if _SECRET_NAME_RE.search(n) and not _POINTER_NAME_RE.search(n)]


def _next_memory(memory: int) -> int:
    return next((step for step in MEMORY_STEPS if step > memory), min(memory * 2, 10240))


def _next_timeout(timeout: int) -> int:
    return min(MAX_TIMEOUT, max(timeout * 2, timeout + 3))


def _nice_timeout(longest_ms: float) -> int:
    """A timeout with room above the longest run seen: about 3x it, rounded to a round number of seconds."""
    want = longest_ms / 1000 * 3
    return next((s for s in (3, 5, 10, 15, 30, 60, 120, 300, 600, MAX_TIMEOUT) if s >= want), MAX_TIMEOUT)


def _runtime_findings(fn: Function, status: RuntimeStatus) -> list[tuple[str, str]]:
    if status.state not in ("deprecated", "blocked", "ending"):
        return []
    target = status.upgrade or "a supported runtime"
    move = (
        f"test the code on it, then aws lambda update-function-configuration {_cli(fn)} --runtime {status.upgrade}"
        if status.upgrade else "test the code on it, then change the runtime"
    )
    if status.state == "blocked":
        return [("warn", (
            f"{fn.runtime} lost support on {status.deprecated}, and since {status.block_update} Lambda blocks "
            f"updates to functions on it: it keeps running, without security patches, and can't be changed. Move "
            f"it to {target}, as a new function if the update is refused."
        ))]
    if status.state == "deprecated":
        blocked = f", and from {status.block_update} Lambda blocks updates to it" if status.block_update else ""
        return [("warn", (
            f"{fn.runtime} reached end of support on {status.deprecated}: AWS no longer patches it{blocked}. "
            f"Move it to {target}: {move}."
        ))]
    blocked = f", and from {status.block_update} Lambda blocks updates" if status.block_update else ""
    return [("warn", (
        f"{fn.runtime} reaches end of support on {status.deprecated} ({_in_days(status.days_left or 0)}): after "
        f"that AWS stops patching it{blocked}. Plan the move to {target}: {move}."
    ))]


def _day_age(moment: datetime | None, now: datetime) -> str:
    """'today', 'yesterday' or '5d ago', by UTC calendar day."""
    if moment is None:
        return "-"
    days = (now.astimezone(timezone.utc).date() - moment.astimezone(timezone.utc).date()).days
    return "today" if days <= 0 else "yesterday" if days == 1 else f"{days}d ago"


def _last_seen(moment: datetime | None, metrics: FunctionMetrics, now: datetime) -> str:
    """', the last today' / ', the last 3d ago' (daily numbers) or ', the last 2h ago' (hourly)."""
    if moment is None:
        return ""
    return f", the last {_day_age(moment, now) if metrics.period >= 86400 else human_age(moment, now)}"


RECENT_DAYS = 3  # how far back "lately" reaches when daily numbers are checked for errors on the rise


def _recent(metrics: FunctionMetrics, now: datetime) -> tuple[float, float] | None:
    """(calls, errors) over the last RECENT_DAYS days of daily numbers; None for hourly numbers or short windows."""
    if metrics.period < 86400 or metrics.days < 2 * RECENT_DAYS:
        return None
    start = _midnight(now) - timedelta(days=RECENT_DAYS - 1)
    rows = [d for d in metrics.daily if d.start >= start]
    return sum(d.invocations for d in rows), sum(d.errors for d in rows)


def _errors_level(metrics: FunctionMetrics | None, now: datetime) -> str | None:
    """'warn' when a function's failed calls are worth acting on (at least 10, and 1% of its calls over the window
    or over the last few days, the last within a week), 'info' when they stopped earlier or are many (100) but a
    small share, None when there are too few to mention."""
    if metrics is None or not metrics.invocations or metrics.errors < 1:
        return None
    recent = _recent(metrics, now)
    high = metrics.errors >= 10 and metrics.errors / metrics.invocations >= 0.01
    if recent and recent[0] and recent[1] >= 10 and recent[1] / recent[0] >= 0.01:
        high = True
    if not high:
        return "info" if metrics.errors >= 100 else None
    lately = metrics.last_error is not None and now - metrics.last_error <= timedelta(days=7)
    return "warn" if lately else "info"


def _metrics_window(metrics: FunctionMetrics) -> str:
    """'30 days' for daily numbers (whole UTC days, today's part included), '24 hours' for hourly ones."""
    days = math.ceil(metrics.days - 1e-6) if metrics.period >= 86400 else metrics.days
    return _window(days * 86400)


def _times(count: int) -> str:
    return "once" if count == 1 else "twice" if count == 2 else f"{count:,} times"


def _longer(fn: Function) -> str:
    """What to do about runs that need more time than the timeout gives them."""
    if fn.timeout >= MAX_TIMEOUT:
        return (f"it's already at the most Lambda allows ({MAX_TIMEOUT} s), so split the work into smaller pieces, "
                "or run it somewhere without the limit (Step Functions, ECS or Batch)")
    return f"aws lambda update-function-configuration {_cli(fn)} --timeout {_next_timeout(fn.timeout)}"


def _usage_findings(
    fn: Function, metrics: FunctionMetrics, detail: FunctionDetail, prices: dict[str, float], now: datetime
) -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    window = _metrics_window(metrics)
    name, cli = fn.name, _cli(fn)

    def last(moment: datetime | None) -> str:
        return _last_seen(moment, metrics, now)

    if not metrics.invocations:
        if not detail.provisioned:  # provisioned concurrency has its own finding, with the cost
            unused = not detail.triggers and not {"policy", "triggers"} & set(detail.errors)
            found.append(("info", (
                f"It wasn't called in the last {window}"
                + (
                    ", and nothing triggers it (no event source mapping or resource policy): it may be unused, or "
                    f"only called directly. If it's no longer needed, keep its code (code('{name}')), then delete "
                    f"it: aws lambda delete-function {cli}."
                    if unused else "."
                )
            )))
        return found
    errors, calls = metrics.errors, metrics.invocations
    rate = errors / calls
    level = _errors_level(metrics, now)
    if level:
        recent = _recent(metrics, now)
        rising = ""
        if recent and recent[0] and recent[1] >= 10 and recent[1] / recent[0] >= max(0.01, 2 * rate):
            rising = (f", and rising: {_pct(recent[1] / recent[0])} of calls failed in the last {RECENT_DAYS} days "
                      f"({_count(round(recent[1]))} of {_count(round(recent[0]))})")
        found.append((level, (
            f"{_count(round(errors))} of {_count(round(calls))} calls failed ({_pct(rate)}) in the last {window}"
            f"{last(metrics.last_error)}{rising}: errors('{name}') groups them by cause."
        )))
    if metrics.throttles >= 1:
        found.append(("warn", (
            f"{_count(round(metrics.throttles))} calls were throttled in the last {window}"
            f"{last(metrics.last_throttle)}: more arrived than it may run at once (its reserved concurrency, or the "
            f"region's limit), so they were turned away or retried later. function_info('{name}') shows its "
            "concurrency."
        )))
    if metrics.duration_max and fn.timeout:
        share = metrics.duration_max / (fn.timeout * 1000)
        if share >= 0.9:
            found.append(("warn", (
                f"Its longest run took {human_ms(metrics.duration_max)} of its {fn.timeout} s timeout: runs that "
                f"need longer are cut off and fail. performance('{name}') shows how close they come; if they need "
                f"more time: {_longer(fn)}."
            )))
        elif fn.timeout >= 300 and calls >= 20 and share <= 0.1:
            suggested = _nice_timeout(metrics.duration_max)
            found.append(("info", (
                f"Its timeout is {fn.timeout} s, but its longest run took {human_ms(metrics.duration_max)}: a call "
                f"that hangs keeps running, and billing, for up to {fn.timeout} s. A timeout nearer its runs stops "
                f"that sooner: aws lambda update-function-configuration {cli} --timeout {suggested}."
            )))
    cost = function_monthly_cost(fn, metrics, prices=prices)
    compute = cost.get("compute") or 0.0
    saving = compute * (1 - prices["gb_second_arm"] / prices["gb_second"])
    if not fn.arm and fn.package_type == "Zip" and saving >= 1:
        found.append(("info", (
            f"It runs on x86_64; on arm64 (Graviton) its compute would cost about {human_money(saving)}/month less, "
            "and it often runs faster. Its code and layers must have no x86-only binaries: build them for arm64, "
            f"then aws lambda update-function-code {cli} --architectures arm64 --zip-file fileb://function.zip."
        )))
    if fn.memory <= 128 and (metrics.avg_duration or 0) >= 1000:
        found.append(("info", (
            f"It has 128 MB, which also means the least CPU, and its runs average {human_ms(metrics.avg_duration)}. "
            f"More memory often makes it faster for about the same cost: performance('{name}') shows what it uses."
        )))
    logs = cost.get("logs")
    if logs is not None and logs >= 1 and logs > compute:
        found.append(("info", (
            f"It logs {human_size(metrics.log_bytes)} every {window} ({human_money(logs)}/month at "
            f"{human_money(prices['log_ingestion'])} per GB), more than its compute costs ({human_money(compute)}/month). Log "
            "less, or switch to JSON logs and raise the log level (console: Configuration, Monitoring and operations "
            "tools)."
        )))
    return found


def function_findings(
    fn: Function,
    metrics: FunctionMetrics | None = None,
    detail: FunctionDetail | None = None,
    *,
    prices: dict[str, float] | None = None,
    now: datetime | None = None,
) -> list[tuple[str, str]]:
    """What's wrong with a function, or worth knowing: (level, message) pairs, each with the next step. Works on
    what you have: the configuration alone, plus CloudWatch's numbers (metrics), plus the rest of describe()'s
    answer (detail: triggers, provisioned concurrency, the log group, versions...)."""
    prices = {**LAMBDA_PRICES, **(prices or {})}
    now = now or _utcnow()
    detail = detail or FunctionDetail(fn)
    metrics = metrics or detail.metrics
    name, cli = fn.name, _cli(fn)
    found: list[tuple[str, str]] = []
    if fn.state == "Failed":
        found.append(("warn", (
            f"It's in the Failed state{_reason(fn.state_reason)}: every call fails until that's fixed. Fix the "
            "cause, then deploy it again."
        )))
    elif fn.state == "Inactive":
        found.append(("info", (
            f"It's Inactive{_reason(fn.state_reason)}: it went unused for weeks, so Lambda released what it had "
            "ready. The next call waits while it starts again."
        )))
    if fn.last_update == "Failed":
        found.append(("warn", (
            f"Its last update failed{_reason(fn.last_update_reason)}: it still runs the code and settings from "
            "before. Fix the cause and deploy again."
        )))
    if fn.reserved_concurrency == 0:
        found.append(("warn", (
            "Its reserved concurrency is 0, so every call is throttled: the function is switched off. To switch it "
            f"back on: aws lambda delete-function-concurrency {cli}."
        )))
    if fn.env_error:
        found.append(("warn", (
            f"Lambda can't decrypt its environment variables ({fn.env_error.rstrip('.')}): calls fail until its "
            "KMS key can be used again."
        )))
    found += _runtime_findings(fn, runtime_status(fn.runtime, package_type=fn.package_type, today=now.date()))
    if metrics is not None:
        found += _usage_findings(fn, metrics, detail, prices, now)
    window = _metrics_window(metrics) if metrics else ""
    for pc in detail.provisioned:
        copies = pc.billed or pc.requested
        money = human_money(provisioned_monthly_cost(fn, copies, prices))
        peak = metrics.concurrency_max if metrics else None
        remove = f"aws lambda delete-provisioned-concurrency-config {cli} --qualifier {pc.qualifier}"
        if pc.status == "FAILED":
            found.append(("warn", (
                f"Provisioned concurrency on {pc.qualifier} failed{_reason(pc.reason)}: none of the {pc.requested} "
                f"copies it asks for are ready. Fix the cause, or remove it: {remove}."
            )))
        elif metrics is not None and not metrics.invocations:
            found.append(("warn", (
                f"{pc.qualifier} keeps {copies} copies started and ready (provisioned concurrency), costing {money}"
                f"/month, but the function wasn't called in the last {window}. Remove it: {remove}."
            )))
        elif peak is not None and copies >= 2 and peak < copies / 2:
            lower = max(1, math.ceil(peak * 1.5))
            found.append(("warn", (
                f"{pc.qualifier} keeps {copies} copies ready (provisioned concurrency), costing {money}/month, but "
                f"at most {peak:,.0f} ran at once in the last {window}. Lower it: aws lambda "
                f"put-provisioned-concurrency-config {cli} --qualifier {pc.qualifier} "
                f"--provisioned-concurrent-executions {lower}."
            )))
        else:
            found.append(("info", (
                f"{pc.qualifier} keeps {copies} copies ready (provisioned concurrency): {money}/month whether "
                "they're used or not."
            )))
    for t in detail.triggers:
        if t.public and t.kind == "Function URL":
            found.append(("warn", (
                "Its function URL needs no sign-in (auth type NONE): anyone who has the URL can run it, and you pay "
                f"for every call. If that isn't meant to be: aws lambda update-function-url-config {cli} "
                "--auth-type AWS_IAM."
            )))
        elif t.public:
            found.append(("warn", (
                f"Its resource policy lets anyone invoke it (statement {t.statement or '?'}: Principal \"*\" with no "
                "condition): any AWS account can run it, and you pay for every call. Remove that statement: "
                f"aws lambda remove-permission {cli} --statement-id {t.statement or '<id>'}."
            )))
        elif t.unscoped:
            found.append(("info", (
                f"Its resource policy lets {t.principal} invoke it without naming the {t.kind} (no SourceArn or "
                "SourceAccount condition), so that service may call it for other accounts' resources too. Grant it "
                "again with --source-arn (and --source-account), then remove statement "
                f"{t.statement or '?'}: aws lambda remove-permission {cli} --statement-id {t.statement or '<id>'}."
            )))
        if t.via == "event source mapping":
            if t.last_result and t.last_result.upper().startswith("PROBLEM"):
                found.append(("warn", (
                    f"Its {t.kind} {t.source} reports '{t.last_result}': records from it aren't being processed. "
                    f"errors('{name}') shows why the calls fail."
                )))
            elif (t.state or "").lower() == "disabled":
                found.append(("info", (
                    f"Its trigger from {t.kind} {t.source} is disabled: nothing from it reaches the function, and "
                    "messages wait (or expire). If it should run: aws lambda update-event-source-mapping --uuid "
                    f"{t.uuid} --enabled{_region_flag(fn)}."
                )))
    asynchronous = list(dict.fromkeys(f"{t.kind} {t.source}".strip() for t in detail.triggers if t.asynchronous))
    if asynchronous and detail.async_config is not None and not (detail.async_config.on_failure or fn.dead_letter):
        account = fn.arn.split(":")[4] if fn.arn.count(":") >= 5 else "123456789012"
        queue = f"arn:aws:sqs:{fn.region or 'us-east-1'}:{account}:{fn.name}-failed"
        sources = ", ".join(asynchronous[:3]) + (f" and {len(asynchronous) - 3} more" if len(asynchronous) > 3 else "")
        found.append(("info", (
            f"Events from {sources} that still fail after {detail.async_config.retries} retries are "
            "dropped: nothing keeps them. Send them to a queue to look at later (create it first): aws lambda "
            f"put-function-event-invoke-config {cli} --destination-config "
            f"'{{\"OnFailure\":{{\"Destination\":\"{queue}\"}}}}'."
        )))
    group = detail.log_group
    if group is not None and group.retention_days is None and (group.stored_bytes or 0) >= 100 * MB:
        found.append(("info", (
            f"Its log group {group.name} keeps logs forever: {human_size(group.stored_bytes)} so far, "
            f"{human_money(log_storage_monthly_cost(group, prices))}/month and growing. Keep 30 days of them: aws logs "
            f"put-retention-policy --log-group-name {group.name} --retention-in-days 30{_region_flag(fn)}."
        )))
    secrets = secret_like(fn.env_names)
    if secrets:
        found.append(("info", (
            (f"Environment variables {', '.join(secrets)} look like secrets" if len(secrets) > 1
             else f"Environment variable {secrets[0]} looks like a secret")
            + ": anyone allowed lambda:GetFunction can read the values. Keep secrets in Secrets Manager or Parameter "
            "Store, and read them when the function starts."
        )))
    if detail.runtime_updates == "Manual":
        pinned = detail.runtime_version.split(":")[-1][:12] if detail.runtime_version else "one version"
        found.append(("info", (
            f"Runtime updates are Manual: it stays on runtime version {pinned}, so it doesn't get AWS's security "
            f"patches. Let Lambda update it: aws lambda put-runtime-management-config {cli} --update-runtime-on Auto."
        )))
    if detail.versions:
        stored = sum(v.code_size for v in detail.versions)
        if len(detail.versions) >= 50 or stored >= GB:
            found.append(("info", (
                f"It has {len(detail.versions):,} published versions holding {human_size(stored)} of code, which "
                "counts against the region's code storage. Delete the ones no alias points to: aws lambda "
                f"delete-function {cli} --qualifier <version>."
            )))
    return found


def account_findings(limits: AccountLimits, days: float = 30) -> list[tuple[str, str]]:
    """A region's limits that are close to running out: code storage, and how many runs may happen at once."""
    found: list[tuple[str, str]] = []
    region = limits.region
    if limits.code_storage is not None and limits.code_storage_limit:
        share = limits.code_storage / limits.code_storage_limit
        if share >= 0.8:
            found.append(("warn", (
                f"Lambda code in {region} takes {human_size(limits.code_storage)} of its "
                f"{human_size(limits.code_storage_limit)} limit ({_pct(share)}): deploys fail once it's full. Old "
                "published versions usually hold most of it (function_info() lists a function's versions); Service "
                "Quotas can raise the limit."
            )))
    if limits.concurrency and limits.peak_concurrency is not None:
        share = limits.peak_concurrency / limits.concurrency
        if share >= 0.8:
            found.append(("warn", (
                f"Up to {limits.peak_concurrency:,.0f} runs happened at once in {region} in the last "
                f"{_window(days * 86400)}, of the {limits.concurrency:,} the region allows: past that, calls are "
                "throttled. Ask for more in Service Quotas (AWS Lambda, Concurrent executions)."
            )))
    if limits.concurrency and limits.unreserved is not None and limits.unreserved < min(100, limits.concurrency):
        found.append(("warn", (
            f"Reserved concurrency leaves {limits.unreserved:,} of {region}'s {limits.concurrency:,} concurrent runs "
            "for every other function, so they're throttled sooner. function_info() shows each function's reserved "
            "concurrency."
        )))
    return found


# ------------------------------------------------------------------ log lines

_REQUEST_ID_RE = re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b")
_TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?Z?$")
_TIMED_OUT_RE = re.compile(r"Task timed out after ([\d.]+) seconds")
_ERROR_TYPE_RE = re.compile(
    r"^((?:[A-Za-z_]\w*\.)*(?:[A-Za-z_]\w*?(?:Error|Exception|Exit|Fault|Failure|Timeout|Interrupt)|Error|Exception)"
    r"|Runtime\.\w+)\s*(?::\s*(.*))?$",
    re.S,
)
_LEVEL_RE = re.compile(r"^\[(ERROR|CRITICAL|FATAL)\]\s*(.*)$", re.S)
_REPORT_RE = re.compile(r"^REPORT RequestId:\s*(\S+)")
_REPORT_FIELD_RE = re.compile(
    r"(Billed Restore Duration|Restore Duration|Billed Duration|Init Duration|Duration|Memory Size|Max Memory Used): "
    r"([\d.]+)"
)
_LIFECYCLE_RE = re.compile(r"^(START|END) RequestId:")
_ACCESS_DENIED_RE = re.compile(r"AccessDenied|not authorized to perform|UnauthorizedOperation|Forbidden", re.I)
_DENIED_ACTION_RE = re.compile(r"not authorized to perform:?\s*([\w-]+:[\w*]+)(?:\s+on resource:?\s*([^\s,;]+))?")
_THROTTLED_RE = re.compile(r"Throttl|TooManyRequests|Rate exceeded|SlowDown|ProvisionedThroughputExceeded")
_CONNECT_RE = re.compile(r"ConnectTimeout|EndpointConnectionError|ETIMEDOUT|ECONNREFUSED|Connect timeout|timed out")
_LOAD_ERRORS = ("Runtime.ImportModuleError", "Runtime.HandlerNotFound", "Runtime.UserCodeSyntaxError")


def request_id_of(message: str) -> str | None:
    """The request ID a log line belongs to (Lambda writes it in each line it adds), or None."""
    if message.lstrip().startswith("{"):
        try:
            doc = json.loads(message)
        except ValueError:
            doc = None
        if isinstance(doc, dict):
            record = doc.get("record") if isinstance(doc.get("record"), dict) else {}
            found = doc.get("requestId") or doc.get("AWSRequestId") or record.get("requestId")
            if found:
                return str(found)
    match = _REQUEST_ID_RE.search(message)
    return match.group(0) if match else None


def _typed(text: str) -> tuple[str, str] | None:
    """'KeyError: 'x'' -> ('KeyError', "KeyError: 'x'"); None when the line doesn't start with an error type."""
    match = _ERROR_TYPE_RE.match(text.strip())
    if not match:
        return None
    return match.group(1).split(".")[-1] if not match.group(1).startswith("Runtime.") else match.group(1), text.strip()


def _json_error(text: str) -> tuple[str, str] | None:
    """An error in a JSON log line: Lambda's own (errorType / errorMessage) or a record at level ERROR."""
    try:
        doc = json.loads(text)
    except ValueError:
        return None
    if not isinstance(doc, dict):
        return None
    record = doc.get("record") if isinstance(doc.get("record"), dict) else {}
    if str(doc.get("type", "")).startswith("platform."):
        status = record.get("status")
        if doc.get("type") in ("platform.runtimeDone", "platform.initRuntimeDone") and status == "timeout":
            return "Timeout", "Task timed out"
        if status in ("error", "failure") and record.get("errorType"):
            kind = "Out of memory" if str(record["errorType"]).endswith("OutOfMemory") else str(record["errorType"])
            return kind, str(record["errorType"])
        return None
    inner = doc.get("message")
    payload = inner if isinstance(inner, dict) else doc
    error_type = payload.get("errorType") or doc.get("errorType")
    error_message = payload.get("errorMessage") or doc.get("errorMessage")
    if error_type:
        text = f"{error_type}: {error_message}" if error_message else str(error_type)
        kind = str(error_type)
        return (kind if kind.startswith("Runtime.") else kind.split(".")[-1]), text.splitlines()[0][:500]
    if str(doc.get("level", "")).upper() in ("ERROR", "CRITICAL", "FATAL"):
        message = inner if isinstance(inner, str) else json.dumps(inner)
        return "Logged error", (message or "").splitlines()[0][:500] if message else "Logged error"
    return None


def classify_error(message: str) -> tuple[str, str] | None:
    """(kind, summary) for a log line that reports an error, or None for any other line. Kinds: 'Timeout',
    'Out of memory', 'Runtime exited', an exception type ('KeyError', 'Runtime.ImportModuleError', 'TypeError',
    ...) or 'Logged error' (an ERROR line the code wrote). REPORT lines are skipped: they repeat what the line
    before them said."""
    text = message.strip()
    if not text or text.startswith(("REPORT RequestId", "INIT_REPORT", "START RequestId", "END RequestId")):
        return None
    if text.startswith("{"):
        return _json_error(text)
    if match := _TIMED_OUT_RE.search(text):
        return "Timeout", f"Task timed out after {match.group(1)} seconds"
    if "Runtime.OutOfMemory" in text or "signal: killed" in text:
        return "Out of memory", "Runtime exited: out of memory (signal: killed)"
    if "Runtime exited with error" in text or text.startswith("Runtime.ExitError"):
        detail = re.search(r"Runtime exited with error: ([^\n]+)", text)
        return "Runtime exited", f"Runtime exited with error: {detail.group(1).strip()}" if detail else "Runtime exited"
    if match := _LEVEL_RE.match(text):  # Python: "[ERROR] KeyError: 'x'" or "[ERROR]\t<time>\t<request>\tmessage"
        rest = match.group(2)
        parts = rest.split("\t")
        if len(parts) >= 3 and _TIMESTAMP_RE.match(parts[0].strip()):
            rest = "\t".join(parts[2:])
        first = (rest.strip().splitlines() or [""])[0].strip()
        return _typed(first) or ("Logged error", first[:500] or match.group(1))
    parts = text.split("\t")  # Node.js and others: "<time>\t<request>\tERROR\tmessage"
    if len(parts) >= 4 and _TIMESTAMP_RE.match(parts[0].strip()) and parts[2].strip() in ("ERROR", "FATAL"):
        body = "\t".join(parts[3:]).strip()
        start = body.find("{")
        if start >= 0:
            found = _json_error(body[start:])
            if found:
                return found
        first = (body.splitlines() or [""])[0].strip()
        return _typed(first) or ("Logged error", first[:500])
    if text.startswith("Traceback (most recent call last)"):
        last = text.splitlines()[-1].strip()
        return _typed(last) or ("Logged error", last[:500])
    if re.match(r"^(ERROR|FATAL|CRITICAL)\b", text) or text.startswith(("panic: ", "Exception in thread")):
        first = text.splitlines()[0].strip()
        return ("panic" if first.startswith("panic: ") else "Logged error"), first[:500]
    return None


def _error_key(text: str) -> str:
    """A message with what changes between occurrences blanked out (IDs, numbers), so repeats group together."""
    text = _REQUEST_ID_RE.sub("<id>", text)
    text = re.sub(r"\b[0-9a-f]{12,}\b", "<id>", text)
    return re.sub(r"\d+(?:\.\d+)?", "N", text)[:300]


def group_errors(events: Iterable[LogEvent]) -> list[ErrorGroup]:
    """Log events that report errors, grouped by cause (kind and message, numbers and IDs aside), most frequent
    first. Lines that aren't errors are skipped."""
    groups: dict[tuple[str, str], ErrorGroup] = {}
    seen: dict[tuple[str, str], list[tuple[datetime, str]]] = defaultdict(list)
    for event in events:
        found = classify_error(event.message)
        if not found:
            continue
        kind, summary = found
        key = (kind, _error_key(summary))
        group = groups.get(key)
        if group is None:
            group = groups[key] = ErrorGroup(kind, summary, 0, event.time, event.time, event.message.strip())
        group.count += 1
        group.first = min(group.first, event.time)
        if event.time >= group.last:
            group.last, group.message, group.example = event.time, summary, event.message.strip()
        if event.request_id:
            seen[key].append((event.time, event.request_id))
    for key, group in groups.items():
        ids = [rid for _, rid in sorted(seen[key], reverse=True)]
        group.request_ids = list(dict.fromkeys(ids))[:5]
    return sorted(groups.values(), key=lambda g: (-g.count, -g.last.timestamp()))


def error_findings(report: ErrorReport) -> list[tuple[str, str]]:
    """What the errors in a window mean and what to do: timeouts, out of memory, code that can't load, missing
    permissions, throttled or unreachable services, and the most common error otherwise."""
    fn = report.function
    name, cli = fn.name, _cli(fn)
    found: list[tuple[str, str]] = []
    covered: set[int] = set()
    for i, g in enumerate(report.groups[:8]):  # the most frequent causes; the table has the rest
        see = f" logs('{name}', request_id='{g.request_ids[0]}') shows that whole run." if g.request_ids else ""
        when = f"the last {human_age(g.last, report.until)}"
        them = "it" if g.count == 1 else "them"
        if g.kind == "Timeout":
            found.append(("warn", (
                f"{_plural(g.count, 'run')} timed out at the {fn.timeout} s limit ({when}): Lambda stopped {them} part "
                f"way. If runs need longer: {_longer(fn)}; if they shouldn't take that long, performance('{name}') "
                f"shows the slowest runs.{see}"
            )))
        elif g.kind == "Out of memory":
            found.append(("warn", (
                f"{_plural(g.count, 'run')} ran out of memory at {fn.memory:,} MB ({when}): Lambda killed {them}. Give "
                f"it more: aws lambda update-function-configuration {cli} --memory-size {_next_memory(fn.memory)}; "
                f"performance('{name}') shows what the other runs use.{see}"
            )))
        elif g.kind in _LOAD_ERRORS:
            found.append(("warn", (
                f"Its code can't be loaded ({g.message}): calls fail before any of it runs. Usually a package "
                f"missing from the deployment package or a layer, or a handler setting ({fn.handler or '-'}) that "
                f"doesn't match a file and function. code('{name}') shows what's in the package."
            )))
        elif _ACCESS_DENIED_RE.search(g.example):
            denied = _DENIED_ACTION_RE.search(g.example)
            role = (fn.role or "").split("/")[-1] or "its execution role"
            if denied:
                on = f" on {denied.group(2).rstrip('.')}" if denied.group(2) else ""
                what = f"its role {role} isn't allowed {denied.group(1)}{on}"
            else:
                what = f"{_clip(g.message, 160)}; its role {role} needs the permission the message names"
            found.append(("warn", (
                f"Its code was refused access {_times(g.count)} ({when}): {what}. Add the permission to the role's "
                f"policy in IAM.{see}"
            )))
        elif _THROTTLED_RE.search(g.example):
            found.append(("info", (
                f"Calls it makes to another service were throttled {_times(g.count)} ({when}): "
                f"{_clip(g.message, 160)}. Retry with backoff, or ask that service for a higher limit.{see}"
            )))
        elif fn.vpc_id and _CONNECT_RE.search(g.example):
            found.append(("info", (
                f"It couldn't reach a service {_times(g.count)} ({when}): {_clip(g.message, 160)}. It runs in "
                f"VPC {fn.vpc_id}, which reaches the internet and AWS APIs only through a NAT gateway or VPC "
                f"endpoints.{see}"
            )))
        else:
            continue
        covered.add(i)
    uncovered = [g for i, g in enumerate(report.groups) if i not in covered]
    if uncovered:
        g = uncovered[0]
        see = f" logs('{name}', request_id='{g.request_ids[0]}') shows the whole run." if g.request_ids else ""
        found.append(("warn" if g.kind != "Logged error" else "info", (
            f"The most common {'error' if not covered else 'other error'}, {_clip(g.message, 160)}, happened "
            f"{_times(g.count)} (the last {human_age(g.last, report.until)}).{see}"
        )))
    m = report.metrics
    window = _window((report.until - report.since).total_seconds())
    if m is not None and m.errors >= 1 and not report.groups and "logs" not in report.errors:
        found.append(("info", (
            f"CloudWatch counted {_count(round(m.errors))} failed calls in the last {window}, but no log line looked "
            f"like an error: the code may fail without logging it, or its logging is off. logs('{name}') shows what "
            "it did log."
        )))
    if m is not None and m.throttles >= 1:
        found.append(("warn", (
            f"{_count(round(m.throttles))} calls were throttled in the last {window} (throttled calls never run, so "
            f"they log nothing): more arrived than it may run at once. function_info('{name}') shows its concurrency."
        )))
    return found


def parse_report(message: str, time: datetime | None = None) -> Invocation | None:
    """A REPORT line (or a JSON platform.report record), which Lambda logs at the end of every run, -> Invocation:
    run time, billed time, memory used, and the start-up time of a cold start. None for any other line."""
    text = message.strip()
    if text.startswith("{"):
        try:
            doc = json.loads(text)
        except ValueError:
            return None
        if not isinstance(doc, dict) or doc.get("type") != "platform.report":
            return None
        record = doc.get("record") if isinstance(doc.get("record"), dict) else {}
        numbers = record.get("metrics") if isinstance(record.get("metrics"), dict) else {}
        status = record.get("status")
        return Invocation(
            str(record.get("requestId") or ""),
            _lambda_time(doc.get("time")) or time,
            float(numbers.get("durationMs") or 0),
            float(numbers.get("billedDurationMs") or 0),
            int(numbers.get("memorySizeMB") or 0),
            int(numbers.get("maxMemoryUsedMB") or 0),
            numbers.get("initDurationMs"),
            numbers.get("restoreDurationMs"),
            None if status in (None, "success") else str(status),
            record.get("errorType"),
        )
    match = _REPORT_RE.match(text)
    if not match:
        return None
    values = {key: float(value) for key, value in _REPORT_FIELD_RE.findall(text)}
    status = re.search(r"Status: (\w+)", text)
    error_type = re.search(r"Error Type: ([\w.]+)", text)
    return Invocation(
        match.group(1),
        time,
        values.get("Duration", 0.0),
        values.get("Billed Duration", 0.0),
        int(values.get("Memory Size", 0)),
        int(values.get("Max Memory Used", 0)),
        values.get("Init Duration"),
        values.get("Restore Duration"),
        status.group(1) if status and status.group(1) != "success" else None,
        error_type.group(1) if error_type else None,
    )


def percentile(values: Iterable[float], q: float) -> float | None:
    """The nearest-rank percentile (q from 0 to 100) of the values; None when there are none."""
    ordered = sorted(values)
    if not ordered:
        return None
    return ordered[max(1, math.ceil(q / 100 * len(ordered))) - 1]


def suggest_memory(max_used: float, memory: int, headroom: float = 1.3) -> int | None:
    """The smallest of MEMORY_STEPS that leaves 30% room above the most memory a run used, when that's less than
    the function has; None when what it has is about right."""
    need = max_used * headroom
    step = next((s for s in MEMORY_STEPS if s >= need), None)
    return step if step is not None and step < memory else None


def performance_findings(perf: Performance, prices: dict[str, float] | None = None) -> list[tuple[str, str]]:
    """What the run times and memory say: timeouts, runs close to the timeout or out of memory, memory that's
    much more than the runs use (and what less would save), and slow cold starts."""
    runs = perf.invocations
    if not runs:
        return []
    prices = {**LAMBDA_PRICES, **(prices or {})}
    fn = perf.function
    name, cli = fn.name, _cli(fn)
    found: list[tuple[str, str]] = []
    durations = [r.duration for r in runs]
    memory = max((r.memory for r in runs if r.memory), default=fn.memory)
    max_used = max((r.max_memory for r in runs), default=0)
    timeouts = [r for r in runs if r.status == "timeout"]
    out_of_memory = [r for r in runs if (r.error_type or "").endswith("OutOfMemory")]
    p99 = percentile(durations, 99) or 0.0
    if timeouts:
        found.append(("warn", (
            f"{_plural(len(timeouts), 'run')} of {len(runs):,} timed out at the {fn.timeout} s limit: Lambda stopped "
            f"{'it' if len(timeouts) == 1 else 'them'} part way. If runs need longer: {_longer(fn)}; "
            f"logs('{name}', request_id='{timeouts[0].request_id}') shows one of them."
        )))
    elif fn.timeout and p99 >= 0.8 * fn.timeout * 1000:
        found.append(("warn", (
            f"1 run in 100 takes {human_ms(p99)} or more, close to its {fn.timeout} s timeout: on a slightly slower "
            f"day they fail. Give them room: {_longer(fn)}."
        )))
    if out_of_memory:
        found.append(("warn", (
            f"{_plural(len(out_of_memory), 'run')} ran out of memory at {memory:,} MB: Lambda killed "
            f"{'it' if len(out_of_memory) == 1 else 'them'}. Give it "
            f"more: aws lambda update-function-configuration {cli} --memory-size {_next_memory(memory)}."
        )))
    elif max_used and memory and max_used >= 0.9 * memory:
        found.append(("warn", (
            f"A run used {max_used:,} of its {memory:,} MB: close to the limit, where Lambda kills the run. Give it "
            f"more: aws lambda update-function-configuration {cli} --memory-size {_next_memory(memory)}."
        )))
    elif max_used and memory:
        suggested = suggest_memory(max_used, memory)
        if suggested:  # a quarter of what it has at most per step: less memory is also less CPU
            suggested = max(suggested, next((s for s in MEMORY_STEPS if s >= memory / 4), suggested))
        if suggested and suggested < memory:
            rate = prices["gb_second_arm" if fn.arm else "gb_second"]
            monthly = sum(r.billed for r in runs) / 1000 * memory / 1024 * rate * DAYS_PER_MONTH / perf.window_days
            saving = monthly * (1 - suggested / memory)
            found.append(("warn" if saving >= 10 else "info", (
                f"Its runs used at most {max_used:,} of the {memory:,} MB it has: {suggested:,} MB still leaves room, "
                f"and would cut its compute cost by up to {_pct(1 - suggested / memory)} (about "
                f"{human_money(saving)}/month), if runs don't get slower: memory also sets the CPU. Try it and "
                f"compare: aws lambda update-function-configuration {cli} --memory-size {suggested}, then "
                f"performance('{name}') a day later."
            )))
    cold = perf.cold_starts
    if cold and len(runs) >= 20:
        share = len(cold) / len(runs)
        average = sum(r.init or 0 for r in cold) / len(cold)
        if share >= 0.05 and average >= 500:
            found.append(("info", (
                f"{_pct(share)} of runs were cold starts, each spending about {human_ms(average)} starting up first. "
                "SnapStart (Java 11+, Python 3.12+, .NET 8+), provisioned concurrency, or a smaller package "
                f"(code('{name}') shows what's in it) make them faster."
            )))
    return found


_HANDLER_EXTENSIONS = {"python": (".py",), "nodejs": (".js", ".mjs", ".cjs"), "ruby": (".rb",)}
_SECRET_FILE_RE = re.compile(
    r"(^|/)(\.env(\.[\w-]+)?|credentials|[\w.-]+\.pem|id_rsa|id_ed25519|\.npmrc|\.pypirc|\.netrc)$", re.I
)
_JUNK_RE = re.compile(r"(^|/)(__pycache__|\.git|\.pytest_cache|tests?|\.venv|\.idea|\.vscode|node_modules/\.cache)/")


def handler_file(handler: str | None, runtime: str | None, names: Iterable[str]) -> str | None:
    """The file in a package that Lambda loads the handler from: 'app.handler' -> 'app.py',
    'pkg.module.handler' -> 'pkg/module.py', 'src/index.handler' -> 'src/index.mjs'. None when the runtime loads
    compiled code (Java, .NET, Go) or no file matches."""
    family = runtime_family(runtime)
    if not handler or family not in _HANDLER_EXTENSIONS:
        return None
    names = set(names)
    module = handler.rsplit(".", 1)[0] if "." in handler else handler
    stem = module.replace(".", "/") if family == "python" else module
    for extension in _HANDLER_EXTENSIONS[family]:
        if stem + extension in names:
            return stem + extension
    for extension in _HANDLER_EXTENSIONS[family]:
        nested = sorted(n for n in names if n.endswith("/" + stem + extension))
        if nested:
            return nested[0]
    return None


def _top_folder(path: str) -> str:
    head, _, rest = path.partition("/")
    return f"{head}/" if rest else "(top level)"


def package_findings(pkg: CodePackage) -> list[tuple[str, str]]:
    """What's worth knowing about a deployment package: a handler no file matches, secrets packed in it, size near
    Lambda's limit, and what it carries that never runs."""
    fn = pkg.function
    found: list[tuple[str, str]] = []
    if not pkg.files:
        return found
    family = runtime_family(fn.runtime)
    if fn.handler and family in _HANDLER_EXTENSIONS and not pkg.handler_file:
        module = fn.handler.rsplit(".", 1)[0]
        expected = (module.replace(".", "/") if family == "python" else module) + _HANDLER_EXTENSIONS[family][0]
        found.append(("warn", (
            f"No file in the package matches its handler {fn.handler} (Lambda looks for {expected}): calls fail "
            "with Runtime.ImportModuleError until the handler setting or the package is fixed."
        )))
    secrets = [f.path for f in pkg.files if _SECRET_FILE_RE.search(f.path)]
    if secrets:
        found.append(("warn", (
            f"The package holds {', '.join(secrets[:5])}{', …' if len(secrets) > 5 else ''}: anyone allowed "
            f"lambda:GetFunction can download the package and read {'it' if len(secrets) == 1 else 'them'}. Leave "
            "secrets out of the package, and keep them in Secrets Manager or Parameter Store."
        )))
    total = pkg.unzipped + fn.layer_size
    if total >= 0.8 * UNZIPPED_LIMIT:
        found.append(("warn", (
            f"Its code and layers take {human_size(total)} unzipped, of the {human_size(UNZIPPED_LIMIT)} Lambda "
            "allows: the next dependency may not fit. Move big libraries into a layer, or deploy it as a container "
            "image (up to 10 GB)."
        )))
    sizes: Counter[str] = Counter()
    for f in pkg.files:
        sizes[_top_folder(f.path)] += f.size
    bundled = sizes.get("boto3/", 0) + sizes.get("botocore/", 0)
    if family == "python" and bundled:
        found.append(("info", (
            f"The package carries its own boto3 and botocore ({human_size(bundled)}), which the Python runtime "
            "already has: leaving them out makes it smaller and its cold starts shorter, unless the code needs a "
            "newer boto3 than the runtime's."
        )))
    junk = sum(f.size for f in pkg.files if _JUNK_RE.search(f.path))
    if junk >= MB:
        found.append(("info", (
            f"{human_size(junk)} of it is caches, tests or version-control files that never run in Lambda "
            "(__pycache__, tests/, .git/): leave them out to make the package smaller."
        )))
    return found


# =============================================================================
# 4. LambdaAnalyzer - pure logic layer (talks to AWS, returns data)
# =============================================================================

# Error codes from a region the account hasn't turned on (opt-in regions): skipped, not reported as a failure.
_NOT_ENABLED = {"UnrecognizedClientException", "InvalidClientTokenId", "AuthFailure", "OptInRequired",
                "InvalidSignatureException"}
# CloudWatch Logs filter patterns. The service narrows the lines down; classify_error() and parse_report() then
# decide, so a pattern that lets a few extra lines through costs nothing but reading them.
_ERROR_PATTERN = (
    '?ERROR ?Error ?error ?Exception ?exception ?Traceback ?FATAL ?CRITICAL ?panic ?errorType ?"Task timed out" '
    '?timeout ?"Runtime exited" ?"signal: killed"'
)
_REPORT_PATTERN = '?"REPORT RequestId" ?"platform.report"'
_SOURCE_LIMIT = 200 * KB  # the most of one file code() reads and shows


def _to_ms(moment: datetime) -> int:
    return int(moment.timestamp() * 1000)


def _from_ms(value: Any) -> datetime | None:
    return None if value in (None, "") else datetime.fromtimestamp(int(value) / 1000, timezone.utc)


def _log_group(entry: dict[str, Any]) -> LogGroup:
    return LogGroup(
        entry.get("logGroupName", ""),
        entry.get("retentionInDays"),
        entry.get("storedBytes"),
        _from_ms(entry.get("creationTime")),
        entry.get("logGroupClass"),
    )


def _pages(client: Any, operation: str, key: str, **params: Any) -> list[dict[str, Any]]:
    """Every item under `key` across the pages of a List / Describe call (Lambda's Marker, or nextToken)."""
    call = getattr(client, operation)
    items: list[dict[str, Any]] = []
    token: dict[str, str] = {}
    for _ in range(100_000):
        resp = call(**params, **token)
        items += resp.get(key) or []
        if resp.get("NextMarker"):
            token = {"Marker": resp["NextMarker"]}
        elif resp.get("nextToken"):
            token = {"nextToken": resp["nextToken"]}
        elif resp.get("NextToken"):
            token = {"NextToken": resp["NextToken"]}
        else:
            break
    return items


def _qualifier(arn: str | None) -> str:
    """'arn:aws:lambda:r:a:function:etl:live' -> 'live'."""
    parts = (arn or "").split(":")
    return parts[7] if len(parts) > 7 else "$LATEST"


class LambdaAnalyzer:
    """Pure-logic Lambda analysis: every method returns data; nothing is printed, invoked or changed.

    Functions are read in the analyzer's region, or in several: overview(regions=['us-east-1', 'eu-west-1']) or
    regions='all'. Methods about one function take its name, 'name:alias', an ARN or a console link, and region=
    when it's in another region (an ARN or a link says so itself, and a name overview() found in one other region
    is looked for there). `prices` overrides LAMBDA_PRICES. `client` is the Lambda client and `clients` other boto3
    clients by service name ('lambda', 'cloudwatch', 'logs', 'ec2'): a client, used in every region, or a function
    that makes one for a region (e.g. for tests).
    """

    def __init__(
        self,
        session: Any = None,
        *,
        region: str | None = None,
        profile: str | None = None,
        client: Any = None,
        clients: dict[str, Any] | None = None,
        prices: dict[str, float] | None = None,
    ):
        self.session = session or boto3.Session(profile_name=profile, region_name=region)
        self._config = Config(retries={"max_attempts": 10, "mode": "adaptive"}, max_pool_connections=50)
        self._given: dict[str, Any] = dict(clients or {})
        if client is not None:
            self._given["lambda"] = client
        self._clients: dict[tuple[str, str | None], Any] = {}
        self._lock = threading.Lock()  # boto3 sessions aren't thread-safe, so clients are made one at a time
        self.prices = {**LAMBDA_PRICES, **(prices or {})}
        self.max_workers = 8  # functions and regions read in parallel
        self._seen: dict[str, set[str]] = defaultdict(set)  # function name -> regions overview() found it in

    # ------------------------------------------------------------------ clients

    def _service(self, name: str, region: str | None = None) -> Any:
        """A boto3 client for `region` (default: the analyzer's), made on first use, or the one passed in."""
        if region is not None and region == self.session.region_name:
            region = None
        with self._lock:
            if (name, region) not in self._clients:
                given = self._given.get(name)
                if given is not None:
                    maker = callable(given) and not hasattr(given, "meta")  # a function that makes clients
                    self._clients[(name, region)] = given(region or self.session.region_name) if maker else given
                else:
                    makers = {  # named one by one so the project's read-only check can see which services are used
                        "lambda": lambda: self.session.client("lambda", region_name=region, config=self._config),
                        "cloudwatch": lambda: self.session.client(
                            "cloudwatch", region_name=region, config=self._config
                        ),
                        "logs": lambda: self.session.client("logs", region_name=region, config=self._config),
                        "ec2": lambda: self.session.client("ec2", region_name=region, config=self._config),
                    }
                    try:
                        self._clients[(name, region)] = makers[name]()
                    except NoRegionError:
                        raise ValueError(
                            "No AWS region is set, and Lambda functions are regional. Pass one: "
                            "LambdaView(LambdaAnalyzer(region='us-east-1')), or set AWS_DEFAULT_REGION."
                        ) from None
            return self._clients[(name, region)]

    @property
    def client(self) -> Any:
        """The Lambda client in the analyzer's region."""
        return self._service("lambda")

    @property
    def region(self) -> str:
        return self.client.meta.region_name

    def _map(self, fn: Callable[[Any], Any], items: list[Any]) -> list[Any]:
        """fn over items, in parallel threads unless max_workers is 1 (a Stubber answers in order)."""
        if self.max_workers <= 1 or len(items) <= 1:
            return [fn(item) for item in items]
        with ThreadPoolExecutor(max_workers=self.max_workers) as pool:
            return list(pool.map(fn, items))

    def regions(self, which: str | Iterable[str] | None = None) -> list[str]:
        """The regions a report covers: None -> the analyzer's; 'all' -> every region turned on for the account
        (EC2 DescribeRegions; without that permission, every region Lambda is offered in); a name, 'us-east-1,
        eu-west-1' or a list -> those."""
        if which is None:
            return [self.region]
        if isinstance(which, str) and which.strip().lower() == "all":
            try:
                found = self._service("ec2").describe_regions().get("Regions") or []
                names = [r["RegionName"] for r in found if r.get("OptInStatus") != "not-opted-in"]
                if names:
                    return sorted(names)
            except (ClientError, BotoCoreError):
                pass
            return sorted(self.session.get_available_regions("lambda"))
        names = re.split(r"[,\s]+", which.strip()) if isinstance(which, str) else [str(r).strip() for r in which]
        names = [n for n in dict.fromkeys(names) if n]
        for name in names:
            if not re.fullmatch(r"[a-z]{2}(-[a-z]+)+-\d+", name):
                close = difflib.get_close_matches(name, self.session.get_available_regions("lambda"), n=2)
                hint = f" Did you mean {' or '.join(map(repr, close))}?" if close else ""
                raise ValueError(f"{name!r} isn't a region name, like 'us-east-1'.{hint} regions='all' covers every one")
        if not names:
            raise ValueError("regions= is empty: pass 'all', a region like 'us-east-1', or a list of them")
        return names

    def locate(self, ref: str, region: str | None = None) -> tuple[str, str | None, str]:
        """(name, qualifier, region) for a function: its name, 'name:alias', an ARN or a console link. The region is
        region= when given, else the one in the ARN or link, else the one other region overview() found the name
        in, else the analyzer's."""
        name, qualifier, found = parse_function_ref(ref)
        if region:
            return name, qualifier, region
        if found:
            return name, qualifier, found
        seen = self._seen.get(name, set())
        if len(seen) == 1 and self.region not in seen:
            return name, qualifier, next(iter(seen))
        return name, qualifier, self.region

    # ------------------------------------------------------------------ functions

    def list_functions(self, region: str | None = None, *, match: str | None = None) -> list[Function]:
        """Every function in the region (its $LATEST configuration), or those whose name matches the glob."""
        where = region or self.region
        found = [parse_function(c, where) for c in _pages(self._service("lambda", region), "list_functions",
                                                          "Functions")]
        return [f for f in found if match is None or fnmatch.fnmatchcase(f.name, match)]

    def function(self, ref: str, *, region: str | None = None) -> Function:
        """One function's configuration, tags, reserved concurrency and the link to its code (GetFunction)."""
        name, qualifier, where = self.locate(ref, region)
        resp = self._service("lambda", where).get_function(
            FunctionName=name, **({"Qualifier": qualifier} if qualifier else {})
        )
        fn = parse_function(resp.get("Configuration") or {}, where)
        fn.tags = dict(resp.get("Tags") or {})
        fn.reserved_concurrency = (resp.get("Concurrency") or {}).get("ReservedConcurrentExecutions")
        code = resp.get("Code") or {}
        fn.code_location = code.get("Location")
        fn.image_uri = code.get("ImageUri") or code.get("ResolvedImageUri")
        return fn

    def event_sources(self, region: str | None = None, *, function: str | None = None) -> dict[str, list[Trigger]]:
        """Event source mappings (the queues and streams Lambda polls for functions), by function ARN."""
        params = {"FunctionName": function} if function else {}
        found: dict[str, list[Trigger]] = defaultdict(list)
        for mapping in _pages(self._service("lambda", region), "list_event_source_mappings", "EventSourceMappings",
                              **params):
            found[_unqualified(mapping.get("FunctionArn") or "")].append(parse_event_source_mapping(mapping))
        return dict(found)

    def policy(self, name: str, region: str | None = None, *, qualifier: str | None = None) -> dict[str, Any] | None:
        """The function's resource-based policy document (who may invoke it), or None when it has none."""
        try:
            resp = self._service("lambda", region).get_policy(
                FunctionName=name, **({"Qualifier": qualifier} if qualifier else {})
            )
        except ClientError as exc:
            if _error_code(exc) == "ResourceNotFoundException":
                return None
            raise
        return json.loads(resp.get("Policy") or "{}")

    def provisioned(self, name: str, region: str | None = None) -> list[ProvisionedConcurrency]:
        """Provisioned concurrency on the function's aliases and versions: copies kept ready, billed used or not."""
        configs = _pages(self._service("lambda", region), "list_provisioned_concurrency_configs",
                         "ProvisionedConcurrencyConfigs", FunctionName=name)
        return [
            ProvisionedConcurrency(
                _qualifier(c.get("FunctionArn")),
                int(c.get("RequestedProvisionedConcurrentExecutions") or 0),
                int(c.get("AllocatedProvisionedConcurrentExecutions") or 0),
                int(c.get("AvailableProvisionedConcurrentExecutions") or 0),
                c.get("Status") or "",
                c.get("StatusReason"),
            )
            for c in configs
        ]

    def account(self, region: str | None = None) -> AccountLimits:
        """The region's limits and usage: concurrent runs allowed and unreserved, code storage used and allowed."""
        resp = self._service("lambda", region).get_account_settings()
        limit, usage = resp.get("AccountLimit") or {}, resp.get("AccountUsage") or {}
        return AccountLimits(
            region or self.region,
            limit.get("ConcurrentExecutions"),
            limit.get("UnreservedConcurrentExecutions"),
            usage.get("TotalCodeSize"),
            limit.get("TotalCodeSize"),
            usage.get("FunctionCount"),
        )

    def log_groups(self, region: str | None = None, *, prefix: str = "/aws/lambda/") -> dict[str, LogGroup]:
        """Log groups whose names start with prefix (by default the one each function gets), by name."""
        entries = _pages(self._service("logs", region), "describe_log_groups", "logGroups", logGroupNamePrefix=prefix)
        return {entry["logGroupName"]: _log_group(entry) for entry in entries if entry.get("logGroupName")}

    def log_group(self, name: str, region: str | None = None) -> LogGroup | None:
        """One log group: how long it keeps logs and how much it holds; None when it doesn't exist yet."""
        return self.log_groups(region, prefix=name).get(name)

    # ------------------------------------------------------------------ CloudWatch

    def metrics(
        self,
        functions: list[Function],
        *,
        since: datetime,
        until: datetime | None = None,
        period: int | None = None,
        logs: bool = True,
        concurrency: bool = False,
    ) -> tuple[dict[str, FunctionMetrics], int]:
        """What CloudWatch counted for each function from `since` to `until`, by function ARN: calls, errors,
        throttles, total and longest run time, with logs=True the bytes it logged, and with concurrency=True the
        most running at once. One row per day (per hour for windows of two days or less). Also returns how many
        metrics were read (GetMetricData bills $0.01 per 1,000). Functions may be in different regions."""
        until = until or _utcnow()
        period = period or (3600 if until - since <= timedelta(days=2) else 86400)
        specs = [
            ("invocations", "Invocations", "Sum"),
            ("errors", "Errors", "Sum"),
            ("throttles", "Throttles", "Sum"),
            ("duration_sum", "Duration", "Sum"),
            ("duration_max", "Duration", "Maximum"),
        ]
        if concurrency:
            specs.append(("concurrency", "ConcurrentExecutions", "Maximum"))
        found: dict[str, FunctionMetrics] = {}
        read = 0
        by_region: dict[str, list[Function]] = defaultdict(list)
        for fn in functions:
            by_region[fn.region or self.region].append(fn)
        for region, group in by_region.items():
            queries: list[dict[str, Any]] = []
            for i, fn in enumerate(group):
                dimensions = [{"Name": "FunctionName", "Value": fn.name}]
                for j, (_, metric, stat) in enumerate(specs):
                    queries.append({"Id": f"m{i}_{j}", "MetricStat": {
                        "Metric": {"Namespace": "AWS/Lambda", "MetricName": metric, "Dimensions": dimensions},
                        "Period": period, "Stat": stat}})
                if logs:
                    queries.append({"Id": f"m{i}_logs", "MetricStat": {
                        "Metric": {"Namespace": "AWS/Logs", "MetricName": "IncomingBytes",
                                   "Dimensions": [{"Name": "LogGroupName", "Value": fn.log_group}]},
                        "Period": period, "Stat": "Sum"}})
            series = self._metric_data(region, queries, since, until)
            read += len(queries)
            for i, fn in enumerate(group):
                rows: dict[datetime, DailyUsage] = {}
                for j, (attribute, _, _) in enumerate(specs):
                    for moment, value in series.get(f"m{i}_{j}", {}).items():
                        row = rows.setdefault(moment, DailyUsage(moment))
                        if attribute in ("duration_max", "concurrency"):
                            setattr(row, attribute, max(getattr(row, attribute) or 0.0, value))
                        else:
                            setattr(row, attribute, getattr(row, attribute) + value)
                daily = sorted(rows.values(), key=lambda r: r.start)
                m = FunctionMetrics(fn.name, (until - since).total_seconds() / 86400, period, daily=daily)
                m.invocations = sum(r.invocations for r in daily)
                m.errors = sum(r.errors for r in daily)
                m.throttles = sum(r.throttles for r in daily)
                m.duration_sum = sum(r.duration_sum for r in daily)
                m.duration_max = max((r.duration_max for r in daily if r.duration_max is not None), default=None)
                m.concurrency_max = max((r.concurrency for r in daily if r.concurrency is not None), default=None)
                if logs:
                    m.log_bytes = sum(series.get(f"m{i}_logs", {}).values())
                found[fn.arn] = m
        return found, read

    def _metric_data(
        self, region: str | None, queries: list[dict[str, Any]], since: datetime, until: datetime
    ) -> dict[str, dict[datetime, float]]:
        """GetMetricData for any number of queries (500 a call): query ID -> {period start: value}."""
        series: dict[str, dict[datetime, float]] = defaultdict(dict)
        client = self._service("cloudwatch", region)
        for start in range(0, len(queries), 500):
            params: dict[str, Any] = {"MetricDataQueries": queries[start : start + 500], "StartTime": since,
                                      "EndTime": until}
            while True:
                resp = client.get_metric_data(**params)
                for result in resp.get("MetricDataResults") or []:
                    for moment, value in zip(result.get("Timestamps") or [], result.get("Values") or []):
                        series[result["Id"]][moment.astimezone(timezone.utc)] = float(value)
                if not resp.get("NextToken"):
                    break
                params["NextToken"] = resp["NextToken"]
        return series

    def peak_concurrency(self, region: str | None, *, since: datetime, until: datetime | None = None) -> float | None:
        """The most runs in progress at once across the region (every function together) in the window."""
        until = until or _utcnow()
        query = {"Id": "peak", "MetricStat": {
            "Metric": {"Namespace": "AWS/Lambda", "MetricName": "ConcurrentExecutions"},
            "Period": 3600 if until - since <= timedelta(days=2) else 86400, "Stat": "Maximum"}}
        values = self._metric_data(region, [query], since, until).get("peak", {})
        return max(values.values()) if values else None

    # ------------------------------------------------------------------ overview

    def overview(
        self,
        match: str | None = None,
        *,
        regions: str | Iterable[str] | None = None,
        days: int = 30,
        metrics: bool = True,
        details: bool = True,
        progress: Callable[..., None] | None = None,
    ) -> Overview:
        """Every function in the region (or regions; 'all' for every region turned on for the account) with what
        invokes it, CloudWatch's numbers for the last `days` days, each region's limits and its functions' log
        groups. details=False skips the reads made per function (resource policy, provisioned concurrency);
        metrics=False skips CloudWatch. match='etl-*' keeps matching names. What can't be read is in .errors, as
        'region:section'."""
        days = _as_int(days, "days")
        if not 1 <= days <= 455:
            raise ValueError(f"days= must be between 1 and 455 (what CloudWatch keeps of hourly numbers); got {days}")
        names = self.regions(regions)
        until = _utcnow()
        since = _midnight(until) - timedelta(days=days - 1)  # whole UTC days, today included
        ov = Overview(names, days, prices=self.prices, details=details)
        found: dict[str, list[Function]] = {}

        def scan(region: str) -> tuple[str, dict[str, Any]]:
            out: dict[str, Any] = {"functions": [], "errors": {}}
            try:
                out["functions"] = self.list_functions(region, match=match)
            except ClientError as exc:
                code = _error_code(exc)
                if code in _NOT_ENABLED and len(names) > 1:  # an opt-in region the account hasn't turned on
                    out["skipped"] = code
                else:
                    out["errors"]["list"] = code
                return region, out
            except BotoCoreError as exc:
                out["errors"] = {"list": type(exc).__name__}
                return region, out
            if not out["functions"]:
                return region, out
            for section, call in (
                ("triggers", lambda: self.event_sources(region)),
                ("account", lambda: self.account(region)),
                ("logs", lambda: self.log_groups(region)),
            ):
                try:
                    out[section] = call()
                except ClientError as exc:
                    out["errors"][section] = _error_code(exc)
                except BotoCoreError as exc:
                    out["errors"][section] = type(exc).__name__
            return region, out

        for done, (region, out) in enumerate(self._map(scan, names), 1):
            if progress and len(names) > 1:
                progress(done, len(names))
            if "skipped" in out:
                ov.skipped[region] = out["skipped"]
                continue
            ov.errors.update({f"{region}:{section}": code for section, code in out["errors"].items()})
            found[region] = out["functions"]
            ov.functions += out["functions"]
            for fn in out["functions"]:
                self._seen[fn.name].add(region)
                ov.triggers[fn.arn] = list(out.get("triggers", {}).get(fn.arn, []))
                group = out.get("logs", {}).get(fn.log_group)
                if group is not None:
                    ov.log_groups[fn.arn] = group
            if out.get("account") is not None:
                ov.accounts[region] = out["account"]
        if details and ov.functions:
            for region in found:  # make each region's client before the threads start using it
                self._service("lambda", region)

            def extra(fn: Function) -> tuple[Function, list[Trigger], list[ProvisionedConcurrency]]:
                triggers: list[Trigger] = []
                provisioned: list[ProvisionedConcurrency] = []
                try:
                    triggers = parse_policy(self.policy(fn.name, fn.region))
                except ClientError as exc:
                    fn.errors["policy"] = _error_code(exc)
                except BotoCoreError as exc:
                    fn.errors["policy"] = type(exc).__name__
                try:
                    provisioned = self.provisioned(fn.name, fn.region)
                except ClientError as exc:
                    fn.errors["provisioned"] = _error_code(exc)
                except BotoCoreError as exc:
                    fn.errors["provisioned"] = type(exc).__name__
                return fn, triggers, provisioned

            pending = list(ov.functions)
            done = 0
            for start in range(0, len(pending), 50):  # in batches, so progress moves as they finish
                for fn, triggers, provisioned in self._map(extra, pending[start : start + 50]):
                    ov.triggers.setdefault(fn.arn, []).extend(triggers)
                    if provisioned:
                        ov.provisioned[fn.arn] = provisioned
                done = min(start + 50, len(pending))
                if progress:
                    progress(done, len(pending))
        if metrics:
            for region, functions in found.items():
                if not functions:
                    continue
                try:  # concurrency too: what provisioned concurrency is weighed against
                    numbers, read = self.metrics(functions, since=since, until=until, concurrency=True)
                    ov.metrics.update(numbers)
                    ov.metrics_read += read
                    if region in ov.accounts:
                        ov.accounts[region].peak_concurrency = self.peak_concurrency(region, since=since, until=until)
                        ov.metrics_read += 1
                except ClientError as exc:
                    ov.errors[f"{region}:metrics"] = _error_code(exc)
                except BotoCoreError as exc:
                    ov.errors[f"{region}:metrics"] = type(exc).__name__
        return ov

    # ------------------------------------------------------------------ one function

    def describe(
        self,
        ref: str,
        *,
        region: str | None = None,
        days: int = 30,
        metrics: bool = True,
        progress: Callable[..., None] | None = None,
    ) -> FunctionDetail:
        """Everything about one function: its configuration (GetFunction), what triggers it (event source mappings
        and its resource policy), its function URL, what happens to failed asynchronous events, versions, aliases,
        provisioned concurrency, runtime updates, its log group, and CloudWatch's numbers for the last `days` days.
        Sections that can't be read are named in .errors."""
        days = _as_int(days, "days")
        if not 1 <= days <= 455:
            raise ValueError(f"days= must be between 1 and 455; got {days}")
        name, qualifier, where = self.locate(ref, region)
        fn = self.function(ref, region=where)  # a missing function raises ResourceNotFoundException here
        detail = FunctionDetail(fn)
        client = self._service("lambda", where)
        qualified = {"Qualifier": qualifier} if qualifier else {}
        steps = 9 + (fn.package_type == "Zip") + bool(metrics)  # GetFunction, then one read per section
        done = [1]

        def read(section: str, call: Callable[[], Any], missing: Any = None) -> Any:
            try:
                return call()
            except ClientError as exc:
                if _error_code(exc) == "ResourceNotFoundException":
                    return missing
                detail.errors[section] = _error_code(exc)
            except BotoCoreError as exc:
                detail.errors[section] = type(exc).__name__
            finally:
                done[0] += 1
                if progress:
                    progress(done[0], steps)
            return None

        detail.policy = read("policy", lambda: self.policy(name, where, qualifier=qualifier))
        policy_triggers = parse_policy(detail.policy)
        sources = read("triggers", lambda: self.event_sources(where, function=name)) or {}
        detail.triggers = [t for found in sources.values() for t in found]
        url = read("url", lambda: client.get_function_url_config(FunctionName=name, **qualified))
        if url:
            detail.url = FunctionUrl(
                url.get("FunctionUrl", ""),
                url.get("AuthType", ""),
                list((url.get("Cors") or {}).get("AllowOrigins") or []),
                url.get("InvokeMode"),
            )
        if "url" not in detail.errors:  # keep the policy's URL statements only when there is a URL to call
            public = any(t.public for t in policy_triggers if t.kind == "Function URL")
            policy_triggers = [t for t in policy_triggers if t.kind != "Function URL"]
            if detail.url is not None:
                if detail.url.auth_type != "NONE":
                    how = "callers sign their requests with IAM (auth type AWS_IAM)"
                elif public:
                    how = "no sign-in (auth type NONE)"
                elif "policy" in detail.errors:
                    how = "auth type NONE (couldn't read the resource policy to see who may call it)"
                else:
                    how = "auth type NONE, but no policy statement lets the public call it, so calls are refused"
                policy_triggers.append(Trigger("Function URL", detail.url.url, via="function URL",
                                               public=detail.url.auth_type == "NONE" and public, detail=how))
        detail.triggers += policy_triggers
        config = read("async", lambda: client.get_function_event_invoke_config(FunctionName=name, **qualified),
                      missing={})
        if config is not None:
            destinations = config.get("DestinationConfig") or {}
            detail.async_config = AsyncConfig(
                int(config.get("MaximumRetryAttempts", 2)),
                int(config.get("MaximumEventAgeInSeconds", 6 * 3600)),
                (destinations.get("OnSuccess") or {}).get("Destination"),
                (destinations.get("OnFailure") or {}).get("Destination"),
            )
        versions = read("versions", lambda: _pages(client, "list_versions_by_function", "Versions", FunctionName=name))
        detail.versions = [
            Version(v.get("Version", ""), int(v.get("CodeSize") or 0), _lambda_time(v.get("LastModified")),
                    v.get("Description") or "", v.get("Runtime"))
            for v in versions or [] if v.get("Version") != "$LATEST"
        ]
        aliases = read("aliases", lambda: _pages(client, "list_aliases", "Aliases", FunctionName=name))
        detail.aliases = [
            Alias(a.get("Name", ""), a.get("FunctionVersion", ""),
                  dict((a.get("RoutingConfig") or {}).get("AdditionalVersionWeights") or {}), a.get("Description") or "")
            for a in aliases or []
        ]
        detail.provisioned = read("provisioned", lambda: self.provisioned(name, where)) or []
        if fn.package_type == "Zip":
            runtime = read("runtime_updates", lambda: client.get_runtime_management_config(FunctionName=name,
                                                                                         **qualified))
            if runtime:
                detail.runtime_updates = runtime.get("UpdateRuntimeOn")
                detail.runtime_version = runtime.get("RuntimeVersionArn")
        detail.log_group = read("log_group", lambda: self.log_group(fn.log_group, where))
        if metrics:
            until = _utcnow()
            since = _midnight(until) - timedelta(days=days - 1)
            numbers = read("metrics", lambda: self.metrics([fn], since=since, until=until, concurrency=True))
            detail.metrics = numbers[0].get(fn.arn) if numbers else None
        return detail

    # ------------------------------------------------------------------ logs

    @staticmethod
    def _window(since: Any, until: Any = None) -> tuple[datetime, datetime]:
        """(start, end) from since= / until=: '1h', '24h', '7d', '2026-10-01' or a datetime."""
        now = _utcnow()
        try:  # '24h' and '7d' are measured back from now, for since= and until= alike
            end = parse_time(until, now=now) if until is not None else now
            start = parse_time(since, now=now)
        except ValueError:
            raise ValueError(
                f"since= and until= take a time like '1h', '24h', '7d', '2026-10-01' or a datetime; got "
                f"{since!r}" + (f" and {until!r}" if until is not None else "")
            ) from None
        if start is None or end is None or start >= end:
            raise ValueError(f"since= ({since!r}) must be before until=; try since='24h'")
        return start, end

    def _filter(
        self,
        group: str,
        region: str | None,
        start: datetime,
        end: datetime,
        pattern: str | None = None,
        limit: int | None = 10_000,
        *,
        stream: str | None = None,
        progress: Callable[..., None] | None = None,
    ) -> tuple[list[LogEvent], bool, datetime | None]:
        """The newest `limit` events from start to end that match the filter pattern, oldest first; whether reading
        stopped at the limit, and then how far back it reached. Reads backwards in growing slices (a minute, then
        4, 16, ...), so the newest events are the ones kept."""
        client = self._service("logs", region)
        events: list[LogEvent] = []
        low, high = _to_ms(start), _to_ms(end)
        size = 60_000
        truncated = False
        while high >= low and (limit is None or len(events) < limit):
            first = max(low, high - size + 1)
            params: dict[str, Any] = {"logGroupName": group, "startTime": first, "endTime": high}
            if pattern:
                params["filterPattern"] = pattern
            if stream:
                params["logStreamNames"] = [stream]
            batch: list[LogEvent] = []
            while True:
                resp = client.filter_log_events(**params)  # read-only: FilterLogEvents only reads log events
                for e in resp.get("events") or []:
                    message = e.get("message") or ""
                    moment = _from_ms(e.get("timestamp")) or start
                    batch.append(LogEvent(moment, message, e.get("logStreamName") or "", request_id_of(message)))
                token = resp.get("nextToken")
                if not token or (limit is not None and len(events) + len(batch) >= limit):
                    break
                params["nextToken"] = token
            batch.sort(key=lambda e: e.time)
            if limit is not None and len(events) + len(batch) >= limit and (token or first > low or len(
                    events) + len(batch) > limit):
                batch = batch[len(events) + len(batch) - limit:]
                truncated = True
            events = batch + events
            if progress:
                progress(len(events))
            high = first - 1
            size *= 4
        return events, truncated, (events[0].time if truncated and events else None)

    def _run_of(self, fn: Function, event: LogEvent) -> str | None:
        """The run a log line without a request ID belongs to: the next REPORT line in its stream (a stream runs one
        call at a time, and every run ends with one). None when there's none within the timeout."""
        if not event.stream:
            return None
        start = _to_ms(event.time)
        try:
            resp = self._service("logs", fn.region).filter_log_events(  # read-only: FilterLogEvents only reads
                logGroupName=fn.log_group, logStreamNames=[event.stream], startTime=start,
                endTime=start + (fn.timeout + 5) * 1000, filterPattern='"REPORT RequestId"', limit=1)
        except (ClientError, BotoCoreError):
            return None
        found = [request_id_of(e.get("message") or "") for e in resp.get("events") or []]
        return next((rid for rid in found if rid), None)

    def _latest(self, group: str, region: str | None) -> datetime | None:
        """When the log group last got an event (its busiest stream's last one), or None."""
        try:
            streams = self._service("logs", region).describe_log_streams(
                logGroupName=group, orderBy="LastEventTime", descending=True, limit=1
            ).get("logStreams") or []
        except (ClientError, BotoCoreError):
            return None
        return _from_ms(streams[0].get("lastEventTimestamp")) if streams else None

    def log_events(
        self,
        ref: str,
        *,
        since: Any = "1h",
        until: Any = None,
        pattern: str | None = None,
        request_id: str | None = None,
        limit: int | None = 10_000,
        region: str | None = None,
        progress: Callable[..., None] | None = None,
    ) -> LogPage:
        """What a function logged from `since` to `until` (the newest `limit` lines when there are more), matching a
        CloudWatch Logs filter pattern ('"KeyError"', '?ERROR ?WARN'). request_id= gives every line of that one
        run, the lines it printed without the ID included."""
        fn = self.function(ref, region=region)
        start, end = self._window(since, until)
        page = LogPage(fn, fn.log_group, start, end, pattern=pattern)
        try:
            if request_id:
                page.pattern = f'"{request_id}"'
                hits, _, _ = self._filter(fn.log_group, fn.region, start, end, page.pattern, 1_000)
                if hits:  # one run's lines are everything its stream logged between its first and last line
                    stream = hits[-1].stream
                    first = min(e.time for e in hits if e.stream == stream)
                    last = max(e.time for e in hits if e.stream == stream)
                    page.events, page.truncated, page.covered_from = self._filter(
                        fn.log_group, fn.region, first, last, None, limit, stream=stream, progress=progress)
            else:
                page.events, page.truncated, page.covered_from = self._filter(
                    fn.log_group, fn.region, start, end, pattern, limit, progress=progress)
        except ClientError as exc:
            page.errors["logs"] = _error_code(exc)
            return page
        except BotoCoreError as exc:
            page.errors["logs"] = type(exc).__name__
            return page
        if not page.events:
            page.latest = self._latest(fn.log_group, fn.region)
        return page

    def errors(
        self,
        ref: str,
        *,
        since: Any = "24h",
        until: Any = None,
        limit: int | None = 10_000,
        region: str | None = None,
        progress: Callable[..., None] | None = None,
    ) -> ErrorReport:
        """A function's errors from `since` to `until`, from its logs and grouped by cause (group_errors), with
        CloudWatch's count of failed and throttled calls over the same window."""
        fn = self.function(ref, region=region)
        start, end = self._window(since, until)
        report = ErrorReport(fn, fn.log_group, start, end)
        events: list[LogEvent] = []
        try:
            events, report.truncated, report.covered_from = self._filter(
                fn.log_group, fn.region, start, end, _ERROR_PATTERN, limit, progress=progress)
        except ClientError as exc:
            report.errors["logs"] = _error_code(exc)
        except BotoCoreError as exc:
            report.errors["logs"] = type(exc).__name__
        failures = [e for e in events if classify_error(e.message)]
        unnamed = [e for e in sorted(failures, key=lambda e: e.time, reverse=True) if not e.request_id]
        for event in unnamed[:10]:  # the newest lines that don't say which run they're from (Python's tracebacks)
            event.request_id = self._run_of(fn, event)
        report.errors_found = len(failures)
        report.groups = group_errors(failures)
        report.newest = sorted(failures, key=lambda e: e.time, reverse=True)[:10]
        try:
            report.metrics = self.metrics([fn], since=start, until=end, logs=False)[0].get(fn.arn)
        except ClientError as exc:
            report.errors["metrics"] = _error_code(exc)
        except BotoCoreError as exc:
            report.errors["metrics"] = type(exc).__name__
        return report

    def performance(
        self,
        ref: str,
        *,
        since: Any = "24h",
        until: Any = None,
        limit: int | None = 5_000,
        region: str | None = None,
        progress: Callable[..., None] | None = None,
    ) -> Performance:
        """Every run from `since` to `until` (the newest `limit` when there are more), from the REPORT line Lambda
        logs after each: run time, billed time, memory used, and the start-up time of cold starts."""
        fn = self.function(ref, region=region)
        start, end = self._window(since, until)
        perf = Performance(fn, fn.log_group, start, end)
        try:
            events, perf.truncated, perf.covered_from = self._filter(
                fn.log_group, fn.region, start, end, _REPORT_PATTERN, limit, progress=progress)
        except ClientError as exc:
            perf.errors["logs"] = _error_code(exc)
            return perf
        except BotoCoreError as exc:
            perf.errors["logs"] = type(exc).__name__
            return perf
        runs = [run for run in (parse_report(e.message, e.time) for e in events) if run is not None]
        perf.invocations = sorted(runs, key=lambda r: r.time or start, reverse=True)
        return perf

    # ------------------------------------------------------------------ code

    def _download(self, url: str, max_bytes: int | None) -> bytes:
        """The deployment package, from the short-lived link GetFunction gives (an HTTPS download from S3)."""
        if not url.lower().startswith("https://"):
            raise ValueError("Lambda's link to the code isn't an https:// address, so it isn't downloaded")
        too_big = (
            "The package is {size}, more than max_size ({most}): pass max_size='{more}' to read it anyway."
        )
        try:
            with urllib.request.urlopen(url, timeout=60) as resp:
                length = int(resp.headers.get("Content-Length") or 0)
                if max_bytes is not None and length > max_bytes:
                    raise ValueError(too_big.format(size=human_size(length), most=human_size(max_bytes),
                                                    more=f"{math.ceil(length / MB) + 1}MB"))
                data = resp.read() if max_bytes is None else resp.read(max_bytes + 1)
        except OSError as exc:  # no route to S3, a timeout, an expired link
            raise ValueError(
                f"Couldn't download the code ({getattr(exc, 'reason', exc)}): the notebook must reach S3 over HTTPS. "
                "In a VPC without internet access, that takes an S3 gateway endpoint."
            ) from None
        if max_bytes is not None and len(data) > max_bytes:
            raise ValueError(too_big.format(size=f"over {human_size(max_bytes)}", most=human_size(max_bytes),
                                            more=f"{math.ceil(max_bytes * 2 / MB)}MB"))
        return data

    def code(
        self, ref: str, *, file: str | None = None, max_size: Any = "50MB", region: str | None = None
    ) -> CodePackage:
        """A function's deployment package: every file in the .zip with its size, the file that holds the
        handler, and the text of that file (or of file=, a path, a file name or a glob like '*.py'). Downloads the
        package (up to max_size) from the link Lambda gives; nothing in it is run. A secrets file (.env, keys,
        credentials) is named but its text isn't read, and a container image is named, not pulled."""
        fn = self.function(ref, region=region)
        package = CodePackage(fn)
        if fn.package_type == "Image":
            package.note = (
                f"It's a container image ({fn.image_uri or 'image URI not shown'}): its code is in the image, which "
                "this doesn't pull. docker pull it from ECR to look inside."
            )
            return package
        if not fn.code_location:
            package.note = "Lambda gave no link to download its code."
            return package
        data = self._download(fn.code_location, parse_size(max_size))
        package.size = len(data)
        try:
            archive = zipfile.ZipFile(io.BytesIO(data))
        except zipfile.BadZipFile:
            raise ValueError(f"The package ({human_size(len(data))}) isn't a readable .zip file") from None
        with archive:
            entries = [info for info in archive.infolist() if not info.is_dir()]
            package.files = [CodeFile(info.filename, info.file_size, info.compress_size) for info in entries]
            names = [info.filename for info in entries]
            package.handler_file = handler_file(fn.handler, fn.runtime, names)
            wanted = _pick_file(file, names) if file else package.handler_file
            if wanted and _SECRET_FILE_RE.search(wanted):
                package.shown_file = wanted
                package.note = (
                    f"{wanted} looks like a secrets file, so its text isn't shown: a notebook's output is easy to "
                    f"share. To read it anyway, aws lambda get-function {_cli(fn)} --query Code.Location --output "
                    "text prints a link to the package that works for 10 minutes."
                )
            elif wanted:
                with archive.open(wanted) as handle:
                    raw = handle.read(_SOURCE_LIMIT + 1)
                package.source_truncated = len(raw) > _SOURCE_LIMIT
                raw = raw[:_SOURCE_LIMIT]
                package.shown_file = wanted
                if b"\x00" in raw[:8192]:
                    package.note = f"{wanted} is a binary file, so its text isn't shown."
                else:
                    package.source = raw.decode("utf-8", errors="replace")
        return package


def _pick_file(wanted: str, names: list[str]) -> str:
    """A file in the package from what the user typed: its path, its name, the end of its path, or a glob."""
    wanted = wanted.strip().lstrip("/")
    if wanted in names:
        return wanted
    for test in (
        lambda n: n.endswith("/" + wanted),
        lambda n: posixpath.basename(n) == wanted,
        lambda n: fnmatch.fnmatchcase(n, wanted) or fnmatch.fnmatchcase(posixpath.basename(n), wanted),
    ):
        hits = sorted((n for n in names if test(n)), key=lambda n: (n.count("/"), n))
        if hits:
            return hits[0]
    close = difflib.get_close_matches(wanted, names, n=3) or difflib.get_close_matches(
        wanted, [posixpath.basename(n) for n in names], n=3)
    hint = f" Did you mean {' or '.join(map(repr, close))}?" if close else ""
    raise ValueError(f"No file {wanted!r} in the package.{hint} code() without file= lists them")


# =============================================================================
# 5. LambdaView - notebook UI layer (renders what LambdaAnalyzer returns)
# =============================================================================


@dataclass
class _Title:
    text: str
    sub: str = ""


@dataclass
class _Cards:
    items: list[
        tuple[str, ...]
    ]  # (label, value), or (label, value, tone) with tone 'warn' | 'bad' | 'ok'


@dataclass
class _Table:
    headers: list[str]
    rows: list[list[Any]]  # cells are text, or _Tone for a coloured status
    title: str = ""
    bars: list[float] | None = None  # 0..1 per row, drawn as an extra column
    bar_label: str = "Share"
    tree: bool = False  # first column holds indented tree labels
    max_rows: int | None = None  # None = view default, 0 = no cap
    code_cols: tuple[int, ...] = ()  # columns holding calls to copy, shown as code
    prose_cols: tuple[
        int, ...
    ] = ()  # columns of sentences this tool wrote (findings): calls in them shown as code
    collapsed: bool = False  # a secondary view: folded under its title in HTML


@dataclass
class _Note:
    text: str
    level: str = "info"  # 'info' | 'warn' | 'ok'


@dataclass
class _Text:
    text: str
    title: str = ""
    wrap: bool = False  # prose: wrap long lines instead of scrolling sideways
    code: bool = False  # a snippet to copy: in HTML one click selects all of it
    collapsed: bool = (
        False  # a secondary view (raw JSON): folded under its title in HTML
    )


@dataclass
class _Findings:
    items: list[tuple[str, str]]  # (level, message) pairs from a *_findings function
    empty: str = ""  # said (as an ok note) when there are none; nothing when blank


@dataclass
class _Next:
    items: list[
        tuple[str, str]
    ]  # (call, what it shows): the commands worth running next, arguments filled in
    title: str = "Next"


@dataclass
class _Tone:
    """A table cell with a status colour: a pill in HTML, plain text elsewhere."""

    text: str
    tone: str = "warn"  # 'warn' | 'bad' | 'ok'

    def __str__(self) -> str:
        return self.text


_CSS = """<style>
.lmb{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;font-size:13px;line-height:1.45}
.lmb h3{margin:10px 0 2px;font-size:16px}
.lmb h3 .badge{display:inline-block;vertical-align:2px;margin-right:8px;padding:1px 7px;border-radius:9px;font-size:10px;font-weight:600;letter-spacing:.04em;text-transform:uppercase;background:rgba(59,130,246,.14);color:#3b82f6}
.lmb h4{margin:14px 0 4px;font-size:13px}
.lmb .sub{opacity:.65;font-size:12px;margin-bottom:6px}
.lmb .cards{display:flex;flex-wrap:wrap;gap:8px;margin:8px 0}
.lmb .card{border:1px solid rgba(127,127,127,.3);border-radius:6px;padding:6px 12px;min-width:96px}
.lmb .card.warn{border-color:rgba(245,158,11,.8);background:rgba(245,158,11,.08)}
.lmb .card.bad{border-color:rgba(239,68,68,.8);background:rgba(239,68,68,.08)}
.lmb .card.ok{border-color:rgba(16,185,129,.7)}
.lmb .card .l{font-size:11px;opacity:.65}
.lmb .card .v{font-size:15px;font-weight:600;overflow-wrap:anywhere}
.lmb .tw{max-width:100%;overflow-x:auto;margin:2px 0 8px}
.lmb .tw.scroll{max-height:640px;overflow:auto}
.lmb table.t{border-collapse:collapse;width:auto;font-size:inherit}
.lmb table.t th{text-align:left;font-weight:600;padding:4px 10px;border-bottom:1px solid rgba(127,127,127,.5)}
.lmb .tw.scroll table.t th{position:sticky;top:0;z-index:1;box-shadow:inset 0 -1px rgba(127,127,127,.5);backdrop-filter:blur(8px)}
.lmb .tw.scroll table.t th{background:var(--jp-layout-color0,var(--vscode-editor-background,transparent))}
.lmb table.t td{text-align:left;padding:3px 10px;border-bottom:1px solid rgba(127,127,127,.15);vertical-align:top}
.lmb table.t td{white-space:pre-line;overflow-wrap:break-word;max-width:640px}
.lmb table.t tbody tr:hover td{background:rgba(127,127,127,.07)}
.lmb table.t td.s{white-space:nowrap}
.lmb table.t td.n{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
.lmb table.t td.tree{white-space:pre;font-family:ui-monospace,Menlo,Consolas,monospace;font-size:12px}
.lmb table.t td.bar{white-space:nowrap;font-variant-numeric:tabular-nums}
.lmb .track{display:inline-block;width:110px;height:8px;border-radius:2px;background:rgba(127,127,127,.18)}
.lmb .track{vertical-align:middle;margin-right:6px}
.lmb .fill{display:block;height:100%;border-radius:2px;background:#3b82f6}
.lmb .pill{display:inline-block;padding:0 7px;border-radius:9px;font-weight:600;font-size:12px}
.lmb .pill.warn{background:rgba(245,158,11,.18);box-shadow:inset 0 0 0 1px rgba(245,158,11,.6)}
.lmb .pill.bad{background:rgba(239,68,68,.16);box-shadow:inset 0 0 0 1px rgba(239,68,68,.6)}
.lmb .pill.ok{background:rgba(16,185,129,.14);box-shadow:inset 0 0 0 1px rgba(16,185,129,.55)}
.lmb .note{padding:5px 10px;margin:4px 0;border-left:3px solid #3b82f6;background:rgba(59,130,246,.08)}
.lmb .note::before{content:"\\2139\\FE0E";margin-right:7px;opacity:.7}
.lmb .note.warn{border-left-color:#f59e0b;background:rgba(245,158,11,.10)}
.lmb .note.warn::before{content:"\\26A0\\FE0E"}
.lmb .note.ok{border-left-color:#10b981;background:rgba(16,185,129,.10)}
.lmb .note.ok::before{content:"\\2713"}
.lmb .fh{font-size:12px;font-weight:600;opacity:.75;margin:10px 0 2px}
.lmb code{font-family:ui-monospace,Menlo,Consolas,monospace;font-size:12px;padding:0 4px;border-radius:4px}
.lmb code{background:rgba(127,127,127,.15);user-select:all;-webkit-user-select:all;cursor:text}
.lmb .more{opacity:.6;font-size:12px;margin:-4px 0 8px}
.lmb pre{max-height:420px;overflow:auto;padding:8px 10px;border:1px solid rgba(127,127,127,.3);border-radius:6px;font-size:12px}
.lmb pre.wrap{white-space:pre-wrap;overflow-wrap:anywhere;font-family:inherit;font-size:13px;line-height:1.5;max-height:560px}
.lmb pre.code{user-select:all;-webkit-user-select:all;cursor:text}
.lmb .hint{font-weight:400;font-size:11px;opacity:.55;margin-left:8px}
.lmb details.sec{margin:14px 0 4px}
.lmb details.sec>summary{cursor:pointer;font-weight:600;margin-bottom:4px}
.lmb .next{display:flex;flex-wrap:wrap;align-items:baseline;gap:6px 18px;margin:12px 0 4px;padding-top:8px}
.lmb .next{border-top:1px dashed rgba(127,127,127,.35)}
.lmb .next .nl{font-size:11px;font-weight:600;letter-spacing:.04em;text-transform:uppercase;opacity:.6}
.lmb .next .nw{font-size:12px;opacity:.65;margin-left:6px}
</style>"""


_BADGE = "⚡ Lambda"  # the chip before each report's title, so reports from different analyzers are easy to tell apart


_NUMERIC_RE = re.compile(r"^-?(<?\$)?[\d,]+(\.\d+)?\+?( ?(B|KB|MB|GB|TB|PB|%|s))?$")


# A command in a sentence: a call (kb_info(), documents(status='FAILED'), .core.find(...), S3View().preview('s3://..'))
# or an AWS CLI command with its options (aws dynamodb update-table --table-name orders --deletion-protection-enabled).
_CALL_RE = re.compile(
    r"((?<![\w.])\.?(?:[A-Za-z_]\w*(?:\(\))?\.)*[A-Za-z_]\w*"
    r"\((?:[^()'\"]|'[^']*'|\"[^\"]*\"|\((?:[^()'\"]|'[^']*'|\"[^\"]*\")*\))*\)"
    r"|\baws [a-z0-9-]+ [a-z0-9-]+(?: --[\w-]+(?: (?!--)[^\s,;]*[^\s,;.])?)*)"
)


_TONES = ("warn", "bad", "ok")


_MARKS = {
    "warn": "[!] ",
    "ok": "[ok] ",
}  # text-mode prefix of a note by level; anything else is "[i] "


_SELECT = ' title="Click to select, then copy"'


def _esc(value: Any) -> str:
    return html.escape("" if value is None else str(value))


def _prose(value: Any) -> str:
    """Escaped HTML for a sentence this tool wrote, with the calls in it as code that one click selects.
    The text is split on the calls and every piece escaped before it's wrapped, so nothing in it becomes markup."""
    pieces = _CALL_RE.split("" if value is None else str(value))
    return "".join(
        f"<code{_SELECT}>{_esc(piece)}</code>" if i % 2 else _esc(piece)
        for i, piece in enumerate(pieces)
    )


def _call(name: str, *args: Any, **kwargs: Any) -> str:
    """_call('tree', 's3://b/', depth=2) -> "tree('s3://b/', depth=2)": a next step, ready to copy."""

    def literal(
        value: Any,
    ) -> str:  # repr, but DynamoDB numbers read 42 rather than Decimal('42')
        if type(value).__name__ == "Decimal":
            return str(value)
        if isinstance(value, dict):
            return (
                "{"
                + ", ".join(f"{literal(k)}: {literal(v)}" for k, v in value.items())
                + "}"
            )
        if isinstance(value, (list, tuple)):
            inner = ", ".join(map(literal, value)) + (
                "," if isinstance(value, tuple) and len(value) == 1 else ""
            )
            return f"[{inner}]" if isinstance(value, list) else f"({inner})"
        return repr(value)

    return f"{name}({', '.join([literal(a) for a in args] + [f'{k}={literal(v)}' for k, v in kwargs.items()])})"


def _signature(function: Callable) -> str:
    """'(uri, *, top_n=10, limit=None)': a command's parameters without self or type hints."""
    sig = inspect.signature(function)
    params = [
        p.replace(annotation=inspect.Parameter.empty)
        for name, p in sig.parameters.items()
        if name != "self"
    ]
    return str(
        sig.replace(parameters=params, return_annotation=inspect.Signature.empty)
    )


def _tone(item: tuple[str, ...]) -> str:
    """The tone of a card: its third element, when it's one this renderer colours."""
    return item[2] if len(item) > 2 and item[2] in _TONES else ""


def _ordered(findings: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """Warnings first, then notes, each in the order they were found."""
    return sorted(findings, key=lambda f: {"warn": 0, "info": 1}.get(f[0], 2))


def _counts(findings: list[tuple[str, str]], sep: str) -> str:
    """'2 warnings · 3 notes'."""
    warns = sum(level == "warn" for level, _ in findings)
    return sep.join(
        filter(
            None,
            [
                _plural(warns, "warning") if warns else "",
                _plural(len(findings) - warns, "note") if len(findings) > warns else "",
            ],
        )
    )


def _visible_rows(table: _Table, default_max: int) -> tuple[list[list[Any]], int]:
    cap = default_max if table.max_rows is None else table.max_rows
    rows = table.rows if not cap else table.rows[:cap]
    return rows, len(table.rows) - len(rows)


def _hidden(count: int, table: _Table, default_max: int) -> str:
    """The line under a table that was cut short, and how to see the rest when the view's max_rows cut it."""
    text = f"... {count:,} more rows not shown"
    return text + (
        f" (the view shows {default_max:,}; ui.max_rows = 0 shows all)"
        if table.max_rows is None
        else ""
    )


def _render_html(blocks: list[Any], max_rows: int) -> str:
    out = [_CSS, '<div class="lmb">']
    for block in blocks:
        if isinstance(block, _Title):
            out.append(
                f'<h3><span class="badge">{_esc(_BADGE)}</span>{_esc(block.text)}</h3>'
            )
            if block.sub:
                out.append(f'<div class="sub">{_prose(block.sub)}</div>')
        elif isinstance(block, _Cards):
            cards = "".join(
                f'<div class="{" ".join(filter(None, ["card", _tone(item)]))}"><div class="l">'
                f'{_esc(item[0])}</div><div class="v">{_esc(item[1])}</div></div>'
                for item in block.items
            )
            out.append(f'<div class="cards">{cards}</div>')
        elif isinstance(block, _Note):
            out.append(f'<div class="note {block.level}">{_prose(block.text)}</div>')
        elif isinstance(block, _Findings):
            items = _ordered(block.items)
            if items:
                notes = "".join(
                    f'<div class="note {level}">{_prose(message)}</div>'
                    for level, message in items
                )
                head = f'<div class="fh">Findings · {_esc(_counts(items, " · "))}</div>'
                out.append(f'<div class="fd">{head}{notes}</div>')
            elif block.empty:
                out.append(f'<div class="note ok">{_prose(block.empty)}</div>')
        elif isinstance(block, _Next):
            if block.items:
                calls = "".join(
                    f'<span class="ni"><code{_SELECT}>{_esc(call)}</code>'
                    + (f'<span class="nw">{_esc(why)}</span>' if why else "")
                    + "</span>"
                    for call, why in block.items
                )
                out.append(
                    f'<div class="next"><span class="nl">{_esc(block.title)}</span>{calls}</div>'
                )
        elif isinstance(block, _Table):
            if not block.rows:
                if block.title:
                    out.append(f"<h4>{_prose(block.title)}</h4>")
                out.append('<div class="more">(none)</div>')
                continue
            rows, hidden = _visible_rows(block, max_rows)
            head = "".join(f"<th>{_esc(h)}</th>" for h in block.headers)
            head += (
                f"<th>{_esc(block.bar_label)}</th>" if block.bars is not None else ""
            )
            body = []
            for i, row in enumerate(rows):
                cells = []
                for j, cell in enumerate(row):
                    text = "" if cell is None else str(cell)
                    inner = _esc(text)
                    if isinstance(cell, _Tone) and cell.tone in _TONES and text:
                        inner = f'<span class="pill {cell.tone}">{inner}</span>'
                    if block.tree and j == 0:
                        css = "tree"
                    elif j in block.code_cols and text:
                        css, inner = "c", f"<code{_SELECT}>{inner}</code>"
                    elif j in block.prose_cols:
                        css, inner = "", _prose(text)
                    elif _NUMERIC_RE.match(text):
                        css = "n"
                    else:
                        css = "s" if len(text) <= 16 and "\n" not in text else ""
                    cells.append(
                        f'<td class="{css}">{inner}</td>'
                        if css
                        else f"<td>{inner}</td>"
                    )
                if block.bars is not None:
                    pct = max(0.0, min(1.0, block.bars[i])) * 100
                    cells.append(
                        f'<td class="bar"><span class="track"><span class="fill" style="width:{pct:.1f}%">'
                        f"</span></span>{pct:.1f}%</td>"
                    )
                body.append(f"<tr>{''.join(cells)}</tr>")
            table = (
                f'<div class="tw{" scroll" if len(rows) > 30 else ""}"><table class="t"><thead><tr>{head}</tr>'
                f"</thead><tbody>{''.join(body)}</tbody></table></div>"
            )
            if hidden:
                table += (
                    f'<div class="more">{_esc(_hidden(hidden, block, max_rows))}</div>'
                )
            if block.collapsed:
                out.append(
                    f'<details class="sec"><summary>{_prose(block.title or "Details")} '
                    f"({len(block.rows):,})</summary>{table}</details>"
                )
            else:
                if block.title:
                    out.append(f"<h4>{_prose(block.title)}</h4>")
                out.append(table)
        elif isinstance(block, _Text):
            css = " ".join(
                filter(
                    None, ["wrap" if block.wrap else "", "code" if block.code else ""]
                )
            )
            pre = (
                f'<pre class="{css}"{_SELECT if block.code else ""}>{_esc(block.text)}</pre>'
                if css
                else f"<pre>{_esc(block.text)}</pre>"
            )
            if block.collapsed:
                out.append(
                    f'<details class="sec"><summary>{_prose(block.title or "Details")}</summary>{pre}</details>'
                )
            else:
                hint = (
                    '<span class="hint">click it to select all, then copy</span>'
                    if block.code
                    else ""
                )
                if block.title or hint:
                    out.append(f"<h4>{_prose(block.title)}{hint}</h4>")
                out.append(pre)
    out.append("</div>")
    return "".join(out)


def _text_bar(fraction: float, width: int = 20) -> str:
    fraction = max(0.0, min(1.0, fraction))
    filled = round(fraction * width)
    return "█" * filled + "░" * (width - filled) + f" {fraction * 100:5.1f}%"


def _render_text(blocks: list[Any], max_rows: int) -> str:
    out: list[str] = []
    for block in blocks:
        if isinstance(block, _Title):
            out += ["", block.text, "=" * min(len(block.text), 100)] + (
                [block.sub] if block.sub else []
            )
        elif isinstance(block, _Cards):
            line = ""
            for entry in block.items:
                item = f"{entry[0]}: {entry[1]}" + (
                    " (!)" if _tone(entry) in ("warn", "bad") else ""
                )
                if line and len(line) + len(item) > 100:
                    out.append(line)
                    line = ""
                line += ("   " if line else "") + item
            out.append(line)
        elif isinstance(block, _Note):
            out.append(_MARKS.get(block.level, "[i] ") + block.text)
        elif isinstance(block, _Findings):
            items = _ordered(block.items)
            if items:
                out += ["", f"-- Findings: {_counts(items, ', ')} --"]
                out += [_MARKS.get(level, "[i] ") + message for level, message in items]
            elif block.empty:
                out.append("[ok] " + block.empty)
        elif isinstance(block, _Next):
            if block.items:
                width = max(len(call) for call, _ in block.items)
                out += ["", f"{block.title}:"]
                out += [
                    f"  {call.ljust(width)}   {why}".rstrip()
                    for call, why in block.items
                ]
        elif isinstance(block, _Table):
            out.append("")
            if block.title:
                out.append(f"-- {block.title} --")
            if not block.rows:
                out.append("(none)")
                continue
            rows, hidden = _visible_rows(block, max_rows)
            headers = list(block.headers) + (
                [block.bar_label] if block.bars is not None else []
            )
            cells = [
                [_clip(("" if c is None else str(c)).replace("\n", ", ")) for c in row]
                + ([_text_bar(block.bars[i])] if block.bars is not None else [])
                for i, row in enumerate(rows)
            ]
            widths = [
                max([_width(h)] + [_width(r[j]) for r in cells])
                for j, h in enumerate(headers)
            ]

            def line_of(values: list[str], widths: list[int] = widths) -> str:
                return "  ".join(
                    _pad(v, w, right=bool(_NUMERIC_RE.match(v)))
                    for v, w in zip(values, widths)
                ).rstrip()

            out += [line_of(headers), "  ".join("-" * w for w in widths)] + [
                line_of(r) for r in cells
            ]
            if hidden:
                out.append(_hidden(hidden, block, max_rows))
        elif isinstance(block, _Text):
            if block.title:
                out += ["", f"-- {block.title} --"]
            out.append(block.text)
    return "\n".join(out)


def _in_notebook() -> bool:
    try:
        from IPython.core.getipython import get_ipython
    except ImportError:
        return False
    shell = get_ipython()
    return shell is not None and type(shell).__name__ != "TerminalInteractiveShell"


def _progress_bar_class(notebook: bool) -> Any:
    """tqdm's widget bar in a notebook (it needs ipywidgets) or its text bar elsewhere; None without tqdm."""
    try:
        if notebook:
            importlib.import_module("ipywidgets")
            return importlib.import_module("tqdm.notebook").tqdm
        return importlib.import_module("tqdm").tqdm
    except Exception:  # not installed, or too old to import cleanly: the plain progress line takes over
        return None


def _progress_bar(bar_class: Any, label: str, unit: str, total: int | None) -> Any:
    """A tqdm bar that shows up after half a second and disappears when closed. unit='B' counts bytes."""
    options: dict[str, Any] = {
        "desc": label,
        "total": total,
        "leave": False,
        "delay": 0.5,
        "mininterval": 0.25,
        "dynamic_ncols": True,
        "disable": False,
        "unit_scale": True,
    }
    if unit == "B":
        options.update(unit="B", unit_divisor=1024)
    else:
        known = total is not None
        counts = "{percentage:3.0f}%|{bar}| {n:,}/{total:,}" if known else "{n:,}"
        timing = (
            "{elapsed}<{remaining}, {rate_fmt}" if known else "{elapsed}, {rate_fmt}"
        )
        options.update(
            unit=f" {unit}", bar_format=f"{{desc}}: {counts} {unit} [{timing}]"
        )
    return bar_class(**options)


def _duration(seconds: float) -> str:
    """0.42 -> '0.4s', 42.4 -> '42s', 125 -> '2m 05s', 7500 -> '2h 05m'."""
    if seconds < 10:
        return f"{seconds:.1f}s"
    if seconds < 60:
        return f"{seconds:.0f}s"
    if seconds < 3600:
        return f"{int(seconds // 60)}m {int(seconds % 60):02d}s"
    return f"{int(seconds // 3600)}h {int(seconds % 3600 // 60):02d}m"


def _progress_text(
    label: str, unit: str, count: int, total: int | None, elapsed: float
) -> str:
    """The progress line shown without tqdm: 'Reading... 1.2 GB of 3.0 GB (40%) · 12s · 98.0 MB/s · about 18s left'."""
    amount = human_size if unit == "B" else (lambda n: f"{n:,}")
    text = f"{label}... {amount(count)}"
    if total:
        text += f" of {amount(total)}"
    text += "" if unit == "B" else f" {unit}"
    if total:
        text += f" ({min(count / total, 1):.0%})"
    text += f" · {_duration(elapsed)}"
    if elapsed >= 1 and count:
        rate = count / elapsed
        text += (
            f" · {human_size(rate)}/s"
            if unit == "B"
            else f" · {rate:,.0f}/s"
            if rate >= 10
            else f" · {rate:.1f}/s"
        )
        if total and total > count:
            text += f" · about {_duration((total - count) / rate)} left"
    return text


def _fmt_dt(moment: datetime | None) -> str:
    return (
        "-"
        if moment is None
        else moment.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M")
    )


def _share(part: float, whole: float) -> float:
    return part / whole if whole else 0.0


def _count(value: int | None) -> str:
    return "-" if value is None else f"{value:,}"


def _stamp(moment: datetime | None) -> str:
    """A log line's time, to the second, in UTC."""
    return "-" if moment is None else moment.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


_SECTIONS = {  # what a section of a report is, and the permission that reads it
    "list": ("functions", "lambda:ListFunctions"),
    "policy": ("resource policy", "lambda:GetPolicy"),
    "triggers": ("event source mappings", "lambda:ListEventSourceMappings"),
    "url": ("function URL", "lambda:GetFunctionUrlConfig"),
    "async": ("settings for failed asynchronous calls", "lambda:GetFunctionEventInvokeConfig"),
    "versions": ("versions", "lambda:ListVersionsByFunction"),
    "aliases": ("aliases", "lambda:ListAliases"),
    "provisioned": ("provisioned concurrency", "lambda:ListProvisionedConcurrencyConfigs"),
    "runtime_updates": ("runtime update setting", "lambda:GetRuntimeManagementConfig"),
    "account": ("limits", "lambda:GetAccountSettings"),
    "log_group": ("log group", "logs:DescribeLogGroups"),
    "logs": ("log groups", "logs:DescribeLogGroups"),
    "metrics": ("numbers from CloudWatch", "cloudwatch:GetMetricData"),
}
_PARTS = {  # function_monthly_cost's parts, in plain English
    "requests": "Requests",
    "compute": "Compute (memory x run time)",
    "storage": "/tmp above 512 MB",
    "logs": "Logs sent to CloudWatch",
    "log_storage": "Logs kept in CloudWatch",
    "provisioned": "Provisioned concurrency",
}
_VIA = {
    "event source mapping": "Lambda reads it",
    "resource policy": "allowed to invoke it",
    "function URL": "its HTTPS address",
}
_DURATION_BANDS = (  # performance()'s table of how long runs take
    ("under 100 ms", 100),
    ("100 ms to 1 s", 1_000),
    ("1 to 3 s", 3_000),
    ("3 to 10 s", 10_000),
    ("10 to 30 s", 30_000),
    ("30 s to 1 min", 60_000),
    ("1 to 5 min", 300_000),
    ("5 to 15 min", None),
)


def _unread(errors: dict[str, str], owner: str) -> _Note | None:
    """One note naming the sections that couldn't be read ('its versions', "us-east-1's limits"), each with the
    permission it needs."""
    parts = []
    for section, code in errors.items():
        what, permission = _SECTIONS.get(section, (section, "the permission"))
        parts.append(f"{owner}{what} ({_why(code, permission)})")
    return _Note(f"Couldn't read {'; '.join(parts)}.", "warn") if parts else None


def _label(fn: Function, multi: bool) -> str:
    return f"{fn.name} ({fn.region})" if multi else fn.name


def _triggers_cell(triggers: list[Trigger]) -> Any:
    """'SQS, S3' for a table cell: amber when anyone may call the function."""
    if not triggers:
        return "-"
    labels = list(dict.fromkeys(t.short for t in triggers))
    text = ", ".join(labels[:3]) + (f" +{len(labels) - 3}" if len(labels) > 3 else "")
    return _Tone(text, "warn") if any(t.public for t in triggers) else text


def _runtime_meaning(status: RuntimeStatus) -> str:
    if status.state == "image":
        return "A container image: what's inside is yours to patch, so rebuild it on a current base image now and then"
    newest = f" The newest is {status.upgrade}." if status.upgrade else ""
    if status.state == "unknown":
        return "This file doesn't know its support dates (RUNTIMES): the AWS Lambda runtimes page has them." + newest
    blocked = status.block_update is not None and status.block_update <= _utcnow().date()
    updates = (f" Updates {'blocked since' if blocked else 'blocked from'} {status.block_update}."
               if status.block_update else "")
    if status.state in ("deprecated", "blocked"):
        return f"Support ended on {status.deprecated}: no more security patches.{updates}{newest}"
    if status.state == "ending":
        return f"Supported until {status.deprecated} ({_in_days(status.days_left or 0)}).{updates}{newest}"
    return (f"Supported until {status.deprecated}." if status.deprecated else "Supported.") + newest


def _handler_meaning(fn: Function) -> str:
    if fn.package_type == "Image":
        return "The image's own entry point runs it"
    if not fn.handler:
        return ""
    module, _, function = fn.handler.rpartition(".")
    family = runtime_family(fn.runtime)
    if family == "python" and module:
        return f"Lambda calls {function}() in {module.replace('.', '/')}.py"
    if family == "nodejs" and module:
        return f"Lambda calls the {function} export of {module}.js (or .mjs)"
    if family == "ruby" and module:
        return f"Lambda calls {function} in {module}.rb"
    if family in ("java", "dotnet"):
        return "The class and method Lambda calls"
    return "What Lambda starts"


def _cpu(fn: Function) -> str:
    """The CPU a function's memory buys: 'about 0.58 vCPU', 'about 2.0 vCPUs' (6 at most)."""
    return f"about {fn.vcpus:.2f} vCPU" if fn.vcpus < 1 else f"about {min(fn.vcpus, 6):.1f} vCPUs"


def _log_meaning(fn: Function, group: LogGroup | None, unread: bool) -> str:
    kept = ""
    if group is not None:
        kept = "kept forever" if group.retention_days is None else f"kept {_plural(group.retention_days, 'day')}"
        kept += f", {human_size(group.stored_bytes)} stored" if group.stored_bytes is not None else ""
    elif not unread:
        kept = "no log group yet: it hasn't logged anything"
    return ", ".join(filter(None, [f"{fn.log_format or 'Text'} lines", kept]))


def _concurrency_cell(fn: Function) -> tuple[Any, str]:
    if fn.reserved_concurrency is None:
        return "shared", "No reservation: it shares the region's concurrent runs with every other function"
    if fn.reserved_concurrency == 0:
        return _Tone("0: switched off", "bad"), "Every call is throttled"
    return (f"{fn.reserved_concurrency:,} reserved",
            f"At most {fn.reserved_concurrency:,} run at once (more are throttled), and that many are kept for it")


def _log_line(message: str) -> str:
    """A log line for a table: a REPORT line summed up, JSON shown as its level and message, the rest as written."""
    run = parse_report(message)
    if run is not None:
        parts = [f"run {human_ms(run.duration)} (billed {human_ms(run.billed)})",
                 f"{run.max_memory:,} of {run.memory:,} MB used"]
        if run.init is not None:
            parts.append(f"cold start {human_ms(run.init)}")
        if run.status:
            parts.append(f"status {run.status}" + (f" ({run.error_type})" if run.error_type else ""))
        return "REPORT: " + ", ".join(parts)
    text = message.rstrip()
    if text.startswith("{"):
        try:
            doc = json.loads(text)
        except ValueError:
            doc = None
        if isinstance(doc, dict) and "message" in doc:
            inner = doc["message"]
            body = inner if isinstance(inner, str) else json.dumps(inner, default=str)
            return f"[{doc.get('level', '-')}] {body}"
    return _LINE_PREFIX_RE.sub(r"\g<level>", text, count=1)[:2000]


# What Lambda writes before each line it adds ('<time> <request ID> ', '[ERROR]\t<time>\t<request ID>\t', Node's
# '<time>\t<request ID>\tERROR\t'): the report's Time and Request ID columns say it already.
_LINE_PREFIX_RE = re.compile(
    r"^(?P<level>\[[A-Z]+\]\s*)?\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z\s+"
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\s+"
)


def _lifecycle(message: str) -> bool:
    """START / END / INIT_START lines, and their JSON platform records: there's one of each per run."""
    text = message.lstrip()
    if _LIFECYCLE_RE.match(text) or text.startswith("INIT_START"):
        return True
    return text.startswith('{"time"') and any(
        f'"type":"platform.{kind}"' in text.replace(" ", "") for kind in ("start", "runtimeDone", "initStart"))


def _phrase(search: str | None) -> str | None:
    """The exact text a plain search= looks for (None for a filter pattern with its own syntax), so lines can be
    checked again here."""
    text = str(search or "").strip()
    if len(text) >= 2 and text[0] == text[-1] == '"' and '"' not in text[1:-1]:
        return text[1:-1]
    return text if text and not (text.startswith(("{", "[", "?", "-")) or " ?" in text) else None


def _filter_pattern(search: str | None) -> str | None:
    """search= as a CloudWatch Logs filter pattern: plain text becomes an exact, case-sensitive phrase; a pattern
    ('?ERROR ?WARN', '{ $.level = "ERROR" }') passes through as it is."""
    if not search:
        return None
    text = str(search).strip()
    if text.startswith(("{", "[", "?", "-", '"')) or " ?" in text:
        return text
    return '"' + text.replace('"', '\\"') + '"'


def _since_arg(since: Any, default: str) -> dict[str, Any]:
    """since= for a next step: carried over when the user chose one (as text), left out when it's the default."""
    return {"since": since} if isinstance(since, str) and since != default else {}


class _Hint(ValueError):
    """A question back to the user, shown as a plain note rather than an error."""


def _friendly_errors(method: Callable) -> Callable:
    """Show AWS / input errors as a readable note instead of a traceback."""

    @functools.wraps(method)
    def wrapper(self: LambdaView, *args: Any, **kwargs: Any) -> None:
        try:
            return method(self, *args, **kwargs)
        except ClientError as exc:
            error = exc.response.get("Error", {})
            code, message = error.get("Code", "Error"), error.get("Message", str(exc))
            self._show(
                [
                    _Note(
                        f"{code}: {self._explain(code, message)}  [{method.__name__}]",
                        "warn",
                    )
                ]
            )
        except _Hint as exc:
            self._show([_Note(str(exc))])
        except ImportError as exc:  # a missing optional package: the message says what to pip install
            self._show([_Note(f"{str(exc).rstrip('.')}.", "warn")])
        except (BotoCoreError, ValueError, TypeError, ImportError) as exc:
            self._show(
                [_Note(f"{type(exc).__name__}: {exc}  [{method.__name__}]", "warn")]
            )

    return wrapper


class LambdaView:
    """Notebook UI over LambdaAnalyzer. Each method renders a report and returns nothing; for the underlying data
    call the matching method on `view.core` (a LambdaAnalyzer): functions() -> overview(), function_info() ->
    describe(), logs() -> log_events(), and errors(), performance() and code() by the same name.

    mode: 'auto' (HTML inside Jupyter, text elsewhere), 'html' or 'text'.
    max_rows: default cap for long tables (set to 0 for no cap).
    progress: 'auto' (a tqdm bar while long commands run, when tqdm is installed; else a line with the count,
    rate and time left), 'plain' (always that line) or 'off'.
    """

    _progress_owner: Callable[[], None] | None = None  # clears the progress bar showing now
    _GROUPS = {  # help() lists the commands in these groups, in this order
        "λ Functions": ("functions", "function_info"),
        "🩺 When something goes wrong": ("errors", "logs", "performance"),
        "📦 Code": ("code",),
        "❓ Help": ("help",),
    }
    _START = (
        ("functions()", "every function: runtime, triggers, calls, errors, cost and warnings"),
        ("functions(regions='all')", "the same in every region your account has turned on"),
        ("function_info('name')", "one function in plain English, and its last 30 days"),
    )

    def __init__(
        self,
        core: LambdaAnalyzer | None = None,
        *,
        mode: str = "auto",
        max_rows: int = 50,
        progress: str = "auto",
    ):
        if mode not in ("auto", "html", "text"):
            raise ValueError("mode must be 'auto', 'html' or 'text'")
        if progress not in ("auto", "plain", "off"):
            raise ValueError("progress must be 'auto', 'plain' or 'off'")
        self.core = core or LambdaAnalyzer()
        self.use_html = _in_notebook() if mode == "auto" else mode == "html"
        self.max_rows = max_rows
        self.progress = progress

    # ------------------------------------------------------------------ plumbing

    def _show(self, blocks: list[Any]) -> None:
        if self.use_html:
            try:
                from IPython.display import HTML, display
            except ImportError:  # mode='html' outside Jupyter: show text, and say why (once)
                self.use_html = False
                note = "mode='html' only works in Jupyter (IPython isn't installed here), so this is shown as text."
                blocks = [*blocks, _Note(note, "warn")]
            else:
                display(HTML(_render_html(blocks, self.max_rows)))
                return
        print(_render_text(blocks, self.max_rows))


    @contextmanager
    def _progress(
        self, label: str = "Reading", unit: str = "items read"
    ) -> Generator[Callable[..., None], None, None]:
        """Progress while a long call runs. tick(count) reports a running count; tick(done, total) a known total,
        and a new total starts a new bar. unit='B' counts bytes. A tqdm bar when tqdm is installed (a widget in
        Jupyter when ipywidgets is too), otherwise a line with the count, time, rate and time left. One bar shows
        at a time: when a nested _progress starts showing, the outer one's bar goes away."""
        notebook = self.use_html and _in_notebook()  # elsewhere (or without IPython) the plain line goes to stderr
        bar_class = [
            _progress_bar_class(notebook)
            if self.progress == "auto"
            else None
        ]
        bar: list[Any] = [None]
        handle: list[Any] = [None]
        started: list[Any] = [
            time.monotonic(),
            None,
        ]  # when the current total started, and that total
        shown, width, stopped = [0.0], [0], [False]

        def close_bar() -> None:
            if bar[0] is not None:
                if not stopped[0] and bar[0].total and bar[0].n < bar[0].total:
                    bar[0].total = bar[
                        0
                    ].n  # done early (a file it couldn't read): no red "failed" widget
                bar[0].close()
                bar[0] = None

        def clear() -> None:
            close_bar()
            if handle[0] is not None:
                from IPython.display import HTML

                handle[0].update(HTML(""))
            elif width[0]:
                print("\r" + " " * width[0] + "\r", end="", file=sys.stderr, flush=True)
                width[0] = 0

        def take_over() -> None:
            owner = self._progress_owner
            if owner is not clear:
                if owner is not None:
                    owner()
                self._progress_owner = clear

        def tick(count: int, total: int | None = None) -> None:
            if self.progress == "off":
                return
            if bar_class[0] is not None:
                try:
                    if bar[0] is not None and total != bar[0].total:
                        close_bar()
                    if bar[0] is None:
                        take_over()
                        bar[0] = _progress_bar(bar_class[0], label, unit, total)
                    bar[0].update(count - bar[0].n)
                    return
                except Exception:  # an old tqdm or a broken widget front end: use the plain line instead
                    bar_class[0] = None
            now = time.monotonic()
            if total != started[1]:
                started[:] = [now, total]
            if now - started[0] < 0.5 or now - shown[0] < 0.5:
                return
            shown[0] = now
            take_over()
            text = _progress_text(label, unit, count, total, now - started[0])
            if notebook:
                from IPython.display import HTML, display

                if handle[0] is None:
                    handle[0] = display(HTML(""), display_id=True)
                if handle[0] is not None:  # display() returns None outside IPython
                    handle[0].update(
                        HTML(f'<div style="opacity:.6">{_esc(text)}</div>')
                    )
            else:
                width[0] = max(width[0], len(text))
                print("\r" + text.ljust(width[0]), end="", file=sys.stderr, flush=True)

        try:
            yield tick
        except BaseException:
            stopped[0] = True  # interrupted: a tqdm widget stays, red, where it stopped
            raise
        finally:
            clear()
            if self._progress_owner is clear:
                self._progress_owner = None


    def help(self, command: Any = None) -> None:
        """Every command, grouped by task; help('name') shows one command in full."""
        view = type(self).__name__
        commands = {
            name: inspect.unwrap(member)
            for name, member in vars(type(self)).items()
            if not name.startswith("_") and callable(member)
        }

        def about(name: str) -> str:  # the docstring's first paragraph, on one line
            return " ".join(
                (inspect.getdoc(commands[name]) or "").split("\n\n")[0].split()
            )

        if command is not None:
            name = getattr(command, "__name__", str(command))
            if name not in commands:
                close = difflib.get_close_matches(name, list(commands), n=3)
                hint = (
                    f" Did you mean {' or '.join(map(repr, close))}?" if close else ""
                )
                self._show(
                    [
                        _Note(
                            f"{view} has no command {name!r}.{hint} help() lists them all.",
                            "warn",
                        )
                    ]
                )
                return
            self._show(
                [
                    _Title(
                        f"{name}{_signature(commands[name])}",
                        f"{view} command · help() lists them all",
                    ),
                    _Text(
                        inspect.getdoc(commands[name]) or "(no description)", wrap=True
                    ),
                ]
            )
            return
        grouped = {name for names in self._GROUPS.values() for name in names}
        groups = {
            **self._GROUPS,
            "Other": tuple(name for name in commands if name not in grouped),
        }
        blocks: list[Any] = [
            _Title(
                f"{view} commands",
                "help('name') shows one in full · the data behind each report "
                f"comes from .core ({type(self.core).__name__})",
            ),
            _Next(list(self._START), title="Start here"),
        ]
        for group, names in groups.items():
            rows = [
                [f"{name}{_signature(commands[name])}", about(name)]
                for name in names
                if name in commands
            ]
            if rows:
                blocks.append(
                    _Table(
                        ["Command", "What it shows"],
                        rows,
                        title=group,
                        max_rows=0,
                        code_cols=(0,),
                    )
                )
        self._show(blocks)

    def _explain(self, code: str, message: str) -> str:
        """The AWS error message plus what to do about it."""
        lowered = message.lower()
        text = message.rstrip() + ("" if message.rstrip().endswith((".", "!", "?")) else ".")
        if code == "ResourceNotFoundException":
            return (f"{text} Names are case-sensitive, and functions are regional (this is {self.core.region}): "
                    "functions() lists them, and functions(regions='all') looks in every region.")
        if code in ("AccessDeniedException", "AccessDenied", "UnauthorizedOperation") or "not authorized" in lowered:
            return f"{text} README lists the read-only IAM permissions each command needs."
        if code in ("ThrottlingException", "TooManyRequestsException"):
            return f"{text} AWS throttled the call: wait a few seconds and retry."
        return message

    def _price_basis(self) -> str:
        return "us-east-1 list prices" if self.core.prices == LAMBDA_PRICES else "your prices"

    def _missing(self, ref: str, region: str | None) -> _Hint:
        """What to say when a function isn't there: the closest names, and where else functions() saw it."""
        name, qualifier, where = self.core.locate(ref, region)
        try:
            names = [f.name for f in self.core.list_functions(where)]
        except (ClientError, BotoCoreError):
            names = []
        if qualifier and name in names:
            return _Hint(f"{name} has no alias or version {qualifier!r}. function_info({name!r}) lists its aliases "
                         "and versions.")
        close = difflib.get_close_matches(name, names, n=3, cutoff=0.6)
        hint = f" Did you mean {' or '.join(map(repr, close))}?" if close else ""
        others = sorted(self.core._seen.get(name, set()) - {where})
        elsewhere = f" functions() found it in {', '.join(others)}: pass region='{others[0]}'." if others else ""
        return _Hint(
            f"No function {name!r} in {where}.{hint}{elsewhere} Names are case-sensitive: functions() lists the ones "
            f"in {where}, and functions(regions='all') looks in every region."
        )

    @contextmanager
    def _named(self, ref: str, region: str | None) -> Generator[None, None, None]:
        """Turns AWS's bare 'function not found' into a hint with the closest names."""
        try:
            yield
        except ClientError as exc:
            if _error_code(exc) != "ResourceNotFoundException":
                raise
            raise self._missing(ref, region) from None

    def _call_for(self, command: str, fn: Function, *args: Any, **kwargs: Any) -> str:
        """A next step about a function, with region= when it isn't in the view's region."""
        if fn.region and fn.region != self.core.region:
            kwargs = {**kwargs, "region": fn.region}
        return _call(command, fn.name, *args, **kwargs)

    # ------------------------------------------------------------------ functions

    @_friendly_errors
    def functions(
        self,
        match: str | None = None,
        *,
        regions: Any = None,
        days: int = 30,
        metrics: bool = True,
        details: bool = True,
    ) -> None:
        """Every Lambda function in the region: its runtime (and when that loses support), memory, timeout, what
        triggers it, calls, error rate and run time over the last 30 days, the estimated monthly cost, and
        warnings. regions='all' covers every region your account has turned on (or pass a list); match='etl-*'
        keeps matching names; metrics=False skips CloudWatch and details=False the reads made per function
        (resource policy, provisioned concurrency), for speed."""
        with self._progress("Reading functions", unit="functions") as tick:
            ov = self.core.overview(match=match, regions=regions, days=days, metrics=metrics, details=details,
                                    progress=tick)
        now = _utcnow()
        prices = self.core.prices
        multi = len(ov.regions) > 1
        warnings: list[list[str]] = []
        warned: Counter[str] = Counter()
        rows: list[list[Any]] = []
        calls = failed = total = 0.0
        costed = unsupported = 0
        error_warning = False
        regional: dict[str, dict[str, float]] = defaultdict(lambda: {"functions": 0, "calls": 0.0, "cost": 0.0})
        for fn in sorted(ov.functions, key=lambda f: (f.region, f.name.lower())):
            detail = ov.detail(fn)
            m = detail.metrics
            found = function_findings(fn, m, detail, prices=prices, now=now)
            warns = [message for level, message in found if level == "warn"]
            warnings += [[_label(fn, multi), message] for message in warns]
            warned[fn.arn] = len(warns)
            status = runtime_status(fn.runtime, package_type=fn.package_type, today=now.date())
            unsupported += status.state in ("deprecated", "blocked")
            cost = _total(function_monthly_cost(fn, m, detail.provisioned, detail.log_group, prices=prices))
            if cost is not None:
                costed += 1
                total += cost
            region_row = regional[fn.region]
            region_row["functions"] += 1
            region_row["cost"] += cost or 0.0
            row: list[Any] = [fn.name] + ([fn.region] if multi else []) + [
                _Tone(status.label, status.tone) if status.tone else status.label,
                _mb(fn.memory),
                f"{fn.timeout} s",
                _triggers_cell(detail.triggers),
            ]
            if metrics:
                level = _errors_level(m, now)
                error_warning = error_warning or level == "warn"
                if m is not None:
                    calls += m.invocations
                    failed += m.errors
                    region_row["calls"] += m.invocations
                rate = _pct(m.error_rate) if m is not None and m.invocations else "-"
                row += [
                    _count(round(m.invocations)) if m is not None else "-",
                    _Tone(rate, "warn") if level == "warn" else rate,
                    _ms(m.avg_duration) if m is not None and m.invocations else "-",
                    (_day_age(m.last_invoked, now) if m.invocations else f"not in {ov.days}d") if m is not None else "-",
                ]
            row += [human_money(cost) if cost is not None else "-", _Tone(str(len(warns)), "warn" if warns else "")]
            rows.append(row)
        for region, limits in sorted(ov.accounts.items()):
            warnings += [[region, message] for level, message in account_findings(limits, ov.days) if level == "warn"]

        title = (f"Lambda functions in {ov.regions[0]}" if not multi
                 else f"Lambda functions in {len(ov.regions)} regions")
        sub = [f"names matching {match!r}" if match else ""]
        if metrics:
            sub.append(f"calls, errors and run time from CloudWatch over the last {ov.days} days")
        sub.append(f"cost estimated at {self._price_basis()}, before the free tier")
        cards: list[tuple[str, ...]] = [("Functions", f"{len(ov.functions):,}")]
        if multi:
            cards.append(("Regions with functions", f"{len(regional):,} of {len(ov.regions):,}"))
        if metrics:
            cards.append((f"Calls ({ov.days}d)", _count(round(calls))))
            cards.append(("Error rate", _pct(failed / calls) if calls else "-", "warn" if error_warning else ""))
        cards.append(("Est. cost / month", human_money(total) if costed else "-"))
        if unsupported:
            cards.append(("Unsupported runtimes", f"{unsupported:,}", "bad"))
        if not multi and ov.regions[0] in ov.accounts:
            limits = ov.accounts[ov.regions[0]]
            if limits.code_storage is not None and limits.code_storage_limit:
                full = limits.code_storage >= 0.8 * limits.code_storage_limit
                cards.append(("Code storage", f"{human_size(limits.code_storage)} of "
                              f"{human_size(limits.code_storage_limit)}", "warn" if full else ""))
        cards.append(("With warnings", f"{sum(1 for n in warned.values() if n):,}", "warn" if warnings else "ok"))
        blocks: list[Any] = [_Title(title, " · ".join(filter(None, sub))), _Cards(cards)]

        listed = {key.split(":")[0] for key in ov.errors if key.endswith(":list")}
        for region in sorted(listed):
            code = ov.errors[f"{region}:list"]
            blocks.append(_Note(f"Couldn't list the functions in {region} ({_why(code, 'lambda:ListFunctions')}).",
                                "warn"))
        by_region: dict[str, dict[str, str]] = defaultdict(dict)
        for key, code in ov.errors.items():
            region, _, section = key.partition(":")
            if section != "list":
                by_region[region][section] = code
        for region, errors in sorted(by_region.items()):
            note = _unread(errors, f"{region}'s ")
            if note:
                blocks.append(note)
        for section in ("policy", "provisioned"):
            codes = Counter(fn.errors[section] for fn in ov.functions if section in fn.errors)
            if codes:
                code, count = codes.most_common(1)[0]
                what, permission = _SECTIONS[section]
                blocks.append(_Note(
                    f"Couldn't read the {what} of {_plural(count, 'function')} ({_why(code, permission)}), so "
                    + ("triggers from other services and public access aren't shown for them."
                       if section == "policy" else "the cost of provisioned concurrency is missing for them."),
                    "warn"))
        if ov.skipped:
            blocks.append(_Note(
                f"Left out {_plural(len(ov.skipped), 'region')} not turned on for this account: "
                f"{', '.join(sorted(ov.skipped))}."
            ))
        if not ov.functions:
            if not listed:
                where = f"matching {match!r} " if match else ""
                place = ov.regions[0] if not multi else f"these {len(ov.regions)} regions"
                more = "" if multi else " Functions are regional: functions(regions='all') looks in every region."
                blocks.append(_Note(f"No Lambda functions {where}in {place}.{more}"))
            self._show(blocks)
            return
        if not details:
            blocks.append(_Note(
                "details=False: resource policies and provisioned concurrency weren't read, so triggers from other "
                "services, public access and the cost of provisioned concurrency are missing."
            ))
        headers = ["Function"] + (["Region"] if multi else []) + ["Runtime", "Memory", "Timeout", "Triggers"]
        if metrics:
            headers += [f"Calls ({ov.days}d)", "Error rate", "Avg run time", "Last called"]
        headers += ["Est. $/month", "Warnings"]
        blocks.append(_Table(headers, rows, title="Functions (function_info(name) explains one)"))
        if multi:
            region_rows = []
            for region in sorted(regional):
                limits = ov.accounts.get(region)
                storage = "-"
                peak = "-"
                if limits is not None and limits.code_storage is not None:
                    storage = human_size(limits.code_storage) + (
                        f" of {human_size(limits.code_storage_limit)}" if limits.code_storage_limit else "")
                if limits is not None and limits.peak_concurrency is not None:
                    peak = f"{limits.peak_concurrency:,.0f}" + (f" of {limits.concurrency:,}" if limits.concurrency else "")
                numbers = regional[region]
                region_rows.append([region, f"{numbers['functions']:,.0f}",
                                    _count(round(numbers["calls"])) if metrics else "-",
                                    human_money(numbers["cost"]), storage, peak])
            blocks.append(_Table(
                ["Region", "Functions", f"Calls ({ov.days}d)", "Est. $/month", "Code storage", "Most at once"],
                region_rows, title="By region", max_rows=0,
            ))
        if warnings:
            blocks.append(_Table(
                ["Function", "Warning"], warnings, prose_cols=(1,), max_rows=0,
                title="Warnings (function_info(name) shows every finding for one function)",
            ))
        if ov.metrics_read:
            cost = ov.metrics_read / 1000 * prices["metric_request"]
            blocks.append(_Note(
                f"Cost estimates scale CloudWatch's last {ov.days} days to a month, and include provisioned concurrency "
                f"and the logs kept. This report read {ov.metrics_read:,} CloudWatch metrics (about "
                f"{human_money(cost)})."
            ))
        busiest = sorted(ov.functions, key=lambda f: (
            -warned[f.arn], -(ov.metrics[f.arn].invocations if f.arn in ov.metrics else 0), f.name))
        steps = [(self._call_for("function_info", busiest[0]), "everything about it, and its last 30 days")]
        failing = [f for f in ov.functions if f.arn in ov.metrics and ov.metrics[f.arn].errors]
        if failing:  # the one with an error warning first, then the most failed calls
            worst = max(failing, key=lambda f: (_errors_level(ov.metrics[f.arn], now) == "warn",
                                                ov.metrics[f.arn].errors))
            steps.append((self._call_for("errors", worst), "its errors, grouped by cause"))
        if not multi and regions is None:
            steps.append(("functions(regions='all')", "every region your account has turned on"))
        blocks.append(_Next(steps))
        self._show(blocks)

    @_friendly_errors
    def function_info(self, name: str, *, region: str | None = None, days: int = 30) -> None:
        """One function in plain English: what it runs (the runtime and when it loses support, memory and the CPU
        it buys, timeout), what triggers it and who may call it, what happens to failed events, what it can reach
        (role, network, environment variable names: values stay hidden), versions, aliases and provisioned
        concurrency, its last 30 days day by day, the estimated monthly cost, and findings with the command for
        each. name can also be 'name:alias', an ARN or a console link."""
        with self._progress("Reading the function", unit="parts") as tick, self._named(name, region):
            detail = self.core.describe(name, region=region, days=days, progress=tick)
        fn, m = detail.function, detail.metrics
        now = _utcnow()
        prices = self.core.prices
        found = function_findings(fn, m, detail, prices=prices, now=now)
        status = runtime_status(fn.runtime, package_type=fn.package_type, today=now.date())
        cost = function_monthly_cost(fn, m, detail.provisioned, detail.log_group, prices=prices)
        level = _errors_level(m, now)
        near_timeout = bool(m and m.duration_max and fn.timeout and m.duration_max >= 0.9 * fn.timeout * 1000)
        public = any(t.public for t in detail.triggers)
        _, qualifier, _ = parse_function_ref(name)
        cards: list[tuple[str, ...]] = [
            ("Runtime", status.label, status.tone),
            ("Memory", _mb(fn.memory)),
            ("Timeout", f"{fn.timeout} s", "warn" if near_timeout else ""),
            ("Architecture", fn.architecture),
        ]
        if m is not None:
            cards += [
                (f"Calls ({days}d)", _count(round(m.invocations))),
                ("Error rate", _pct(m.error_rate) if m.invocations else "-", "warn" if level == "warn" else ""),
                ("Avg / longest run",
                 f"{human_ms(m.avg_duration)} / {human_ms(m.duration_max)}" if m.invocations else "-",
                 "warn" if near_timeout else ""),
            ]
        cards.append(("Est. cost / month", human_money(_total(cost)) if cost else "-"))
        cards.append(("Triggers", f"{len(detail.triggers):,}", "warn" if public else ""))
        sub = " · ".join(filter(None, [
            fn.arn + (f":{qualifier}" if qualifier else ""),
            _clip(fn.description, 120),
            f"changed {human_age(fn.last_modified, now)}" if fn.last_modified else "",
        ]))
        blocks: list[Any] = [_Title(f"Function {fn.name}" + (f" ({qualifier})" if qualifier else ""), sub),
                             _Cards(cards)]
        note = _unread(detail.errors, "its ")
        if note:
            blocks.append(note)
        blocks.append(_Findings(found, empty=f"No problems found in its settings or its last {days} days."))

        package = (f"{fn.package_type}, {human_size(fn.code_size)}" if fn.package_type == "Zip"
                   else f"container image {fn.image_uri or ''}".strip())
        if fn.layers:
            package += f", plus {_plural(len(fn.layers), 'layer')} ({human_size(fn.layer_size)})"
        concurrency, concurrency_meaning = _concurrency_cell(fn)
        state = fn.state or "-"
        runs: list[list[Any]] = [
            ["Runtime", _Tone(status.label, status.tone) if status.tone else status.label, _runtime_meaning(status)],
            ["Handler", fn.handler or "-", _handler_meaning(fn)],
            ["Package", package, self._call_for("code", fn) + " lists its files and shows the handler's source"
             if fn.package_type == "Zip" else "The code is inside the image"],
            ["Memory", f"{fn.memory:,} MB ({_cpu(fn)})", f"Memory also sets the CPU: a full vCPU at "
             f"{MEMORY_PER_VCPU:,} MB"],
            ["Timeout", f"{fn.timeout:,} seconds", f"A run is stopped after this (the most Lambda allows is "
             f"{MAX_TIMEOUT} s)"],
            ["/tmp storage", f"{fn.ephemeral_storage:,} MB" + (" (512 MB free)" if fn.ephemeral_storage > 512 else
                                                                " (free)"),
             "Above 512 MB is billed by the GB-second" if fn.ephemeral_storage > 512 else "Scratch space for a run"],
            ["Architecture", fn.architecture, "Graviton: 20% less per GB-second than x86_64" if fn.arm
             else "arm64 (Graviton) costs 20% less per GB-second"],
            ["Concurrency", concurrency, concurrency_meaning],
            ["Tracing", "X-Ray on" if fn.tracing == "Active" else "off",
             "Each call's path through AWS is traced in X-Ray" if fn.tracing == "Active"
             else "No X-Ray traces of its calls"],
            ["Logs", fn.log_group, _log_meaning(fn, detail.log_group, "log_group" in detail.errors)],
            ["State", _Tone(state, "warn") if state not in ("Active", "-") else state,
             fn.state_reason or ("Ready to run" if state == "Active" else "")],
            ["Last change", f"{_fmt_dt(fn.last_modified)} UTC ({human_age(fn.last_modified, now)})",
             f"Last update: {fn.last_update or '-'}" + _reason(fn.last_update_reason)],
        ]
        if detail.runtime_updates:
            runs.append(["Runtime updates", detail.runtime_updates,
                         "Pinned to one runtime version: no security patches" if detail.runtime_updates == "Manual"
                         else "Lambda applies runtime patches itself"])
        if fn.snapstart or runtime_family(fn.runtime) in ("java", "python", "dotnet"):
            runs.append(["SnapStart", "on" if fn.snapstart else "off",
                         "New copies start from a snapshot, for faster cold starts (published versions)"])
        blocks.append(_Table(["Setting", "Value", "What it means"], runs, title="What it runs", max_rows=0,
                             prose_cols=(2,)))

        trigger_rows = []
        for t in detail.triggers:
            disabled = (t.state or "").lower() == "disabled"
            state_cell: Any = (_Tone("public", "warn") if t.public else _Tone(t.state or "", "warn") if disabled
                               else t.state or "-")
            trigger_rows.append([t.kind, t.source or "-", _VIA.get(t.via, t.via), state_cell,
                                 "; ".join(filter(None, [t.detail, t.last_result])) or "-"])
        if trigger_rows:
            blocks.append(_Table(["Trigger", "Source", "How", "State", "Details"], trigger_rows, max_rows=0,
                                 title="What triggers it (event source mappings, its resource policy and URL)"))
        elif not {"policy", "triggers"} & set(detail.errors):
            blocks.append(_Note(
                "Nothing in its resource policy or event source mappings calls it: it's called directly (an SDK, "
                "aws lambda invoke, Step Functions, or a service that uses its own role), or not at all."
            ))
        async_config = detail.async_config
        asynchronous = [t for t in detail.triggers if t.asynchronous]
        if async_config is not None and (asynchronous or async_config.on_failure or async_config.on_success
                                         or fn.dead_letter):
            blocks.append(_Table(["Setting", "Value"], [
                ["Retries after a failure", _times(async_config.retries) if async_config.retries else "none"],
                ["Oldest event kept", _window(async_config.max_age)],
                ["When it succeeds, the result goes to", async_config.on_success or "-"],
                ["When it fails for good, the event goes to", async_config.on_failure or fn.dead_letter
                 or _Tone("nowhere: dropped", "warn" if asynchronous else "")],
            ], title="When an asynchronous call fails (events from S3, SNS, EventBridge...)", max_rows=0))

        if fn.vpc_id:
            network = (f"VPC {fn.vpc_id} ({_plural(len(fn.subnets), 'subnet')}, "
                       f"{_plural(len(fn.security_groups), 'security group')})")
            network_meaning = ("It reaches your VPC's resources, and the internet and AWS APIs only through a NAT "
                               "gateway or VPC endpoints")
        else:
            network = "Lambda's own network"
            network_meaning = "It reaches the internet and AWS APIs, but nothing private in your VPCs"
        access: list[list[Any]] = [
            ["Execution role", (fn.role or "-").split("/")[-1],
             f"What its code may do in AWS: the role's policies in IAM ({fn.role})" if fn.role else ""],
            ["Network", network, network_meaning],
            ["Environment variables", ", ".join(fn.env_names) if fn.env_names else "none",
             "Values hidden here: they often hold secrets" if fn.env_names else ""],
        ]
        if fn.file_systems:
            access.append(["File systems", "; ".join(fn.file_systems), "EFS file systems mounted into it"])
        if fn.layers:
            access.append(["Layers", ", ".join(f"{layer.name} ({human_size(layer.code_size)})" for layer in fn.layers),
                           "Shared code added next to its own"])
        if fn.env_names:
            access.append(["Encryption", fn.kms_key.split("/")[-1] if fn.kms_key else "AWS-managed key",
                           "The key that encrypts its environment variables"])
        blocks.append(_Table(["Setting", "Value", "What it means"], access, title="What it can reach", max_rows=0,
                             prose_cols=(2,)))

        version_rows: list[list[Any]] = []
        for alias in detail.aliases:
            weights = ", ".join(f"{share:.0%} to version {v}" for v, share in alias.weights.items())
            version_rows.append([f"alias {alias.name}", f"version {alias.version}" + (f" ({weights})" if weights else ""),
                                 alias.description or "-"])
        if detail.versions:
            ordered = sorted(detail.versions, key=lambda v: int(v.version) if v.version.isdigit() else 0)
            newest = ordered[-1]
            version_rows.append([_plural(len(ordered), "published version"),
                                 f"newest {newest.version} ({human_age(newest.last_modified, now)})",
                                 f"{human_size(sum(v.code_size for v in ordered))} of code kept"])
        for pc in detail.provisioned:
            copies = pc.billed or pc.requested
            version_rows.append([f"provisioned concurrency on {pc.qualifier}",
                                 f"{pc.allocated:,} of {pc.requested:,} ready ({pc.status or '-'})",
                                 f"{human_money(provisioned_monthly_cost(fn, copies, prices))}/month, used or not"])
        if version_rows:
            blocks.append(_Table(["What", "Value", "Notes"], version_rows, max_rows=0,
                                 title="Versions, aliases and provisioned concurrency"))

        if m is not None and m.invocations:
            day_rows, bars = [], []
            busiest = max((d.invocations for d in m.daily), default=0.0)
            for d in sorted(m.daily, key=lambda d: d.start, reverse=True):
                day_rows.append([d.start.strftime("%Y-%m-%d %a"), _count(round(d.invocations)), _count(round(d.errors)),
                                 _count(round(d.throttles)), _ms(d.avg_duration), _ms(d.duration_max)])
                bars.append(_share(d.invocations, busiest))
            blocks.append(_Table(
                ["Day (UTC)", "Calls", "Errors", "Throttles", "Avg run time", "Longest"], day_rows, bars=bars,
                bar_label="Calls vs. the busiest day", max_rows=0,
                title=f"The last {days} days (CloudWatch; days without data are left out)",
            ))
        if cost:
            basis = (f"{self._price_basis()}; usage over the last {days} days scaled to a month" if m is not None
                     else self._price_basis())
            blocks.append(_Table(
                ["Part", "Est. $/month"], [[_PARTS.get(part, part), human_money(value)] for part, value in cost.items()],
                title=f"Estimated monthly cost: {human_money(_total(cost))} ({basis}, before the free tier)",
                max_rows=0,
            ))
        if fn.tags:
            blocks.append(_Table(["Tag", "Value"], [[k, v] for k, v in sorted(fn.tags.items())], title="Tags",
                                 collapsed=True, max_rows=0))
        blocks.append(_Text(json.dumps(fn.raw, indent=2, default=str), collapsed=True,
                            title="Configuration, as Lambda returns it (environment values hidden)"))
        steps = []
        if m is not None and m.errors:
            steps.append((self._call_for("errors", fn), "its errors, grouped by cause"))
        steps.append((self._call_for("performance", fn), "run times, memory used and cold starts"))
        if fn.package_type == "Zip":
            steps.append((self._call_for("code", fn), "the files in its package, and the handler's source"))
        if len(steps) < 3:
            steps.append((self._call_for("logs", fn), "the newest lines it logged"))
        blocks.append(_Next(steps))
        self._show(blocks)

    # ------------------------------------------------------------------ when something goes wrong

    @_friendly_errors
    def errors(self, name: str, *, since: Any = "24h", region: str | None = None, limit: Any = 10_000) -> None:
        """A function's errors in the last 24 hours, grouped by cause: timeouts, running out of memory, code that
        can't load, missing permissions and exceptions, each with how often, when, an example and the run to look
        at, next to CloudWatch's count of failed and throttled calls. since='7d' or '2026-10-01' looks further
        back; reading stops at the newest `limit` error lines."""
        limit = _as_count(limit, "limit")
        with self._progress("Reading error lines", unit="lines") as tick, self._named(name, region):
            report = self.core.errors(name, since=since, region=region, limit=limit, progress=tick)
        fn, m = report.function, report.metrics
        window = _window((report.until - report.since).total_seconds())
        found = error_findings(report)
        timeouts = sum(g.count for g in report.groups if g.kind == "Timeout")
        out_of_memory = sum(g.count for g in report.groups if g.kind == "Out of memory")
        last = max((g.last for g in report.groups), default=None)
        cards: list[tuple[str, ...]] = [
            ("Failed calls (CloudWatch)", _count(round(m.errors)) if m is not None else "-"),
            ("Calls", _count(round(m.invocations)) if m is not None else "-"),
            ("Error rate", _pct(m.error_rate) if m is not None and m.invocations else "-"),
            ("Error lines", _count(report.errors_found)),
            ("Causes", _count(len(report.groups))),
            ("Timeouts", _count(timeouts), "warn" if timeouts else ""),
            ("Out of memory", _count(out_of_memory), "warn" if out_of_memory else ""),
            ("Last error", human_age(last, report.until) if last else "-"),
        ]
        if m is not None and m.throttles:
            cards.insert(3, ("Throttled calls", _count(round(m.throttles)), "warn"))
        sub = " · ".join(filter(None, [
            f"log group {report.log_group}",
            f"the last {window} (since {_fmt_dt(report.since)} UTC)",
            fn.region if fn.region != self.core.region else "",
        ]))
        blocks: list[Any] = [_Title(f"Errors in {fn.name}", sub), _Cards(cards)]
        blocks += self._log_notes(report.errors, fn, report.log_group)
        if report.truncated:
            blocks.append(_Note(
                f"Reading stopped at the newest {_count(limit)} error lines, which reach back to "
                f"{_fmt_dt(report.covered_from)} UTC: pass limit= to read more, or a shorter since=."
            ))
        empty = (f"No errors in the last {window}: no error lines in its logs"
                 + (", and CloudWatch counted no failed calls." if m is not None else "."))
        blocks.append(_Findings(found, empty=empty if "logs" not in report.errors else ""))
        if report.groups:
            lines = sum(g.count for g in report.groups)
            blocks.append(_Table(
                ["Cause", "Times", "Last", "First", "Example"],
                [[g.kind, _count(g.count), human_age(g.last, report.until), human_age(g.first, report.until),
                  _clip(g.message, 300)] for g in report.groups],
                bars=[_share(g.count, lines) for g in report.groups], bar_label="Share",
                title="Errors by cause (from its log lines: a failed call can log more than one)",
            ))
            blocks.append(_Table(
                ["Time (UTC)", "Request ID", "Line"],
                [[_stamp(e.time), e.request_id or "-", _clip(_log_line(e.message).strip().splitlines()[0], 300)]
                 for e in report.newest],
                title="The newest error lines", max_rows=0,
            ))
        steps = []
        newest = next((e for e in report.newest if e.request_id), None)
        if newest is not None:
            steps.append((self._call_for("logs", fn, request_id=newest.request_id, **_since_arg(since, "24h")),
                          "the whole run behind the newest error"))
        if timeouts or out_of_memory:
            steps.append((self._call_for("performance", fn, **_since_arg(since, "24h")),
                          "run times and memory: how close runs come to the limits"))
        steps.append((self._call_for("function_info", fn), "its settings, triggers and last 30 days"))
        blocks.append(_Next(steps))
        self._show(blocks)

    def _log_notes(self, errors: dict[str, str], fn: Function, group: str) -> list[Any]:
        """Notes for what couldn't be read: no log group yet, logs that can't be read, or CloudWatch's numbers."""
        notes: list[Any] = []
        code = errors.get("logs")
        if code == "ResourceNotFoundException":
            notes.append(_Note(
                f"{fn.name} has no log group yet ({group}): it hasn't logged anything. It creates one on its first "
                "run, if its role may write logs (logs:CreateLogGroup, logs:CreateLogStream, logs:PutLogEvents).",
                "warn"))
        elif code:
            notes.append(_Note(f"Couldn't read its logs ({_why(code, 'logs:FilterLogEvents')}).", "warn"))
        if errors.get("metrics"):
            notes.append(_Note(
                f"Couldn't read CloudWatch's numbers ({_why(errors['metrics'], 'cloudwatch:GetMetricData')}).", "warn"))
        return notes

    @_friendly_errors
    def logs(
        self,
        name: str,
        *,
        since: Any = None,
        search: str | None = None,
        request_id: str | None = None,
        n: Any = 50,
        region: str | None = None,
    ) -> None:
        """The newest lines a function logged (50, from the last hour), with each run's REPORT line summed up: run
        time, memory used and cold start. search='KeyError' keeps lines with that text (case-sensitive; a
        CloudWatch Logs filter pattern works too), request_id= shows one run from start to end (from the last 24
        hours), and since='24h' or '2026-10-01' looks further back."""
        n = _as_int(n, "n")
        if n < 1:
            raise ValueError("n= is how many lines to show: 1 or more")
        chosen = since  # next steps carry over a since= the user picked, not this command's default
        since = since if since is not None else ("24h" if request_id else "1h")
        pattern = _filter_pattern(search)
        limit = 10_000 if request_id else max(3 * n, 300)
        with self._progress("Reading log lines", unit="lines") as tick, self._named(name, region):
            page = self.core.log_events(name, since=since, pattern=pattern, request_id=request_id, limit=limit,
                                        region=region, progress=tick)
        fn = page.function
        window = _window((page.until - page.since).total_seconds())
        events = page.events
        phrase = _phrase(search)
        if phrase and not request_id:  # the same check here, for log stores that only roughly apply patterns
            events = [e for e in events if phrase in e.message]
        if not request_id:
            events = [e for e in events if not _lifecycle(e.message)]
        shown = events[-n:]
        runs = {e.request_id for e in shown if e.request_id and parse_report(e.message)}
        error_lines = [e for e in shown if classify_error(e.message)]
        cards: list[tuple[str, ...]] = [
            ("Lines shown", _count(len(shown))),
            ("Runs (REPORT lines)", _count(len(runs))),
            ("Error lines", _count(len(error_lines))),
            ("Newest line", human_age(shown[-1].time, page.until) if shown else "-"),
        ]
        what = (f"run {request_id}" if request_id
                else f"the newest {_plural(len(shown), 'line')} of the last {window}" if shown
                else f"the last {window}")
        sub = " · ".join(filter(None, [
            f"log group {page.log_group}",
            (f"lines containing {phrase!r}" if phrase else f"filter pattern {pattern}") if search and not request_id
            else "",
            what,
            "START and END lines left out" if not request_id else "",
        ]))
        title = f"Run {request_id} of {fn.name}" if request_id else f"Logs of {fn.name}"
        blocks: list[Any] = [_Title(title, sub), _Cards(cards)]
        blocks += self._log_notes(page.errors, fn, page.log_group)
        if not shown and "logs" not in page.errors:
            if request_id:
                blocks.append(_Note(
                    f"No run {request_id} in the last {window}. errors() and performance() name runs by their "
                    "request IDs; pass since= to look further back."
                ))
            elif page.latest is not None:
                back = max(1, math.ceil((page.until - page.latest).total_seconds() / 86400) + 1)
                blocks.append(_Note(
                    f"No lines{' matching ' + repr(search) if search else ''} in the last {window}. The newest line in "
                    f"its log group is from {human_age(page.latest, page.until)}: "
                    f"{self._call_for('logs', fn, since=f'{back}d', **({'search': search} if search else {}))}."
                ))
            else:
                blocks.append(_Note(
                    f"No lines in the last {window}"
                    + (f" matching {search!r}" if search else ", and none at all in its log group: it hasn't run, "
                       "or it logs somewhere else") + "."
                ))
        if page.truncated:
            blocks.append(_Note(
                f"Reading stopped after {_count(len(page.events))} lines, back to {_fmt_dt(page.covered_from)} UTC: "
                "these are the newest."
            ))
        if shown:
            rows = []
            for e in shown:
                kind = classify_error(e.message)
                rows.append([
                    _stamp(e.time),
                    e.request_id or "-",
                    _Tone(kind[0], "bad") if kind else ("report" if parse_report(e.message) else ""),
                    _log_line(e.message),
                ])
            blocks.append(_Table(["Time (UTC)", "Request ID", "Kind", "Line"], rows, max_rows=0,
                                 title="Oldest first, the newest at the bottom"))
        steps = []
        if error_lines:
            steps.append((self._call_for("errors", fn, **_since_arg(chosen, "24h")), "every error, grouped by cause"))
            rid = next((e.request_id for e in reversed(error_lines) if e.request_id), None)
            if rid and not request_id:
                steps.append((self._call_for("logs", fn, request_id=rid), "the whole run behind the newest error"))
        if not request_id and since == "1h":
            steps.append((self._call_for("logs", fn, since="24h"), "the last 24 hours"))
        steps.append((self._call_for("performance", fn), "run times, memory used and cold starts"))
        blocks.append(_Next(steps[:3]))
        self._show(blocks)

    @_friendly_errors
    def performance(self, name: str, *, since: Any = "24h", region: str | None = None, limit: Any = 5_000) -> None:
        """How a function's runs went in the last 24 hours, from the REPORT line Lambda logs after each one: run time
        (median, slowest 1 in 100, longest) against the timeout, memory used against what it has and the size that
        would do, cold starts and their start-up time, timeouts, and the slowest runs. since='7d' looks further
        back; reading stops at the newest `limit` runs."""
        limit = _as_count(limit, "limit")
        with self._progress("Reading REPORT lines", unit="lines") as tick, self._named(name, region):
            perf = self.core.performance(name, since=since, region=region, limit=limit, progress=tick)
        fn, runs = perf.function, perf.invocations
        window = _window((perf.until - perf.since).total_seconds())
        found = performance_findings(perf, prices=self.core.prices)
        blocks: list[Any] = [_Title(
            f"Performance of {fn.name}",
            f"from {_plural(len(runs), 'REPORT line')} (one per run) in the last {window} · log group "
            f"{perf.log_group}",
        )]
        if not runs:
            blocks += self._log_notes(perf.errors, fn, perf.log_group)
            if "logs" not in perf.errors:
                blocks.append(_Note(
                    f"No REPORT lines in the last {window}: it wasn't called, or its logs go somewhere else. "
                    f"{self._call_for('function_info', fn)} shows when it was last called."
                ))
            self._show(blocks)
            return
        durations = [r.duration for r in runs]
        billed = [r.billed for r in runs]
        used = [float(r.max_memory) for r in runs if r.max_memory]
        cold = perf.cold_starts
        init = [r.init for r in cold if r.init is not None]
        memory = max((r.memory for r in runs if r.memory), default=fn.memory)
        timeouts = [r for r in runs if r.status == "timeout"]
        longest = max(durations)
        # the same limits performance_findings() warns at, so a card is amber only when a warning is about it
        near_timeout = bool(timeouts) or (percentile(durations, 99) or 0) >= 0.8 * fn.timeout * 1000
        memory_warning = any((r.error_type or "").endswith("OutOfMemory") for r in runs) or (
            max(used, default=0) >= 0.9 * memory)
        cards: list[tuple[str, ...]] = [
            ("Runs", _count(len(runs))),
            ("Median run time", human_ms(percentile(durations, 50))),
            ("1 in 100 takes", human_ms(percentile(durations, 99))),
            ("Longest", f"{human_ms(longest)} of {fn.timeout} s", "warn" if near_timeout else ""),
            ("Memory used", f"{max(used, default=0):,.0f} of {memory:,} MB", "warn" if memory_warning else ""),
            ("Cold starts", f"{_pct(len(cold) / len(runs))}" + (f" · {human_ms(sum(init) / len(init))}" if init else "")),
            ("Timeouts", _count(len(timeouts)), "warn" if timeouts else ""),
        ]
        blocks.append(_Cards(cards))
        blocks += self._log_notes(perf.errors, fn, perf.log_group)
        if perf.truncated:
            blocks.append(_Note(
                f"Reading stopped at the newest {_count(limit)} runs, back to {_fmt_dt(perf.covered_from)} UTC: "
                "pass limit= to read more."
            ))
        blocks.append(_Findings(found, empty=(
            f"Runs stay well inside the {fn.timeout} s timeout and the {memory:,} MB of memory, and the memory isn't "
            "far more than they use.")))

        def spread(values: list[float], show: Callable[[float | None], str]) -> list[str]:
            return [show(percentile(values, q)) for q in (50, 90, 99)] + [show(max(values) if values else None)]

        measures = [
            ["Run time", *spread(durations, _ms)],
            ["Billed time", *spread(billed, _ms)],
            ["Memory used", *spread(used, _mb)],
        ]
        if init:
            measures.append([f"Cold start ({_plural(len(init), 'run')})", *spread(init, _ms)])
        blocks.append(_Table(["Measure", "Median", "p90", "p99", "Most"], measures, max_rows=0,
                             title=f"Run time and memory (it has {memory:,} MB and a {fn.timeout} s timeout)"))
        bands: list[list[Any]] = []
        counts: list[int] = []
        low = 0.0
        for label, high in _DURATION_BANDS:
            count = sum(1 for d in durations if d >= low and (high is None or d < high))
            if count:
                bands.append([label, _count(count)])
                counts.append(count)
            low = float(high or 0)
        blocks.append(_Table(["Run time", "Runs"], bands, bars=[_share(c, len(runs)) for c in counts],
                             title="How long runs take", max_rows=0))
        slowest = sorted(runs, key=lambda r: r.duration, reverse=True)[:10]
        blocks.append(_Table(
            ["Time (UTC)", "Request ID", "Run time", "Billed", "Memory used", "Cold start", "Status"],
            [[_stamp(r.time), r.request_id, _ms(r.duration), _ms(r.billed), _mb(r.max_memory),
              _ms(r.init) if r.init is not None else "-",
              _Tone(r.status + (f" ({r.error_type})" if r.error_type else ""), "bad") if r.status else "ok"]
             for r in slowest],
            title="The slowest runs", max_rows=0,
        ))
        steps = [(self._call_for("logs", fn, request_id=slowest[0].request_id, **_since_arg(since, "24h")),
                  "everything the slowest run logged")]
        if timeouts:
            steps.append((self._call_for("errors", fn, **_since_arg(since, "24h")), "every error, grouped by cause"))
        steps.append((self._call_for("function_info", fn), "its settings, triggers and last 30 days"))
        blocks.append(_Next(steps))
        self._show(blocks)

    # ------------------------------------------------------------------ code

    @_friendly_errors
    def code(self, name: str, file: str | None = None, *, region: str | None = None, max_size: Any = "50MB") -> None:
        """What's in a function's deployment package: its files and folders by size, findings (secrets packed in it,
        size near the limit, files that never run), and the source of the file that holds the handler, or of
        file='utils.py' (a path, a name or a glob). Downloads the .zip, up to max_size, from the link Lambda gives;
        nothing in it is run. A secrets file's text (.env, keys) isn't shown, and a container image is named, not
        pulled."""
        with self._named(name, region):
            package = self.core.code(name, file=file, max_size=max_size, region=region)
        fn = package.function
        found = package_findings(package)
        blocks: list[Any] = [_Title(
            f"Code of {fn.name}",
            " · ".join(filter(None, [
                f"{fn.package_type} package" if fn.package_type == "Zip" else "container image",
                f"handler {fn.handler}" if fn.handler else "",
                fn.runtime or "",
            ])),
        )]
        if package.note and not package.files:
            blocks += [_Note(package.note), _Next([(self._call_for("function_info", fn), "its settings")])]
            self._show(blocks)
            return
        # the same checks package_findings() warns about, so a card is amber only when a warning is about it
        handler_missing = bool(fn.handler) and runtime_family(fn.runtime) in _HANDLER_EXTENSIONS and not (
            package.handler_file)
        too_big = package.unzipped + fn.layer_size >= 0.8 * UNZIPPED_LIMIT
        blocks.append(_Cards([
            ("Package (zipped)", human_size(package.size)),
            ("Unzipped", human_size(package.unzipped), "warn" if too_big else ""),
            ("Files", _count(len(package.files))),
            ("Layers", f"{len(fn.layers):,}" + (f" · {human_size(fn.layer_size)}" if fn.layers else "")),
            ("Handler file", package.handler_file or "-", "warn" if handler_missing else ""),
        ]))
        blocks.append(_Findings(found, empty="Nothing in the package stands out: the handler's file is there, no "
                                             "secrets files, and it's well inside Lambda's size limit."))
        folders: dict[str, list[int]] = defaultdict(lambda: [0, 0])
        for f in package.files:
            folders[_top_folder(f.path)][0] += 1
            folders[_top_folder(f.path)][1] += f.size
        top = sorted(folders.items(), key=lambda item: -item[1][1])[:12]
        blocks.append(_Table(
            ["Folder", "Files", "Size"], [[folder, _count(n), human_size(size)] for folder, (n, size) in top],
            bars=[_share(size, package.unzipped) for _, (_, size) in top], bar_label="Share",
            title="What takes the space (unzipped)", max_rows=0,
        ))
        blocks.append(_Table(
            ["File", "Size"], [[f.path, human_size(f.size)] for f in sorted(package.files, key=lambda f: f.path)],
            title="Every file", collapsed=True,
        ))
        if package.note:
            blocks.append(_Note(package.note))
        if package.source is not None:
            label = "the handler's file" if package.shown_file == package.handler_file else "the file you asked for"
            blocks.append(_Text(package.source, title=f"{package.shown_file} ({label})"))
            if package.source_truncated:
                blocks.append(_Note(f"Only the first {human_size(_SOURCE_LIMIT)} of {package.shown_file} is shown."))
        others = [f.path for f in package.files if f.path != package.shown_file and not _JUNK_RE.search(f.path)
                  and f.path.endswith((".py", ".js", ".mjs", ".ts", ".rb")) and "/" not in f.path]
        steps = []
        if others:
            steps.append((self._call_for("code", fn, file=others[0]), "another of its own files"))
        steps.append((self._call_for("function_info", fn), "its settings, triggers and last 30 days"))
        blocks.append(_Next(steps))
        self._show(blocks)
