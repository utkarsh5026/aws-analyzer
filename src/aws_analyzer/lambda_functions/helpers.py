"""
Parsing and formatting: runtimes and their end-of-support dates, Lambda's limits, function references
(names, ARNs, console links) and the times, durations and sizes Lambda answers in.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timezone
from typing import Any
from urllib.parse import unquote

from .._kit.fmt import _duration


KB, MB, GB, TB = 1024, 1024**2, 1024**3, 1024**4


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


_FUNCTION_ARN_RE = re.compile(
    r"^arn:aws[\w-]*:lambda:([a-z0-9-]+):(\d{12}):function:([A-Za-z0-9_-]{1,64})(?::([\w$-]+))?$"
)

_PARTIAL_ARN_RE = re.compile(r"^(\d{12}):function:([A-Za-z0-9_-]{1,64})(?::([\w$-]+))?$")

_CONSOLE_RE = re.compile(r"console\.aws\.amazon\.com/lambda/.*#/functions/([A-Za-z0-9_%-]+)", re.IGNORECASE)

_CONSOLE_REGION_RE = re.compile(r"[?&]region=([a-z0-9-]+)|^https?://([a-z]{2}(?:-[a-z]+)+-\d)\.console\.")

_NAME_RE = re.compile(r"^([A-Za-z0-9_-]{1,64})(?::([\w$-]+))?$")


def parse_function_ref(text: str) -> tuple[str, str | None, str | None]:
    """
    A function the way you'd paste it -> (name, qualifier, region): 'etl', 'etl:prod' (an alias or a version),
    an ARN ('arn:aws:lambda:eu-west-1:123456789012:function:etl'), a partial ARN ('123456789012:function:etl') or a
    link to it in the Lambda console. qualifier and region are None when the text doesn't say.
    """
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


_STREAM_VERSION_RE = re.compile(r"\[([^\]]+)\]")


def stream_version(stream: str) -> str | None:
    """
    The version of a function that wrote a log stream, from the name Lambda gives it:
    '2026/10/22/[3]8f5ce35b...' -> '3', '2026/10/22/[$LATEST]8f5c...' -> '$LATEST'; None for a name that doesn't say.
    """
    match = _STREAM_VERSION_RE.search(stream or "")
    return match.group(1) if match else None


_QUALIFIER_RE = re.compile(r"^[A-Za-z0-9_$-]{1,128}$")


def parse_qualifier(value: Any) -> str:
    """
    A version or alias the way you'd write it -> what Lambda takes: 3, '3' or 'v3' -> '3', 'latest' -> '$LATEST',
    'prod' -> 'prod'. A ValueError says what it takes.
    """
    text = str(value).strip()
    if text.lower() in ("latest", "$latest"):
        return "$LATEST"
    if text[:1] in ("v", "V") and text[1:].isdigit():
        return text[1:]
    if not _QUALIFIER_RE.match(text):
        raise ValueError(f"{value!r} isn't a version or an alias: give a version number (3), an alias ('prod') or "
                         "'$LATEST'")
    return text


def runtime_family(runtime: str | None) -> str | None:
    """
    'python3.12' -> 'python', 'nodejs20.x' -> 'nodejs', 'dotnetcore3.1' -> 'dotnet', 'provided.al2' ->
    'provided', 'go1.x' -> 'go'; None for a container image (no runtime).
    """
    if not runtime:
        return None
    match = re.match(r"[a-z]+", runtime)
    family = match.group(0) if match else runtime
    return "dotnet" if family.startswith("dotnet") else family


def _lambda_time(value: Any) -> datetime | None:
    """
    Lambda's '2024-05-01T10:00:00.000+0000' (or a datetime) -> an aware datetime; None when missing or unreadable.
    """
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
    """
    The start of the moment's UTC day.
    """
    return moment.astimezone(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)


def _day(text: str | None) -> date | None:
    return date.fromisoformat(text) if text else None


def human_ms(ms: float | None) -> str:
    """
    A run time: 0.42 -> '0.42 ms', 102.4 -> '102 ms', 1530 -> '1.53 s', 75000 -> '75.0 s', 185000 -> '3m 05s'.
    """
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
    """
    A run time in a table: always in ms, so a column of them lines up, with a second or more also said the way
    people read it: '102 ms', '1,530 ms (1.5s)', '30,000 ms (30s)', '185,000 ms (3m 05s)'.
    """
    if ms is None:
        return "-"
    if 0 < ms < 1:
        return f"{ms:.2f} ms"
    return f"{ms:,.0f} ms" + (f" ({_duration(ms / 1000)})" if ms >= 1000 else "")


def _mb(value: float | None) -> str:
    return "-" if value is None else f"{value:,.0f} MB"


def _pct(fraction: float | None) -> str:
    """
    0.024 -> '2.4%', 0.0004 -> '<0.1%', 0 -> '0%'.
    """
    if fraction is None:
        return "-"
    if fraction == 0:
        return "0%"
    return "<0.1%" if fraction < 0.001 else f"{fraction * 100:.1f}%"


def _in_days(days: int) -> str:
    """
    26 -> 'in 26 days', 1 -> 'tomorrow', 0 -> 'today', -40 -> '40 days ago'.
    """
    if days == 0:
        return "today"
    if days == 1:
        return "tomorrow"
    return f"in {days:,} days" if days > 0 else f"{-days:,} days ago"


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
