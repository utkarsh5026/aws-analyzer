"""
Why Athena queries fail, in plain words: each failed query's message sorted into a kind (Athena's own code, like
TABLE_NOT_FOUND, when the message starts with one), what that kind means and how to fix it, and the failures
grouped by kind.
"""

from __future__ import annotations

import re

from ..models import FailureGroup, Query


_CODE_RE = re.compile(r"^\s*([A-Z][A-Z0-9_]{3,})\b")

_PHRASES = (
    ("Access Denied", "S3_ACCESS_DENIED"),
    ("Permission denied", "S3_ACCESS_DENIED"),
    ("exhausted resources", "EXHAUSTED_RESOURCES"),
    ("Bytes scanned limit", "SCAN_LIMIT"),
    ("maximum time limit", "TIMEOUT"),
    ("timed out", "TIMEOUT"),
    ("reduce your request rate", "S3_SLOW_DOWN"),
    ("Slow Down", "S3_SLOW_DOWN"),
    ("cancelled", "CANCELLED"),
    ("does not exist", "TABLE_NOT_FOUND"),
    ("mismatched input", "SYNTAX_ERROR"),
)

KNOWN_FAILURES: dict[str, tuple[str, str]] = {
    "TABLE_NOT_FOUND": ("the query names a table the catalog doesn't have (a typo, another database, or it was "
                        "dropped)", "check the name with catalog(), and name its database ('sales.orders')"),
    "SCHEMA_NOT_FOUND": ("the database the query names doesn't exist", "catalog() lists the databases"),
    "COLUMN_NOT_FOUND": ("a column the query names isn't in the table, or you may not read it (Lake Formation)",
                         "table_info('<database.table>') lists its columns"),
    "SYNTAX_ERROR": ("Athena couldn't read the SQL (a typo, a missing comma, or a function Trino doesn't have)",
                     "the message gives the line and column; quote names with dashes or capitals in double quotes"),
    "TYPE_MISMATCH": ("a value is compared or combined with one of another type ('42' with 42)",
                      "CAST one side, CAST(day AS date)"),
    "INVALID_CAST_ARGUMENT": ("a value couldn't be turned into the type it's CAST to",
                              "use TRY_CAST, which gives NULL instead of failing"),
    "INVALID_FUNCTION_ARGUMENT": ("a function got a value it can't take (a bad date format, a negative length)",
                                  "check the values the message names, or wrap the call in TRY()"),
    "FUNCTION_NOT_FOUND": ("the query calls a function Athena doesn't have, or with the wrong argument types",
                           "check Trino's function list for its name and arguments"),
    "DIVISION_BY_ZERO": ("a division by zero", "use NULLIF(divisor, 0)"),
    "HIVE_PARTITION_SCHEMA_MISMATCH": ("a partition's columns differ from the table's (a crawler changed one "
                                       "partition's types)", "set the crawler to update partitions from the "
                                       "table, or rebuild the partitions with MSCK REPAIR TABLE"),
    "HIVE_BAD_DATA": ("a file holds a value that doesn't fit its column's type (text in a number column, a bad "
                      "line in a JSON file)", "find the file the message names; fix the column's type or the file"),
    "HIVE_CURSOR_ERROR": ("Athena couldn't read a file (cut short, compressed oddly, or changed while it was read)",
                          "find the file the message names and check it opens"),
    "HIVE_CANNOT_OPEN_SPLIT": ("Athena couldn't open a file (it moved, is in Glacier, or S3 refused)",
                               "check the file the message names still exists and isn't archived"),
    "HIVE_METASTORE_ERROR": ("the Glue Data Catalog answered with an error (a table definition it can't read)",
                             "look at the table in table_info(); a column type may be invalid"),
    "HIVE_TOO_MANY_OPEN_PARTITIONS": ("CREATE TABLE AS or INSERT wrote to more than 100 partitions at once",
                                      "write the partitions in batches of 100 or fewer"),
    "ICEBERG_COMMIT_ERROR": ("two writes to an Iceberg table collided", "retry the query"),
    "S3_ACCESS_DENIED": ("Athena couldn't read the table's files, or write its results, in S3",
                         "give the caller s3:GetObject and s3:ListBucket on the table's folder, and s3:PutObject on "
                         "the result location"),
    "S3_SLOW_DOWN": ("S3 slowed Athena down: too many files are read at once from one folder",
                     "fewer, larger files help; retry the query"),
    "EXHAUSTED_RESOURCES": ("the query needed more memory than Athena gives one query (a big JOIN, ORDER BY or "
                            "GROUP BY)", "filter early, put the smaller table on the right of a JOIN, or add LIMIT "
                            "to ORDER BY"),
    "EXCEEDED_LOCAL_MEMORY_LIMIT": ("the query needed more memory than Athena gives one node", "filter early, or "
                                    "split the query"),
    "SCAN_LIMIT": ("the workgroup's per-query scan limit stopped the query",
                   "filter on the partition keys so it reads less, or raise the workgroup's limit"),
    "TIMEOUT": ("the query ran past Athena's time limit (30 minutes by default)",
                "read less (partition filters, Parquet), or ask for a higher limit with a quota increase"),
    "CANCELLED": ("someone or something cancelled the query", "nothing to fix unless it wasn't meant to be"),
    "GENERIC_INTERNAL_ERROR": ("Athena failed inside (often a column type the files don't match, or an empty "
                               "file)", "the message says more; check the table's column types against its files"),
    "NOT_SUPPORTED": ("the query uses something Athena doesn't support on this table or engine version",
                      "the message names what; engine version 3 supports more"),
}

_UNKNOWN = ("Athena's message says what went wrong", "read the message, then query('<id>') for the whole query")


def failure_kind(message: str) -> str:
    """
    The kind of a failed query's error: Athena's code when the message starts with one ('TABLE_NOT_FOUND: line
    1:15: ...'), else a kind read from its words ('Access Denied (Service: Amazon S3 ...)' -> 'S3_ACCESS_DENIED'),
    else 'OTHER'.
    """
    text = message or ""
    code = _CODE_RE.match(text)
    if code:
        return code.group(1)
    for phrase, kind in _PHRASES:
        if phrase.lower() in text.lower():
            return kind
    return "OTHER"


def explain_failure(kind: str) -> tuple[str, str]:
    """
    What a kind of failure means and how to fix it, in plain words.
    """
    return KNOWN_FAILURES.get(kind, _UNKNOWN)


def did_not_finish(query: Query) -> bool:
    """
    Whether a query failed, or was stopped by its workgroup's scan limit (Athena cancels those).
    """
    stopped_by_limit = query.state == "CANCELLED" and failure_kind(query.error_message) == "SCAN_LIMIT"
    return query.failed or stopped_by_limit


def group_failures(queries: list[Query]) -> list[FailureGroup]:
    """
    The failed queries (and those the scan limit stopped) grouped by kind, the most common first, each with its
    newest message and query ID.
    """
    groups: dict[str, FailureGroup] = {}
    for query in queries:
        if not did_not_finish(query):
            continue
        kind = failure_kind(query.error_message)
        meaning, fix = explain_failure(kind)
        group = groups.setdefault(kind, FailureGroup(kind, meaning=meaning, fix=fix))
        group.count += 1
        group.workgroups.add(query.workgroup)
        is_newer = group.last is None or (query.submitted is not None and query.submitted > group.last)
        if is_newer:
            group.last, group.last_id, group.message = query.submitted, query.id, query.error_message
    return sorted(groups.values(), key=lambda g: (-g.count, g.kind))
