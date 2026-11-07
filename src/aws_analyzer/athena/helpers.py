"""
Small helpers for Athena and the Glue Data Catalog: the references commands take (a table as 'db.table', a query by
its ID), S3 locations, a table's storage format in plain words, a crawler's schedule, and the units bills use.
"""

from __future__ import annotations

import re
from datetime import timedelta


MB = 1024**2

TB = 1024**4

DEFAULT_CATALOG = "AwsDataCatalog"

DEFAULT_WORKGROUP = "primary"

STATE_TONES = {"SUCCEEDED": "ok", "FAILED": "bad", "CANCELLED": "warn"}

COLUMNAR = {"Parquet", "ORC", "Iceberg", "Delta", "Hudi"}

QUERY_ID_RE = re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b")

S3_URI_RE = re.compile(r"^s3[an]?://(?P<bucket>[^/]+)/?(?P<prefix>.*)$")

_FORMAT_WORDS = (
    ("parquet", "Parquet"), ("orc", "ORC"), ("avro", "Avro"), ("json", "JSON"), ("csv", "CSV"),
    ("opencsv", "CSV"), ("lazysimple", "CSV"), ("regex", "Text"), ("grok", "Text"), ("ion", "Ion"),
)

_TABLE_TYPES = {"iceberg": "Iceberg", "delta": "Delta", "hudi": "Hudi"}

_SCHEDULE_FIELDS = 6

_DAILY = timedelta(days=1)

_WEEKLY = timedelta(days=7)

_MONTHLY = timedelta(days=31)

_HOURLY = timedelta(hours=1)


def parse_table_ref(ref: str, default_database: str | None = None) -> tuple[str, str]:
    """
    A table reference as (database, table): 'sales.orders', 'sales/orders', '"sales"."orders"',
    'AwsDataCatalog.sales.orders' or `sales`.`orders`; a bare name takes default_database.
    """
    text = (ref or "").strip().replace("`", "").replace('"', "")
    if not text:
        raise ValueError("Name a table: 'database.table', like 'sales.orders'")
    parts = [p.strip() for p in re.split(r"[./]", text) if p.strip()]
    if len(parts) >= 3 and parts[0].lower() == DEFAULT_CATALOG.lower():
        parts = parts[1:]
    if len(parts) == 2:
        return parts[0].lower(), parts[1].lower()
    if len(parts) == 1 and default_database:
        return default_database.lower(), parts[0].lower()
    raise ValueError(f"Name a table with its database, 'database.table' (like 'sales.orders'), not {ref!r}")


def split_s3(uri: str) -> tuple[str, str]:
    """
    's3://bucket/folder/' -> ('bucket', 'folder/'); a folder always ends in '/'. ('', '') when it isn't an S3 URI.
    """
    found = S3_URI_RE.match((uri or "").strip())
    if not found:
        return "", ""
    prefix = found.group("prefix")
    if prefix and not prefix.endswith("/"):
        prefix += "/"
    return found.group("bucket"), prefix


def storage_format(input_format: str, serde: str, parameters: dict[str, str]) -> str:
    """
    How a table's files are stored, in a word: 'Parquet', 'ORC', 'CSV', 'JSON', 'Avro', 'Iceberg', ... or '' when
    the catalog doesn't say (a view).
    """
    table_type = (parameters.get("table_type") or "").lower()
    if table_type in _TABLE_TYPES:
        return _TABLE_TYPES[table_type]
    classification = (parameters.get("classification") or "").lower()
    for text in (classification, (serde or "").lower(), (input_format or "").lower()):
        for word, label in _FORMAT_WORDS:
            if word in text:
                return label
    return "Text" if input_format else ""


def is_columnar(fmt: str) -> bool:
    """
    Whether Athena can read only the columns a query names from files of this format (Parquet, ORC, ...).
    """
    return fmt in COLUMNAR


def schedule_period(expression: str) -> timedelta | None:
    """
    About how often a crawler's cron schedule runs it: 'cron(0 2 * * ? *)' -> 1 day, a day of the week -> 7 days, a
    day of the month -> 31 days, every hour -> 1 hour; None when there's no schedule or it can't be read.
    """
    found = re.match(r"^\s*cron\((.*)\)\s*$", expression or "")
    if not found:
        return None
    fields = found.group(1).split()
    if len(fields) != _SCHEDULE_FIELDS:
        return None
    _, hours, day_of_month, _, day_of_week, _ = fields
    if hours == "*" or "/" in hours:
        return _HOURLY
    if day_of_month not in ("*", "?"):
        return _MONTHLY
    if day_of_week not in ("*", "?"):
        return _WEEKLY
    return _DAILY


def one_line(sql: str, width: int = 0) -> str:
    """
    SQL on one line, its whitespace collapsed, cut to `width` characters with '…' when width is given.
    """
    text = " ".join((sql or "").split())
    return text if not width or len(text) <= width else text[: width - 1] + "…"


def quoted(database: str, table: str) -> str:
    """
    A table's name as SQL writes it safely: "sales"."orders".
    """
    return f'"{database}"."{table}"'
