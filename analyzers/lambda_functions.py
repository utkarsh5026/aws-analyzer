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

and a window on top of them, LambdaExplorer (explore()): every function to click
through, and for the one you pick its logs run by run, errors, run times, code and
settings (ipywidgets, which SageMaker notebooks have).

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
    ui.explore()                                      # all of it in a window, by clicking: functions, logs, errors

    explore("etl-nightly", tab="logs")                # the window, straight to one function's logs

    lam = ui.core                                     # same analyzer, raw data
    df = lam.overview(regions="all").to_df()          # one row per function
    detail = lam.describe("etl-nightly")              # FunctionDetail: .function, .triggers, .aliases, .metrics
    runs = lam.log_runs("etl-nightly").runs           # LogRun per call: its lines, status, run time, memory
"""

from __future__ import annotations

import asyncio
import dataclasses
import difflib
import fnmatch
import functools
import html
import importlib
import inspect
import io
import json
import keyword
import math
import posixpath
import re
import sys
import threading
import time
import tokenize
import unicodedata
import urllib.request
import zipfile
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Generator, Iterable
from urllib.parse import quote, unquote

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


def _css_height(height: int | str | None) -> str | None:
    """height= as CSS: a number is pixels (720, '720'), other text is CSS as written ('80vh'); None is None."""
    text = "" if height is None else str(height).strip()
    return f"{text}px" if re.fullmatch(r"\d+(\.\d+)?", text) else text or None


def _running_loop() -> asyncio.AbstractEventLoop | None:
    """The kernel's event loop, which runs the window's clicks in a notebook; None elsewhere (a script, the tests),
    where a test run asks its questions while the click waits."""
    try:
        return asyncio.get_running_loop()
    except RuntimeError:
        return None


def _cell_number() -> Any:
    """The running cell's execution count in IPython (None elsewhere): a cell that ends with chat() shows the window
    once, not twice."""
    try:
        from IPython.core.getipython import get_ipython
    except ImportError:
        return None
    shell = get_ipython()
    return getattr(shell, "execution_count", None) if shell is not None else None


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

    def add_extras(self, found: dict[str, tuple[list[Trigger], list[ProvisionedConcurrency], dict[str, str]]]) -> None:
        """Adds the reads made per function (LambdaAnalyzer._extras: the callers its resource policy allows, its
        provisioned concurrency, and what couldn't be read), by function ARN."""
        for fn in self.functions:
            if fn.arn in found:
                triggers, provisioned, errors = found[fn.arn]
                self.triggers.setdefault(fn.arn, []).extend(triggers)
                if provisioned:
                    self.provisioned[fn.arn] = provisioned
                fn.errors.update(errors)
        self.details = True

    def add_numbers(self, metrics: dict[str, FunctionMetrics], read: int, peaks: dict[str, float | None],
                    errors: dict[str, str]) -> None:
        """Adds CloudWatch's numbers (LambdaAnalyzer._numbers): each function's by ARN, how many metrics that read,
        each region's most runs at once, and 'region:metrics' -> the error code of a region that couldn't be read."""
        self.metrics.update(metrics)
        self.metrics_read += read
        for region, peak in peaks.items():
            if region in self.accounts:
                self.accounts[region].peak_concurrency = peak
        self.errors.update(errors)

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
    matched: bool = False  # it matched the search that found its run (log_runs(search=...))


@dataclass
class LogRun:
    """One run (a call) as its log stream recorded it: its lines from START to REPORT, oldest first, with the start-up
    lines just before START on a cold start. request_id is None for lines that belong to no run in the window."""

    request_id: str | None
    stream: str = ""
    events: list[LogEvent] = field(default_factory=list)
    report: Invocation | None = None  # its REPORT line: run time, memory used, cold start; None until it ends

    @property
    def start(self) -> datetime:
        return self.events[0].time

    @property
    def end(self) -> datetime:
        return self.events[-1].time

    @property
    def key(self) -> str:
        """What tells it apart from the window's other runs, for the window to remember which ones are open."""
        return f"{self.stream}|{self.request_id or self.start.isoformat()}"

    @functools.cached_property
    def failures(self) -> list[tuple[LogEvent, str, str]]:
        """(line, kind, summary) for each line classify_error() says reports an error."""
        found = []
        for event in self.events:
            kind = classify_error(event.message)
            if kind:
                found.append((event, *kind))
        return found

    @property
    def status(self) -> str:
        """'timeout'; 'failed' (an error it didn't handle, running out of memory, the runtime exiting); 'logged' (it
        logged an error, which it may have handled); 'running' (no REPORT line yet: still running, or it ends after
        the window); 'ok'; or 'outside' (lines that belong to no run)."""
        if self.request_id is None:
            return "outside"
        report = self.report
        kinds = {kind for _, kind, _ in self.failures}
        if (report is not None and report.status == "timeout") or "Timeout" in kinds:
            return "timeout"
        if (report is not None and (report.status in ("error", "failure") or report.error_type)) or (
                kinds - {"Logged error"}):
            return "failed"
        if kinds:
            return "logged"
        return "running" if report is None else "ok"

    @property
    def error(self) -> tuple[str, str] | None:
        """(kind, summary) of what made it fail, else of the first error it logged; None when it logged none."""
        if not self.failures and self.report is not None and self.report.error_type:
            return self.report.error_type, f"{self.report.error_type} ({self.report.status or 'error'})"
        serious = [(kind, summary) for _, kind, summary in self.failures if kind != "Logged error"]
        return (serious or [(kind, summary) for _, kind, summary in self.failures] or [None])[0]

    @property
    def cold(self) -> bool:
        """Whether it was a cold start: its REPORT line has a start-up (or SnapStart restore) time, or its stream logged
        the runtime starting just before it."""
        if self.report is not None and (self.report.init is not None or self.report.restore is not None):
            return True
        return any((_marker(e.message) or ("",))[0] == "init" for e in self.events)

    @property
    def duration(self) -> float | None:
        """How long it ran, in ms, from its REPORT line."""
        return self.report.duration if self.report is not None else None

    @property
    def matched(self) -> bool:
        return any(e.matched for e in self.events)


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

    @property
    def runs(self) -> list[LogRun]:
        """The events grouped into the runs they belong to, newest first (split_runs). After a search
        (log_runs(search=...)), only the runs with a line that matched it."""
        runs = split_runs(self.events)
        return [run for run in runs if run.matched] if any(e.matched for e in self.events) else runs

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
    stream: str = ""  # the log stream it ran in (performance() fills it in), where the rest of its lines are


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


_PLATFORM_KINDS = {"platform.start": "start", "platform.runtimeDone": "end", "platform.report": "report",
                   "platform.initStart": "init", "platform.initRuntimeDone": "init", "platform.initReport": "init",
                   "platform.restoreStart": "init", "platform.restoreRuntimeDone": "init",
                   "platform.restoreReport": "init", "platform.extension": "init", "platform.telemetrySubscription":
                   "init"}  # Lambda's own JSON records -> where they sit in a run
_STARTING = ("INIT_START", "INIT_REPORT", "INIT_RUNTIME_DONE", "RESTORE_START", "RESTORE_REPORT", "EXTENSION",
             "TELEMETRY")  # the text lines Lambda writes while an execution environment starts


def _marker(message: str) -> tuple[str, str | None] | None:
    """('start' | 'end' | 'report' | 'init', request ID) for the lines Lambda itself writes around a run (START, END,
    REPORT and the start-up lines), in text or JSON format; None for any other line."""
    text = message.lstrip()
    for kind, prefix in (("start", "START RequestId:"), ("end", "END RequestId:"), ("report", "REPORT RequestId:")):
        if text.startswith(prefix):
            words = text[len(prefix):].split()
            return kind, words[0] if words else None
    if text.startswith(_STARTING):
        return "init", None
    if text.startswith("{") and '"platform.' in text:
        try:
            doc = json.loads(text)
        except ValueError:
            return None
        kind = _PLATFORM_KINDS.get(str(doc.get("type"))) if isinstance(doc, dict) else None
        if kind:
            record = doc.get("record") if isinstance(doc.get("record"), dict) else {}
            return kind, str(record["requestId"]) if record.get("requestId") else None
    return None


def split_runs(events: Iterable[LogEvent]) -> list[LogRun]:
    """Log lines grouped into the runs they belong to, newest run first. An execution environment (a log stream) runs
    one call at a time, so a run is what its stream recorded from its START line to its REPORT line, with the start-up
    lines just before START on a cold start; a line that names an open run's request ID goes to that run. A run the
    window cuts off keeps the lines the window has (no START, or no REPORT yet), and lines that belong to no run come
    back as a LogRun with request_id None, one per stream."""
    streams: dict[str, list[LogEvent]] = defaultdict(list)
    for event in events:
        streams[event.stream].append(event)
    runs: list[LogRun] = []
    for stream, items in streams.items():
        items.sort(key=lambda e: e.time)
        running: dict[str, LogRun] = {}  # request ID -> its run, in the order they started
        ended: dict[str, LogRun] = {}  # request ID -> its run, once its REPORT line came
        pending: list[LogEvent] = []  # lines no run has claimed yet: a cold start's start-up, or a run's cut-off start

        def begin(request_id: str) -> LogRun:
            nonlocal pending
            run = LogRun(request_id, stream, [] if running else pending)
            if not running:
                pending = []
            running[request_id] = run
            return run

        for event in items:
            kind, request_id = _marker(event.message) or ("", None)
            if kind == "start" and request_id:
                if request_id in running:  # a second START for the same ID: the first never reported
                    runs.append(running.pop(request_id))
                begin(request_id).events.append(event)
            elif kind == "end" and request_id in ended and request_id not in running:
                ended[request_id].events.append(event)  # its REPORT line came first (the same millisecond)
            elif kind in ("end", "report") and request_id:
                run = running.get(request_id) or begin(request_id)  # started before the window
                run.events.append(event)
                if kind == "report":
                    run.report = parse_report(event.message, event.time)
                    if run.report is not None:
                        run.report.stream = stream
                    runs.append(running.pop(request_id))
                    ended[request_id] = run
            else:
                run = running.get(event.request_id or "")
                if run is None and running:
                    run = next(reversed(running.values()))  # the run that started last (one at a time, usually)
                if run is not None:
                    run.events.append(event)
                else:
                    pending.append(event)
        runs.extend(running.values())  # no REPORT yet: still running, or it ended after the window
        if pending:
            runs.append(LogRun(None, stream, pending))
    return sorted(runs, key=lambda r: (r.start, r.end), reverse=True)


_LEVEL_TAG_RE = re.compile(r"^\[(ERROR|CRITICAL|FATAL|WARNING|WARN|INFO|DEBUG|TRACE)\]")
_LEVELS = {"ERROR": "error", "CRITICAL": "error", "FATAL": "error", "WARNING": "warn", "WARN": "warn",
           "INFO": "info", "DEBUG": "debug", "TRACE": "debug"}


def line_level(message: str) -> str:
    """What kind of line a log line is: 'report' (the REPORT line that ends a run), 'platform' (Lambda's START, END and
    start-up lines), 'error', 'warn', 'info', 'debug', or '' when it doesn't say. Reads Lambda's text formats (Python's
    '[ERROR]', Node's '<time>\\t<request>\\tWARN\\t...') and JSON logs (their "level")."""
    text = message.lstrip()
    mark = _marker(text)
    if mark is not None:
        return "report" if mark[0] == "report" else "platform"
    if classify_error(text):
        return "error"
    if text.startswith("{"):
        try:
            doc = json.loads(text)
        except ValueError:
            doc = None
        if isinstance(doc, dict):
            return _LEVELS.get(str(doc.get("level") or doc.get("levelname") or doc.get("severity") or "").upper(), "")
    tag = _LEVEL_TAG_RE.match(text)
    if tag:
        return _LEVELS[tag.group(1)]
    parts = text.split("\t", 3)
    if len(parts) >= 3 and _TIMESTAMP_RE.match(parts[0].strip()) and parts[2].strip() in _LEVELS:
        return _LEVELS[parts[2].strip()]
    word = re.match(r"^(WARNING|WARN|INFO|DEBUG)\b", text)
    return _LEVELS[word.group(1)] if word else ""


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
_SOURCE_LIMIT = 200 * KB  # the most of one file code() reads and shows
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


def read_package(fn: Function, data: bytes | None, file: str | None = None) -> CodePackage:
    """A deployment package (the .zip as code() downloads it) as the files in it, the file that holds the handler, and
    the text of that file, or of file= (a path, a file name or a glob like '*.py'). No AWS calls, and nothing in it is
    run. A secrets file (.env, keys, credentials) is named but its text isn't read. data=None means there's nothing to
    read: a container image, or no link to the code (the note says which)."""
    package = CodePackage(fn)
    if fn.package_type == "Image":
        package.note = (
            f"It's a container image ({fn.image_uri or 'image URI not shown'}): its code is in the image, which "
            "this doesn't pull. docker pull it from ECR to look inside."
        )
        return package
    if data is None:
        package.note = "Lambda gave no link to download its code."
        return package
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


def search_rank(text: str, fields: Iterable[Any]) -> int | None:
    """Where something goes in a search for `text` among its fields (a name, an ID, a description...), best first:
    0 when a field is the text (an ID pasted whole), 1 when one starts with it, 2 when one holds it, 3 when every
    word of it is in some field; None when a word is in none. Case is ignored, so 'k7qj' finds 'K7QJ2M4XNA'; an
    empty search finds everything, at 3."""
    wanted = " ".join(str(text or "").lower().split())
    if not wanted:
        return 3
    values = [" ".join(str(f).lower().split()) for f in fields if f]
    if wanted in values:
        return 0
    if any(v.startswith(wanted) for v in values):
        return 1
    if any(wanted in v for v in values):
        return 2
    if all(any(word in v for v in values) for word in wanted.split()):
        return 3
    return None


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
_SEARCH_HITS = 500  # log_runs(search=...) reads at most this many matching lines (the newest)...
_SEARCH_WINDOWS = 30  # ...and the whole runs around the newest this many of them (a read each)


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
            ov.add_extras(self._all_extras(ov.functions, progress=progress))
        if metrics:
            ov.add_numbers(*self._numbers(ov.functions, since=since, until=until, limits=set(ov.accounts)))
        return ov

    def _extras(self, fn: Function) -> tuple[list[Trigger], list[ProvisionedConcurrency], dict[str, str]]:
        """The reads overview() makes per function: the callers its resource policy allows, and its provisioned
        concurrency, with section -> error code for what couldn't be read."""
        triggers: list[Trigger] = []
        provisioned: list[ProvisionedConcurrency] = []
        errors: dict[str, str] = {}
        try:
            triggers = parse_policy(self.policy(fn.name, fn.region))
        except ClientError as exc:
            errors["policy"] = _error_code(exc)
        except BotoCoreError as exc:
            errors["policy"] = type(exc).__name__
        try:
            provisioned = self.provisioned(fn.name, fn.region)
        except ClientError as exc:
            errors["provisioned"] = _error_code(exc)
        except BotoCoreError as exc:
            errors["provisioned"] = type(exc).__name__
        return triggers, provisioned, errors

    def _all_extras(
        self, functions: list[Function], *, progress: Callable[..., None] | None = None
    ) -> dict[str, tuple[list[Trigger], list[ProvisionedConcurrency], dict[str, str]]]:
        """_extras() for each function, in parallel, by function ARN (Overview.add_extras adds them)."""
        for region in dict.fromkeys(fn.region for fn in functions):  # each region's client before the threads use it
            self._service("lambda", region or None)
        found: dict[str, tuple[list[Trigger], list[ProvisionedConcurrency], dict[str, str]]] = {}
        for start in range(0, len(functions), 50):  # in batches, so progress moves as they finish
            batch = functions[start : start + 50]
            found.update({fn.arn: extras for fn, extras in zip(batch, self._map(self._extras, batch))})
            if progress:
                progress(min(start + 50, len(functions)), len(functions))
        return found

    def _numbers(
        self, functions: list[Function], *, since: datetime, until: datetime, limits: set[str] | None = None
    ) -> tuple[dict[str, FunctionMetrics], int, dict[str, float | None], dict[str, str]]:
        """CloudWatch's numbers for overview() (Overview.add_numbers adds them): each function's from `since` to `until`
        by ARN, concurrency included (what provisioned concurrency is weighed against); how many metrics that read;
        the most runs at once in each region of `limits`; and 'region:metrics' -> the error code of a region that
        couldn't be read."""
        found: dict[str, FunctionMetrics] = {}
        read = 0
        peaks: dict[str, float | None] = {}
        errors: dict[str, str] = {}
        by_region: dict[str, list[Function]] = defaultdict(list)
        for fn in functions:
            by_region[fn.region or self.region].append(fn)
        for region, group in by_region.items():
            try:
                numbers, count = self.metrics(group, since=since, until=until, concurrency=True)
                found.update(numbers)
                read += count
                if region in (limits or ()):
                    peaks[region] = self.peak_concurrency(region, since=since, until=until)
                    read += 1
            except ClientError as exc:
                errors[f"{region}:metrics"] = _error_code(exc)
            except BotoCoreError as exc:
                errors[f"{region}:metrics"] = type(exc).__name__
        return found, read, peaks, errors

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
        return self._log_page(fn, start, end, pattern=pattern, request_id=request_id, limit=limit, progress=progress)

    def _log_page(
        self,
        fn: Function,
        start: datetime,
        end: datetime,
        *,
        pattern: str | None = None,
        request_id: str | None = None,
        limit: int | None = 10_000,
        progress: Callable[..., None] | None = None,
    ) -> LogPage:
        """log_events() for a function already read (no GetFunction)."""
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

    def log_runs(
        self,
        ref: str,
        *,
        since: Any = "1h",
        until: Any = None,
        search: str | None = None,
        limit: int | None = 3_000,
        region: str | None = None,
        progress: Callable[..., None] | None = None,
    ) -> LogPage:
        """What a function logged from `since` to `until`, as runs: .runs is a LogRun per call, newest first, each with
        every line it logged, its status (ok, failed, timeout...), run time, memory and cold start. Reads the newest
        `limit` lines. search= ('KeyError', an order ID, a request ID, or a CloudWatch Logs filter pattern) keeps the
        runs with a matching line, each with all of its lines (the matching ones are marked)."""
        fn = self.function(ref, region=region)
        start, end = self._window(since, until)
        return self._log_runs(fn, start, end, search=search, limit=limit, progress=progress)

    def _log_runs(
        self,
        fn: Function,
        start: datetime,
        end: datetime,
        *,
        search: str | None = None,
        limit: int | None = 3_000,
        progress: Callable[..., None] | None = None,
    ) -> LogPage:
        """log_runs() for a function already read (no GetFunction)."""
        pattern = _filter_pattern(search)
        if not pattern:
            return self._log_page(fn, start, end, limit=limit, progress=progress)
        page = LogPage(fn, fn.log_group, start, end, pattern=pattern)
        try:
            hits, page.truncated, page.covered_from = self._filter(
                fn.log_group, fn.region, start, end, pattern, _SEARCH_HITS, progress=progress)
            phrase = _phrase(search)
            if phrase:  # the same check here, for log stores that only roughly apply patterns
                hits = [e for e in hits if phrase in e.message]
            page.events = self._around(fn, hits, limit)
        except ClientError as exc:
            page.errors["logs"] = _error_code(exc)
            return page
        except BotoCoreError as exc:
            page.errors["logs"] = type(exc).__name__
            return page
        if not page.events:
            page.latest = self._latest(fn.log_group, fn.region)
        return page

    def _around(self, fn: Function, hits: list[LogEvent], limit: int | None = 3_000) -> list[LogEvent]:
        """Every line of the runs that `hits` (lines a search found) belong to, oldest first, the hits marked: each
        stream's lines from a timeout before a hit to a timeout after it (no run lasts longer), windows that overlap
        read once, the newest _SEARCH_WINDOWS of them."""
        pad = timedelta(seconds=fn.timeout + 10)
        spans: dict[str, list[list[datetime]]] = {}
        for hit in sorted(hits, key=lambda e: e.time):
            hit.matched = True
            windows = spans.setdefault(hit.stream, [])
            if windows and hit.time - pad <= windows[-1][1]:
                windows[-1][1] = hit.time + pad
            else:
                windows.append([hit.time - pad, hit.time + pad])
        reads = sorted(((stream, a, b) for stream, windows in spans.items() for a, b in windows if stream),
                       key=lambda job: job[2], reverse=True)[:_SEARCH_WINDOWS]
        wanted = {(e.stream, e.time, e.message) for e in hits}

        def read(job: tuple[str, datetime, datetime]) -> list[LogEvent]:
            stream, a, b = job
            return self._filter(fn.log_group, fn.region, a, b, None, limit, stream=stream)[0]

        found: dict[tuple[str, datetime, str], LogEvent] = {}
        for events in self._map(read, reads):
            for event in events:
                key = (event.stream, event.time, event.message)
                event.matched = key in wanted
                found.setdefault(key, event)
        for hit in hits:  # a hit whose window wasn't read still shows, on its own
            found.setdefault((hit.stream, hit.time, hit.message), hit)
        return sorted(found.values(), key=lambda e: e.time)

    def _run_around(
        self, fn: Function, moment: datetime, *, stream: str = "", request_id: str | None = None
    ) -> LogRun | None:
        """The whole run that logged a line at `moment` in `stream` (the one with request_id, when given): its stream's
        lines from a timeout before to a timeout after. Without a stream, a search for the request ID finds it."""
        pad = timedelta(seconds=fn.timeout + 10)
        if stream:
            events = self._filter(fn.log_group, fn.region, moment - pad, moment + pad, None, 10_000, stream=stream)[0]
        elif request_id:
            events = self._around(fn, self._filter(fn.log_group, fn.region, moment - pad, moment + pad,
                                                   f'"{request_id}"', _SEARCH_HITS)[0], 10_000)
        else:
            return None
        runs = [run for run in split_runs(events) if run.request_id]
        if request_id:
            return next((run for run in runs if run.request_id == request_id), None)
        return next((run for run in runs if run.start <= moment <= run.end), None)

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
        return self._errors(fn, start, end, limit=limit, progress=progress)

    def _errors(
        self,
        fn: Function,
        start: datetime,
        end: datetime,
        *,
        limit: int | None = 10_000,
        progress: Callable[..., None] | None = None,
    ) -> ErrorReport:
        """errors() for a function already read (no GetFunction)."""
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
        return self._performance(fn, start, end, limit=limit, progress=progress)

    def _performance(
        self,
        fn: Function,
        start: datetime,
        end: datetime,
        *,
        limit: int | None = 5_000,
        progress: Callable[..., None] | None = None,
    ) -> Performance:
        """performance() for a function already read (no GetFunction)."""
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
        runs = []
        for event in events:
            run = parse_report(event.message, event.time)
            if run is not None:
                run.stream = event.stream
                runs.append(run)
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

    def _package(self, fn: Function, max_size: Any = "50MB") -> bytes | None:
        """A function's deployment package (fn as function() returns it, with the link GetFunction gives), downloaded up
        to max_size; None for a container image, or when Lambda gave no link."""
        if fn.package_type == "Image" or not fn.code_location:
            return None
        return self._download(fn.code_location, parse_size(max_size))

    def code(
        self, ref: str, *, file: str | None = None, max_size: Any = "50MB", region: str | None = None
    ) -> CodePackage:
        """A function's deployment package: every file in the .zip with its size, the file that holds the
        handler, and the text of that file (or of file=, a path, a file name or a glob like '*.py'). Downloads the
        package (up to max_size) from the link Lambda gives; nothing in it is run. A secrets file (.env, keys,
        credentials) is named but its text isn't read, and a container image is named, not pulled."""
        fn = self.function(ref, region=region)
        return read_package(fn, self._package(fn, max_size), file=file)


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


@dataclass
class _Wiring:
    """How a function is wired, left to right: what calls it, the function, and where its results, failed events and
    logs go. A diagram in HTML, a line for each side in text."""

    inputs: list[tuple[str, str, str, str]]  # (kind, name, detail, tone 'warn' | 'bad' | '')
    center: tuple[str, str]  # (the function's name, what it runs)
    outputs: list[tuple[str, str, str, str]]  # (what goes there, where, detail, tone)
    title: str = ""
    empty: str = "Nothing calls it on its own"  # said in the inputs column when there are none


@dataclass
class _Columns:
    """Numbers over time, a column each (a day, or an hour), with part of each column in a second colour: errors in
    red, or a column's average under its longest. A row of columns in HTML, a sparkline in text."""

    items: list[tuple[str, float, float, str]]  # (label, the column's value, the part in the second colour, tooltip)
    title: str = ""
    part: str = "bad"  # the part's colour: 'bad' (red: errors) or 'dim' (the average under the longest)
    limit: float | None = None  # a dashed line at this value (a timeout), drawn when the columns come near it
    limit_label: str = ""
    unit: str = ""  # 'ms' shows the scale as run times; '' as counts


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
.lmb .wire{display:grid;grid-template-columns:minmax(0,1fr) 26px minmax(0,.9fr) 26px minmax(0,1fr);align-items:center;margin:6px 0 10px;max-width:980px}
.lmb .wire .wc{display:flex;flex-direction:column;gap:6px;min-width:0}
.lmb .wire .wa{text-align:center;font-size:18px;opacity:.45}
.lmb .wire .wn{border:1px solid rgba(127,127,127,.3);border-radius:8px;padding:5px 10px;line-height:1.35;min-width:0}
.lmb .wire .wn.warn{border-color:rgba(245,158,11,.8);background:rgba(245,158,11,.08)}
.lmb .wire .wn.bad{border-color:rgba(239,68,68,.8);background:rgba(239,68,68,.08)}
.lmb .wire .wn.none{border-style:dashed;opacity:.7}
.lmb .wire .wk{display:block;font-size:10px;font-weight:650;letter-spacing:.05em;text-transform:uppercase;opacity:.6}
.lmb .wire .wn b{display:block;font-weight:600;overflow-wrap:anywhere}
.lmb .wire .wd{display:block;font-size:11.5px;opacity:.7;overflow-wrap:anywhere}
.lmb .wire .wf{border:1px solid rgba(234,88,12,.55);border-radius:10px;padding:10px 12px;background:rgba(234,88,12,.06);text-align:center}
.lmb .wire .wf .wl{display:inline-flex;align-items:center;justify-content:center;width:26px;height:26px;border-radius:8px;margin-bottom:4px;color:#fff;font-weight:700;background:linear-gradient(135deg,#f97316,#ea580c)}
.lmb .wire .wf b{display:block;font-size:14px;overflow-wrap:anywhere}
.lmb .cols{display:flex;align-items:flex-end;gap:2px;height:120px;padding:0 0 0 2px;position:relative;border-bottom:1px solid rgba(127,127,127,.35);max-width:980px}
.lmb .cols i{flex:1 1 0;min-width:2px;max-width:34px;border-radius:2px 2px 0 0;background:rgba(59,130,246,.55);position:relative;display:flex;flex-direction:column;justify-content:flex-start}
.lmb .cols i:hover{background:rgba(59,130,246,.8)}
.lmb .cols i b{display:block;width:100%;border-radius:2px 2px 0 0;background:#ef4444}
.lmb .cols.dim i{background:rgba(59,130,246,.22);justify-content:flex-end}
.lmb .cols.dim i b{background:rgba(59,130,246,.75);border-radius:0}
.lmb .cols .lim{position:absolute;left:0;right:0;border-top:1.5px dashed rgba(239,68,68,.7);pointer-events:none}
.lmb .cols .lim span{position:absolute;right:0;top:-16px;font-size:10.5px;color:#ef4444;opacity:.85}
.lmb .cols .top{position:absolute;left:4px;top:-2px;font-size:10.5px;opacity:.55;pointer-events:none}
.lmb .colx{display:flex;justify-content:space-between;font-size:10.5px;opacity:.55;margin:3px 0 10px;max-width:980px}
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
        elif isinstance(block, _Wiring):
            out.append(_wiring_html(block))
        elif isinstance(block, _Columns):
            out.append(_columns_html(block))
    out.append("</div>")
    return "".join(out)


def _wiring_html(block: _Wiring) -> str:
    def node(kind: str, name: str, detail: str, tone: str) -> str:
        return (f'<div class="wn {tone if tone in _TONES else ""}"><span class="wk">{_esc(kind)}</span>'
                f"<b>{_esc(name or '-')}</b>" + (f'<span class="wd">{_prose(detail)}</span>' if detail else "") + "</div>")

    inputs = "".join(node(*item) for item in block.inputs) or f'<div class="wn none">{_esc(block.empty)}</div>'
    outputs = "".join(node(*item) for item in block.outputs)
    name, detail = block.center
    center = (f'<div class="wf"><span class="wl">λ</span><b>{_esc(name)}</b>'
              + (f'<span class="wd">{_esc(detail)}</span>' if detail else "") + "</div>")
    title = f"<h4>{_prose(block.title)}</h4>" if block.title else ""
    return (f'{title}<div class="wire"><div class="wc">{inputs}</div><div class="wa">→</div><div class="wc">{center}'
            f'</div><div class="wa">→</div><div class="wc">{outputs}</div></div>')


def _columns_html(block: _Columns) -> str:
    title = f"<h4>{_prose(block.title)}</h4>" if block.title else ""
    if not block.items:
        return title + '<div class="more">(none)</div>'
    tallest = max(value for _, value, _, _ in block.items)
    shown_limit = block.limit is not None and tallest >= 0.5 * block.limit
    top = max(tallest, block.limit or 0) if shown_limit else tallest
    top = top or 1
    bars = "".join(
        f'<i style="height:{max(value / top * 100, 1.5 if value else 0):.1f}%" title="{_esc(tip)}">'
        + (f'<b style="height:{min(100.0, part / value * 100):.1f}%"></b>' if value and part else "") + "</i>"
        for _, value, part, tip in block.items
    )
    scale = human_ms(tallest) if block.unit == "ms" else f"{tallest:,.0f}"
    limit = ""
    if shown_limit and block.limit is not None:
        limit = (f'<span class="lim" style="bottom:{block.limit / top * 100:.1f}%"><span>'
                 f"{_esc(block.limit_label)}</span></span>")
    labels = [block.items[0][0], block.items[len(block.items) // 2][0], block.items[-1][0]]
    axis = "".join(f"<span>{_esc(label)}</span>" for label in (labels if len(block.items) > 2 else labels[::2]))
    return (f'{title}<div class="cols{" dim" if block.part == "dim" else ""}"><span class="top">{_esc(scale)}</span>'
            f'{limit}{bars}</div><div class="colx">{axis}</div>')


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
        elif isinstance(block, _Wiring):
            out += ["", f"-- {block.title} --"] if block.title else [""]
            calls = "; ".join(f"{kind} {name}".strip() + (" (!)" if tone in ("warn", "bad") else "")
                              for kind, name, _, tone in block.inputs) or block.empty
            name, detail = block.center
            goes = "; ".join(f"{what}: {where}" + (" (!)" if tone in ("warn", "bad") else "")
                             for what, where, _, tone in block.outputs)
            out += [f"  Called by: {calls}", f"  Runs:      {name}" + (f" ({detail})" if detail else ""),
                    f"  Then:      {goes}"]
        elif isinstance(block, _Columns):
            out += ["", f"-- {block.title} --"] if block.title else [""]
            if block.items:
                tallest = max(value for _, value, _, _ in block.items) or 1
                spark = "".join(" ▁▂▃▄▅▆▇█"[min(8, round(value / tallest * 8))] for _, value, _, _ in block.items)
                label, value = max(((label, value) for label, value, _, _ in block.items), key=lambda lv: lv[1])
                most = human_ms(value) if block.unit == "ms" else f"{value:,.0f}"
                out.append(f"  {block.items[0][0]} {spark} {block.items[-1][0]}   (the most: {most}, {label})")
            else:
                out.append("(none)")
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


_INFO_ORDER = ("head", "runs", "triggers", "async", "access", "versions", "days", "cost", "tags", "raw", "next")  # function_info()'s parts


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
        "🧭 Explore by clicking": ("explore",),
        "λ Functions": ("functions", "function_info"),
        "🩺 When something goes wrong": ("errors", "logs", "performance"),
        "📦 Code": ("code",),
        "❓ Help": ("help",),
    }
    _START = (
        ("explore()", "the explorer window: every function, its logs run by run, errors and code, by clicking"),
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
        self.explorer: LambdaExplorer | None = None  # the window explore() opened last

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
        sections = self._function_sections(detail, name=name, days=days)
        self._show([block for part in _INFO_ORDER for block in sections[part]])

    def _function_sections(self, detail: FunctionDetail, *, name: str | None = None, days: int = 30,
                           ) -> dict[str, list[Any]]:
        """function_info()'s blocks by part (_INFO_ORDER), so the explorer window can show its health and its setup
        on different tabs: 'head' (title, cards, findings), 'runs', 'triggers', 'async', 'access', 'versions', 'days',
        'cost', 'tags', 'raw' and 'next'."""
        fn, m = detail.function, detail.metrics
        now = _utcnow()
        prices = self.core.prices
        found = function_findings(fn, m, detail, prices=prices, now=now)
        status = runtime_status(fn.runtime, package_type=fn.package_type, today=now.date())
        cost = function_monthly_cost(fn, m, detail.provisioned, detail.log_group, prices=prices)
        level = _errors_level(m, now)
        near_timeout = bool(m and m.duration_max and fn.timeout and m.duration_max >= 0.9 * fn.timeout * 1000)
        public = any(t.public for t in detail.triggers)
        _, qualifier, _ = parse_function_ref(name or fn.name)
        sections: dict[str, list[Any]] = {part: [] for part in _INFO_ORDER}
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
        head = sections["head"]
        head += [_Title(f"Function {fn.name}" + (f" ({qualifier})" if qualifier else ""), sub), _Cards(cards)]
        note = _unread(detail.errors, "its ")
        if note:
            head.append(note)
        head.append(_Findings(found, empty=f"No problems found in its settings or its last {days} days."))

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
        sections["runs"].append(_Table(["Setting", "Value", "What it means"], runs, title="What it runs", max_rows=0,
                                       prose_cols=(2,)))

        trigger_rows = []
        for t in detail.triggers:
            disabled = (t.state or "").lower() == "disabled"
            state_cell: Any = (_Tone("public", "warn") if t.public else _Tone(t.state or "", "warn") if disabled
                               else t.state or "-")
            trigger_rows.append([t.kind, t.source or "-", _VIA.get(t.via, t.via), state_cell,
                                 "; ".join(filter(None, [t.detail, t.last_result])) or "-"])
        if trigger_rows:
            sections["triggers"].append(_Table(
                ["Trigger", "Source", "How", "State", "Details"], trigger_rows, max_rows=0,
                title="What triggers it (event source mappings, its resource policy and URL)"))
        elif not {"policy", "triggers"} & set(detail.errors):
            sections["triggers"].append(_Note(
                "Nothing in its resource policy or event source mappings calls it: it's called directly (an SDK, "
                "aws lambda invoke, Step Functions, or a service that uses its own role), or not at all."
            ))
        async_config = detail.async_config
        asynchronous = [t for t in detail.triggers if t.asynchronous]
        if async_config is not None and (asynchronous or async_config.on_failure or async_config.on_success
                                         or fn.dead_letter):
            sections["async"].append(_Table(["Setting", "Value"], [
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
        sections["access"].append(_Table(["Setting", "Value", "What it means"], access, title="What it can reach",
                                         max_rows=0, prose_cols=(2,)))

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
            sections["versions"].append(_Table(["What", "Value", "Notes"], version_rows, max_rows=0,
                                               title="Versions, aliases and provisioned concurrency"))

        if m is not None and m.invocations:
            day_rows, bars = [], []
            busiest = max((d.invocations for d in m.daily), default=0.0)
            for d in sorted(m.daily, key=lambda d: d.start, reverse=True):
                day_rows.append([d.start.strftime("%Y-%m-%d %a"), _count(round(d.invocations)), _count(round(d.errors)),
                                 _count(round(d.throttles)), _ms(d.avg_duration), _ms(d.duration_max)])
                bars.append(_share(d.invocations, busiest))
            sections["days"].append(_Table(
                ["Day (UTC)", "Calls", "Errors", "Throttles", "Avg run time", "Longest"], day_rows, bars=bars,
                bar_label="Calls vs. the busiest day", max_rows=0,
                title=f"The last {days} days (CloudWatch; days without data are left out)",
            ))
        if cost:
            basis = (f"{self._price_basis()}; usage over the last {days} days scaled to a month" if m is not None
                     else self._price_basis())
            sections["cost"].append(_Table(
                ["Part", "Est. $/month"], [[_PARTS.get(part, part), human_money(value)] for part, value in cost.items()],
                title=f"Estimated monthly cost: {human_money(_total(cost))} ({basis}, before the free tier)",
                max_rows=0,
            ))
        if fn.tags:
            sections["tags"].append(_Table(["Tag", "Value"], [[k, v] for k, v in sorted(fn.tags.items())],
                                           title="Tags", collapsed=True, max_rows=0))
        sections["raw"].append(_Text(json.dumps(fn.raw, indent=2, default=str), collapsed=True,
                                     title="Configuration, as Lambda returns it (environment values hidden)"))
        steps = []
        if m is not None and m.errors:
            steps.append((self._call_for("errors", fn), "its errors, grouped by cause"))
        steps.append((self._call_for("performance", fn), "run times, memory used and cold starts"))
        if fn.package_type == "Zip":
            steps.append((self._call_for("code", fn), "the files in its package, and the handler's source"))
        if len(steps) < 3:
            steps.append((self._call_for("logs", fn), "the newest lines it logged"))
        sections["next"].append(_Next(steps))
        return sections

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
        self._show(self._errors_blocks(report, since=since, limit=limit))

    def _errors_blocks(self, report: ErrorReport, *, since: Any = "24h", limit: int | None = 10_000,
                       for_window: bool = False) -> list[Any]:
        """errors()'s report, as blocks; for_window=True leaves out the newest error lines (the explorer lists them as
        runs to click)."""
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
            if not for_window:
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
        return blocks

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
        self._show(self._performance_blocks(perf, since=since, limit=limit))

    def _performance_blocks(self, perf: Performance, *, since: Any = "24h", limit: int | None = 5_000,
                            for_window: bool = False) -> list[Any]:
        """performance()'s report, as blocks; for_window=True leaves out the slowest runs (the explorer lists them as runs
        to click)."""
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
            return blocks
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
        if not for_window:
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
        return blocks

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
        self._show(self._code_blocks(package))

    def _code_blocks(self, package: CodePackage, *, source: bool = True) -> list[Any]:
        """code()'s report, as blocks; source=False leaves out the shown file's text (the explorer shows it on its
        own)."""
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
            return blocks
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
        if package.note and source:
            blocks.append(_Note(package.note))
        if package.source is not None and source:
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
        return blocks

    # ------------------------------------------------------------------ the window

    @_friendly_errors
    def explore(self, name: str | None = None, *, tab: str | None = None, region: str | None = None,
                height: int | str | None = None) -> None:
        """The explorer window: every function in the region to click through, and for the one you pick, how it's
        doing, its logs run by run (failed runs in red, a search box, a time range, and Live to watch new runs come
        in), its errors grouped by cause, its run times, the code in its package and every setting. Needs Jupyter and
        ipywidgets; view.explorer is the window. name opens one function, on tab= 'logs', 'errors', 'performance',
        'code' or 'settings'; region='all' lists every region."""
        if not self.use_html:
            raise _Hint(
                "The explorer window needs Jupyter (SageMaker, JupyterLab or VS Code). Here, functions() lists every "
                "function, logs('name') shows what one logged, and errors('name') why it fails."
            )
        _require("ipywidgets", "The explorer window")
        self.explorer = LambdaExplorer(name, tab=tab, region=region, view=self, height=height, mode="widgets")


# ----------------------------------------------------------------------------- the explorer window

# The window's tabs, in order: (key, title, what it holds, the line drawing (24 x 24) shown before the title as a mask
# in the text's colour, so it follows the theme).
_EXPLORER_TABS = (
    ("functions", "Functions", "Every function in the region: search, filter, sort, and click one to open it",
     "<path d='M9.5 6.5h10.5M9.5 12h10.5M9.5 17.5h10.5'/><path d='M4.5 6.5h.01M4.5 12h.01M4.5 17.5h.01' "
     "stroke-width='3.2'/>"),
    ("overview", "Overview", "How the function is doing: what to fix, what calls it, its last 30 days and its cost",
     "<path d='M3.5 12.5h3.8l2.4-6.5 4.6 12 2.4-5.5h3.8'/>"),
    ("logs", "Logs", "What it logged, run by run: click a run for its lines, search them, or watch new runs live",
     "<rect x='3.5' y='4.5' width='17' height='15' rx='2.5'/><path d='M7.5 9.5l3 2.5-3 2.5M12.5 15h4'/>"),
    ("errors", "Errors", "Its errors grouped by cause, with what to do, and the runs that failed",
     "<path d='M12 4.2 20.8 19.5H3.2z'/><path d='M12 10v4.2M12 17h.01'/>"),
    ("performance", "Performance", "Run times, memory used and cold starts, and the slowest runs",
     "<circle cx='12' cy='13.5' r='7'/><path d='M12 13.5V9.8M9.8 3.5h4.4M18.4 7.1l1.4-1.4'/>"),
    ("code", "Code", "The files in its deployment package, and the source of the one you click",
     "<path d='M8.5 7 3.5 12l5 5M15.5 7l5 5-5 5'/>"),
    ("settings", "Settings", "Every setting in plain English, and as Lambda returns it",
     "<path d='M4 7.5h9M17.5 7.5H20M4 16.5h2.5M11 16.5h9'/><circle cx='15' cy='7.5' r='2.5'/>"
     "<circle cx='8.5' cy='16.5' r='2.5'/>"),
)
_FUNCTION_TABS = ("overview", "logs", "errors", "performance", "code", "settings")  # the tabs about one function
_LOGO = ('<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2.3" '
         'stroke-linecap="round" stroke-linejoin="round"><path d="M6.5 4.5h2.3c.9 0 1.6.5 2 1.3l6.7 13.7"/>'
         '<path d="M12.3 10.6 6.4 19.5"/></svg>')
_RANGES = (("Last 15 minutes", "15m"), ("Last hour", "1h"), ("Last 3 hours", "3h"), ("Last 12 hours", "12h"),
           ("Last 24 hours", "24h"), ("Last 3 days", "3d"), ("Last 7 days", "7d"), ("Last 30 days", "30d"))
_FUNCTION_PAGE = 40  # functions on a page of the list; « ‹ › » move between pages
_RUN_PAGE = 25  # runs on a page of the Logs tab
_CODE_PAGE = 60  # files on a page of the Code tab's list
_RUN_LINES = 3_000  # log lines the Logs tab reads at a time, the newest first ("Older runs" reads more)
_BODY_LINES = 500  # lines an open run shows; the rest are counted
_LIVE_SECONDS = 5  # how often Live looks for new lines...
_LIVE_MINUTES = 15  # ...and for how long, before it stops by itself
_COLUMNS = (  # the function list's columns: (sort key, header, tooltip, width); a header sorts, again reverses
    ("name", "Function", "Sort by name", ""),
    ("calls", "Calls · 30d", "Sort by calls in the last 30 days", "86px"),
    ("errors", "Errors", "Sort by the share of calls that failed", "64px"),
    ("duration", "Avg run", "Sort by the average run time", "72px"),
    ("cost", "$ / month", "Sort by the estimated monthly cost", "76px"),
    ("called", "Last called", "Sort by when it was last called", "92px"),
    ("problems", "⚠", "Problems first: the most warnings, then the most failed calls", "38px"),
)
_FUNCTION_CHIPS = (  # (key, label, tone): a chip shows only the functions it names; again shows every function
    ("", "All", "all"), ("attention", "Needs attention", "warn"), ("errors", "With errors", "bad"),
    ("runtime", "Old runtime", "warn"), ("public", "Public", "warn"), ("idle", "Not called", "idle"),
)
_RUN_CHIPS = (("", "All runs", "all"), ("failed", "Failed", "bad"), ("timeout", "Timed out", "bad"),
              ("logged", "Logged an error", "warn"), ("cold", "Cold starts", "cold"))
_RUN_STATES = {  # LogRun.status -> (icon, tone, what it means)
    "ok": ("✓", "ok", "ran"), "failed": ("✕", "bad", "failed"), "timeout": ("⏱", "bad", "timed out"),
    "logged": ("!", "warn", "logged an error"), "running": ("•", "run", "no REPORT line yet: still running, or it "
                                                                         "ends after the time range"),
    "outside": ("·", "", "lines that belong to no run in the time range"),
}
_SETUP_CARDS = {"Runtime", "Memory", "Timeout", "Architecture", "Triggers"}  # function_info()'s cards the Overview
# keeps: the window's header already shows the calls, errors, run times and cost
_DESTINATIONS = {"sqs": "SQS queue", "sns": "SNS topic", "lambda": "Lambda function", "events": "EventBridge bus",
                 "s3": "S3 bucket"}  # an on-success / on-failure destination's service -> what it is


def _explorer_rules() -> str:
    """The explorer's tab icons: .lmx-i-<key> sets --lx-icon, which a tab's ::before draws."""
    svg = ("<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='black' stroke-width='2' "
           "stroke-linecap='round' stroke-linejoin='round'>{}</svg>")
    return "\n".join(f'.lmx-app .lmx-i-{key}{{--lx-icon:url("data:image/svg+xml,{quote(svg.format(paths))}")}}'
                     for key, _, _, paths in _EXPLORER_TABS)


_EXPLORER_DARK = ("--lx-accent:#60a5fa;--lx-accent-2:#a78bfa;--lx-soft:rgba(96,165,250,.15);--lx-ring:rgba(96,165,250,"
                  ".38);--lx-raised:rgba(255,255,255,.11);--lx-shadow:0 1px 2px rgba(0,0,0,.35),0 8px 24px rgba(0,0,0,"
                  ".28);--lx-ink-bad:#f87171;--lx-ink-warn:#fbbf24;--lx-ink-ok:#34d399;--lx-code:rgba(255,255,255,.035);"
                  "--lx-tok-k:#c792ea;--lx-tok-s:#a5d6a7;--lx-tok-n:#f78c6c;--lx-tok-f:#82aaff;--lx-tok-c:#7f8c98")
_EXPLORER_CSS = """<style>
.lmx-app{--lx-accent:#2563eb;--lx-button:#2563eb;--lx-accent-2:#7c3aed;--lx-soft:rgba(37,99,235,.10);--lx-ring:rgba(37,99,235,.28);--lx-line:rgba(127,127,127,.22);--lx-line-2:rgba(127,127,127,.36);--lx-tint:rgba(127,127,127,.06);--lx-tint-2:rgba(127,127,127,.11);--lx-bg:var(--jp-layout-color0,var(--vscode-editor-background,#fff));--lx-surface:var(--jp-layout-color1,var(--vscode-editor-background,#fff));--lx-raised:var(--lx-surface);--lx-shadow:0 1px 2px rgba(15,23,42,.06),0 8px 24px rgba(15,23,42,.07);--lx-ok:#10b981;--lx-warn:#f59e0b;--lx-bad:#ef4444;--lx-ink-bad:#dc2626;--lx-ink-warn:#b45309;--lx-ink-ok:#047857;--lx-mono:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;--lx-code:rgba(127,127,127,.05);--lx-tok-k:#7c3aed;--lx-tok-s:#047857;--lx-tok-n:#c2410c;--lx-tok-f:#2563eb;--lx-tok-c:#64748b}
body[data-jp-theme-light="false"] .lmx-app,body.vscode-dark .lmx-app,body.vscode-high-contrast .lmx-app{""" + _EXPLORER_DARK + """}
@media (prefers-color-scheme:dark){body:not([data-jp-theme-light]):not(.vscode-light) .lmx-app{""" + _EXPLORER_DARK + """}}
.lmx-app{position:relative;isolation:isolate;box-sizing:border-box;border:1px solid var(--lx-line);border-radius:18px;padding:14px 16px 10px;background:var(--lx-bg);box-shadow:var(--lx-shadow);font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;font-size:13px;line-height:1.45}
.lmx-app *{box-sizing:border-box}
.lmx-app .widget-html-content,.lmx-app .jupyter-widget-html-content{min-width:0;line-height:1.45}
.lmx-app.lmx-app .lmx-flat>*{margin:0}
.lmx-app.lmx-app button.jupyter-button{color:inherit;background:var(--lx-tint);border:1px solid var(--lx-line);border-radius:9px;box-shadow:none;outline:none;font-family:inherit;font-weight:500;transition:background-color .15s,border-color .15s,color .15s,opacity .15s,box-shadow .15s}
.lmx-app.lmx-app button.jupyter-button:hover:enabled{background:var(--lx-tint-2);border-color:var(--lx-line-2);box-shadow:none}
.lmx-app.lmx-app button.jupyter-button:focus{box-shadow:none;outline:none}
.lmx-app.lmx-app button.jupyter-button:focus-visible{outline:2px solid var(--lx-ring);outline-offset:-1px}
.lmx-app.lmx-app button.jupyter-button:active:enabled{transform:translateY(1px)}
.lmx-app.lmx-app button.jupyter-button.mod-primary{background:var(--lx-button);border-color:transparent;color:#fff;font-weight:600}
.lmx-app.lmx-app button.jupyter-button.mod-primary:hover:enabled{background:var(--lx-button);filter:brightness(1.08)}
.lmx-app.lmx-app button.jupyter-button:disabled{opacity:.45;cursor:default}
.lmx-app.lmx-app .widget-text input,.lmx-app.lmx-app .jupyter-widget-text input,.lmx-app.lmx-app .widget-dropdown>select,.lmx-app.lmx-app .jupyter-widget-dropdown>select{height:32px;border:1px solid var(--lx-line-2);border-radius:9px;background-color:var(--lx-surface);color:inherit;padding:0 11px;transition:border-color .15s,box-shadow .15s}
.lmx-app.lmx-app .widget-dropdown>select,.lmx-app.lmx-app .jupyter-widget-dropdown>select{padding-right:26px;cursor:pointer}
.lmx-app.lmx-app .widget-text input:focus,.lmx-app.lmx-app .jupyter-widget-text input:focus,.lmx-app.lmx-app .widget-dropdown>select:focus,.lmx-app.lmx-app .jupyter-widget-dropdown>select:focus{outline:none;border-color:var(--lx-accent);box-shadow:0 0 0 3px var(--lx-soft)}
.lmx-app.lmx-app .widget-text,.lmx-app.lmx-app .widget-dropdown,.lmx-app.lmx-app .jupyter-widget-text,.lmx-app.lmx-app .jupyter-widget-dropdown{margin:0;height:auto}
.lmx-app.lmx-app .lmx-head{gap:12px;padding:0 0 12px;margin:0 0 12px;border-bottom:1px solid var(--lx-line);overflow:visible}
.lmx-app.lmx-app .lmx-top{align-items:center;gap:10px;overflow:visible}
.lmx-app.lmx-app .lmx-meta{align-items:flex-start;gap:10px 14px;flex-wrap:wrap;overflow:visible}
.lmx-app .lmx-brand{display:flex;align-items:center;gap:12px;min-width:0}
.lmx-app .lmx-logo{width:38px;height:38px;border-radius:12px;display:inline-flex;align-items:center;justify-content:center;color:#fff;background:linear-gradient(135deg,#fb923c,#ea580c);box-shadow:0 2px 8px rgba(234,88,12,.32);flex:0 0 auto}
.lmx-app .lmx-name{font-size:17px;font-weight:650;line-height:1.25;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.lmx-app .lmx-name span{font-weight:500;opacity:.55;margin-left:6px;font-size:13px}
.lmx-app .lmx-sub{font-size:12px;opacity:.62;margin-top:1px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.lmx-app.lmx-app .lmx-region{flex:0 0 auto;width:auto;min-width:170px}
.lmx-app.lmx-app button.lmx-small{width:auto;height:30px;padding:0 13px;border-radius:999px;font-size:12px;flex:0 0 auto}
.lmx-app.lmx-app button.lmx-ghost{background:transparent;border-color:transparent}
.lmx-app.lmx-app button.lmx-ghost:hover:enabled{background:var(--lx-tint-2)}
.lmx-app.lmx-app button.lmx-x{padding:0;width:26px;min-width:26px;height:26px;border-radius:999px;background:transparent;border-color:transparent;opacity:.6;flex:0 0 auto}
.lmx-app.lmx-app button.lmx-x:hover:enabled{opacity:1;background:rgba(239,68,68,.12);color:var(--lx-ink-bad)}
.lmx-app .lmx-stats{display:flex;flex-wrap:wrap;gap:8px}
.lmx-app .lmx-stat{border:1px solid var(--lx-line);border-radius:12px;padding:6px 12px;min-width:84px;background:var(--lx-tint)}
.lmx-app .lmx-stat .l{display:block;font-size:10px;font-weight:650;letter-spacing:.05em;text-transform:uppercase;opacity:.58;white-space:nowrap}
.lmx-app .lmx-stat b{display:block;font-size:15px;font-weight:650;margin-top:1px;white-space:nowrap}
.lmx-app .lmx-stat.warn{border-color:rgba(245,158,11,.7);background:rgba(245,158,11,.08)}
.lmx-app .lmx-stat.bad{border-color:rgba(239,68,68,.7);background:rgba(239,68,68,.08)}
.lmx-app .lmx-stat.ok{border-color:rgba(16,185,129,.55)}
.lmx-app .lmx-stat.sk-on b{color:transparent;border-radius:6px;background-size:300% 100%;background-image:linear-gradient(90deg,var(--lx-tint-2) 30%,var(--lx-line) 50%,var(--lx-tint-2) 70%);animation:lmx-glow 1.3s ease-in-out infinite}
.lmx-app.lmx-app .lmx-field{position:relative;overflow:visible;flex:0 0 320px;max-width:100%}
.lmx-app.lmx-app .lmx-field.lmx-open{z-index:41}
.lmx-app.lmx-app .lmx-trig{position:relative;min-height:52px;overflow:visible}
.lmx-app.lmx-app .lmx-trig>.lmx-trig-b,.lmx-app.lmx-app .lmx-opt>.lmx-opt-b,.lmx-app.lmx-app .lmx-row>.lmx-row-b,.lmx-app.lmx-app .lmx-run-h>.lmx-run-b{position:absolute;top:0;left:0;width:100%;height:100%;margin:0;padding:0;border:1px solid transparent;background:transparent;box-shadow:none}
.lmx-app.lmx-app .lmx-trig>.lmx-trig-b{border-color:var(--lx-line-2);border-radius:12px;background:var(--lx-surface);box-shadow:0 1px 2px rgba(15,23,42,.05)}
.lmx-app.lmx-app .lmx-trig>.lmx-trig-b:hover:enabled{border-color:var(--lx-accent);background:var(--lx-surface)}
.lmx-app.lmx-app .lmx-open .lmx-trig>.lmx-trig-b{border-color:var(--lx-accent);box-shadow:0 0 0 3px var(--lx-soft)}
.lmx-app.lmx-app .lmx-trig>.lmx-trig-b:active:enabled,.lmx-app.lmx-app .lmx-opt>.lmx-opt-b:active:enabled,.lmx-app.lmx-app .lmx-row>.lmx-row-b:active:enabled,.lmx-app.lmx-app .lmx-run-h>.lmx-run-b:active:enabled{transform:none}
.lmx-app.lmx-app .lmx-trig>.lmx-face,.lmx-app.lmx-app .lmx-opt>.lmx-opt-t,.lmx-app.lmx-app .lmx-row>.lmx-row-t,.lmx-app.lmx-app .lmx-run-h>.lmx-run-t{position:relative;z-index:1;pointer-events:none;margin:0;min-width:0;width:100%}
.lmx-app .fx{position:relative;padding:8px 34px 8px 13px;line-height:1.3}
.lmx-app .fxl{font-size:10px;font-weight:650;letter-spacing:.06em;text-transform:uppercase;opacity:.55}
.lmx-app .fxv{display:flex;align-items:center;gap:7px;margin-top:3px;font-size:13.5px;white-space:nowrap;min-width:0}
.lmx-app .fxv b{font-weight:650;overflow:hidden;text-overflow:ellipsis;min-width:0}
.lmx-app .fxi,.lmx-app .opi{font-family:var(--lx-mono);font-size:11px;opacity:.55;overflow:hidden;text-overflow:ellipsis;min-width:0;flex:0 10 auto}
.lmx-app .chev{position:absolute;right:14px;top:50%;width:7px;height:7px;margin-top:-6px;border-right:1.6px solid currentColor;border-bottom:1.6px solid currentColor;transform:rotate(45deg);opacity:.5;transition:transform .15s,margin-top .15s}
.lmx-app .lmx-open .chev{transform:rotate(225deg);margin-top:-2px;opacity:.9;color:var(--lx-accent)}
.lmx-app .dot{display:inline-block;width:8px;height:8px;border-radius:50%;flex:0 0 auto;background:rgba(127,127,127,.45)}
.lmx-app .dot.ok{background:var(--lx-ok)}
.lmx-app .dot.warn{background:var(--lx-warn)}
.lmx-app .dot.bad{background:var(--lx-bad)}
.lmx-app .dot.idle{background:transparent;box-shadow:inset 0 0 0 1.5px rgba(127,127,127,.55)}
.lmx-app.lmx-app .lmx-pop{position:absolute;top:calc(100% + 6px);left:0;z-index:40;width:min(500px,calc(100vw - 48px));padding:8px;border:1px solid var(--lx-line-2);border-radius:14px;background:var(--lx-bg);box-shadow:0 14px 36px rgba(15,23,42,.22),0 3px 8px rgba(15,23,42,.08);overflow:visible}
.lmx-app.lmx-app .lmx-pop>*{margin:0}
.lmx-app.lmx-app .lmx-opts{max-height:360px;overflow:hidden auto;margin:6px 0 0}
.lmx-app.lmx-app .lmx-opts>*{flex:0 0 auto}
.lmx-app.lmx-app .lmx-opt{position:relative;margin:0 0 2px;overflow:visible}
.lmx-app.lmx-app .lmx-opt>.lmx-opt-b{border-radius:10px}
.lmx-app.lmx-app .lmx-opt>.lmx-opt-b:hover:enabled{background:var(--lx-tint-2)}
.lmx-app.lmx-app .lmx-opt.lmx-on>.lmx-opt-b{background:var(--lx-soft);border-color:var(--lx-ring)}
.lmx-app .op{display:flex;align-items:flex-start;gap:10px;padding:7px 10px;line-height:1.35;min-width:0}
.lmx-app .op .dot{margin-top:6px}
.lmx-app .opb{flex:1 1 auto;min-width:0}
.lmx-app .opt{display:flex;align-items:baseline;gap:8px;white-space:nowrap;min-width:0}
.lmx-app .opt b{font-weight:600;overflow:hidden;text-overflow:ellipsis;min-width:0;flex:0 1 auto}
.lmx-app .opn{font-size:11.5px;opacity:.62;margin-top:1px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.lmx-app .opf{font-size:11px;opacity:.65;padding:7px 8px 0;margin-top:4px;border-top:1px solid var(--lx-line);line-height:1.4}
.lmx-app .opf.warn{opacity:1;color:var(--lx-ink-warn)}
.lmx-app mark{background:rgba(250,204,21,.4);color:inherit;border-radius:3px;padding:0 1px}
.lmx-app.lmx-app .lmx-backdrop,.lmx-app.lmx-app .lmx-backdrop:hover:enabled,.lmx-app.lmx-app .lmx-backdrop:active:enabled,.lmx-app.lmx-app .lmx-backdrop:focus-visible{position:absolute;top:0;left:0;z-index:30;width:100%;height:100%;margin:0;padding:0;border:0;border-radius:inherit;background:transparent;box-shadow:none;outline:none;transform:none;cursor:default}
.lmx-app.lmx-app .lmx-tabs{flex-wrap:nowrap;gap:2px;padding:3px;border-radius:12px;background:var(--lx-tint-2);margin:0 0 10px;overflow:hidden;container-type:inline-size}
.lmx-app.lmx-app button.lmx-tab{flex:1 1 0;min-width:0;height:32px;margin:0;padding:0 8px;border:0;border-radius:9px;background:transparent;opacity:.72;font-weight:500;display:inline-flex;align-items:center;justify-content:center;gap:7px;white-space:nowrap;overflow:hidden}
.lmx-app.lmx-app button.lmx-tab::before{content:"";width:15px;height:15px;flex:0 0 auto;background:currentColor;-webkit-mask:var(--lx-icon) center/contain no-repeat;mask:var(--lx-icon) center/contain no-repeat}
.lmx-app.lmx-app button.lmx-tab:hover:enabled{background:var(--lx-tint);opacity:.95}
.lmx-app.lmx-app button.lmx-tab.lmx-on,.lmx-app.lmx-app button.lmx-tab.lmx-on:hover:enabled{background:var(--lx-raised);opacity:1;font-weight:650;box-shadow:0 1px 3px rgba(15,23,42,.16)}
.lmx-app.lmx-app button.lmx-tab.lmx-on::before{background:var(--lx-accent)}
.lmx-app.lmx-app button.lmx-tab.lmx-dim{opacity:.42}
.lmx-app.lmx-app button.lmx-tab.lmx-alert::after,.lmx-app.lmx-app button.lmx-tab.lmx-alarm::after{content:"";width:7px;height:7px;border-radius:50%;background:var(--lx-warn);flex:0 0 auto}
.lmx-app.lmx-app button.lmx-tab.lmx-alarm::after{background:var(--lx-bad)}
@container (max-width:640px){.lmx-app.lmx-app button.lmx-tab::before{display:none}}
.lmx-app.lmx-app .lmx-page{height:clamp(560px,calc(100vh - 330px),1400px);overflow:hidden;padding:0}
body[class*=vscode-] .lmx-app.lmx-app .lmx-page{height:620px}
.lmx-app.lmx-app .lmx-page>*{flex:0 0 auto;margin:0}
.lmx-app.lmx-app .lmx-page>.lmx-scroll,.lmx-app.lmx-app .lmx-page>.lmx-split{flex:1 1 auto;min-height:0}
.lmx-app.lmx-app .lmx-scroll{overflow:hidden auto;padding:2px 6px 2px 2px}
.lmx-app.lmx-app .lmx-codehead{max-height:45%;overflow:hidden auto;padding:2px 6px 6px 2px}
.lmx-app.lmx-app .lmx-codehead>*{flex:0 0 auto;margin:0}
.lmx-app.lmx-app .lmx-scroll>*{flex:0 0 auto;margin:0}
.lmx-app.lmx-app .lmx-find{position:relative;align-items:center}
.lmx-app.lmx-app .lmx-find input,.lmx-app.lmx-app .lmx-find input:focus{padding-left:32px;background:var(--lx-surface) url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='%23888' stroke-width='2.4' stroke-linecap='round'%3E%3Ccircle cx='11' cy='11' r='6.5'/%3E%3Cpath d='m20 20-4-4'/%3E%3C/svg%3E") no-repeat 11px center/14px}
.lmx-app.lmx-app .lmx-bar{gap:8px;align-items:center;padding:0 0 8px;flex-wrap:wrap;overflow:visible}
.lmx-app.lmx-app .lmx-bar>*{margin:0}
.lmx-app.lmx-app .lmx-chips{flex-wrap:wrap;gap:6px;margin:0 0 8px}
.lmx-app.lmx-app .lmx-chips>*{margin:0}
.lmx-app.lmx-app button.lmx-chip{width:auto;height:26px;padding:0 10px;border-radius:999px;font-size:12px;background:transparent;border:1px solid var(--lx-line);display:inline-flex;align-items:center;gap:6px}
.lmx-app.lmx-app button.lmx-chip::before{content:"";width:7px;height:7px;border-radius:50%;background:rgba(127,127,127,.5)}
.lmx-app.lmx-app button.lmx-chip.lmx-t-all::before{display:none}
.lmx-app.lmx-app button.lmx-chip.lmx-t-bad::before{background:var(--lx-bad)}
.lmx-app.lmx-app button.lmx-chip.lmx-t-warn::before{background:var(--lx-warn)}
.lmx-app.lmx-app button.lmx-chip.lmx-t-ok::before{background:var(--lx-ok)}
.lmx-app.lmx-app button.lmx-chip.lmx-t-cold::before{background:#38bdf8}
.lmx-app.lmx-app button.lmx-chip.lmx-t-idle::before{background:transparent;box-shadow:inset 0 0 0 1.5px rgba(127,127,127,.6)}
.lmx-app.lmx-app button.lmx-chip.lmx-on,.lmx-app.lmx-app button.lmx-chip.lmx-on:hover:enabled{background:var(--lx-soft);border-color:var(--lx-ring);color:var(--lx-accent);font-weight:650}
.lmx-app.lmx-app .lmx-rows,.lmx-app.lmx-app .lmx-runs{overflow:hidden auto;padding:0 4px 4px 0;border:1px solid var(--lx-line);border-radius:14px;background:var(--lx-surface)}
.lmx-app.lmx-app .lmx-rows>*,.lmx-app.lmx-app .lmx-runs>*{flex:0 0 auto;margin:0}
.lmx-app.lmx-app .lmx-lhead{position:sticky;top:0;z-index:3;gap:10px;padding:6px 10px 6px 12px;background:var(--lx-surface);border-bottom:1px solid var(--lx-line);align-items:center}
.lmx-app.lmx-app .lmx-lhead>*{margin:0}
.lmx-app.lmx-app button.lmx-col{height:24px;padding:0 4px;border:0;background:transparent;font-size:10.5px;font-weight:650;letter-spacing:.03em;text-transform:uppercase;opacity:.6;text-align:right;justify-content:flex-end;flex:0 0 auto}
.lmx-app.lmx-app button.lmx-col.lmx-c-name{flex:1 1 auto;text-align:left;padding-left:18px}
.lmx-app.lmx-app button.lmx-col:hover:enabled{opacity:1;background:var(--lx-tint-2)}
.lmx-app.lmx-app button.lmx-col.lmx-on{opacity:1;color:var(--lx-accent)}
.lmx-app.lmx-app .lmx-row{position:relative;height:54px;margin:0 0 1px;overflow:visible}
.lmx-app.lmx-app .lmx-row.lmx-short{height:42px}
.lmx-app.lmx-app .lmx-row>.lmx-row-b{border-radius:10px}
.lmx-app.lmx-app .lmx-row>.lmx-row-b:hover:enabled{background:var(--lx-tint-2)}
.lmx-app.lmx-app .lmx-row.lmx-on>.lmx-row-b,.lmx-app.lmx-app .lmx-row.lmx-on>.lmx-row-b:hover:enabled{background:var(--lx-soft);border-color:var(--lx-ring)}
.lmx-app.lmx-app button.lmx-act{position:absolute;right:10px;top:50%;transform:translateY(-50%);z-index:2;display:none;width:auto;height:26px;padding:0 11px;border-radius:999px;font-size:12px;background:var(--lx-raised);border-color:var(--lx-line-2);color:var(--lx-accent);box-shadow:0 1px 3px rgba(15,23,42,.12)}
.lmx-app.lmx-app .lmx-row:hover>button.lmx-act,.lmx-app.lmx-app .lmx-row.lmx-on>button.lmx-act{display:inline-flex;align-items:center}
.lmx-app.lmx-app button.lmx-act:active:enabled{transform:translateY(-50%)}
.lmx-app .fr{display:grid;grid-template-columns:8px minmax(0,1fr) 86px 64px 72px 76px 92px 38px 52px;align-items:center;gap:0 10px;height:54px;padding:0 10px 0 12px;min-width:0}
.lmx-app .fm{min-width:0;line-height:1.3}
.lmx-app .fn{display:flex;align-items:baseline;gap:8px;min-width:0;white-space:nowrap}
.lmx-app .fn b{font-weight:600;overflow:hidden;text-overflow:ellipsis;min-width:0;flex:0 1 auto}
.lmx-app .rt{flex:0 0 auto;font-size:11px;padding:0 7px;border-radius:999px;background:var(--lx-tint-2);font-weight:500}
.lmx-app .rt.warn{background:rgba(245,158,11,.16);color:var(--lx-ink-warn)}
.lmx-app .rt.bad{background:rgba(239,68,68,.13);color:var(--lx-ink-bad)}
.lmx-app .rg{flex:0 0 auto;font-size:11px;opacity:.6;font-family:var(--lx-mono)}
.lmx-app .ff{font-size:11.5px;opacity:.68;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;margin-top:2px}
.lmx-app .fc{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap;font-size:12.5px;min-width:0;overflow:hidden;text-overflow:ellipsis}
.lmx-app .fc.dim{opacity:.55}
.lmx-app .fc .pill{display:inline-block;padding:0 7px;border-radius:999px;font-weight:600;font-size:11.5px}
.lmx-app .fc .pill.warn{background:rgba(245,158,11,.18);color:var(--lx-ink-warn)}
.lmx-app .fc .pill.bad{background:rgba(239,68,68,.15);color:var(--lx-ink-bad)}
.lmx-app .wb{display:inline-flex;align-items:center;justify-content:center;min-width:22px;height:20px;padding:0 6px;border-radius:999px;font-size:11.5px;font-weight:700;background:rgba(245,158,11,.18);color:var(--lx-ink-warn)}
.lmx-app .sk{display:inline-block;width:42px;height:10px;border-radius:5px;vertical-align:middle;background-size:300% 100%;background-image:linear-gradient(90deg,var(--lx-tint) 30%,var(--lx-tint-2) 50%,var(--lx-tint) 70%);animation:lmx-glow 1.3s ease-in-out infinite}
@container (max-width:900px){.lmx-app .fr{grid-template-columns:8px minmax(0,1fr) 80px 60px 70px 38px 52px}.lmx-app .fr .c-dur,.lmx-app .fr .c-called{display:none}.lmx-app.lmx-app .lmx-lhead .lmx-c-duration,.lmx-app.lmx-app .lmx-lhead .lmx-c-called{display:none}}
.lmx-app.lmx-app .lmx-listpage{container-type:inline-size}
.lmx-app .lmx-empty{padding:26px 10px;text-align:center;opacity:.62;font-size:12.5px;line-height:1.5}
.lmx-app.lmx-app .lmx-pager{align-items:center;gap:2px;padding:6px 2px 0;margin-top:2px}
.lmx-app.lmx-app .lmx-pager>*{margin:0}
.lmx-app.lmx-app button.lmx-pg{width:30px;min-width:30px;height:26px;padding:0;border:0;background:transparent;font-size:15px;line-height:1}
.lmx-app .lmx-pager .widget-html-content,.lmx-app .lmx-pager .jupyter-widget-html-content{font-size:12px;opacity:.72;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.lmx-app.lmx-app .lmx-ask{flex:1 1 300px;align-items:center;gap:4px;padding:3px 3px 3px 6px;border:1px solid var(--lx-line-2);border-radius:999px;background:var(--lx-surface);transition:border-color .15s,box-shadow .15s;min-width:0}
.lmx-app.lmx-app .lmx-ask>*{margin:0}
.lmx-app.lmx-app .lmx-ask:focus-within{border-color:var(--lx-accent);box-shadow:0 0 0 3px var(--lx-soft)}
.lmx-app.lmx-app .lmx-ask .widget-text input,.lmx-app.lmx-app .lmx-ask .jupyter-widget-text input,.lmx-app.lmx-app .lmx-ask .widget-text input:focus,.lmx-app.lmx-app .lmx-ask .jupyter-widget-text input:focus{border:0;box-shadow:none;background-color:transparent;height:28px;font-size:13px}
.lmx-app.lmx-app .lmx-range{flex:0 0 auto;width:150px}
.lmx-app.lmx-app button.lmx-live{width:auto;height:32px;padding:0 14px;border-radius:999px;font-size:12.5px;font-weight:600;display:inline-flex;align-items:center;gap:7px;flex:0 0 auto}
.lmx-app.lmx-app button.lmx-live::before{content:"";width:8px;height:8px;border-radius:50%;background:rgba(127,127,127,.55)}
.lmx-app.lmx-app button.lmx-live.lmx-on,.lmx-app.lmx-app button.lmx-live.lmx-on:hover:enabled{background:rgba(239,68,68,.1);border-color:rgba(239,68,68,.55);color:var(--lx-ink-bad)}
.lmx-app.lmx-app button.lmx-live.lmx-on::before{background:#ef4444;animation:lmx-pulse 1.4s ease-in-out infinite}
@keyframes lmx-pulse{0%,100%{box-shadow:0 0 0 0 rgba(239,68,68,.5)}50%{box-shadow:0 0 0 5px rgba(239,68,68,0)}}
.lmx-app .lmx-sum{display:flex;flex-wrap:wrap;gap:4px 16px;align-items:baseline;font-size:12px;padding:0 2px 8px}
.lmx-app .lmx-sum span{white-space:nowrap;opacity:.78}
.lmx-app .lmx-sum b{font-weight:650;opacity:1}
.lmx-app .lmx-sum .bad{color:var(--lx-ink-bad);opacity:1}
.lmx-app .lmx-sum .warn{color:var(--lx-ink-warn);opacity:1}
.lmx-app .lmx-sum .lmx-note{flex:1 1 100%;white-space:normal;opacity:.75}
.lmx-app.lmx-app .lmx-run{margin:0;border-bottom:1px solid var(--lx-line);overflow:visible}
.lmx-app.lmx-app .lmx-run:last-child{border-bottom:0}
.lmx-app.lmx-app .lmx-run-h{position:relative;height:42px;margin:0;overflow:visible}
.lmx-app.lmx-app .lmx-run-h>.lmx-run-b{border-radius:0}
.lmx-app.lmx-app .lmx-run-h>.lmx-run-b:hover:enabled{background:var(--lx-tint-2)}
.lmx-app.lmx-app .lmx-run.lmx-open>.lmx-run-h>.lmx-run-b{background:var(--lx-tint)}
.lmx-app .rr{display:grid;grid-template-columns:12px 18px 92px 136px 104px 64px minmax(0,1fr) 76px;align-items:center;gap:0 10px;height:42px;padding:0 12px 0 10px;font-size:12.5px;min-width:0;white-space:nowrap}
.lmx-app .rr .rv{width:7px;height:7px;border-right:1.6px solid currentColor;border-bottom:1.6px solid currentColor;transform:rotate(-45deg);opacity:.45;transition:transform .15s}
.lmx-app .lmx-open .rr .rv{transform:rotate(45deg);opacity:.8}
.lmx-app .rr .ri{display:inline-flex;align-items:center;justify-content:center;width:18px;height:18px;border-radius:50%;font-size:11px;font-weight:800;background:var(--lx-tint-2)}
.lmx-app .rr.ok .ri{background:rgba(16,185,129,.16);color:var(--lx-ink-ok)}
.lmx-app .rr.bad .ri{background:rgba(239,68,68,.16);color:var(--lx-ink-bad)}
.lmx-app .rr.warn .ri{background:rgba(245,158,11,.2);color:var(--lx-ink-warn)}
.lmx-app .rr.run .ri{background:rgba(59,130,246,.16);color:var(--lx-accent)}
.lmx-app .rr .rw{font-variant-numeric:tabular-nums;opacity:.8}
.lmx-app .rr .rd{display:flex;align-items:center;gap:7px;font-variant-numeric:tabular-nums}
.lmx-app .rr .rb{display:inline-block;width:44px;height:6px;border-radius:3px;background:var(--lx-tint-2);overflow:hidden;flex:0 0 auto}
.lmx-app .rr .rb i{display:block;height:100%;border-radius:3px;background:rgba(59,130,246,.7)}
.lmx-app .rr .rb i.warn{background:var(--lx-warn)}
.lmx-app .rr .rb i.bad{background:var(--lx-bad)}
.lmx-app .rr .rm{font-variant-numeric:tabular-nums;opacity:.75;overflow:hidden;text-overflow:ellipsis}
.lmx-app .rr .rc{font-size:11px;font-weight:600;color:#0284c7;overflow:hidden;text-overflow:ellipsis}
.lmx-app .rr .rs{overflow:hidden;text-overflow:ellipsis;opacity:.75;min-width:0}
.lmx-app .rr .rs.bad{color:var(--lx-ink-bad);opacity:1;font-weight:500}
.lmx-app .rr .rs.warn{color:var(--lx-ink-warn);opacity:1}
.lmx-app .rr .rq{font-family:var(--lx-mono);font-size:11px;opacity:.5;text-align:right;overflow:hidden;text-overflow:ellipsis}
@container (max-width:820px){.lmx-app .rr{grid-template-columns:12px 18px 80px 120px minmax(0,1fr)}.lmx-app .rr .rm,.lmx-app .rr .rc,.lmx-app .rr .rq{display:none}}
.lmx-app.lmx-app .lmx-logpage{container-type:inline-size}
.lmx-app .rbody{padding:4px 0 10px;background:var(--lx-code);border-top:1px solid var(--lx-line)}
.lmx-app .rl{display:grid;grid-template-columns:70px 50px minmax(0,1fr);gap:0 10px;padding:1px 14px 1px 40px;font-family:var(--lx-mono);font-size:12px;line-height:1.55}
.lmx-app .rl:hover{background:var(--lx-tint)}
.lmx-app .rl .lt{opacity:.48;text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
.lmx-app .rl .lv{align-self:start;margin-top:2px;justify-self:start;font-size:9.5px;font-weight:700;letter-spacing:.04em;padding:0 5px;border-radius:4px;line-height:15px;opacity:.85}
.lmx-app .rl .lv.error{background:rgba(239,68,68,.16);color:var(--lx-ink-bad)}
.lmx-app .rl .lv.warn{background:rgba(245,158,11,.2);color:var(--lx-ink-warn)}
.lmx-app .rl .lv.info{background:rgba(59,130,246,.13);color:var(--lx-accent)}
.lmx-app .rl .lv.debug{background:var(--lx-tint-2)}
.lmx-app .rl .lm{white-space:pre-wrap;overflow-wrap:anywhere;min-width:0}
.lmx-app .rl.error{background:rgba(239,68,68,.06)}
.lmx-app .rl.error .lm{color:var(--lx-ink-bad)}
.lmx-app .rl.warn .lm{color:var(--lx-ink-warn)}
.lmx-app .rl.platform{opacity:.55}
.lmx-app .rl.hit{box-shadow:inset 3px 0 var(--lx-accent)}
.lmx-app .rl details{display:inline}
.lmx-app .rl summary{display:inline;cursor:pointer;font-size:11px;opacity:.6;margin-left:8px;list-style:none}
.lmx-app .rl summary::-webkit-details-marker{display:none}
.lmx-app .rl details[open] summary{opacity:.9}
.lmx-app .rl .jf{display:block;margin:3px 0 4px;padding:6px 9px;border-radius:7px;border:1px solid var(--lx-line);background:var(--lx-bg);white-space:pre-wrap;font-size:11.5px;color:var(--jp-content-font-color1,inherit)}
.lmx-app .rf{display:flex;flex-wrap:wrap;gap:6px 14px;align-items:center;padding:8px 14px 2px 40px;font-size:11.5px}
.lmx-app .rf span{white-space:nowrap;opacity:.75}
.lmx-app .rf b{font-weight:650}
.lmx-app .rf code{font-family:var(--lx-mono);font-size:11px;padding:1px 5px;border-radius:5px;background:var(--lx-tint-2);user-select:all;-webkit-user-select:all;cursor:text;opacity:1}
.lmx-app .rf .warn{color:var(--lx-ink-warn);opacity:1}
.lmx-app .rf .bad{color:var(--lx-ink-bad);opacity:1}
.lmx-app .rmore{padding:4px 14px 0 40px;font-size:11.5px;opacity:.6}
.lmx-app .er{display:grid;grid-template-columns:18px 112px 150px minmax(0,1fr) 84px;align-items:center;gap:0 10px;height:42px;padding:0 12px 0 10px;font-size:12.5px;white-space:nowrap;min-width:0}
.lmx-app .er .ri{display:inline-flex;align-items:center;justify-content:center;width:18px;height:18px;border-radius:50%;font-size:11px;font-weight:800;background:rgba(239,68,68,.16);color:var(--lx-ink-bad)}
.lmx-app .er.slow .ri{background:rgba(245,158,11,.2);color:var(--lx-ink-warn)}
.lmx-app .er .ew{font-variant-numeric:tabular-nums;opacity:.8}
.lmx-app .er .ek{font-weight:600;overflow:hidden;text-overflow:ellipsis}
.lmx-app .er .es{overflow:hidden;text-overflow:ellipsis;opacity:.8}
.lmx-app .er .eq{font-family:var(--lx-mono);font-size:11px;opacity:.5;text-align:right}
.lmx-app .er .eo{color:var(--lx-accent);font-size:12px;text-align:right;opacity:0;transition:opacity .15s}
.lmx-app .lmx-row:hover .er .eo{opacity:1}
.lmx-app .lmx-h{font-size:13px;font-weight:650;margin:14px 0 6px}
.lmx-app .lmx-h span{font-weight:400;opacity:.6;font-size:12px;margin-left:6px}
.lmx-app.lmx-app .lmx-split{gap:12px;align-items:stretch;overflow:hidden}
.lmx-app.lmx-app .lmx-left{flex:0 0 300px;min-width:240px;max-width:40%;border:1px solid var(--lx-line);border-radius:14px;padding:8px 8px 6px;background:var(--lx-surface);overflow:hidden}
.lmx-app.lmx-app .lmx-left>*{margin:0;flex:0 0 auto}
.lmx-app.lmx-app .lmx-left>.lmx-files{flex:1 1 auto;min-height:0;overflow:hidden auto;margin:6px -2px 0;padding:0 2px}
.lmx-app.lmx-app .lmx-files>*{flex:0 0 auto;margin:0}
.lmx-app.lmx-app .lmx-right{flex:1 1 300px;min-width:0;overflow:hidden auto;padding:0 6px 0 2px}
.lmx-app.lmx-app .lmx-right>*{flex:0 0 auto;margin:0}
@media (max-width:900px){.lmx-app.lmx-app .lmx-split{flex-wrap:wrap;overflow:hidden auto}.lmx-app.lmx-app .lmx-left{flex:1 1 100%;max-width:100%;height:360px}.lmx-app.lmx-app .lmx-right{flex:1 1 100%;overflow:visible}}
.lmx-app .cf{display:flex;align-items:center;gap:9px;height:42px;padding:0 8px;min-width:0}
.lmx-app .cf .ct{flex:0 0 auto;width:34px;height:20px;border-radius:6px;display:inline-flex;align-items:center;justify-content:center;font-size:9px;font-weight:750;letter-spacing:.03em;background:var(--lx-tint-2)}
.lmx-app .cf .ct.py{background:rgba(59,130,246,.14);color:var(--lx-accent)}
.lmx-app .cf .ct.js{background:rgba(234,179,8,.18);color:#a16207}
.lmx-app .cf .ct.cfg{background:rgba(16,185,129,.14);color:var(--lx-ink-ok)}
.lmx-app .cf .ct.key{background:rgba(239,68,68,.13);color:var(--lx-ink-bad)}
.lmx-app .cf .cm{flex:1 1 auto;min-width:0;line-height:1.25}
.lmx-app .cf .cn{font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.lmx-app .cf .cd{font-size:11px;opacity:.6;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.lmx-app .cf .cz{flex:0 0 auto;font-size:11px;opacity:.6;font-variant-numeric:tabular-nums}
.lmx-app .cf.junk{opacity:.55}
.lmx-app .cf .hb{font-size:9.5px;font-weight:700;padding:0 5px;border-radius:4px;background:rgba(234,88,12,.14);color:#c2410c;margin-left:6px}
.lmx-app .srch{display:flex;align-items:baseline;gap:10px;flex-wrap:wrap;margin:2px 0 8px}
.lmx-app .srch b{font-size:14px;font-family:var(--lx-mono)}
.lmx-app .srch span{font-size:12px;opacity:.65}
.lmx-app .src{display:flex;border:1px solid var(--lx-line);border-radius:10px;background:var(--lx-code);overflow:auto;max-height:none;font-family:var(--lx-mono);font-size:12px;line-height:1.55}
.lmx-app .src pre{margin:0;padding:8px 12px;border:0;background:transparent;white-space:pre;overflow:visible;max-height:none;font-size:inherit;line-height:inherit;font-family:inherit}
.lmx-app .src pre.gut{flex:0 0 auto;text-align:right;opacity:.38;user-select:none;-webkit-user-select:none;border-right:1px solid var(--lx-line);padding-right:10px}
.lmx-app .src pre.txt{flex:1 1 auto;user-select:text}
.lmx-app .src .pk{color:var(--lx-tok-k)}
.lmx-app .src .js,.lmx-app .src .jk{color:var(--lx-tok-s)}
.lmx-app .src .jk{font-weight:600}
.lmx-app .src .jn,.lmx-app .src .jl{color:var(--lx-tok-n)}
.lmx-app .src .pf{color:var(--lx-tok-f)}
.lmx-app .src .pa{color:var(--lx-tok-n);font-style:italic}
.lmx-app .src .pc{color:var(--lx-tok-c);font-style:italic}
.lmx-app .lmx-status{font-size:12px;padding:8px 4px 0;min-height:26px;line-height:1.4}
.lmx-app .lmx-status .st{opacity:.72}
.lmx-app .lmx-status .st.warn{opacity:1;color:var(--lx-ink-warn)}
.lmx-app .lmx-status .st.warn::before{content:"\\26A0\\FE0E";margin-right:6px}
.lmx-app .lmx-status .st.ok::before{content:"\\2713";margin-right:6px;color:var(--lx-ok)}
.lmx-app .lmx-status .st.live::before{content:"";display:inline-block;width:7px;height:7px;border-radius:50%;margin-right:7px;background:#ef4444;animation:lmx-pulse 1.4s ease-in-out infinite}
.lmx-app .lmx-status code,.lmx-app .lmx-hint code{font-family:var(--lx-mono);font-size:11px;padding:1px 5px;border-radius:5px;background:var(--lx-tint-2);user-select:all;-webkit-user-select:all}
.lmx-app .spin{display:inline-block;width:10px;height:10px;margin-right:8px;vertical-align:-1px;border:2px solid rgba(127,127,127,.3);border-top-color:var(--lx-accent);border-radius:50%;animation:lmx-spin .8s linear infinite}
@keyframes lmx-spin{to{transform:rotate(360deg)}}
.lmx-app .skw{padding:6px 2px}
.lmx-app .skw .skl{display:flex;align-items:center;opacity:.75;margin:4px 0 14px}
.lmx-app .skw .sk{display:block;width:auto;height:12px;margin:10px 0;border-radius:6px}
.lmx-app .skw .sk.t{height:18px;width:42%;margin-bottom:16px}
.lmx-app .skc{display:flex;gap:8px;margin:0 0 18px}
.lmx-app .skc .sk{flex:1;height:52px;margin:0;border-radius:12px}
@keyframes lmx-glow{from{background-position:100% 0}to{background-position:0 0}}
.lmx-app .lmx-hint{display:flex;gap:12px;align-items:flex-start;padding:12px 14px;margin:6px 0 10px;border:1px dashed var(--lx-line-2);border-radius:12px;opacity:.85;line-height:1.5}
.lmx-app .lmx-hint b{font-weight:650}
.lmx-app .lmb h3{font-size:17px;margin:6px 0 2px}
.lmx-app .lmb h3 .badge{display:none}
.lmx-app .lmb .card{border-radius:12px;background:var(--lx-tint);border-color:var(--lx-line)}
.lmx-app .lmb .card.warn{border-color:rgba(245,158,11,.75);background:rgba(245,158,11,.08)}
.lmx-app .lmb .card.bad{border-color:rgba(239,68,68,.75);background:rgba(239,68,68,.08)}
.lmx-app .lmb .card.ok{border-color:rgba(16,185,129,.55)}
.lmx-app .lmb .note{border-radius:4px 10px 10px 4px}
.lmx-app .lmb pre{border-radius:10px}
.lmx-app .lmb .wire .wn,.lmx-app .lmb .wire .wf{border-radius:12px;background-color:var(--lx-surface)}
@media (prefers-reduced-motion:reduce){.lmx-app *,.lmx-app *::before{transition:none!important;animation-duration:2.5s!important}}
""" + _explorer_rules() + "\n</style>"


_IN_WINDOW = {  # a command a report names -> where the explorer window shows the same
    "functions": "the Functions tab",
    "function_info": "the Settings tab",
    "errors": "the Errors tab",
    "logs": "the Logs tab",
    "performance": "the Performance tab",
    "code": "the Code tab",
}


_WINDOW_WORDS = [(re.compile(pattern), plain) for pattern, plain in (  # a command's arguments -> what they do
    (r"\bfunctions\(regions='all'\)", "All regions in the region field"),
    (r"\bfunction_info\([^()]*\) shows when it was last called", "the Overview tab shows when it was last called"),
    (r"\bpass limit= to read more, or a shorter since=", "pick a shorter time range"),
    (r"\bpass limit= to read more", "pick a shorter time range"),
)]


def _window_text(text: str) -> str:
    """A sentence from a report, as the explorer window says it: a command the window has a tab for becomes that tab
    (errors('etl') -> "the Errors tab", logs('etl', request_id='8f5c...') -> "the Logs tab (run 8f5ce35b)"), and the
    arguments it names become what they do; other calls and AWS CLI commands stay."""

    def swap(match: re.Match[str]) -> str:
        call = match.group(0)
        name = call.split("(", 1)[0].lstrip(".")
        run = re.search(r"request_id='([\w-]+)'", call)
        if name == "logs" and run:
            return f"run {run.group(1)[:8]} in the Logs tab"
        return _IN_WINDOW.get(name, call)

    text = str(text or "")
    for pattern, plain in _WINDOW_WORDS:
        text = pattern.sub(plain, text)
    text = _CALL_RE.sub(swap, text)
    return _SENTENCE_START_RE.sub(lambda m: m.group(1) + m.group(2)[0].upper() + m.group(2)[1:], text)


_SENTENCE_START_RE = re.compile(r"(^|[.!?]\s+)(the [A-Z]\w* tab|the region field|run [0-9a-f]{8} in the Logs tab)")


def _for_window(blocks: list[Any]) -> list[Any]:
    """A report's blocks for the explorer window: no Next block (its calls are for a cell), and the commands its
    sentences name changed into the tabs that show the same (_window_text)."""
    out = []
    for block in blocks:
        if isinstance(block, _Next):
            continue
        if isinstance(block, _Findings):
            block = dataclasses.replace(block, items=[(level, _window_text(m)) for level, m in block.items],
                                        empty=_window_text(block.empty))
        elif isinstance(block, _Note):
            block = dataclasses.replace(block, text=_window_text(block.text))
        elif isinstance(block, _Title):
            block = dataclasses.replace(block, sub=_window_text(block.sub))
        elif isinstance(block, _Table):
            rows = [[_window_text(cell) if j in block.prose_cols and isinstance(cell, str) else cell
                     for j, cell in enumerate(row)] for row in block.rows]
            block = dataclasses.replace(block, rows=rows, title=_window_text(block.title))
        out.append(block)
    return out


def _skeleton(text: str) -> str:
    """A page still loading: what's being read, with a spinner, over grey bars where the report will be."""
    return (f'<div class="skw"><div class="skl"><span class="spin"></span>{_esc(text)}</div><div class="sk t"></div>'
            '<div class="skc"><div class="sk"></div><div class="sk"></div><div class="sk"></div><div class="sk"></div>'
            '</div><div class="sk"></div><div class="sk" style="width:86%"></div><div class="sk" style="width:64%">'
            "</div></div>")


def _class_if(widget: Any, name: str, on: bool) -> None:
    """Adds or removes a widget's CSS class, sending nothing when it's already as wanted."""
    if on and name not in widget._dom_classes:
        widget.add_class(name)
    elif not on and name in widget._dom_classes:
        widget.remove_class(name)


def _marked(text: str, words: Iterable[str]) -> str:
    """HTML for `text` with every place a search word is found, in any case and inside longer words ('k7qj' in
    'K7QJ2M4XNA'), in <mark>. Each piece is escaped before it's wrapped."""
    unique = sorted({w for w in words if w}, key=len, reverse=True)
    if not unique:
        return _esc(text)
    regex = re.compile("(" + "|".join(re.escape(w) for w in unique) + ")", re.IGNORECASE)
    return "".join(f"<mark>{_esc(piece)}</mark>" if i % 2 else _esc(piece) for i, piece in enumerate(regex.split(text)))


_JSON_TOKEN_RE = re.compile(r'("(?:[^"\\]|\\.)*")(\s*:)?|(-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)|\b(true|false|null)\b')


def _json_source_html(text: str) -> str:
    """JSON text with its keys, text, numbers and true / false / null in their own colours (escaped)."""
    out, last = [], 0
    for m in _JSON_TOKEN_RE.finditer(text):
        out.append(_esc(text[last:m.start()]))
        if m.group(1):
            css = "jk" if m.group(2) else "js"
            out.append(f'<span class="{css}">{_esc(m.group(1))}</span>{_esc(m.group(2) or "")}')
        else:
            out.append(f'<span class="{"jn" if m.group(3) else "jl"}">{_esc(m.group(0))}</span>')
        last = m.end()
    return "".join(out) + _esc(text[last:])


_PY_LITERALS = frozenset({"True", "False", "None"})


def _python_html(code: str) -> str:
    """Python as HTML: keywords, text, numbers, True / False / None, calls, keyword arguments, dict keys and
    comments in their own colours. Every piece is escaped; code that doesn't tokenize is shown plain."""
    try:
        tokens = [t for t in tokenize.generate_tokens(io.StringIO(code).readline)
                  if t.type not in (tokenize.ENDMARKER, tokenize.INDENT, tokenize.DEDENT)]
    except (tokenize.TokenError, SyntaxError):
        return _esc(code)
    starts = [0]
    for line in code.splitlines(keepends=True):
        starts.append(starts[-1] + len(line))
    real = [t for t in tokens if t.type not in (tokenize.NL, tokenize.NEWLINE, tokenize.COMMENT)]
    after = {id(t): real[k + 1].string if k + 1 < len(real) else "" for k, t in enumerate(real)}
    before = {id(t): real[k - 1].string if k else "" for k, t in enumerate(real)}
    out, last = [], 0
    for t in tokens:
        a = starts[t.start[0] - 1] + t.start[1] if t.start[0] - 1 < len(starts) else len(code)
        b = starts[t.end[0] - 1] + t.end[1] if t.end[0] - 1 < len(starts) else len(code)
        if a < last or b <= a:
            continue
        css = ""
        if t.type == tokenize.COMMENT:
            css = "pc"
        elif t.type == tokenize.STRING:
            css = "jk" if after.get(id(t)) == ":" else "js"
        elif t.type == tokenize.NUMBER:
            css = "jn"
        elif t.type == tokenize.NAME:
            if t.string in _PY_LITERALS:
                css = "jl"
            elif keyword.iskeyword(t.string):
                css = "pk"
            elif after.get(id(t)) == "(":
                css = "pf"
            elif after.get(id(t)) == "=" and before.get(id(t)) in ("(", ","):
                css = "pa"
        out.append(_esc(code[last:a]))
        out.append(f'<span class="{css}">{_esc(code[a:b])}</span>' if css else _esc(code[a:b]))
        last = b
    return "".join(out) + _esc(code[last:])


@dataclass
class _Facts:
    """What the explorer's list says about one function, worked out once each time more is read."""

    fn: Function
    metrics: FunctionMetrics | None
    status: RuntimeStatus
    findings: list[tuple[str, str]]
    triggers: list[Trigger]
    cost: float | None
    tone: str  # its dot: 'bad' (failing), 'warn' (a warning), 'ok', 'idle' (not called), '' (not read yet)

    @property
    def warnings(self) -> list[str]:
        return [message for level, message in self.findings if level == "warn"]


def _function_facts(fn: Function, ov: Overview, prices: dict[str, float], now: datetime) -> _Facts:
    detail = ov.detail(fn)
    m = detail.metrics
    found = function_findings(fn, m, detail, prices=prices, now=now)
    status = runtime_status(fn.runtime, package_type=fn.package_type, today=now.date())
    cost = _total(function_monthly_cost(fn, m, detail.provisioned, detail.log_group, prices=prices))
    if _errors_level(m, now) == "warn" or fn.state == "Failed" or fn.last_update == "Failed" or (
            fn.reserved_concurrency == 0):
        tone = "bad"
    elif any(level == "warn" for level, _ in found):
        tone = "warn"
    elif m is not None:
        tone = "ok" if m.invocations else "idle"
    else:
        tone = ""
    return _Facts(fn, m, status, found, detail.triggers, cost, tone)


def _seconds(since: str) -> float:
    """How far back a time range reaches, in seconds: '1h' -> 3600, '7d' -> 604800, a date -> the time since it."""
    start = parse_time(since)
    return (_utcnow() - start).total_seconds() if start is not None else 0.0


def _retries(count: int) -> str:
    return "no retries" if not count else "1 retry" if count == 1 else f"{count} retries"


def _short_day(moment: datetime) -> str:
    return f"{moment:%b} {moment.day}"


def _run_clock(moment: datetime, today: date) -> str:
    """A run's start in UTC: '14:02:31' today, 'Oct 5 14:02' another day."""
    moment = moment.astimezone(timezone.utc)
    return moment.strftime("%H:%M:%S") if moment.date() == today else f"{_short_day(moment)} {moment:%H:%M}"


def _offset(seconds: float) -> str:
    """How far into a run a line came: '+0.052s', '+12.4s', '+184s'."""
    if seconds < 10:
        return f"+{seconds:.3f}s"
    return f"+{seconds:.1f}s" if seconds < 100 else f"+{seconds:,.0f}s"


def _line_parts(message: str) -> tuple[str, str]:
    """A log line as the window shows it: (its text, without what Lambda writes before it (the time, request ID and
    level, which the window shows in its own columns), and its other fields as indented JSON when it's a JSON line)."""
    text = message.rstrip()
    if text.startswith("{"):
        try:
            doc = json.loads(text)
        except ValueError:
            doc = None
        if isinstance(doc, dict):
            if str(doc.get("type", "")).startswith("platform."):
                record = doc.get("record")
                return f"{doc['type']} {json.dumps(record, default=str)}" if record else str(doc["type"]), ""
            inner = doc.get("message", doc.get("msg"))
            rest = {k: v for k, v in doc.items() if k not in ("message", "msg", "level", "timestamp", "time",
                                                                "requestId", "AWSRequestId")}
            if isinstance(inner, dict) and (inner.get("errorType") or inner.get("errorMessage")):
                body = _error_text_of(inner)  # an error Lambda's runtime logged: its type, message and stack
                rest.update({k: v for k, v in inner.items() if k not in ("errorType", "errorMessage", "stack",
                                                                         "stackTrace", "trace")})
            elif inner is None:
                if not (doc.get("errorType") or doc.get("errorMessage")):
                    return text, ""
                body = _error_text_of(doc)
                rest = {k: v for k, v in rest.items() if k not in ("errorType", "errorMessage", "stack",
                                                                   "stackTrace", "trace")}
            else:
                body = inner if isinstance(inner, str) else json.dumps(inner, default=str)
            for key in ("exception", "stack_trace", "stackTrace"):  # a logger's traceback (Powertools: exception)
                if isinstance(rest.get(key), str) and rest[key].strip():
                    body = f"{body}\n{rest.pop(key).rstrip()}"
            return body, json.dumps(rest, indent=2, default=str, ensure_ascii=False) if rest else ""
    text = _LINE_PREFIX_RE.sub("", text, count=1)  # '[INFO]\t<time>\t<request ID>\t', '<time>\t<request ID>\t'
    return _LEVEL_PREFIX_RE.sub("", text, count=1), ""  # then '[ERROR] ' (Python), 'INFO\t' (Node.js)


_LEVEL_PREFIX_RE = re.compile(r"^(?:\[(?:ERROR|CRITICAL|FATAL|WARNING|WARN|INFO|DEBUG|TRACE)\]\s*|"
                              r"(?:ERROR|CRITICAL|FATAL|WARNING|WARN|INFO|DEBUG|TRACE)\t)")


def _error_text_of(doc: dict[str, Any]) -> str:
    """An error as JSON logs carry it ({"errorType", "errorMessage", "stack"}) as a traceback reads: 'TypeError:
    Cannot read ...' and then the stack's lines."""
    head = ": ".join(str(doc[k]) for k in ("errorType", "errorMessage") if doc.get(k))
    stack = doc.get("stack") or doc.get("stackTrace") or doc.get("trace") or []
    lines = stack.splitlines() if isinstance(stack, str) else [str(line) for line in stack]
    if lines and head and lines[0].strip().startswith(head.split(":")[0]):
        lines = lines[1:]  # Node.js puts the message first in its stack too
    return "\n".join([head, *lines]) if lines else head


def _run_summary(run: LogRun) -> tuple[str, str]:
    """(what a run's line says after its time and numbers, its tone): what made it fail, else the first thing it
    logged."""
    error = run.error
    if error is not None:
        return error[1].splitlines()[0][:300], "bad" if run.status in ("failed", "timeout") else "warn"
    for event in run.events:
        if _marker(event.message) is None:
            text = _line_parts(event.message)[0].strip()
            if text:
                return text.splitlines()[0][:300], ""
    if run.status == "outside":
        return f"{_plural(len(run.events), 'line')} outside a run", ""
    return "(it logged nothing of its own)", ""


def _run_tip(run: LogRun, opened: bool) -> str:
    """A run's tooltip: what happened, when, its request ID, and what a click does."""
    label = _RUN_STATES.get(run.status, ("", "", run.status))[2]
    return " · ".join(filter(None, [label[:1].upper() + label[1:], f"started {_stamp(run.start)} UTC",
                                    f"request {run.request_id}" if run.request_id else "",
                                    "click to fold its lines" if opened else "click to see its lines"]))


def _run_face(run: LogRun, timeout: int, words: list[str], today: date) -> str:
    """A run's line in the Logs tab: its status, when it started, its run time against the timeout, the memory it
    used, a cold start, what failed or the first thing it logged, and its request ID."""
    icon, tone, _ = _RUN_STATES.get(run.status, ("?", "", run.status))
    report = run.report
    took = ""
    if report is not None:
        share = min(1.0, report.duration / (timeout * 1000)) if timeout else 0.0
        fill = "bad" if share >= 0.9 else "warn" if share >= 0.7 else ""
        took = (f'<span class="rb"><i class="{fill}" style="width:{max(share * 100, 2):.1f}%"></i></span>'
                f"{_esc(human_ms(report.duration))}")
    memory = f"{report.max_memory:,} of {report.memory:,} MB" if report is not None and report.memory else ""
    cold = ""
    if report is not None and (report.init is not None or report.restore is not None):
        cold = f"cold {human_ms(report.init if report.init is not None else report.restore)}"
    elif run.cold:
        cold = "cold start"
    summary, summary_tone = _run_summary(run)
    request = run.request_id or ""
    return (f'<div class="rr {tone}"><span class="rv"></span><span class="ri">{_esc(icon)}'
            f'</span><span class="rw">{_esc(_run_clock(run.start, today))}</span><span class="rd">{took}</span>'
            f'<span class="rm">{_esc(memory)}</span><span class="rc">{_esc(cold)}</span>'
            f'<span class="rs {summary_tone}">{_marked(summary, words)}</span>'
            f'<span class="rq">{_esc(request[:8])}</span></div>')


def _run_body(run: LogRun, words: list[str], limit: int = _BODY_LINES) -> str:
    """An open run's lines: how far into the run each came, its level, and its text (JSON lines as their message, with
    their other fields a click away), then what its REPORT line says and its request ID to copy."""
    started = next((e.time for e in run.events if (_marker(e.message) or ("",))[0] == "start"), run.start)
    lines = [e for e in run.events if (_marker(e.message) or ("",))[0] not in ("start", "end", "report")]
    rows = []
    for event in lines[:limit]:
        level = line_level(event.message)
        text, fields = _line_parts(event.message)
        seconds = (event.time - started).total_seconds()
        when = "init" if seconds < 0 else _offset(seconds)
        badge = {"error": "ERROR", "warn": "WARN", "info": "INFO", "debug": "DEBUG"}.get(level, "")
        extra = (f'<details><summary>{{…}} fields</summary><span class="jf">{_json_source_html(fields)}</span>'
                 "</details>" if fields else "")
        rows.append(f'<div class="rl {level}{" hit" if event.matched else ""}"><span class="lt" title="'
                    f'{_esc(_stamp(event.time))} UTC">{_esc(when)}</span><span class="lv {level}">{badge}</span>'
                    f'<span class="lm">{_marked(text, words) or "&nbsp;"}{extra}</span></div>')
    if not lines:
        rows.append('<div class="rmore">It logged nothing of its own: only Lambda\'s START and REPORT lines.</div>')
    elif len(lines) > limit:
        rows.append(f'<div class="rmore">… {len(lines) - limit:,} more lines. x.ui.logs(name, request_id=...) shows '
                    "all of them.</div>")
    facts = []
    report = run.report
    if report is not None:
        facts.append(f"<span>Ran <b>{_esc(human_ms(report.duration))}</b></span>")
        facts.append(f"<span>billed {_esc(human_ms(report.billed))}</span>")
        if report.memory:
            tight = report.max_memory >= 0.9 * report.memory
            facts.append(f'<span class="{"warn" if tight else ""}">used <b>{report.max_memory:,}</b> of '
                         f"{report.memory:,} MB</span>")
        if report.init is not None:
            facts.append(f"<span>cold start {_esc(human_ms(report.init))}</span>")
        if report.restore is not None:
            facts.append(f"<span>SnapStart restore {_esc(human_ms(report.restore))}</span>")
        if report.status:
            facts.append(f'<span class="bad">status {_esc(report.status)}'
                         + (f" ({_esc(report.error_type)})" if report.error_type else "") + "</span>")
    elif run.request_id:
        facts.append("<span>No REPORT line yet: still running, or it ended after the time range</span>")
    if run.request_id:
        facts.append(f'<span>request <code title="Click to select, then copy">{_esc(run.request_id)}</code></span>')
    if run.stream:
        facts.append(f'<span title="The log stream: one execution environment">{_esc(_clip(run.stream, 70))}</span>')
    return f'<div class="rbody">{"".join(rows)}<div class="rf">{"".join(facts)}</div></div>'


def _function_face(f: _Facts, words: list[str], multi: bool, numbers: bool, now: datetime) -> str:
    """A function's line in the list: its health dot, name, runtime and description, then its numbers in columns."""
    fn, m = f.fn, f.metrics
    runtime = f.status.label
    parts = [fn.description, _triggers_text(f.triggers), f"{fn.memory:,} MB", f"{fn.timeout} s"]
    line = " · ".join(p for p in parts if p)
    loading = '<span class="sk"></span>'

    def cell(css: str, value: str, tone: str = "") -> str:
        inner = f'<span class="pill {tone}">{_esc(value)}</span>' if tone else _esc(value)
        return f'<div class="fc {css}">{inner}</div>'

    if m is None:
        dash = loading if numbers else "-"
        cells = "".join(f'<div class="fc {css}">{dash}</div>' for css in ("", "", "c-dur", "", "c-called"))
    else:
        level = _errors_level(m, now)
        rate = _pct(m.error_rate) if m.invocations else "-"
        cells = (cell("", _count(round(m.invocations)) if m.invocations else "0")
                 + cell("" if m.invocations else "dim", rate, "bad" if level == "warn" else "warn" if level else "")
                 + cell("c-dur" + ("" if m.invocations else " dim"), human_ms(m.avg_duration) if m.invocations
                        else "-")
                 + cell("" if f.cost is not None else "dim", human_money(f.cost) if f.cost is not None else "-")
                 + cell("c-called" + ("" if m.invocations else " dim"),
                        _day_age(m.last_invoked, now) if m.invocations else "not in 30d"))
    warnings = len(f.warnings)
    badge = f'<span class="wb">{warnings}</span>' if warnings else ""
    region = f'<span class="rg">{_esc(fn.region)}</span>' if multi else ""
    return (f'<div class="fr"><span class="dot {f.tone}"></span><div class="fm"><div class="fn">'
            f"<b>{_marked(fn.name, words)}</b>"
            f'<span class="rt {f.status.tone}">{_esc(runtime)}</span>{region}</div>'
            f'<div class="ff">{_marked(line, words)}</div></div>{cells}<div class="fc">{badge}</div><div></div></div>')


def _triggers_text(triggers: list[Trigger]) -> str:
    labels = list(dict.fromkeys(t.short for t in triggers))
    return ", ".join(labels[:3]) + (f" +{len(labels) - 3}" if len(labels) > 3 else "")


def _destination(arn: str | None) -> tuple[str, str]:
    """(what it is, its name) for an on-success / on-failure destination or a dead-letter queue: an SQS queue, an SNS
    topic, another function..."""
    parts = (arn or "").split(":")
    kind = _DESTINATIONS.get(parts[2], parts[2]) if len(parts) > 5 else ""
    return kind or "destination", _source_name(arn)[0] or (arn or "-")


def _code_file_face(f: CodeFile, words: list[str], handler: str | None) -> str:
    """A file's line in the Code tab: its type, name and folder, and its size."""
    folder, _, name = f.path.rpartition("/")
    kind = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    badge, tone = {"py": ("PY", "py"), "js": ("JS", "js"), "mjs": ("JS", "js"), "cjs": ("JS", "js"),
                   "ts": ("TS", "js"), "rb": ("RB", "py"), "json": ("JSON", "cfg"), "yaml": ("YAML", "cfg"),
                   "yml": ("YAML", "cfg"), "toml": ("TOML", "cfg"), "txt": ("TXT", ""), "md": ("MD", "")}.get(
        kind, ((kind or "file")[:4].upper(), ""))
    if _SECRET_FILE_RE.search(f.path):
        badge, tone = "KEY", "key"
    junk = " junk" if _JUNK_RE.search(f.path) else ""
    hb = '<span class="hb">HANDLER</span>' if f.path == handler else ""
    return (f'<div class="cf{junk}"><span class="ct {tone}">{_esc(badge)}</span><div class="cm"><div class="cn">'
            f'{_marked(name, words)}{hb}</div><div class="cd">{_marked(folder + "/" if folder else "top level", words)}'
            f'</div></div><span class="cz">{_esc(human_size(f.size))}</span></div>')


def _source_html(path: str, text: str) -> str:
    """A file's text with line numbers, Python and JSON in colour (every piece escaped)."""
    lines = text.count("\n") + (0 if text.endswith("\n") else 1)
    body = (_python_html(text) if path.endswith(".py") else _json_source_html(text) if path.endswith(".json")
            else _esc(text))
    gutter = "\n".join(str(i) for i in range(1, lines + 1))
    return f'<div class="src"><pre class="gut">{gutter}</pre><pre class="txt">{body}</pre></div>'


def _window_errors(method: Callable) -> Callable:
    """For the explorer's own commands: an AWS or input error is said in the window's status line (or, without the
    window, as a note) instead of a traceback."""

    @functools.wraps(method)
    def wrapper(self: LambdaExplorer, *args: Any, **kwargs: Any) -> None:
        try:
            return method(self, *args, **kwargs)
        except (ClientError, BotoCoreError, ValueError, TypeError) as exc:
            if self._w is None:
                self.ui._show([_Note(self._error_text(exc), "warn")])
            else:
                self._status(self._error_text(exc), "warn")
        return None

    return wrapper


class _FunctionField:
    """The explorer's function field: a button under its face (the function's name, health dot and runtime) that opens
    a list over the window, with a search box that finds a function by any part of its name, description, runtime or
    region. Enter picks the first line, or hands what's typed to on_text when no line has it (an ARN, or a function the
    list doesn't hold). Plain widgets and CSS: each line is a button under its text, so the whole line is the click
    target. While the list is open, the window's backdrop takes a click anywhere else and closes it."""

    def __init__(self, app: LambdaExplorer, *, on_pick: Callable[[Function], None], on_text: Callable[[str], None]):
        w, layout = app._w, app._w.Layout
        self.app, self.on_pick, self.on_text = app, on_pick, on_text
        self.choices: list[Function] = []
        self.value = ""  # the ARN shown
        self.problem = ""  # why there's nothing to list
        self.message = ""  # what went wrong with the last Enter
        self.shown: list[str] = []  # the ARNs the list shows, top to bottom
        self.rows: dict[str, tuple[Any, Any, Any]] = {}  # ARN -> (its line, the line's button, its text)
        self.button = w.Button(tooltip="Pick another function", layout=layout(width="100%", height="100%"))
        self.button.add_class("lmx-trig-b")
        self.button.on_click(app._safely(lambda _button: self.toggle()))
        self.face = w.HTML(layout=layout(width="100%"))
        self.face.add_class("lmx-face")
        trigger = w.Box([self.button, self.face], layout=layout(width="100%"))
        trigger.add_class("lmx-trig")
        self.search = w.Text(placeholder="Search by name, description, runtime or region", continuous_update=True,
                             layout=layout(flex="1 1 auto", width="auto"))
        self.search.add_class("lmx-find")
        self.search.observe(app._safely(lambda _change: self._draw_list()), names="value")
        self.search.on_msg(app._on_enter(self._entered))
        close = w.Button(description="✕", tooltip="Close the list", layout=layout(flex="0 0 auto"))
        close.add_class("lmx-x")
        close.on_click(app._safely(lambda _button: self.close()))
        self.list = w.VBox(layout=layout(width="100%"))
        self.list.add_class("lmx-opts")
        self.foot = w.HTML(layout=layout(width="100%"))
        self.panel = w.VBox([w.HBox([self.search, close], layout=layout(width="100%", align_items="center")),
                             self.list, self.foot], layout=layout(display="none"))
        self.panel.add_class("lmx-pop")
        self.box = w.VBox([trigger, self.panel])
        self.box.add_class("lmx-field")
        self.draw()

    @property
    def is_open(self) -> bool:
        return self.panel.layout.display != "none"

    def set_choices(self, functions: Iterable[Function]) -> None:
        self.choices = sorted(functions, key=lambda f: (f.name.lower(), f.region))
        self.draw()

    def _choice(self, arn: str) -> Function | None:
        return next((f for f in self.choices if f.arn == arn), None)

    def draw(self) -> None:
        fn = self._choice(self.value) or self.app.function
        if fn is not None and fn.arn != self.value:
            fn = None
        facts = self.app._facts.get(fn.arn) if fn is not None else None
        tone = facts.tone if facts is not None else ""
        shown = (f"<b>{_esc(fn.name)}</b>" if fn is not None
                 else '<b style="opacity:.55;font-weight:500">Pick a function</b>')
        runtime = (f'<span class="fxi">{_esc(fn.runtime or "container image")}'
                   + (f" · {_esc(fn.region)}" if self.app.multi else "") + "</span>") if fn is not None else ""
        self.app._set(self.face, '<div class="fx"><div class="fxl">Function</div><div class="fxv">'
                                 + (f'<span class="dot {tone}"></span>' if fn is not None else "") + shown + runtime
                                 + '</div><span class="chev"></span></div>')
        if self.is_open:
            self._draw_list()

    def open(self) -> None:
        if self.is_open:
            return
        self.message = ""
        self.panel.layout.display = ""
        self.app.backdrop.layout.display = ""
        _class_if(self.box, "lmx-open", True)
        self._draw_list()
        if hasattr(self.search, "focus"):  # ipywidgets 8
            self.search.focus()

    def close(self) -> None:
        if not self.is_open:
            return
        self.panel.layout.display = "none"
        self.app.backdrop.layout.display = "none"
        _class_if(self.box, "lmx-open", False)
        self.app._quietly(self.search, value="")
        self.message = ""

    def toggle(self) -> None:
        self.close() if self.is_open else self.open()

    def matches(self) -> list[Function]:
        text = str(self.search.value or "")
        try:
            name, _, _ = parse_function_ref(text)
            text = name if text.strip().startswith(("arn:", "http")) else text
        except ValueError:
            pass
        ranked = [(rank, i, f) for i, f in enumerate(self.choices)
                  if (rank := search_rank(text, (f.name, f.description, f.runtime, f.region, f.arn))) is not None]
        return [f for _, _, f in sorted(ranked, key=lambda r: r[:2])]

    def _row(self, fn: Function, words: list[str]) -> Any:
        if fn.arn not in self.rows:
            w, layout = self.app._w, self.app._w.Layout
            button = w.Button(layout=layout(width="100%", height="100%"))
            button.add_class("lmx-opt-b")
            button.on_click(self.app._safely(lambda _button, arn=fn.arn: self._clicked(arn)))
            text = w.HTML(layout=layout(width="100%"))
            text.add_class("lmx-opt-t")
            row = w.Box([button, text], layout=layout(width="100%"))
            row.add_class("lmx-opt")
            self.rows[fn.arn] = (row, button, text)
        row, button, text = self.rows[fn.arn]
        _class_if(row, "lmx-on", fn.arn == self.value)
        facts = self.app._facts.get(fn.arn)
        m = facts.metrics if facts is not None else None
        numbers = (f"{_count(round(m.invocations))} calls" + (f", {_pct(m.error_rate)} failed" if m.errors else "")
                   if m is not None and m.invocations else "not called in 30 days" if m is not None else "")
        note = " · ".join(p for p in (fn.description, fn.region if self.app.multi else "", numbers) if p)
        tip = " · ".join(p for p in (fn.name, fn.runtime or "container image", note) if p)
        if button.tooltip != tip:
            button.tooltip = tip
        self.app._set(text, f'<div class="op"><span class="dot {facts.tone if facts else ""}"></span><div class="opb">'
                            f'<div class="opt"><b>{_marked(fn.name, words)}</b><span class="opi">'
                            f'{_marked(fn.runtime or "container image", words)}</span></div>'
                            + (f'<div class="opn">{_marked(note, words)}</div>' if note else "") + "</div></div>")
        return row

    def _draw_list(self) -> None:
        text = str(self.search.value or "").strip()
        found = self.matches()
        words = text.split()
        self.list.children = [self._row(f, words) for f in found[:80]]
        self.shown = [f.arn for f in found[:80]]
        level, line = "", ""
        where = self.app._where()
        if self.message:
            level, line = "warn", self.message
        elif not self.choices:
            level, line = ("warn" if self.problem else ""), self.problem or f"There are no functions in {where}."
        elif text and not found:
            level, line = "warn", f"No function in {where} matches {text!r}. Enter looks it up by name or ARN."
        else:
            line = (f"{len(found):,} of {len(self.choices):,}" if text else _plural(len(self.choices), "function"))
            line += f" in {where}" + (" · Enter picks the first" if text else "")
            if len(found) > 80:
                line += " · type to narrow the list"
        self.app._set(self.foot, f'<div class="opf {level}">{_prose(line)}</div>')

    def _clicked(self, arn: str) -> None:
        self.close()
        fn = self._choice(arn)
        if fn is not None and arn != self.value:
            self.on_pick(fn)

    def _entered(self) -> None:
        text = str(self.search.value or "").strip()
        if not text:
            self.close()
            return
        found = self.matches()
        try:
            if found:
                self._clicked(found[0].arn)
            else:
                self.on_text(text)
                self.close()
        except (ValueError, ClientError, BotoCoreError) as exc:  # said under the list, where the eyes are
            self.message = str(exc) if isinstance(exc, ValueError) else self.app._error_text(exc)
            self._draw_list()


class _Row:
    """One reusable line of a list: a full-width button under its face (HTML), so the whole line is the click target,
    with an optional small action button on its right (the function list's Logs). `item` is what it shows."""

    def __init__(self, app: LambdaExplorer, on_click: Callable[[Any], None], *, short: bool = False,
                 action: tuple[str, str, Callable[[Any], None]] | None = None):
        w, layout = app._w, app._w.Layout
        self.item: Any = None
        self.button = w.Button(layout=layout(width="100%", height="100%"))
        self.button.add_class("lmx-row-b")
        self.button.on_click(app._safely(lambda _button: on_click(self.item) if self.item is not None else None))
        self.face = w.HTML(layout=layout(width="100%"))
        self.face.add_class("lmx-row-t")
        children = [self.button, self.face]
        self.action = None
        if action is not None:
            label, tip, act = action
            self.action = w.Button(description=label, tooltip=tip, layout=layout(width="auto"))
            self.action.add_class("lmx-act")
            self.action.on_click(app._safely(lambda _button: act(self.item) if self.item is not None else None))
            children.append(self.action)
        self.box = w.Box(children, layout=layout(width="100%"))
        self.box.add_class("lmx-row")
        if short:
            self.box.add_class("lmx-short")


class _RunRow:
    """One reusable run in the Logs tab: a full-width button under its face (its status, when it started, run time,
    memory, cold start, what failed, its request ID) that opens its lines below it, or folds them again."""

    def __init__(self, app: LambdaExplorer):
        w, layout = app._w, app._w.Layout
        self.run: LogRun | None = None
        self.button = w.Button(tooltip="Click to see its lines", layout=layout(width="100%", height="100%"))
        self.button.add_class("lmx-run-b")
        self.button.on_click(app._safely(lambda _button: app._toggle_run(self)))
        self.face = w.HTML(layout=layout(width="100%"))
        self.face.add_class("lmx-run-t")
        head = w.Box([self.button, self.face], layout=layout(width="100%"))
        head.add_class("lmx-run-h")
        self.body = w.HTML(layout=layout(width="100%", display="none"))
        self.box = w.VBox([head, self.body], layout=layout(width="100%"))
        self.box.add_class("lmx-run")


class LambdaExplorer:
    """The Lambda explorer: a window to look through your functions by clicking, with nothing to type but a search.
    The region field and the function field at the top pick what it shows; the cards beside them say how it's doing.

        Functions    every function in the region (or in every region): runtime, calls, errors, run time, cost and
                     warnings, the ones that need attention first. Search, filter, sort by a column, and click one to
                     open it (or Logs on its line, to go straight to its logs)
        Overview     how the picked function is doing: what's wrong and what to do, what calls it and where its
                     results, failed events and logs go, calls and run time day by day, and what it costs
        Logs         what it logged, run by run, newest first: each run's start, status, run time, memory and cold
                     start, the failed ones in red; click a run for its lines. Type to find runs with some text (an
                     order ID, KeyError, a request ID), Enter to search CloudWatch for the whole time range, pick the
                     time range, show only failed runs or cold starts, and turn on Live to see new runs as they come
        Errors       its errors grouped by cause, with what to do about each, and the runs that failed: click one to
                     read every line of it
        Performance  run times against the timeout, memory used against what it has, cold starts, and the slowest
                     runs (click one to read it)
        Code         the files in its deployment package, and the source of the one you click (secrets files held
                     back)
        Settings     every setting in plain English, and as Lambda returns it (environment values hidden)

    name: a function to open (its name, 'name:alias', an ARN or a console link), on tab= ('overview', 'logs',
    'errors', 'performance', 'code' or 'settings'). region: the region to list, or 'all' for every region your account
    has turned on (the region field switches it). view / core: a LambdaView or LambdaAnalyzer to use (else one is made
    from region / profile). height: the height of the tabs' pages, which fill the browser window unless set (pixels, or
    CSS like '80vh'). mode: 'auto' (the window in Jupyter, reports elsewhere), 'widgets' or 'text'.

    Nothing here invokes or changes a function: the window only reads, and where a change would help it shows the
    command. x.ui is a LambdaView for reports in other cells (x.ui.errors(...)); x.overview, x.function, x.detail,
    x.logs_page and x.package hold the data behind what's shown."""

    def __init__(
        self,
        name: str | None = None,
        *,
        tab: str | None = None,
        view: LambdaView | None = None,
        core: LambdaAnalyzer | None = None,
        region: str | None = None,
        profile: str | None = None,
        height: int | str | None = None,
        mode: str = "auto",
        progress: str = "auto",
    ):
        if mode not in ("auto", "widgets", "text"):
            raise ValueError("mode must be 'auto', 'widgets' or 'text'")
        if tab is not None and tab not in {key for key, *_ in _EXPLORER_TABS}:
            raise ValueError(f"tab= is one of {', '.join(repr(key) for key, *_ in _EXPLORER_TABS)}; got {tab!r}")
        every = isinstance(region, str) and region.strip().lower() == "all"
        if view is None:
            view = LambdaView(core or LambdaAnalyzer(region=None if every else region, profile=profile),
                              mode="text" if mode == "text" else "auto", progress=progress)
        self.ui = view
        self.core = view.core
        self.height = height
        self.region: str | None = "all" if every else region  # the region field: a region, or 'all'
        self.overview: Overview | None = None  # the functions listed
        self.function: Function | None = None  # the one picked
        self.detail: FunctionDetail | None = None  # describe() of it: the Overview and Settings tabs
        self.logs_page: LogPage | None = None  # the Logs tab's read of the time range
        self.found: LogPage | None = None  # the Logs tab's CloudWatch search, or the one run opened
        self.error_report: ErrorReport | None = None  # the Errors tab
        self.perf: Performance | None = None  # the Performance tab
        self.package: CodePackage | None = None  # the Code tab
        self.shown: dict[str, list[Any]] = {}  # page -> the blocks drawn there last (for tests, and the curious)
        self.quiet = False  # set while the code (not a person) changes a widget, so its observer does nothing
        self._w: Any = None
        self._facts: dict[str, _Facts] = {}  # function ARN -> what the list says about it
        self._numbers_pending = False  # CloudWatch's numbers are being read for the list
        self._want, self._want_tab = name, tab  # opened once the functions are listed...
        self._want_search: str | None = None  # ...and searched for in its logs
        self._tab = "functions"
        self._asked: set[str] = set()  # the function's tabs whose data was asked for
        self._listing_started = 0.0
        self._query, self._chip, self._sort, self._descending, self._offset = "", "", "problems", True, 0
        self._rows: list[_Row] = []
        self._visible: list[_Facts] = []  # the functions the list's search and chip let through, in order
        self._range, self._log_query, self._run_chip, self._run_offset = "1h", "", "", 0
        self._runs: list[LogRun] = []  # logs_page's runs, newest first
        self._found_runs: list[LogRun] = []  # found's
        self._searched = ""  # what the CloudWatch search (or the run opened) looked for
        self._open_runs: set[str] = set()  # LogRun.key of the runs opened to show their lines
        self._run_rows: list[_RunRow] = []
        self._live, self._live_token, self._live_started, self._live_new = False, 0, 0.0, 0
        self._errors_range, self._perf_range = "24h", "24h"
        self._error_rows: list[_Row] = []
        self._slow_rows: list[_Row] = []
        self._code_fn: Function | None = None  # the function as GetFunction gave it, with the link to its package
        self._package_data: bytes | None = None
        self._code_query, self._code_offset, self._code_file = "", 0, None
        self._code_rows: list[_Row] = []
        self._pagers: dict[str, tuple[Any, dict[str, Any]]] = {}
        self._function_chips: dict[str, Any] = {}  # chip key -> its button, made once
        self._run_chip_buttons: dict[str, Any] = {}
        self._empties: dict[str, Any] = {}  # list -> the line it shows when it's empty
        self._jobs: dict[str, int] = {}  # background job -> its latest number: an older one's result is dropped
        self._tasks: dict[str, Any] = {}  # background job -> its asyncio task (tests wait for them)
        self._pool: ThreadPoolExecutor | None = None
        self._counted = 0  # what the background read has read so far
        self._said: tuple[str, str, bool, str] = ("", "", False, "")  # the status line: text, level, busy, whose
        self._listing = False  # the functions are being listed
        self._shown_at: Any = object()
        note = ""
        if mode != "text" and (mode == "widgets" or _in_notebook()):
            try:
                self._w = _require("ipywidgets", "The explorer window")
            except ImportError as exc:
                note = (f"{exc}, which SageMaker notebooks normally have: install it and restart the kernel. Until "
                        "then, the same in reports:")
        if self._w is None:
            self._reports(note, mode)
            return
        self._build()
        self._begin()
        self._display()

    # ------------------------------------------------------------------ public commands

    def __repr__(self) -> str:
        name = self.function.name if self.function is not None else "no function picked"
        return f"LambdaExplorer({name}) · help(LambdaExplorer) says what it shows"

    @_window_errors
    def open(self, name: str, *, tab: str | None = None) -> None:
        """Opens a function (its name, 'name:alias', an ARN or a console link), as picking it in the function field
        does: its Overview, or tab= ('logs', 'errors', 'performance', 'code', 'settings')."""
        if tab is not None and tab not in _FUNCTION_TABS:
            raise ValueError(f"tab= is one of {', '.join(map(repr, _FUNCTION_TABS))}; got {tab!r}")
        parse_function_ref(name)  # says what a name looks like, before anything is read
        if self._w is None:
            self._report_for(name, tab)
            return
        if self._listing:
            self._want, self._want_tab, self._want_search = name, tab, None
            self._status(f"{name} opens once the functions are listed.")
            return
        self._open_named(name, tab)

    @_window_errors
    def logs(self, name: str | None = None, *, search: str | None = None, since: str | None = None) -> None:
        """Shows a function's logs in the Logs tab (the one picked, or name=), as clicking the tab does. since= picks
        the time range ('15m', '1h', '24h', '7d', a date...), and search= finds the runs with a line that has the text
        (an order ID, KeyError, a request ID, or a CloudWatch Logs filter pattern), searching the whole time range."""
        if since is not None:
            LambdaAnalyzer._window(since)  # a time it can't read is said before anything changes
        if self._w is None:
            target = name or (self.function.name if self.function is not None else None)
            if target is None:
                raise _Hint("Name the function: x.logs('my-function').")
            self.ui.logs(target, search=search, since=since)
            return
        if since is not None:
            self._set_range(str(since))
        if name is not None:
            if self._listing:
                self._want, self._want_tab, self._want_search = name, "logs", search
                self._status(f"{name}'s logs open once the functions are listed.")
                return
            self._open_named(name, "logs", search=search)
            return
        if self.function is None:
            raise _Hint("Pick a function first: click one in the Functions tab, or x.logs('my-function').")
        if search:
            self._show_tab("logs", load=False)
            self._quietly(self.log_find, value=search)
            self._log_query = search
            self._log_search()
        else:
            self._show_tab("logs", load=False)
            self._load_logs()

    @_window_errors
    def run(self, request_id: str) -> None:
        """Shows one run of the picked function in the Logs tab, every line of it, by its request ID (from an error, a
        REPORT line or your own logs), looking back at least 24 hours."""
        if self.function is None:
            raise _Hint("Pick a function first: click one in the Functions tab, or x.open('my-function').")
        if self._w is None:
            self.ui.logs(self.function.name, request_id=request_id)
            return
        if _seconds(self._range) < 86400:
            self._set_range("24h")
        self._show_tab("logs", load=False)
        self._quietly(self.log_find, value=request_id)
        self._log_query = request_id.strip()
        self._log_search()

    @_window_errors
    def refresh(self) -> None:
        """Reads everything again: the functions, and the picked function's tabs (the ↻ button)."""
        keep = self.function
        self._load_list(reopen=keep)

    # ------------------------------------------------------------------ plumbing

    def _ipython_display_(self) -> None:
        """A cell ending with the explorer shows its window, unless the same cell already did (without the window,
        its reports were shown when it was made)."""
        if self._w is not None and _cell_number() != self._shown_at:
            self._display()

    def _display(self) -> None:
        self._shown_at = _cell_number()
        if self._shown_at is None:
            return  # outside IPython there's nowhere to show widgets
        from IPython.display import display

        display(self.root)

    def _reports(self, note: str, mode: str) -> None:
        """Without the window (no ipywidgets, or not in Jupyter): the same as reports."""
        if note:
            self.ui._show([_Note(note, "warn")])
        elif mode != "text":
            self.ui._show([_Note("The explorer window needs Jupyter (SageMaker, JupyterLab or VS Code). Here, the same "
                                 "in reports: function_info('name') explains one function, logs('name') shows what it "
                                 "logged and errors('name') why it fails.")])
        if self._want:
            self._report_for(self._want, self._want_tab)
        else:
            regions = "all" if self.region == "all" else [self.region] if self.region else None
            self.ui.functions(regions=regions)

    def _report_for(self, name: str, tab: str | None) -> None:
        """A function's tab, as a report (without the window)."""
        region = None if self.region in (None, "all") else self.region
        command = {"logs": self.ui.logs, "errors": self.ui.errors, "performance": self.ui.performance,
                   "code": self.ui.code}.get(tab or "", self.ui.function_info)
        command(name, region=region)

    @property
    def multi(self) -> bool:
        """Whether the list holds more than one region's functions."""
        return self.region == "all" or bool(self.overview is not None and len(self.overview.regions) > 1)

    def _where(self) -> str:
        return "every region" if self.region == "all" else str(self.region or "this region")

    def _full(self) -> Function:
        """The picked function as GetFunction gave it, once describe() has read it (else as the list has it)."""
        assert self.function is not None
        return self.detail.function if self.detail is not None else self.function

    def _safely(self, handler: Callable[..., Any]) -> Callable[..., Any]:
        @functools.wraps(handler)
        def run(*args: Any, **kwargs: Any) -> Any:
            try:
                return handler(*args, **kwargs)
            except Exception as exc:  # shown in the window: a widget callback's error would go to the browser log
                self._status(self._error_text(exc), "warn")
                return None

        return run

    def _on_enter(self, handler: Callable[..., Any]) -> Callable[..., Any]:
        """A text box's Enter: the 'submit' message the box sends (on_submit is deprecated), so leaving the box
        doesn't trigger it."""
        safe = self._safely(handler)
        return lambda _widget, content, _buffers: safe() if content.get("event") == "submit" else None

    def _error_text(self, exc: BaseException) -> str:
        if isinstance(exc, ClientError):
            error = exc.response.get("Error", {})
            code, message = error.get("Code", "Error"), error.get("Message", str(exc))
            return f"{code}: {_window_text(self.ui._explain(code, message))}"
        if isinstance(exc, _Hint):
            return _window_text(str(exc))
        return f"{type(exc).__name__}: {exc}"

    def _status(self, text: str, level: str = "", busy: bool = False, owner: str = "") -> None:
        """The line under the tabs. owner: the background job a busy line is about (it says the job's last word)."""
        self._said = (text, level, busy, owner)
        spin = '<span class="spin"></span>' if busy else ""
        self._set(self.status, f'<div class="st {level}">{spin}{_prose(text)}</div>' if text else "")

    def _finish(self, owner: str, text: str, level: str = "", *, tabs: Iterable[str] = (), busy: bool = False) -> None:
        """A background job's word in the status line (its last, unless busy= says it goes on): said while the line is
        still that job's (busy), or while one of its tabs is open; otherwise the line stays as it is."""
        if (self._said[2] and self._said[3] == owner) or self._tab in tabs:
            self._status(text, level, busy=busy, owner=owner if busy else "")

    def _quietly(self, widget: Any, **values: Any) -> None:
        self.quiet = True
        try:
            for name, value in values.items():
                setattr(widget, name, value)
        finally:
            self.quiet = False

    @staticmethod
    def _set(widget: Any, value: str) -> None:
        """An HTML widget's new content, sent only when it changed: sending the same HTML again would fold up every
        run and table the user opened."""
        if widget.value != value:
            widget.value = value

    def _html(self, blocks: list[Any]) -> str:
        """Report blocks as the window shows them: the commands in their sentences turned into tabs, and without the
        report CSS each one carries (the window's style holds it)."""
        return _render_html(_for_window(blocks), self.ui.max_rows).replace(_CSS, "", 1)

    def _draw(self, page: str, widget: Any, blocks: list[Any]) -> None:
        self.shown[page] = blocks
        self._set(widget, self._html(blocks))

    def _workers(self) -> ThreadPoolExecutor:
        if self._pool is None:
            self._pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="lambda-explorer")
        return self._pool

    def _later(self, key: str, work: Callable[[], Any], done: Callable[[Any], None],
               failed: Callable[[BaseException], None] | None = None, counting: str = "", owner: str = "") -> None:
        """Runs work() off the notebook's event loop, then done(result) on it, so a click returns at once and the
        window keeps answering while AWS is read. Without a running loop (a script, the tests) it runs here and now.
        A newer job of the same key makes this one's result go unused. counting: the status line to show while it runs
        and the line is still owner's, {n} standing for the count read so far."""
        self._jobs[key] = job = self._jobs.get(key, 0) + 1
        failed = failed or (lambda exc: self._status(self._error_text(exc), "warn"))

        def finish(handler: Callable[[Any], None], value: Any) -> None:
            if self._jobs.get(key) != job:
                return
            try:
                handler(value)
            except Exception as exc:  # a bug in drawing it: said in the window, like a click's
                self._status(self._error_text(exc), "warn")

        loop = _running_loop()
        if loop is None:
            try:
                result = work()
            except Exception as exc:
                finish(failed, exc)
            else:
                finish(done, result)
            return

        async def run() -> None:
            future = loop.run_in_executor(self._workers(), work)
            while counting and not future.done():
                await asyncio.wait([future], timeout=0.4)
                if self._jobs.get(key) == job and not future.done() and self._counted and self._said[3] == owner:
                    self._status(counting.replace("{n}", f"{self._counted:,}"), busy=True, owner=owner)
            try:
                result = await future
            except Exception as exc:
                finish(failed, exc)
            else:
                finish(done, result)
            finally:
                if self._tasks.get(key) is task:
                    del self._tasks[key]

        task = loop.create_task(run())
        self._tasks[key] = task

    def _empty(self, key: str, text: str) -> Any:
        """The line an empty list shows, one widget per list, kept."""
        if key not in self._empties:
            self._empties[key] = self._w.HTML(layout=self._w.Layout(width="100%"))
        self._set(self._empties[key], f'<div class="lmx-empty">{_esc(text)}</div>')
        return self._empties[key]

    def _drop(self, *keys: str) -> None:
        """Makes what's still being read for these jobs go unused when it comes back."""
        for key in keys:
            self._jobs[key] = self._jobs.get(key, 0) + 1

    # ------------------------------------------------------------------ layout

    def _build(self) -> None:
        w, layout = self._w, self._w.Layout
        style = w.HTML(_CSS + _EXPLORER_CSS, layout=layout(display="none"))
        self.backdrop = w.Button(layout=layout(display="none"))
        self.backdrop.add_class("lmx-backdrop")
        self.backdrop.on_click(self._safely(lambda _button: self.field.close()))
        self.title = w.HTML(layout=layout(flex="1 1 auto", min_width="0"))
        self.region_pick = w.Dropdown(options=[("Region: …", "")], value="", layout=layout(width="auto"))
        self.region_pick.add_class("lmx-region")
        self.region_pick.observe(self._safely(self._region_changed), names="value")
        self.refresh_button = w.Button(description="↻ Refresh", tooltip="Read everything again: the functions, and "
                                       "the picked function's tabs", layout=layout(width="auto"))
        self.refresh_button.add_class("lmx-small")
        self.refresh_button.on_click(self._safely(lambda _button: self.refresh()))
        top = w.HBox([self.title, self.region_pick, self.refresh_button], layout=layout(width="100%"))
        top.add_class("lmx-top")
        self.field = _FunctionField(self, on_pick=self._picked, on_text=self._typed)
        self.stats = w.HTML(layout=layout(flex="1 1 360px", min_width="0"))
        meta = w.HBox([self.field.box, self.stats], layout=layout(width="100%"))
        meta.add_class("lmx-meta")
        head = w.VBox([top, meta], layout=layout(width="100%"))
        head.add_class("lmx-head")

        self.tab_buttons: dict[str, Any] = {}
        for key, title, tip, _ in _EXPLORER_TABS:
            button = w.Button(description=title, tooltip=tip, layout=layout(width="auto"))
            for name in ("lmx-tab", f"lmx-i-{key}"):
                button.add_class(name)
            button.on_click(self._safely(lambda _button, key=key: self._show_tab(key)))
            self.tab_buttons[key] = button
        tabs = w.HBox(list(self.tab_buttons.values()), layout=layout(width="100%"))
        tabs.add_class("lmx-tabs")

        self.overview_view = w.HTML(layout=layout(width="100%"))
        self.settings_view = w.HTML(layout=layout(width="100%"))
        self.pages = {
            "functions": self._functions_page(),
            "overview": self._scroll_page([self.overview_view]),
            "logs": self._logs_page(),
            "errors": self._errors_page(),
            "performance": self._performance_page(),
            "code": self._code_page(),
            "settings": self._scroll_page([self.settings_view]),
        }
        height = _css_height(self.height)
        for key, page in self.pages.items():
            page.add_class("lmx-page")
            page.layout.width = "100%"
            if height:
                page.layout.height = height
            if key != self._tab:
                page.layout.display = "none"
        self.status = w.HTML(layout=layout(width="100%"))
        self.status.add_class("lmx-status")
        self.root = w.VBox([style, head, tabs, *self.pages.values(), self.status, self.backdrop],
                           layout=layout(width="100%"))
        self.root.add_class("lmx-app")
        self._show_tab(self._tab)

    def _scroll_page(self, children: list[Any], above: list[Any] | None = None) -> Any:
        """A tab's page: what stays at its top (`above`), then a box that scrolls (`children`)."""
        w = self._w
        scroller = w.VBox(children, layout=w.Layout(width="100%"))
        scroller.add_class("lmx-scroll")
        return w.VBox([*(above or []), scroller])

    def _renew(self, key: str) -> None:
        """A new scrolling box for a tab's page, which starts at the top (widgets can't be scrolled from Python)."""
        page = self.pages[key]
        old = page.children[-1]
        scroller = self._w.VBox(list(old.children), layout=self._w.Layout(width="100%"))
        scroller.add_class("lmx-scroll")
        page.children = [*page.children[:-1], scroller]

    def _make_pager(self, key: str) -> Any:
        w, layout = self._w, self._w.Layout
        text = w.HTML(layout=layout(flex="1 1 auto", min_width="0"))
        buttons = {}
        for where, glyph, tip in (("first", "«", "The first page"), ("previous", "‹", "The page before"),
                                  ("next", "›", "The next page"), ("last", "»", "The last page")):
            button = w.Button(description=glyph, tooltip=tip, layout=layout(flex="0 0 auto"))
            button.add_class("lmx-pg")
            button.on_click(self._safely(lambda _button, where=where: self._page(key, where)))
            buttons[where] = button
        self._pagers[key] = (text, buttons)
        return [text, *buttons.values()]

    def _draw_pager(self, key: str, offset: int, total: int, size: int, noun: str, whole: int | None = None) -> None:
        text, buttons = self._pagers[key]
        first, last = offset + 1, min(offset + size, total)
        shown = f"<b>{first:,}–{last:,}</b> of {total:,} {noun}" if total else f"0 {noun}"
        self._set(text, shown + (f" (of {whole:,})" if whole is not None and whole != total else ""))
        more = total > size
        for where, button in buttons.items():
            button.layout.display = "" if more else "none"
            button.disabled = (offset == 0) if where in ("first", "previous") else (last >= total)

    def _page(self, key: str, where: str) -> None:
        size = {"functions": _FUNCTION_PAGE, "runs": _RUN_PAGE, "files": _CODE_PAGE}[key]
        total = {"functions": len(self._visible), "runs": len(self._visible_runs()),
                 "files": len(self._code_files())}[key]
        offset = {"functions": self._offset, "runs": self._run_offset, "files": self._code_offset}[key]
        end = max(0, (total - 1) // size * size)
        offset = {"first": 0, "previous": max(0, offset - size), "next": min(end, offset + size), "last": end}[where]
        if key == "functions":
            self._offset = offset
            self._draw_rows(renew=True)
        elif key == "runs":
            self._run_offset = offset
            self._draw_runs(renew=True)
        else:
            self._code_offset = offset
            self._draw_code_rows(renew=True)

    def _functions_page(self) -> Any:
        w, layout = self._w, self._w.Layout
        self.find = w.Text(placeholder="Search functions: name, description, runtime, trigger…",
                           continuous_update=True, layout=layout(flex="1 1 auto", width="auto"))
        self.find.add_class("lmx-find")
        self.find.observe(self._safely(self._found_typed), names="value")
        clear = w.Button(description="✕", tooltip="Clear the search", layout=layout(flex="0 0 auto"))
        clear.add_class("lmx-x")
        clear.on_click(self._safely(lambda _button: setattr(self.find, "value", "")))
        finder = w.HBox([self.find, clear], layout=layout(width="100%", align_items="center"))
        finder.add_class("lmx-bar")
        self.chips = w.HBox(layout=layout(width="100%"))
        self.chips.add_class("lmx-chips")
        self.list_note = w.HTML(layout=layout(width="100%"))
        self.column_buttons: dict[str, Any] = {}
        for key, label, tip, width in _COLUMNS:
            button = w.Button(description=label, tooltip=tip, layout=layout(width=width or "auto"))
            for name in ("lmx-col", f"lmx-c-{key}"):
                button.add_class(name)
            button.on_click(self._safely(lambda _button, key=key: self._sort_by(key)))
            self.column_buttons[key] = button
        spacer = w.HTML(layout=layout(width="52px", flex="0 0 auto"))
        self.list_head = w.HBox([*self.column_buttons.values(), spacer], layout=layout(width="100%"))
        self.list_head.add_class("lmx-lhead")
        self.rows_box = w.VBox([self.list_head], layout=layout(width="100%", flex="1 1 auto"))
        self.rows_box.add_class("lmx-rows")
        pager = w.HBox(self._make_pager("functions"), layout=layout(width="100%"))
        pager.add_class("lmx-pager")
        page = w.VBox([finder, self.chips, self.list_note, self.rows_box, pager])
        page.add_class("lmx-listpage")
        return page

    def _logs_page(self) -> Any:
        w, layout = self._w, self._w.Layout
        self.range_pick = w.Dropdown(options=list(_RANGES), value="1h", layout=layout(width="auto"))
        self.range_pick.add_class("lmx-range")
        self.range_pick.observe(self._safely(self._range_changed), names="value")
        self.log_find = w.Text(placeholder="Find runs with: an order ID, KeyError, a request ID… (Enter searches "
                                           "CloudWatch)", continuous_update=True,
                               layout=layout(flex="1 1 auto", width="auto"))
        self.log_find.observe(self._safely(self._log_typed), names="value")
        self.log_find.on_msg(self._on_enter(self._log_search))
        clear = w.Button(description="✕", tooltip="Clear the search: every run again", layout=layout(flex="0 0 auto"))
        clear.add_class("lmx-x")
        clear.on_click(self._safely(lambda _button: setattr(self.log_find, "value", "")))
        ask = w.HBox([self.log_find, clear], layout=layout(width="auto"))
        for name in ("lmx-ask", "lmx-find"):
            ask.add_class(name)
        self.live_button = w.Button(description="Live", tooltip=f"Look for new lines every {_LIVE_SECONDS} seconds "
                                    f"and show new runs as they come (stops after {_LIVE_MINUTES} minutes)",
                                    layout=layout(width="auto"))
        self.live_button.add_class("lmx-live")
        self.live_button.on_click(self._safely(lambda _button: self._toggle_live()))
        again = w.Button(description="↻", tooltip="Read the time range again", layout=layout(width="auto"))
        again.add_class("lmx-small")
        again.on_click(self._safely(lambda _button: self._reload_logs()))
        bar = w.HBox([self.range_pick, ask, self.live_button, again], layout=layout(width="100%"))
        bar.add_class("lmx-bar")
        self.run_chips = w.HBox(layout=layout(width="100%"))
        self.run_chips.add_class("lmx-chips")
        self.run_head = w.HTML(layout=layout(width="100%"))
        self.jump_button = w.Button(layout=layout(width="auto", display="none"))
        self.jump_button.add_class("lmx-small")
        self.jump_button.on_click(self._safely(lambda _button: self._jump()))
        self.runs_box = w.VBox(layout=layout(width="100%", flex="1 1 auto"))
        self.runs_box.add_class("lmx-runs")
        self.older_button = w.Button(description="Older runs ›", tooltip="Read the lines before the oldest one read",
                                     layout=layout(width="auto", display="none"))
        self.older_button.add_class("lmx-small")
        self.older_button.on_click(self._safely(lambda _button: self._older()))
        pager = w.HBox([*self._make_pager("runs"), self.older_button], layout=layout(width="100%"))
        pager.add_class("lmx-pager")
        page = w.VBox([bar, self.run_chips, self.run_head, self.jump_button, self.runs_box, pager])
        page.add_class("lmx-logpage")
        return page

    def _range_bar(self, value: str, on_change: Callable[[dict[str, Any]], None], label: str) -> tuple[Any, Any]:
        """A time range dropdown and a ↻ button that reads the tab again, for the Errors and Performance tabs."""
        w, layout = self._w, self._w.Layout
        pick = w.Dropdown(options=[option for option in _RANGES if option[1] != "15m"], value=value,
                          layout=layout(width="auto"))
        pick.add_class("lmx-range")
        pick.observe(self._safely(on_change), names="value")
        again = w.Button(description="↻", tooltip=f"Read {label} again", layout=layout(width="auto"))
        again.add_class("lmx-small")
        bar = w.HBox([pick, again], layout=layout(width="100%"))
        bar.add_class("lmx-bar")
        return bar, (pick, again)

    def _errors_page(self) -> Any:
        w, layout = self._w, self._w.Layout
        bar, (self.errors_pick, again) = self._range_bar("24h", self._errors_range_changed, "its errors")
        again.on_click(self._safely(lambda _button: self._load_errors()))
        self.errors_view = w.HTML(layout=layout(width="100%"))
        self.error_head = w.HTML(layout=layout(width="100%"))
        self.error_rows_box = w.VBox(layout=layout(width="100%"))
        return self._scroll_page([self.errors_view, self.error_head, self.error_rows_box], above=[bar])

    def _performance_page(self) -> Any:
        w, layout = self._w, self._w.Layout
        bar, (self.perf_pick, again) = self._range_bar("24h", self._perf_range_changed, "its runs")
        again.on_click(self._safely(lambda _button: self._load_perf()))
        self.perf_view = w.HTML(layout=layout(width="100%"))
        self.slow_head = w.HTML(layout=layout(width="100%"))
        self.slow_rows_box = w.VBox(layout=layout(width="100%"))
        return self._scroll_page([self.perf_view, self.slow_head, self.slow_rows_box], above=[bar])

    def _code_page(self) -> Any:
        w, layout = self._w, self._w.Layout
        self.code_view = w.HTML(layout=layout(width="100%"))
        self.code_more = w.Button(layout=layout(width="auto", display="none"), button_style="primary")
        self.code_more.on_click(self._safely(lambda _button: self._load_code(unlimited=True)))
        self.code_find = w.Text(placeholder="Find a file", continuous_update=True,
                                layout=layout(flex="1 1 auto", width="auto"))
        self.code_find.add_class("lmx-find")
        self.code_find.observe(self._safely(self._code_typed), names="value")
        self.files_box = w.VBox(layout=layout(width="100%"))
        self.files_box.add_class("lmx-files")
        pager = w.HBox(self._make_pager("files"), layout=layout(width="100%"))
        pager.add_class("lmx-pager")
        self.code_left = w.VBox([self.code_find, self.files_box, pager])
        self.code_left.add_class("lmx-left")
        self.source_view = w.HTML(layout=layout(width="100%"))
        self.code_right = w.VBox([self.source_view])
        self.code_right.add_class("lmx-right")
        self.code_split = w.HBox([self.code_left, self.code_right], layout=layout(display="none"))
        self.code_split.add_class("lmx-split")
        head = w.VBox([self.code_view, self.code_more], layout=layout(width="100%"))
        head.add_class("lmx-codehead")
        return w.VBox([head, self.code_split])

    def _show_tab(self, key: str, *, load: bool = True) -> None:
        if key != "logs" and self._live:
            self._stop_live("Live stopped: it runs while the Logs tab is open.")
        self._tab = key
        for name, button in self.tab_buttons.items():
            _class_if(button, "lmx-on", name == key)
            _class_if(button, "lmx-dim", name in _FUNCTION_TABS and self.function is None and name != key)
            self.pages[name].layout.display = "" if name == key else "none"
        if key in _FUNCTION_TABS and self.function is None:
            self._need_function(key)
        elif load and key in _FUNCTION_TABS and key not in self._asked:
            {"logs": self._load_logs, "errors": self._load_errors, "performance": self._load_perf,
             "code": self._load_code}.get(key, lambda: None)()
        if not self._said[2] and self._said[1] != "warn":  # the line under the tabs says what this one does
            self._status(self._tab_line(key))

    def _need_function(self, key: str) -> None:
        """A function's tab before one is picked: how to pick one."""
        hint = ('<div class="lmx-hint">👆 <div><b>Pick a function first.</b> Click one in the <b>Functions</b> tab, or '
                "in the function field above (it searches as you type).</div></div>")
        widget = {"overview": self.overview_view, "settings": self.settings_view, "errors": self.errors_view,
                  "performance": self.perf_view, "code": self.code_view, "logs": self.run_head}[key]
        self._set(widget, hint)

    def _tab_line(self, key: str) -> str:
        """The status line for a tab: what's in it, or how to use it."""
        fn, ov = self.function, self.overview
        if key == "functions":
            if ov is None:
                return ""
            attention = sum(1 for f in self._facts.values() if f.warnings)
            return (f"{_plural(len(ov.functions), 'function')} in {self._where()}"
                    + (f" · {attention:,} need attention, listed first" if attention else "")
                    + " · click one to open it, or Logs on its line for its logs")
        if fn is None:
            return "Pick a function: click one in the Functions tab, or in the field above."
        if key == "logs":
            return self._logs_line()
        if key == "overview" and self.detail is not None:
            warnings = sum(level == "warn" for level, _ in function_findings(
                self.detail.function, self.detail.metrics, self.detail, prices=self.core.prices))
            return (f"{fn.name}: " + (_plural(warnings, "warning") if warnings else "no warnings")
                    + " · the Logs tab shows what it logged, run by run")
        if key == "errors" and self.error_report is not None:
            report = self.error_report
            return (f"{_plural(report.errors_found, 'error line')} in the last "
                    f"{_window((report.until - report.since).total_seconds())}, "
                    f"{_plural(len(report.groups), 'cause')} · click a failed run to read it")
        if key == "performance" and self.perf is not None:
            return (f"{_plural(len(self.perf.invocations), 'run')} in the last "
                    f"{_window((self.perf.until - self.perf.since).total_seconds())} · click a slow run to read it")
        if key == "code" and self.package is not None:
            return (f"{_plural(len(self.package.files), 'file')} in its package · click one to see its source"
                    if self.package.files else "")
        if key == "settings" and self.detail is not None:
            return "Every setting in plain English, then as Lambda returns it (folded, at the end)."
        return ""

    # ------------------------------------------------------------------ the functions

    def _begin(self) -> None:
        try:
            if self.region != "all":
                self.region = self.region or self.core.region
        except ValueError as exc:  # no region set anywhere
            self._status(str(exc), "warn")
            self._set(self.list_note, self._html([_Note(str(exc), "warn")]))
            self._draw_title()
            return
        self._fill_regions()
        self._load_list()

    def _fill_regions(self) -> None:
        here = self.core.region
        try:
            others = sorted(set(self.core.session.get_available_regions("lambda")) - {here})
        except Exception:  # an odd session: the field still offers this region and every region
            others = []
        options = [(f"Region: {here}", here), ("Region: every region", "all")]
        if self.region not in (here, "all", None) and self.region not in others:
            options.append((f"Region: {self.region}", self.region))
        options += [(f"Region: {name}", name) for name in others]
        self._quietly(self.region_pick, options=options, value=self.region or here)

    def _region_changed(self, change: dict[str, Any]) -> None:
        if self.quiet or not change.get("new"):
            return
        self.region = str(change["new"])
        self._load_list(reopen=self.function, elsewhere=False)

    def _load_list(self, reopen: Function | None = None, elsewhere: bool = True) -> None:
        """Lists the region's functions (in the background); then CloudWatch's numbers, then who may call each.
        reopen: the function to show again once they're listed; elsewhere=False closes it if the list doesn't hold it
        (another region was picked)."""
        self._drop("list", "numbers", "extras")
        self.overview, self._facts, self._numbers_pending = None, {}, False
        self._offset = 0
        self.field.problem = ""
        self.field.set_choices([])
        self._listing_started = time.monotonic()
        self._listing = True
        self._draw_title()
        self._draw_stats()
        self._draw_chips()
        self._set(self.list_note, "")
        self._refilter()
        where = self._where()
        regions = None if self.region == self.core.region else "all" if self.region == "all" else [self.region]
        self._counted = 0
        self._status(f"Listing the functions in {where}…", busy=True, owner="list")

        def progress(done: int, total: int | None = None) -> None:
            self._counted = done

        self._later("list", lambda: self.core.overview(regions=regions, metrics=False, details=False,
                                                       progress=progress),
                    lambda ov: self._listed(ov, reopen, elsewhere), self._list_failed,
                    counting="Listing the functions in every region… {n} regions read so far" if self.region == "all"
                    else "",
                    owner="list")

    def _list_failed(self, exc: BaseException) -> None:
        self._listing = False
        text = self._error_text(exc)
        self._status(text, "warn")
        self.field.problem = f"Couldn't list the functions ({text}). Type a function's name or ARN and press Enter."
        self.field.draw()
        self._set(self.list_note, self._html([_Note(f"Couldn't list the functions: {text}", "warn")]))
        self._draw_rows()
        self._open_wanted()

    def _listed(self, ov: Overview, reopen: Function | None = None, elsewhere: bool = True) -> None:
        self.overview = ov
        self._listing = False
        self._compute_facts()
        denied = sorted({code for key, code in ov.errors.items() if key.endswith(":list")})
        self.field.problem = (f"Couldn't list the functions ({', '.join(denied)}; needs lambda:ListFunctions). Type "
                              "a function's name or ARN and press Enter." if denied and not ov.functions else "")
        self.field.set_choices(ov.functions)
        self._draw_title()
        self._draw_stats()
        self._draw_chips()
        self._draw_list_note()
        self._refilter()
        if ov.functions:
            self._numbers_pending = True
            self._draw_rows()
            until = _utcnow()
            since = _midnight(until) - timedelta(days=ov.days - 1)
            functions, limits = list(ov.functions), set(ov.accounts)
            self._finish("list", f"{_plural(len(ov.functions), 'function')} in {self._where()} · reading their calls, "
                         "errors and run times from CloudWatch…", tabs=("functions",), busy=True)
            self._later("numbers", lambda: self.core._numbers(functions, since=since, until=until, limits=limits),
                        self._got_numbers, self._numbers_failed)
        elif not any(key.endswith(":list") for key in ov.errors):
            self._finish("list", f"No Lambda functions in {self._where()}"
                         + ("." if self.region == "all" else ": pick every region in the region field to look in "
                                                              "all of them."), tabs=("functions",))
        same = next((f for f in ov.functions if f.arn == reopen.arn), None) if reopen is not None else None
        if reopen is not None and (same is not None or elsewhere):
            self._open_function(same or reopen, self._tab if self._tab in _FUNCTION_TABS else None)
        elif reopen is not None:
            self._close_function()
        else:
            self._open_wanted()

    def _open_wanted(self) -> None:
        want, tab, search = self._want, self._want_tab, self._want_search
        self._want, self._want_tab, self._want_search = None, None, None
        if want:
            self._open_named(want, tab, search=search)
        elif tab and tab not in _FUNCTION_TABS:
            self._show_tab(tab)

    def _numbers_failed(self, exc: BaseException) -> None:
        self._numbers_pending = False
        self._draw_rows()
        self._status(f"Couldn't read CloudWatch's numbers: {self._error_text(exc)}", "warn")

    def _got_numbers(self, got: tuple[dict[str, FunctionMetrics], int, dict[str, float | None], dict[str, str]]
                     ) -> None:
        ov = self.overview
        if ov is None:
            return
        ov.add_numbers(*got)
        self._numbers_pending = False
        self._redraw_list()
        functions = list(ov.functions)
        self._finish("list", f"{_plural(len(functions), 'function')} in {self._where()} · reading who may call each one…",
                     tabs=("functions",), busy=True)
        self._later("extras", lambda: self.core._all_extras(functions), self._got_extras, self._extras_failed)

    def _extras_failed(self, exc: BaseException) -> None:
        self._status(f"Couldn't read the functions' resource policies: {self._error_text(exc)}", "warn")

    def _got_extras(self, found: dict[str, tuple[list[Trigger], list[ProvisionedConcurrency], dict[str, str]]]) -> None:
        ov = self.overview
        if ov is None:
            return
        ov.add_extras(found)
        self._redraw_list()
        attention = sum(1 for f in self._facts.values() if f.warnings)
        took = time.monotonic() - self._listing_started
        cost = ov.metrics_read / 1000 * self.core.prices["metric_request"]
        read = f" ({ov.metrics_read:,} CloudWatch metrics, about {human_money(cost)})" if ov.metrics_read else ""
        self._finish("list", f"{_plural(len(ov.functions), 'function')} in {self._where()}, read in {took:.1f}s{read}"
                     + (f" · {attention:,} need attention: the Functions tab lists them first" if attention
                        else " · none needs attention"), "" if attention else "ok", tabs=("functions",))

    def _redraw_list(self) -> None:
        self._compute_facts()
        self.field.draw()
        self._draw_title()
        self._draw_stats()
        self._draw_chips()
        self._draw_list_note()
        self._refilter()
        self._draw_alerts()

    def _compute_facts(self) -> None:
        ov = self.overview
        now = _utcnow()
        self._facts = {fn.arn: _function_facts(fn, ov, self.core.prices, now) for fn in ov.functions} if ov else {}

    def _draw_list_note(self) -> None:
        """Notes above the list: regions whose functions couldn't be listed, sections that couldn't be read, regions
        left out, and limits that are close to running out."""
        ov = self.overview
        if ov is None:
            return
        notes: list[Any] = []
        for key, code in sorted(ov.errors.items()):
            region, _, section = key.partition(":")
            if section == "list":
                notes.append(_Note(f"Couldn't list the functions in {region} ({_why(code, 'lambda:ListFunctions')}).",
                                   "warn"))
        unread: dict[str, dict[str, str]] = defaultdict(dict)
        for key, code in ov.errors.items():
            region, _, section = key.partition(":")
            if section != "list":
                unread[region][section] = code
        for region, errors in sorted(unread.items()):
            note = _unread(errors, f"{region}'s ")
            if note:
                notes.append(note)
        if ov.skipped and self.region == "all":
            notes.append(_Note(f"Left out {_plural(len(ov.skipped), 'region')} not turned on for this account: "
                               f"{', '.join(sorted(ov.skipped))}."))
        for region, limits in sorted(ov.accounts.items()):
            notes += [_Note(f"{message}", "warn") for level, message in account_findings(limits, ov.days)
                      if level == "warn"]
        self._set(self.list_note, self._html(notes) if notes else "")

    def _found_typed(self, change: dict[str, Any]) -> None:
        if self.quiet:
            return
        self._query = str(self.find.value or "").strip()
        self._offset = 0
        self._refilter()

    def _pick_chip(self, key: str) -> None:
        self._chip = "" if key == self._chip else key
        self._offset = 0
        self._draw_chips()
        self._refilter(renew=True)

    def _sort_by(self, key: str) -> None:
        if key == self._sort:
            self._descending = not self._descending
        else:
            self._sort, self._descending = key, key != "name"
        self._offset = 0
        self._refilter(renew=True)

    def _in_chip(self, f: _Facts, key: str) -> bool:
        m = f.metrics
        return {"": True, "attention": bool(f.warnings), "errors": bool(m and m.errors >= 1),
                "runtime": f.status.state in ("deprecated", "blocked", "ending"),
                "public": any(t.public for t in f.triggers), "idle": bool(m is not None and not m.invocations)}[key]

    def _chip_button(self, store: dict[str, Any], key: str, tone: str, on_click: Callable[[str], None]) -> Any:
        """The chip button for `key`, made once and kept: redrawing changes its label, not the widget (Live redraws
        the run chips every few seconds, and a widget is never freed unless it's closed)."""
        if key not in store:
            chip = self._w.Button(layout=self._w.Layout(width="auto"))
            for name in ("lmx-chip", f"lmx-t-{tone}"):
                chip.add_class(name)
            chip.on_click(self._safely(lambda _button: on_click(key)))
            store[key] = chip
        return store[key]

    def _show_chips(self, box: Any, chips: list[tuple[Any, str, str, bool]]) -> None:
        """Puts chips (button, label, tooltip, picked) in their row, changing only what changed."""
        for chip, label, tip, picked in chips:
            if chip.description != label:
                chip.description = label
            if chip.tooltip != tip:
                chip.tooltip = tip
            _class_if(chip, "lmx-on", picked)
        shown = tuple(chip for chip, _, _, _ in chips)
        if tuple(box.children) != shown:
            box.children = shown

    def _draw_chips(self) -> None:
        """A chip per kind of function there is, with how many: a click shows only those (again shows all)."""
        facts = list(self._facts.values())
        chips = []
        for key, label, tone in _FUNCTION_CHIPS:
            n = sum(1 for f in facts if self._in_chip(f, key))
            if key and not n:
                continue
            tip = ("Show every function" if not key
                   else f"Show only the functions that are {label.lower()} (click again for all)")
            chips.append((self._chip_button(self._function_chips, key, tone, self._pick_chip), f"{label} {n:,}", tip,
                          key == self._chip))
        self._show_chips(self.chips, chips if facts else [])

    def _sort_key(self, f: _Facts) -> Any:
        m = f.metrics
        calls = m.invocations if m is not None else -1.0
        if self._sort == "name":
            return (f.fn.name.lower(), f.fn.region)
        if self._sort == "calls":
            return (calls,)
        if self._sort == "errors":
            return ((m.error_rate or 0.0) if m is not None else -1.0, m.errors if m is not None else -1.0)
        if self._sort == "duration":
            return ((m.avg_duration or 0.0) if m is not None else -1.0,)
        if self._sort == "cost":
            return (f.cost if f.cost is not None else -1.0,)
        if self._sort == "called":
            return ((m.last_invoked.timestamp() if m is not None and m.last_invoked else 0.0),)
        return ({"bad": 3, "warn": 2}.get(f.tone, 0), len(f.warnings), m.errors if m is not None else 0.0, calls)

    def _refilter(self, renew: bool = False) -> None:
        facts = [f for f in self._facts.values() if self._in_chip(f, self._chip)] if self._chip else list(
            self._facts.values())
        if self._query:
            ranked = []
            for i, f in enumerate(facts):
                fn = f.fn
                rank = search_rank(self._query, (fn.name, fn.description, fn.runtime, fn.region, fn.handler,
                                                 _triggers_text(f.triggers), " ".join(t.source for t in f.triggers),
                                                 (fn.role or "").split("/")[-1]))
                if rank is not None:
                    ranked.append((rank, i, f))
            facts = [f for _, _, f in sorted(ranked, key=lambda r: r[:2])]
        if not self._query or self._sort != "problems":  # a search ranks its best match first, unless a column sorts
            facts = sorted(facts, key=lambda f: (f.fn.name.lower(), f.fn.region))
            facts = sorted(facts, key=self._sort_key, reverse=self._descending)
        self._visible = facts
        self._offset = min(self._offset, max(0, (len(facts) - 1) // _FUNCTION_PAGE * _FUNCTION_PAGE))
        for key, button in self.column_buttons.items():
            _class_if(button, "lmx-on", key == self._sort)
            label = next(label for k, label, _, _ in _COLUMNS if k == key)
            arrow = (" ▾" if self._descending else " ▴") if key == self._sort and key != "problems" else ""
            if button.description != label + arrow:
                button.description = label + arrow
        self._draw_rows(renew=renew)

    def _draw_rows(self, renew: bool = False) -> None:
        page = self._visible[self._offset:self._offset + _FUNCTION_PAGE]
        while len(self._rows) < len(page):
            self._rows.append(_Row(self, self._clicked_function,
                                   action=("Logs", "Open its logs", self._clicked_logs)))
        words = self._query.split()
        now = _utcnow()
        for row, f in zip(self._rows, page):
            row.item = f.fn
            self._set(row.face, _function_face(f, words, self.multi, self._numbers_pending, now))
            warns = f.warnings
            tip = (f"{f.fn.name}" + (f": {f.fn.description}" if f.fn.description else "")
                   + (f" · {_plural(len(warns), 'warning')}" if warns else "") + " · click to open it")
            if row.button.tooltip != tip:
                row.button.tooltip = tip
            _class_if(row.box, "lmx-on", self.function is not None and f.fn.arn == self.function.arn)
        children: list[Any] = [self.list_head, *(row.box for row in self._rows[:len(page)])]
        if not page:
            if self.overview is None:
                text = "Listing the functions…" if self._listing else "No functions listed."
            elif not self.overview.functions:
                text = (f"No Lambda functions in {self._where()}."
                        + ("" if self.region == "all" else " Functions are regional: pick every region in the region "
                                                           "field above to look in all of them."))
            else:
                text = "No function matches." + (" Clear the search, or pick All above." if self._query or self._chip
                                                 else "")
            children = [self.list_head, self._empty("functions", text)]
        if renew:
            box = self._w.VBox(children, layout=self._w.Layout(width="100%", flex="1 1 auto"))
            box.add_class("lmx-rows")
            page_box = self.pages["functions"] if hasattr(self, "pages") else None
            if page_box is not None:
                page_box.children = [box if child is self.rows_box else child for child in page_box.children]
            self.rows_box = box
        else:
            self.rows_box.children = children
        whole = len(self._facts) if self._facts else None
        self._draw_pager("functions", self._offset, len(self._visible), _FUNCTION_PAGE, "functions", whole)

    def _mark_rows(self) -> None:
        for row in self._rows:
            _class_if(row.box, "lmx-on", self.function is not None and row.item is not None
                      and row.item.arn == self.function.arn)

    def _clicked_function(self, fn: Function) -> None:
        self._open_function(fn, "overview")

    def _clicked_logs(self, fn: Function) -> None:
        self._open_function(fn, "logs")

    # ------------------------------------------------------------------ header

    def _draw_title(self) -> None:
        fn, ov = self.function, self.overview
        if fn is None:
            sub = [_plural(len(ov.functions), "function") if ov is not None else "",
                   "every function, its logs run by run, its errors and code", "read-only"]
        else:
            sub = [fn.name, fn.runtime or "container image", fn.region if self.multi else "",
                   _clip(fn.description, 90) if fn.description else ""]
        where = "every region" if self.region == "all" else (self.region or "")
        self._set(self.title, f'<div class="lmx-brand"><span class="lmx-logo">{_LOGO}</span><div style="min-width:0">'
                              f'<div class="lmx-name">Lambda explorer<span>{_esc(where)}</span></div>'
                              f'<div class="lmx-sub">{_esc(" · ".join(p for p in sub if p))}</div></div></div>')

    def _draw_stats(self) -> None:
        """The cards beside the function field: the region at a glance, or the picked function."""
        now = _utcnow()
        cards: list[tuple[str, str, str]] = []
        loading = self._numbers_pending or (self.overview is None and self._listing)
        if self.function is None:
            facts = list(self._facts.values())
            if self.overview is None:
                cards = [("Functions", "…", "sk-on"), ("Calls · 30d", "…", "sk-on"), ("Error rate", "…", "sk-on")]
            else:
                read = [f.metrics for f in facts if f.metrics is not None]
                calls = sum(m.invocations for m in read)
                failed = sum(m.errors for m in read)
                costs = [f.cost for f in facts if f.cost is not None]
                warn = any(_errors_level(f.metrics, now) == "warn" for f in facts)
                attention = sum(1 for f in facts if f.warnings)
                cards.append(("Functions", f"{len(facts):,}", ""))
                cards.append(("Calls · 30d", "…" if loading else _count(round(calls)), "sk-on" if loading else ""))
                cards.append(("Error rate", "…" if loading else (_pct(failed / calls) if calls else "-"),
                              "sk-on" if loading else "warn" if warn else ""))
                cards.append(("Est. $ / month", "…" if loading else (human_money(sum(costs)) if costs else "-"),
                              "sk-on" if loading else ""))
                cards.append(("Need attention", f"{attention:,}", "warn" if attention else "ok" if facts else ""))
        else:
            fn = self._full()
            f = self._facts.get(self.function.arn)
            m = self.detail.metrics if self.detail is not None and self.detail.metrics is not None else (
                f.metrics if f is not None else None)
            if m is None:
                waiting = loading or self.detail is None
                cards += [(label, "…" if waiting else "-", "sk-on" if waiting else "")
                          for label in ("Calls · 30d", "Error rate", "Avg / longest run")]
            else:
                level = _errors_level(m, now)
                near = bool(m.duration_max and fn.timeout and m.duration_max >= 0.9 * fn.timeout * 1000)
                cards.append(("Calls · 30d", _count(round(m.invocations)), ""))
                cards.append(("Error rate", _pct(m.error_rate) if m.invocations else "-",
                              "bad" if level == "warn" else "warn" if level else ""))
                cards.append(("Avg / longest run", f"{human_ms(m.avg_duration)} / {human_ms(m.duration_max)}"
                              if m.invocations else "not called in 30 days", "warn" if near else ""))
            if self.detail is not None:
                cost = _total(function_monthly_cost(fn, self.detail.metrics, self.detail.provisioned,
                                                    self.detail.log_group, prices=self.core.prices))
                warnings = sum(level == "warn" for level, _ in function_findings(
                    fn, self.detail.metrics, self.detail, prices=self.core.prices, now=now))
            else:
                cost = f.cost if f is not None else None
                warnings = len(f.warnings) if f is not None else 0
            cards.append(("Est. $ / month", human_money(cost) if cost is not None else "-", ""))
            cards.append(("Warnings", f"{warnings:,}", "warn" if warnings else "ok"))
        html_cards = "".join(f'<div class="lmx-stat {tone}"><span class="l">{_esc(label)}</span><b>{_esc(value)}</b>'
                             "</div>" for label, value, tone in cards)
        self._set(self.stats, f'<div class="lmx-stats">{html_cards}</div>')

    def _draw_alerts(self) -> None:
        """A dot on a tab that holds something to look at: amber on Overview for warnings, red on Errors when calls
        fail often enough to act on."""
        fn = self.function
        f = self._facts.get(fn.arn) if fn is not None else None
        m = self.detail.metrics if self.detail is not None and self.detail.metrics is not None else (
            f.metrics if f is not None else None)
        warned = bool(f.warnings) if f is not None else False
        if self.detail is not None:
            warned = any(level == "warn" for level, _ in function_findings(
                self.detail.function, self.detail.metrics, self.detail, prices=self.core.prices))
        _class_if(self.tab_buttons["overview"], "lmx-alert", fn is not None and warned)
        _class_if(self.tab_buttons["errors"], "lmx-alarm", fn is not None and _errors_level(m, _utcnow()) == "warn")

    # ------------------------------------------------------------------ one function

    def _close_function(self) -> None:
        """Back to no function picked: the Functions tab, the header showing the region."""
        self._drop("describe", "logs", "search", "older", "errors", "perf", "code", "lookup")
        self._stop_live()
        self.function = self.detail = None
        self.logs_page = self.found = self.error_report = self.perf = self.package = None
        self._asked = set()
        self.field.value = ""
        self.field.draw()
        self._draw_title()
        self._draw_stats()
        self._draw_alerts()
        self._mark_rows()
        self._show_tab("functions")

    def _picked(self, fn: Function) -> None:
        self._open_function(fn)

    def _typed(self, text: str) -> None:
        self._open_named(text, None)

    def _open_named(self, name: str, tab: str | None, *, search: str | None = None) -> None:
        """Opens a function named the way you'd paste it: from the list when it's there, else read with GetFunction
        (an ARN in another region, a function the list doesn't hold)."""
        wanted, _, region = parse_function_ref(name)
        listed = self.overview.functions if self.overview is not None else []
        fn = next((f for f in listed if f.name == wanted and (region is None or f.region == region)), None)
        if fn is not None:
            self._open_function(fn, tab, search=search)
            return
        where = region or (self.region if self.region not in (None, "all") else None)
        self._status(f"Looking up {wanted}…", busy=True, owner="lookup")

        def failed(exc: BaseException) -> None:
            if isinstance(exc, ClientError) and _error_code(exc) == "ResourceNotFoundException":
                close = difflib.get_close_matches(wanted, [f.name for f in listed], n=3, cutoff=0.6)
                hint = f" Did you mean {' or '.join(map(repr, close))}?" if close else ""
                if self.region == "all" and not region:
                    self._status(f"No function {wanted!r} in any region listed.{hint} Names are case-sensitive.",
                                 "warn")
                else:
                    self._status(f"No function {wanted!r} in {where or self.core.region}.{hint} Names are "
                                 "case-sensitive; pick every region in the region field to look in all of them.",
                                 "warn")
            else:
                self._status(self._error_text(exc), "warn")

        self._later("lookup", lambda: self.core.function(name, region=where),
                    lambda got: self._open_function(got, tab, search=search), failed)

    def _open_function(self, fn: Function, tab: str | None = None, *, search: str | None = None) -> None:
        """Shows a function: its Overview (or tab), read in the background; the other tabs read when they're opened."""
        self._drop("describe", "logs", "search", "older", "errors", "perf", "code", "lookup")
        self._stop_live()
        self.function, self.detail = fn, None
        self.logs_page = self.found = self.error_report = self.perf = self.package = None
        self._runs, self._found_runs, self._searched, self._log_query = [], [], "", ""
        self._open_runs, self._run_offset, self._run_chip = set(), 0, ""
        self._code_fn = self._package_data = self._code_file = None
        self._code_query, self._code_offset = "", 0
        self._asked = set()
        self._quietly(self.log_find, value="")
        self._quietly(self.code_find, value="")
        self.field.value = fn.arn
        self.field.draw()
        self._draw_title()
        self._draw_stats()
        self._draw_alerts()
        self._mark_rows()
        name = fn.name
        self._set(self.overview_view, _skeleton(f"Reading {name}: its settings, what calls it, its last 30 days…"))
        self._set(self.settings_view, _skeleton(f"Reading {name}'s settings…"))
        for widget in (self.errors_view, self.perf_view, self.code_view, self.source_view, self.error_head,
                       self.slow_head, self.run_head):
            self._set(widget, "")
        self.error_rows_box.children = self.slow_rows_box.children = self.files_box.children = []
        self.runs_box.children = []
        self.run_chips.children = []
        self.code_split.layout.display = "none"
        self.code_more.layout.display = self.jump_button.layout.display = self.older_button.layout.display = "none"
        for key in ("overview", "settings", "errors", "performance"):
            self._renew(key)
        target = tab or (self._tab if self._tab in _FUNCTION_TABS else "overview")
        self._show_tab(target, load=not search)
        if search:
            self._quietly(self.log_find, value=search)
            self._log_query = search
            self._asked.add("logs")
            self._log_search()
        if target in ("overview", "settings"):
            self._status(f"Reading {name}: its settings, what calls it, its last 30 days…", busy=True,
                         owner="describe")
        self._later("describe", lambda: self.core.describe(name, region=fn.region or None, days=30), self._described,
                    self._describe_failed)

    def _describe_failed(self, exc: BaseException) -> None:
        text = self._error_text(exc)
        self._status(text, "warn")
        for widget in (self.overview_view, self.settings_view):
            self._set(widget, self._html([_Note(text, "warn")]))

    def _described(self, detail: FunctionDetail) -> None:
        self.detail = detail
        self._draw_stats()
        self._draw_alerts()
        self._draw_overview()
        self._draw_settings()
        self._finish("describe", self._tab_line(self._tab), tabs=("overview", "settings"))

    def _draw_overview(self) -> None:
        detail = self.detail
        if detail is None:
            return
        sections = self.ui._function_sections(detail, days=30)
        head = [dataclasses.replace(block, items=[item for item in block.items if item[0] in _SETUP_CARDS])
                if isinstance(block, _Cards) else block for block in sections["head"]]  # the header has the numbers
        blocks = [*head, self._wiring(detail), *self._charts(detail), *sections["cost"]]
        self._draw("overview", self.overview_view, blocks)

    def _wiring(self, detail: FunctionDetail) -> _Wiring:
        """What calls the function, and where its results, failed events and logs go."""
        fn = detail.function
        inputs = []
        for t in detail.triggers:
            disabled = (t.state or "").lower() == "disabled"
            problem = bool(t.last_result and t.last_result.upper().startswith("PROBLEM"))
            detail_text = "; ".join(filter(None, ["anyone on the internet can call it" if t.public else "",
                                                  "disabled" if disabled else "", t.detail, t.last_result]))
            inputs.append((t.kind, t.source or ("anyone" if t.public else "-"), detail_text,
                           "warn" if t.public or disabled or problem else ""))
        outputs = []
        config = detail.async_config
        if config is not None and config.on_success:
            kind, name = _destination(config.on_success)
            outputs.append(("On success", name, f"{kind}: each asynchronous call's result", ""))
        failure = (config.on_failure if config is not None else None) or fn.dead_letter
        asynchronous = list(dict.fromkeys(t.kind for t in detail.triggers if t.asynchronous))
        if failure:
            kind, name = _destination(failure)
            retries = f"after {_retries(config.retries)}" if config is not None else ""
            outputs.append(("On failure", name, f"{kind}, {retries}".rstrip(", "), ""))
        elif asynchronous and config is not None:
            outputs.append(("On failure", "nowhere: dropped",
                            f"events from {', '.join(asynchronous)} that still fail after {_retries(config.retries)} "
                            "are lost", "warn"))
        group = detail.log_group
        kept = ("kept forever" if group is not None and group.retention_days is None
                else f"kept {_plural(group.retention_days, 'day')}" if group is not None and group.retention_days
                else "no log group yet" if group is None and "log_group" not in detail.errors else "")
        stored = f", {human_size(group.stored_bytes)} stored" if group is not None and group.stored_bytes else ""
        forever_and_big = group is not None and group.retention_days is None and (group.stored_bytes or 0) >= 100 * MB
        outputs.append(("Logs", fn.log_group, (kept + stored).strip(", "), "warn" if forever_and_big else ""))
        if fn.role:
            outputs.append(("Allowed to use", fn.role.split("/")[-1],
                            f"its execution role{' · in VPC ' + fn.vpc_id if fn.vpc_id else ''}", ""))
        center = (fn.name, " · ".join(filter(None, [fn.runtime or "container image", f"{fn.memory:,} MB",
                                                    f"{fn.timeout} s timeout", fn.architecture])))
        return _Wiring(inputs, center, outputs, title="How it's wired: what calls it, and where its results and logs go",
                       empty="Nothing calls it on its own: it's called directly (an SDK, Step Functions...)")

    def _charts(self, detail: FunctionDetail) -> list[Any]:
        """Calls a day (the failed part in red) and run time a day (the average under the longest), for 30 days."""
        m, fn = detail.metrics, detail.function
        if m is None or not m.invocations or m.period < 86400:
            return []
        today = _midnight(_utcnow())
        by_day = {d.start.astimezone(timezone.utc).date(): d for d in m.daily}
        days = [today - timedelta(days=30 - 1 - i) for i in range(30)]
        calls, times = [], []
        for day in days:
            d = by_day.get(day.date())
            label = _short_day(day)
            if d is None:
                calls.append((label, 0.0, 0.0, f"{day:%a} {label}: no calls"))
                times.append((label, 0.0, 0.0, f"{day:%a} {label}: no calls"))
                continue
            failed = f", {_count(round(d.errors))} failed ({_pct(d.errors / d.invocations)})" if d.errors and (
                d.invocations) else ""
            calls.append((label, d.invocations, d.errors, f"{day:%a} {label}: {_count(round(d.invocations))} calls"
                          + failed + (f", {_count(round(d.throttles))} throttled" if d.throttles else "")))
            times.append((label, d.duration_max or d.avg_duration or 0.0, d.avg_duration or 0.0,
                          f"{day:%a} {label}: average {human_ms(d.avg_duration)}, longest {human_ms(d.duration_max)}"))
        return [
            _Columns(calls, title="Calls a day, the failed part in red (CloudWatch, the last 30 days, UTC days)"),
            _Columns(times, title=f"Run time a day: the average (dark) under the longest (light), against its "
                                  f"{fn.timeout} s timeout", part="dim", limit=fn.timeout * 1000,
                     limit_label=f"timeout {fn.timeout} s", unit="ms"),
        ]

    def _draw_settings(self) -> None:
        detail = self.detail
        if detail is None:
            return
        fn = detail.function
        sections = self.ui._function_sections(detail, days=30)
        blocks: list[Any] = [_Title(f"Settings of {fn.name}", "in plain English, then as Lambda returns them")]
        for part in ("runs", "triggers", "async", "access", "versions", "tags", "raw"):
            blocks += sections[part]
        flag = f" --region {fn.region}" if fn.region else ""
        commands = [f"aws lambda get-function-configuration --function-name {fn.name}{flag}",
                    f"aws lambda get-policy --function-name {fn.name}{flag}",
                    f"aws lambda list-event-source-mappings --function-name {fn.name}{flag}",
                    f"aws lambda get-function-event-invoke-config --function-name {fn.name}{flag}"]
        blocks.append(_Text("\n".join(commands), title="The same from a terminal (read-only)", code=True))
        self._draw("settings", self.settings_view, blocks)

    # ------------------------------------------------------------------ logs

    def _set_range(self, since: str) -> None:
        """Picks a time range in the Logs tab's field, adding it to the list when it isn't one of the presets."""
        options = list(_RANGES)
        if since not in {value for _, value in options}:
            options.append((f"Since {since}", since))
        self._range = since
        self._quietly(self.range_pick, options=options, value=since)

    def _range_text(self, value: str | None = None) -> str:
        """'the last hour', 'the last 3 days', 'since 2026-10-01'."""
        value = value or self._range
        label = next((label for label, v in _RANGES if v == value), None)
        return f"the {label.lower()}" if label else f"since {value}"

    def _range_changed(self, change: dict[str, Any]) -> None:
        if self.quiet or not change.get("new"):
            return
        self._range = str(change["new"])
        if self.found is not None and self._searched and self._searched == self._log_query:
            self._log_search()
        else:
            self._load_logs()

    def _reload_logs(self) -> None:
        if self.found is not None and self._searched and self._searched == self._log_query:
            self._log_search()
        else:
            self._load_logs()

    def _load_logs(self) -> None:
        """Reads the time range's newest lines, as runs (in the background)."""
        fn = self.function
        if fn is None:
            return
        self._asked.add("logs")
        self._stop_live()
        start, end = LambdaAnalyzer._window(self._range)
        self.logs_page, self._runs, self._run_offset = None, [], 0
        self.jump_button.layout.display = self.older_button.layout.display = "none"
        self._set(self.run_head, _skeleton(f"Reading what {fn.name} logged in {self._range_text()}…"))
        self.runs_box.children = []
        self._counted = 0
        self._status(f"Reading {fn.name}'s logs from {self._range_text()}…", busy=True, owner="logs")
        self._later("logs", lambda: self.core._log_runs(fn, start, end, limit=_RUN_LINES,
                                                        progress=lambda n: setattr(self, "_counted", n)),
                    self._got_logs, self._logs_failed, counting=f"Reading {fn.name}'s logs… {{n}} lines read so far",
                    owner="logs")

    def _logs_failed(self, exc: BaseException) -> None:
        text = self._error_text(exc)
        self._set(self.run_head, self._html([_Note(f"Couldn't read the logs: {text}", "warn")]))
        if self._tab == "logs":
            self._status(text, "warn")

    def _got_logs(self, page: LogPage) -> None:
        self.logs_page = page
        self._runs = page.runs
        self._run_offset = 0
        self._draw_run_chips()
        self._draw_runs(renew=True)
        self._finish("logs", self._logs_line(), "warn" if page.errors else "", tabs=("logs",))

    def _logs_line(self) -> str:
        """The status line for the Logs tab."""
        fn, page = self.function, self.found if self.found is not None else self.logs_page
        if fn is None or page is None:
            return "Reading the logs…" if fn is not None else ""
        if page.errors:
            code = page.errors.get("logs", "")
            return (f"{fn.name} has no log group yet: it hasn't logged anything" if code == "ResourceNotFoundException"
                    else f"Couldn't read its logs ({_why(code, 'logs:FilterLogEvents')})")
        runs = self._found_runs if self.found is not None else self._runs
        failed = sum(1 for r in runs if r.status in ("failed", "timeout"))
        if self.found is not None:
            return (f"{_plural(len(runs), 'run')} with {self._searched!r} in {self._range_text()}"
                    + (f", {failed:,} failed" if failed else "") + " · ✕ shows every run again")
        return (f"{_plural(len(runs), 'run')} in {self._range_text()}" + (f", {failed:,} failed" if failed else "")
                + " · click a run to see its lines" + (" · Older runs reads further back" if page.truncated else ""))

    def _words(self) -> list[str]:
        phrase = _phrase(self._log_query) if self._log_query else None
        return [phrase] if phrase else []

    def _log_typed(self, change: dict[str, Any]) -> None:
        if self.quiet:
            return
        self._log_query = str(self.log_find.value or "").strip()
        if self.found is not None and self._log_query != self._searched:
            self.found, self._found_runs = None, []
            self._draw_run_chips()
            if self.logs_page is None and "logs" not in self._tasks:  # it opened on a search: read the range now
                self._load_logs()
                return
        self._run_offset = 0
        self._draw_runs()
        if self._log_query and _phrase(self._log_query) is None:
            self._status("That's a CloudWatch Logs filter pattern: press Enter to search the time range with it.")
        elif self._log_query:
            shown = len(self._visible_runs())
            truncated = self.logs_page is not None and self.logs_page.truncated
            self._status(f"{shown:,} of {len(self._runs):,} runs read have {self._log_query!r}"
                         + (" · press Enter to search the whole time range in CloudWatch" if truncated
                            else " · Enter searches CloudWatch too"))
        else:
            self._status(self._logs_line())

    def _log_search(self) -> None:
        """Enter in the search box: CloudWatch searches the whole time range, and the runs with a matching line come
        back whole (in the background)."""
        fn = self.function
        text = str(self.log_find.value or "").strip()
        if fn is None:
            return
        if not text:
            self.found, self._found_runs, self._searched = None, [], ""
            self._draw_run_chips()
            if self.logs_page is None and "logs" not in self._tasks:
                self._load_logs()
            else:
                self._draw_runs(renew=True)
            return
        start, end = LambdaAnalyzer._window(self._range)
        self._searched = self._log_query = text
        self._stop_live()
        self._status(f"Searching {fn.name}'s logs from {self._range_text()} for {text!r}, then reading each run it's "
                     "in…", busy=True, owner="logs")
        self._set(self.run_head, _skeleton(f"Searching {self._range_text()} for {text!r}…"))
        self._later("search", lambda: self.core._log_runs(fn, start, end, search=text, limit=_RUN_LINES),
                    self._got_search, self._search_failed)

    def _search_failed(self, exc: BaseException) -> None:
        text = self._error_text(exc)
        self._set(self.run_head, self._html([_Note(f"The search failed: {text}", "warn")]))
        self._status(text, "warn")

    def _got_search(self, page: LogPage) -> None:
        self.found = page
        self._found_runs = page.runs
        self._run_offset = 0
        if len(self._found_runs) <= 3:
            self._open_runs |= {run.key for run in self._found_runs}
        self._draw_run_chips()
        self._draw_runs(renew=True)
        self._finish("logs", self._logs_line(), "warn" if page.errors else "", tabs=("logs",))

    def _show_run(self, moment: datetime, stream: str, request_id: str | None) -> None:
        """Opens one run in the Logs tab, every line of it: a failed run from the Errors tab, a slow one from the
        Performance tab."""
        fn = self._full()
        self._show_tab("logs")
        if request_id:
            self._quietly(self.log_find, value=request_id)
            self._searched = self._log_query = request_id
        label = request_id[:8] if request_id else f"from {_stamp(moment)}"
        self._status(f"Reading run {label}…", busy=True, owner="logs")

        def done(run: LogRun | None) -> None:
            if run is None:
                self._status(f"Couldn't find run {label}'s lines around {_stamp(moment)} UTC: its log stream may have "
                             "been deleted, or the logs aren't kept that long.", "warn")
                return
            self.found = LogPage(fn, fn.log_group, run.start, run.end, events=run.events,
                                 pattern=f'"{run.request_id}"' if run.request_id else None)
            self._searched = self._log_query = run.request_id or self._log_query
            self._quietly(self.log_find, value=self._searched)
            self._found_runs = [run]
            self._open_runs.add(run.key)
            self._run_offset = 0
            self._draw_run_chips()
            self._draw_runs(renew=True)
            self._finish("logs", f"Run {run.request_id or label}: {_RUN_STATES.get(run.status, ('', '', run.status))[2]}"
                         + (f" in {human_ms(run.duration)}" if run.duration is not None else "")
                         + " · ✕ shows every run again", tabs=("logs",))

        self._later("search", lambda: self.core._run_around(fn, moment, stream=stream, request_id=request_id), done)

    def _source_runs(self) -> list[LogRun]:
        return self._found_runs if self.found is not None else self._runs

    def _in_run_chip(self, run: LogRun, key: str) -> bool:
        return {"": True, "failed": run.status in ("failed", "timeout"), "timeout": run.status == "timeout",
                "logged": run.status == "logged", "cold": run.cold}[key]

    def _visible_runs(self) -> list[LogRun]:
        runs = self._source_runs()
        if self._run_chip:
            runs = [r for r in runs if self._in_run_chip(r, self._run_chip)]
        if self._log_query and self.found is None:
            phrase = _phrase(self._log_query)
            if phrase:
                needle = phrase.lower()
                runs = [r for r in runs if needle in (r.request_id or "").lower()
                        or any(needle in e.message.lower() for e in r.events)]
        return runs

    def _pick_run_chip(self, key: str) -> None:
        self._run_chip = "" if key == self._run_chip else key
        self._run_offset = 0
        self._draw_run_chips()
        self._draw_runs(renew=True)

    def _draw_run_chips(self) -> None:
        runs = self._source_runs()
        chips = []
        for key, label, tone in _RUN_CHIPS:
            n = sum(1 for r in runs if self._in_run_chip(r, key))
            if key and not n:
                continue
            tip = "Show every run" if not key else f"Show only the runs that {label.lower()} (click again for every run)"
            chips.append((self._chip_button(self._run_chip_buttons, key, tone, self._pick_run_chip), f"{label} {n:,}", tip,
                          key == self._run_chip))
        self._show_chips(self.run_chips, chips if runs else [])

    def _draw_runs(self, renew: bool = False) -> None:
        visible = self._visible_runs()
        self._run_offset = min(self._run_offset, max(0, (len(visible) - 1) // _RUN_PAGE * _RUN_PAGE))
        page = visible[self._run_offset:self._run_offset + _RUN_PAGE]
        while len(self._run_rows) < len(page):
            self._run_rows.append(_RunRow(self))
        words = self._words()
        today = _utcnow().date()
        timeout = self._full().timeout if self.function is not None else 0
        for row, run in zip(self._run_rows, page):
            row.run = run
            self._set(row.face, _run_face(run, timeout, words, today))
            opened = run.key in self._open_runs
            _class_if(row.box, "lmx-open", opened)
            tip = _run_tip(run, opened)
            if row.button.tooltip != tip:
                row.button.tooltip = tip
            if opened:
                self._set(row.body, _run_body(run, words))
            row.body.layout.display = "" if opened else "none"
        children: list[Any] = [row.box for row in self._run_rows[:len(page)]]
        if not page:
            children = [self._empty("runs", self._no_runs_text())]
        if renew:
            box = self._w.VBox(children, layout=self._w.Layout(width="100%", flex="1 1 auto"))
            box.add_class("lmx-runs")
            logs = self.pages["logs"]
            logs.children = [box if child is self.runs_box else child for child in logs.children]
            self.runs_box = box
        else:
            self.runs_box.children = children
        self._draw_pager("runs", self._run_offset, len(visible), _RUN_PAGE, "runs",
                         len(self._source_runs()) if (self._run_chip or self._log_query) else None)
        page_read = self.logs_page
        self.older_button.layout.display = "" if (self.found is None and page_read is not None
                                                  and page_read.truncated) else "none"
        self._draw_run_head(visible)

    def _no_runs_text(self) -> str:
        page = self.found if self.found is not None else self.logs_page
        if self.function is None:
            return "Pick a function first."
        if page is None:
            return "Reading the logs…" if "logs" in self._asked or self.found is not None else "No logs read yet."
        if page.errors:
            return "No runs to show."
        if self.found is not None:
            return (f"No run in {self._range_text()} has a line with {self._searched!r}. Pick a longer time range, or "
                    "check the spelling: CloudWatch's search is case-sensitive.")
        if self._source_runs():
            return "No run matches. Clear the search, or pick All runs above."
        if page.latest is not None:
            return f"Nothing logged in {self._range_text()}. Its newest line is from {human_age(page.latest)}."
        return (f"Nothing logged in {self._range_text()}, and nothing at all in its log group: it hasn't run, or it "
                "logs somewhere else.")

    def _draw_run_head(self, visible: list[LogRun]) -> None:
        """The line over the runs: how many, how many failed, how long they took, the memory they used, the cold
        starts; and what couldn't be read, or where reading stopped."""
        fn = self.function
        page = self.found if self.found is not None else self.logs_page
        if fn is None or page is None:
            return
        notes: list[Any] = list(self.ui._log_notes(page.errors, fn, page.log_group)) if page.errors else []
        if self.found is None and page.truncated:
            notes.append(_Note(f"These are the newest {_count(_RUN_LINES)} lines, back to "
                               f"{_stamp(page.covered_from)} UTC: Older runs (under the list) reads further back."))
        elif self.found is not None and page.truncated:
            notes.append(_Note(f"CloudWatch stopped at the newest {_count(_SEARCH_HITS)} matching lines, back to "
                               f"{_stamp(page.covered_from)} UTC: pick a shorter time range to see older ones."))
        bits = []
        if visible:
            runs = [r for r in visible if r.request_id]
            failed = sum(1 for r in runs if r.status in ("failed", "timeout"))
            durations = [r.duration for r in runs if r.duration is not None]
            used = [(r.report.max_memory, r.report.memory) for r in runs if r.report is not None and r.report.memory]
            cold = sum(1 for r in runs if r.cold)
            bits.append(f"<span><b>{len(runs):,}</b> {'run' if len(runs) == 1 else 'runs'}</span>")
            if failed:
                bits.append(f'<span class="bad"><b>{failed:,}</b> failed ({_pct(failed / len(runs))})</span>')
            if durations:
                fastest = percentile(durations, 50)
                bits.append(f"<span>median <b>{_esc(human_ms(fastest))}</b></span>")
                bits.append(f'<span class="{"warn" if max(durations) >= 0.9 * fn.timeout * 1000 else ""}">slowest '
                            f"<b>{_esc(human_ms(max(durations)))}</b> of {fn.timeout} s</span>")
            if used:
                most, size = max(used)
                bits.append(f'<span class="{"warn" if most >= 0.9 * size else ""}">memory up to <b>{most:,}</b> of '
                            f"{size:,} MB</span>")
            if cold:
                bits.append(f"<span><b>{cold:,}</b> cold {'start' if cold == 1 else 'starts'}</span>")
            where = (f"with {self._searched!r}" if self.found is not None else self._range_text())
            bits.append(f'<span class="lmx-note">Newest first, {where}; times in UTC. Click a run to see its lines'
                        + (", the matching ones marked" if self.found is not None or self._log_query else "")
                        + ".</span>")
        summary = f'<div class="lmx-sum">{"".join(bits)}</div>' if bits else ""
        self._set(self.run_head, summary + (self._html(notes) if notes else ""))
        latest = page.latest if self.found is None and not self._source_runs() else None
        if latest is not None:
            seconds = (_utcnow() - latest).total_seconds()
            option = next(((label, value) for label, value in _RANGES if _seconds(value) > seconds), None)
            if option is not None and option[1] != self._range:
                self.jump_button.description = f"Show {option[0].lower()} ›"
                self.jump_button.tooltip = f"Its newest line is from {human_age(latest)}"
                self._jump_to = option[1]
                self.jump_button.layout.display = ""
                return
        self.jump_button.layout.display = "none"

    def _jump(self) -> None:
        target = getattr(self, "_jump_to", None)
        if target:
            self.range_pick.value = target  # its observer reads the logs again

    def _toggle_run(self, row: _RunRow) -> None:
        run = row.run
        if run is None:
            return
        if run.key in self._open_runs:
            self._open_runs.discard(run.key)
            row.body.layout.display = "none"
        else:
            self._open_runs.add(run.key)
            self._set(row.body, _run_body(run, self._words()))
            row.body.layout.display = ""
        opened = run.key in self._open_runs
        _class_if(row.box, "lmx-open", opened)
        row.button.tooltip = _run_tip(run, opened)

    def _older(self) -> None:
        """Reads the lines before the oldest one read (when reading stopped at the limit), and adds their runs."""
        page, fn = self.logs_page, self.function
        if page is None or fn is None or not page.truncated or page.covered_from is None:
            return
        start, end = page.since, page.covered_from - timedelta(milliseconds=1)
        self._status(f"Reading older lines, before {_stamp(page.covered_from)} UTC…", busy=True, owner="logs")
        self.older_button.disabled = True

        def done(got: tuple[list[LogEvent], bool, datetime | None]) -> None:
            self.older_button.disabled = False
            if self.logs_page is not page:
                return
            events, truncated, covered = got
            known = {(e.stream, e.time, e.message) for e in page.events}
            page.events = [e for e in events if (e.stream, e.time, e.message) not in known] + page.events
            page.truncated, page.covered_from = truncated, covered if truncated else None
            self._runs = page.runs
            self._draw_run_chips()
            self._draw_runs()
            self._finish("logs", f"Read {len(events):,} more lines" + (f", back to {_stamp(covered)} UTC" if truncated
                                                                       else f": every line of {self._range_text()}"),
                         tabs=("logs",))

        def failed(exc: BaseException) -> None:
            self.older_button.disabled = False
            self._status(self._error_text(exc), "warn")

        self._later("older", lambda: self.core._filter(fn.log_group, fn.region, start, end, None, _RUN_LINES), done,
                    failed)

    # ------------------------------------------------------------------ live

    def _toggle_live(self) -> None:
        if self._live:
            self._stop_live("Live is off.")
            return
        if self.function is None or self.logs_page is None:
            self._status("Live follows the logs shown: open a function's Logs tab first.", "warn")
            return
        if self.found is not None:  # back to every run: Live adds to them
            self._quietly(self.log_find, value="")
            self.found, self._found_runs, self._searched, self._log_query = None, [], "", ""
            self._draw_run_chips()
            self._draw_runs(renew=True)
        self._live, self._live_new, self._live_started = True, 0, time.monotonic()
        self._live_token += 1
        self.live_button.description = "Live"
        _class_if(self.live_button, "lmx-on", True)
        self._status(f"Live: looking for new lines every {_LIVE_SECONDS} seconds; new runs show at the top.", "live")
        loop = _running_loop()
        if loop is None:  # a script or the tests: one look now
            self._poll_now()
            return
        token = self._live_token
        self._tasks["live"] = loop.create_task(self._live_loop(token))

    def _stop_live(self, text: str = "", level: str = "") -> None:
        was = self._live
        self._live = False
        self._live_token += 1
        if hasattr(self, "live_button"):
            _class_if(self.live_button, "lmx-on", False)
        if was and text:
            self._status(text, level)

    def _poll_work(self) -> tuple[LogPage, Callable[[], list[LogEvent]]] | None:
        page, fn = self.logs_page, self.function
        if page is None or fn is None:
            return None
        newest = max((e.time for e in page.events), default=page.until)
        start = min(newest, page.until) - timedelta(seconds=2)  # a little overlap: lines can share a millisecond
        return page, lambda: self.core._filter(fn.log_group, fn.region, start, _utcnow(), None, 2_000)[0]

    def _poll_now(self) -> None:
        polled = self._poll_work()
        if polled is not None:
            page, work = polled
            self._merge_live(page, work())

    async def _live_loop(self, token: int) -> None:
        loop = asyncio.get_running_loop()
        try:
            while self._live and self._live_token == token:
                await asyncio.sleep(_LIVE_SECONDS)
                if not (self._live and self._live_token == token):
                    return
                if time.monotonic() - self._live_started > _LIVE_MINUTES * 60:
                    self._stop_live(f"Live stopped after {_LIVE_MINUTES} minutes: click Live to go on.")
                    return
                polled = self._poll_work()
                if polled is None:
                    continue
                page, work = polled
                try:
                    events = await loop.run_in_executor(self._workers(), work)
                except Exception as exc:
                    if self._live_token == token:
                        self._stop_live(f"Live stopped: {self._error_text(exc)}", "warn")
                    return
                if self._live and self._live_token == token:
                    self._merge_live(page, events)
        finally:
            if self._tasks.get("live") is asyncio.current_task():
                del self._tasks["live"]

    def _merge_live(self, page: LogPage, events: list[LogEvent]) -> None:
        if self.logs_page is not page:
            return
        known = {(e.stream, e.time, e.message) for e in page.events}
        new = [e for e in events if (e.stream, e.time, e.message) not in known]
        page.until = _utcnow()
        checked = f"checked {page.until:%H:%M:%S} UTC"
        if new:
            before = {run.key for run in self._runs}
            page.events = sorted(page.events + new, key=lambda e: e.time)[-20_000:]
            self._runs = page.runs
            self._live_new += sum(1 for run in self._runs if run.key not in before and run.request_id)
            if self.found is None:
                self._draw_run_chips()
                self._draw_runs()
        if self._live:
            self._status(f"Live · {_plural(self._live_new, 'new run')} since it started · {checked} · every "
                         f"{_LIVE_SECONDS} seconds", "live")

    # ------------------------------------------------------------------ errors and performance

    def _errors_range_changed(self, change: dict[str, Any]) -> None:
        if self.quiet or not change.get("new"):
            return
        self._errors_range = str(change["new"])
        self._load_errors()

    def _perf_range_changed(self, change: dict[str, Any]) -> None:
        if self.quiet or not change.get("new"):
            return
        self._perf_range = str(change["new"])
        self._load_perf()

    def _load_errors(self) -> None:
        if self.function is None:
            return
        self._asked.add("errors")
        fn = self._full()
        start, end = LambdaAnalyzer._window(self._errors_range)
        self.error_report = None
        self._set(self.errors_view, _skeleton(f"Reading {fn.name}'s errors from {self._range_text(self._errors_range)}"
                                              "…"))
        self._set(self.error_head, "")
        self.error_rows_box.children = []
        self._counted = 0
        self._status(f"Reading {fn.name}'s error lines…", busy=True, owner="errors")
        self._later("errors", lambda: self.core._errors(fn, start, end, progress=lambda n: setattr(self, "_counted",
                                                                                                  n)),
                    self._got_errors, self._errors_failed,
                    counting=f"Reading {fn.name}'s error lines… {{n}} read so far", owner="errors")

    def _errors_failed(self, exc: BaseException) -> None:
        text = self._error_text(exc)
        self._set(self.errors_view, self._html([_Note(text, "warn")]))
        self._status(text, "warn")

    def _got_errors(self, report: ErrorReport) -> None:
        self.error_report = report
        self._draw("errors", self.errors_view, self.ui._errors_blocks(report, since=self._errors_range, for_window=True))
        failed = [e for e in report.newest]
        today = _utcnow().date()
        while len(self._error_rows) < len(failed):
            self._error_rows.append(_Row(self, self._clicked_failure, short=True))
        for row, event in zip(self._error_rows, failed):
            row.item = event
            kind, summary = classify_error(event.message) or ("Error", _clip(event.message, 200))
            self._set(row.face, f'<div class="er"><span class="ri">✕</span><span class="ew">'
                                f"{_esc(_run_clock(event.time, today))}</span><span class=\"ek\">{_esc(kind)}</span>"
                                f'<span class="es">{_esc(summary.splitlines()[0][:300])}</span>'
                                '<span class="eo">Read the run ›</span></div>')
            tip = f"{_stamp(event.time)} UTC · request {event.request_id or 'unknown'} · click to read every line"
            if row.button.tooltip != tip:
                row.button.tooltip = tip
        self.error_rows_box.children = [row.box for row in self._error_rows[:len(failed)]]
        self._set(self.error_head, '<div class="lmx-h">The newest failed runs<span>click one to read every line of '
                                   "it in the Logs tab</span></div>" if failed else "")
        self._finish("errors", self._tab_line("errors"), tabs=("errors",))

    def _clicked_failure(self, event: LogEvent) -> None:
        self._show_run(event.time, event.stream, event.request_id)

    def _load_perf(self) -> None:
        if self.function is None:
            return
        self._asked.add("performance")
        fn = self._full()
        start, end = LambdaAnalyzer._window(self._perf_range)
        self.perf = None
        self._set(self.perf_view, _skeleton(f"Reading {fn.name}'s runs from {self._range_text(self._perf_range)}…"))
        self._set(self.slow_head, "")
        self.slow_rows_box.children = []
        self._counted = 0
        self._status(f"Reading {fn.name}'s REPORT lines…", busy=True, owner="performance")
        self._later("perf", lambda: self.core._performance(fn, start, end,
                                                           progress=lambda n: setattr(self, "_counted", n)),
                    self._got_perf, self._perf_failed,
                    counting=f"Reading {fn.name}'s REPORT lines… {{n}} read so far", owner="performance")

    def _perf_failed(self, exc: BaseException) -> None:
        text = self._error_text(exc)
        self._set(self.perf_view, self._html([_Note(text, "warn")]))
        self._status(text, "warn")

    def _got_perf(self, perf: Performance) -> None:
        self.perf = perf
        fn = perf.function
        self._draw("performance", self.perf_view, self.ui._performance_blocks(perf, since=self._perf_range,
                                                                              for_window=True))
        slowest = sorted(perf.invocations, key=lambda r: r.duration, reverse=True)[:10]
        today = _utcnow().date()
        while len(self._slow_rows) < len(slowest):
            self._slow_rows.append(_Row(self, self._clicked_slow, short=True))
        for row, inv in zip(self._slow_rows, slowest):
            row.item = inv
            share = min(1.0, inv.duration / (fn.timeout * 1000)) if fn.timeout else 0.0
            fill = "bad" if share >= 0.9 else "warn" if share >= 0.7 else ""
            bits = [f"{inv.max_memory:,} of {inv.memory:,} MB" if inv.memory else "",
                    f"cold start {human_ms(inv.init)}" if inv.init is not None else "",
                    f"status {inv.status}" + (f" ({inv.error_type})" if inv.error_type else "") if inv.status else ""]
            icon = "✕" if inv.status else "⏱"
            self._set(row.face, f'<div class="er slow"><span class="ri">{icon}</span><span class="ew">'
                                f"{_esc(_run_clock(inv.time, today) if inv.time else '-')}</span><span class=\"ek\">"
                                f'<span class="rr" style="display:inline;height:auto;padding:0"><span class="rb">'
                                f'<i class="{fill}" style="width:{max(share * 100, 2):.1f}%"></i></span></span> '
                                f"{_esc(human_ms(inv.duration))}</span><span class=\"es\">"
                                f"{_esc(' · '.join(b for b in bits if b))}</span>"
                                '<span class="eo">Read the run ›</span></div>')
            tip = f"request {inv.request_id} · click to read every line it logged"
            if row.button.tooltip != tip:
                row.button.tooltip = tip
        self.slow_rows_box.children = [row.box for row in self._slow_rows[:len(slowest)]]
        self._set(self.slow_head, '<div class="lmx-h">The slowest runs<span>click one to read every line of it in the '
                                  "Logs tab</span></div>" if slowest else "")
        self._finish("performance", self._tab_line("performance"), tabs=("performance",))

    def _clicked_slow(self, inv: Invocation) -> None:
        if inv.time is not None:
            self._show_run(inv.time, inv.stream, inv.request_id or None)

    # ------------------------------------------------------------------ code

    def _load_code(self, unlimited: bool = False) -> None:
        fn = self.function
        if fn is None:
            return
        self._asked.add("code")
        most = None if unlimited else "50MB"
        self.package = None
        self.code_more.layout.display = "none"
        self.code_split.layout.display = "none"
        self._set(self.code_view, _skeleton(f"Downloading {fn.name}'s deployment package"
                                            + ("" if unlimited else " (up to 50 MB)") + "…"))
        self._status(f"Downloading {fn.name}'s deployment package…", busy=True, owner="code")

        def work() -> tuple[Function, bytes | None]:
            full = self.core.function(fn.name, region=fn.region or None)  # a fresh link to the package
            return full, self.core._package(full, most)

        self._later("code", work, self._got_code, self._code_failed)

    def _code_failed(self, exc: BaseException) -> None:
        text = self._error_text(exc)
        self._set(self.code_view, self._html([_Note(text, "warn")]))
        if isinstance(exc, ValueError) and "max_size" in str(exc):
            size = re.search(r"package is ([\d.]+ [KMGT]?B)", str(exc))
            self.code_more.description = f"Download all {size.group(1)} anyway" if size else "Download it anyway"
            self.code_more.layout.display = ""
            said = (f"The package is {size.group(1)}" if size else "The package is over 50 MB") + (
                ", more than the 50 MB the window downloads at first: the button under this downloads all of it.")
            self._set(self.code_view, self._html([_Note(said, "warn")]))
            self._status(said, "warn")
        else:
            self._status(text, "warn")

    def _got_code(self, got: tuple[Function, bytes | None]) -> None:
        full, data = got
        self._code_fn, self._package_data = full, data
        self.package = package = read_package(full, data)
        blocks = [b for b in self.ui._code_blocks(package, source=False) if not isinstance(b, _Table)]
        self._draw("code", self.code_view, blocks)
        if not package.files:
            self.code_split.layout.display = "none"
            self._finish("code", package.note or "No files in the package.", tabs=("code",))
            return
        self.code_split.layout.display = ""
        self._code_file = package.shown_file
        self._draw_code_rows(renew=True)
        self._draw_source(package)
        self._finish("code", self._tab_line("code"), tabs=("code",))

    def _code_files(self) -> list[CodeFile]:
        package = self.package
        if package is None:
            return []
        handler = package.handler_file
        files = sorted(package.files, key=lambda f: (f.path != handler, bool(_JUNK_RE.search(f.path)), "/" in f.path,
                                                     f.path.lower()))
        if self._code_query:
            files = [f for f in files if search_rank(self._code_query, (f.path, posixpath.basename(f.path)))
                     is not None]
        return files

    def _code_typed(self, change: dict[str, Any]) -> None:
        if self.quiet:
            return
        self._code_query = str(self.code_find.value or "").strip()
        self._code_offset = 0
        self._draw_code_rows(renew=True)

    def _draw_code_rows(self, renew: bool = False) -> None:
        files = self._code_files()
        self._code_offset = min(self._code_offset, max(0, (len(files) - 1) // _CODE_PAGE * _CODE_PAGE))
        page = files[self._code_offset:self._code_offset + _CODE_PAGE]
        while len(self._code_rows) < len(page):
            self._code_rows.append(_Row(self, self._open_code_file, short=True))
        words = self._code_query.split()
        handler = self.package.handler_file if self.package is not None else None
        for row, f in zip(self._code_rows, page):
            row.item = f
            self._set(row.face, _code_file_face(f, words, handler))
            tip = f"{f.path} · {human_size(f.size)} unzipped · click to see it"
            if row.button.tooltip != tip:
                row.button.tooltip = tip
            _class_if(row.box, "lmx-on", f.path == self._code_file)
        children: list[Any] = [row.box for row in self._code_rows[:len(page)]]
        if not page:
            children = [self._empty("files", "No file matches.")]
        if renew:
            box = self._w.VBox(children, layout=self._w.Layout(width="100%"))
            box.add_class("lmx-files")
            self.code_left.children = [box if child is self.files_box else child for child in self.code_left.children]
            self.files_box = box
        else:
            self.files_box.children = children
        total = len(self.package.files) if self.package is not None else 0
        self._draw_pager("files", self._code_offset, len(files), _CODE_PAGE, "files", total)

    def _open_code_file(self, f: CodeFile) -> None:
        if self._code_fn is None:
            return
        self._code_file = f.path
        for row in self._code_rows:
            _class_if(row.box, "lmx-on", row.item is not None and row.item.path == f.path)
        self._draw_source(read_package(self._code_fn, self._package_data, file=f.path))
        self.code_right = self._w.VBox([self.source_view])  # a new box, which starts at the top
        self.code_right.add_class("lmx-right")
        self.code_split.children = [self.code_left, self.code_right]

    def _draw_source(self, package: CodePackage) -> None:
        path = package.shown_file
        if path is None:
            self._set(self.source_view, '<div class="lmx-hint">👈 <div>Click a file to see its source.</div></div>')
            return
        size = next((f.size for f in package.files if f.path == path), None)
        about = " · ".join(filter(None, [human_size(size) if size is not None else "",
                                         "the handler's file" if path == package.handler_file else ""]))
        head = f'<div class="srch"><b>{_esc(path)}</b><span>{_esc(about)}</span></div>'
        if package.source is not None:
            more = (self._html([_Note(f"Only the first {human_size(_SOURCE_LIMIT)} of it is shown.")])
                    if package.source_truncated else "")
            self._set(self.source_view, head + _source_html(path, package.source) + more)
        else:
            self._set(self.source_view, head + self._html([_Note(package.note or "Nothing to show.")]))


def explore(
    name: str | None = None,
    *,
    tab: str | None = None,
    region: str | None = None,
    profile: str | None = None,
    height: int | str | None = None,
) -> LambdaExplorer:
    """Opens the Lambda explorer window and returns it: every function in the region, and for the one you click, how
    it's doing, its logs run by run (failed runs in red, a search box, a time range, and Live to watch new runs come
    in), its errors grouped by cause, its run times, the code in its package and every setting, all by clicking.

        explore()                                   # every function in the notebook's region
        explore("orders-etl")                       # straight to one function (a name, an ARN or a console link)
        explore("orders-etl", tab="logs")           # ...on its logs ('errors', 'performance', 'code', 'settings')
        explore(region="all")                       # every region your account has turned on
        explore(region="eu-west-1", profile="dev")  # another region or AWS profile

    The tabs' pages fill the browser's height; height= sets theirs instead (800 pixels, or CSS such as '70vh'). It only
    reads: where a change would help, it shows the command to run."""
    return LambdaExplorer(name, tab=tab, region=region, profile=profile, height=height)
