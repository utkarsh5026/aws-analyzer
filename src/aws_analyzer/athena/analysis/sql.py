"""
Reading a query's SQL without running it: its shape (the same query but for its values), the tables it names,
whether it reads every column or filters on a table's partition keys; and writing SQL and Python to copy: a sample
query, CREATE TABLE AS to Parquet, MSCK REPAIR TABLE, and the boto3 code that runs a query.
"""

from __future__ import annotations

import re

from ..helpers import DEFAULT_CATALOG, DEFAULT_WORKGROUP, quoted
from ..models import Table


_COMMENT_RE = re.compile(r"--[^\n]*|/\*.*?\*/", re.S)

_STRING_RE = re.compile(r"'(?:[^']|'')*'")

_NUMBER_RE = re.compile(r"\b\d+(?:\.\d+)?\b")

_IN_LIST_RE = re.compile(r"\(\s*\?(?:\s*,\s*\?)+\s*\)")

_SELECT_STAR_RE = re.compile(r"\bselect\s+(?:distinct\s+|all\s+)?(?:\w+\.)?\*", re.I)

_NAME = r'(?:"[^"]+"|`[^`]+`|[A-Za-z_][\w$]*)'

_TABLE_RE = re.compile(rf"\b(?:from|join)\s+({_NAME}(?:\s*\.\s*{_NAME}){{0,2}})", re.I)

_CTE_RE = re.compile(rf"(?:\bwith\b|,)\s*({_NAME})\s+as\s*\(", re.I)

_WHERE_RE = re.compile(r"\bwhere\b", re.I)

_DATE_NAMES = ("dt", "day", "date", "event_date", "ds", "partition_date")

_DATE_TYPES = ("date",)

_SAMPLE_COLUMNS = 8


def normalize_sql(sql: str) -> str:
    """
    A query's shape: comments dropped, strings and numbers as ?, IN lists as (?...), lower case, one line. Two runs
    of the same query with other values have the same shape.
    """
    text = _COMMENT_RE.sub(" ", sql or "")
    text = _STRING_RE.sub("?", text)
    text = _NUMBER_RE.sub("?", text)
    text = " ".join(text.lower().split())
    return _IN_LIST_RE.sub("(?...)", text)


def _bare(name: str) -> str:
    return name.strip().strip('"`').lower()


def tables_in_sql(sql: str, default_database: str = "") -> set[str]:
    """
    The tables a query reads, as 'database.table' (names without a database take default_database), leaving out
    the names its WITH clause defines. Read from the text, so a table named only inside a view isn't seen.
    """
    text = _COMMENT_RE.sub(" ", _STRING_RE.sub("''", sql or ""))
    defined = {_bare(name) for name in _CTE_RE.findall(text)}
    found: set[str] = set()
    for reference in _TABLE_RE.findall(text):
        parts = [_bare(p) for p in re.split(r"\s*\.\s*", reference)]
        if len(parts) == 3 and parts[0] == DEFAULT_CATALOG.lower():
            parts = parts[1:]
        if len(parts) == 1 and (parts[0] in defined or not default_database):
            continue
        database, table = (parts[-2], parts[-1]) if len(parts) >= 2 else (default_database.lower(), parts[0])
        found.add(f"{database}.{table}")
    return found


def selects_every_column(sql: str) -> bool:
    """
    Whether a query reads every column (SELECT *, not COUNT(*)), which a columnar table can't save it from.
    """
    return bool(_SELECT_STAR_RE.search(_COMMENT_RE.sub(" ", sql or "")))


def filters_on(sql: str, columns: list[str]) -> bool:
    """
    Whether a query's WHERE names any of `columns` (a table's partition keys), so Athena can skip partitions.
    """
    text = _STRING_RE.sub("''", _COMMENT_RE.sub(" ", sql or ""))
    parts = _WHERE_RE.split(text, maxsplit=1)
    if len(parts) < 2:
        return False
    where = parts[1].lower()
    return any(re.search(rf'(?<![\w"]){re.escape(c.lower())}(?![\w"])|"{re.escape(c.lower())}"', where)
               for c in columns)


def date_column(table: Table) -> str:
    """
    The column a table would best be partitioned by: one of type date, else one named like a day ('dt', 'day',
    'date', ...); '' when it has none.
    """
    for column in table.columns:
        if column.type.lower() in _DATE_TYPES:
            return column.name
    for column in table.columns:
        if column.name.lower() in _DATE_NAMES:
            return column.name
    return ""


def sample_sql(table: Table, newest: list[str] | None = None) -> str:
    """
    A cheap first query of a table: a few named columns, its newest partition when it has partitions, LIMIT 10.
    """
    columns = ", ".join(f'"{c.name}"' for c in table.columns[:_SAMPLE_COLUMNS]) or "*"
    lines = [f"SELECT {columns}", f"FROM {quoted(table.database, table.name)}"]
    if table.partition_keys:
        values = newest or ["<value>"] * len(table.partition_keys)
        conditions = [f"\"{key.name}\" = '{value}'" for key, value in zip(table.partition_keys, values)]
        lines.append("WHERE " + " AND ".join(conditions))
    lines.append("LIMIT 10")
    return "\n".join(lines)


def ctas_sql(table: Table) -> str:
    """
    CREATE TABLE AS that copies a table to Parquet (Snappy), partitioned by its partition keys or, when it has none,
    by its date column; the copy goes in a _parquet folder beside it.
    """
    keys = [k.name for k in table.partition_keys] or [c for c in [date_column(table)] if c]
    plain = [c.name for c in table.columns if c.name not in keys]
    location = table.location_uri.rstrip("/") + "_parquet/" if table.location_uri else "s3://<bucket>/<folder>/"
    options = ["format = 'PARQUET'", "write_compression = 'SNAPPY'", f"external_location = '{location}'"]
    if keys:
        options.append("partitioned_by = ARRAY[" + ", ".join(f"'{k}'" for k in keys) + "]")
    select = ", ".join(f'"{c}"' for c in plain + keys) or "*"
    return (f"CREATE TABLE {quoted(table.database, table.name + '_parquet')}\n"
            "WITH (\n    " + ",\n    ".join(options) + "\n)\n"
            f"AS SELECT {select}\nFROM {quoted(table.database, table.name)}")


def repair_sql(table: Table) -> str:
    """
    The statement that loads a table's partitions from its folder (Hive-style key=value folders).
    """
    return f"MSCK REPAIR TABLE {quoted(table.database, table.name)}"


def run_code(sql: str, *, database: str = "", workgroup: str = "", region: str = "") -> str:
    """
    The boto3 code that runs a query and waits for it: StartQueryExecution, then GetQueryExecution until it ends,
    then the first page of GetQueryResults.
    """
    context = f'\n    QueryExecutionContext={{"Database": "{database}"}},' if database else ""
    region_arg = f'"athena", region_name="{region}"' if region else '"athena"'
    body = (sql or "").strip().replace('"""', '\\"\\"\\"')
    return (
        "import time\n\nimport boto3\n\n"
        f"athena = boto3.client({region_arg})\n"
        "started = athena.start_query_execution(\n"
        f'    QueryString="""{body}""",{context}\n'
        f'    WorkGroup="{workgroup or DEFAULT_WORKGROUP}",\n'
        ")\n"
        'query_id = started["QueryExecutionId"]\n'
        "while True:\n"
        '    status = athena.get_query_execution(QueryExecutionId=query_id)["QueryExecution"]["Status"]\n'
        '    if status["State"] not in ("QUEUED", "RUNNING"):\n'
        "        break\n"
        "    time.sleep(1)\n"
        'print(status["State"], status.get("StateChangeReason", ""))\n'
        'rows = athena.get_query_results(QueryExecutionId=query_id)["ResultSet"]["Rows"]'
    )


def pandas_code(sql: str, *, database: str = "", workgroup: str = "") -> str:
    """
    The same query read straight into a DataFrame with the AWS SDK for pandas (pip install awswrangler).
    """
    body = (sql or "").strip().replace('"""', '\\"\\"\\"')
    database_arg = f', database="{database}"' if database else ""
    return ("import awswrangler as wr\n\n"
            f'df = wr.athena.read_sql_query("""{body}"""{database_arg}, '
            f'workgroup="{workgroup or DEFAULT_WORKGROUP}")')
