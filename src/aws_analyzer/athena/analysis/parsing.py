"""
Reading what Athena and Glue return into the models: a query execution, a workgroup, a table, a crawler, and a
query's saved result rows.
"""

from __future__ import annotations

import base64
import binascii
import json
import re
from typing import Any

from ..helpers import storage_format
from ..models import Column, Crawler, Query, QueryResults, Table, Workgroup


_PRESTO_VIEW_RE = re.compile(r"^/\*\s*Presto View:\s*([A-Za-z0-9+/=]+)\s*\*/$")


def parse_query(item: dict[str, Any]) -> Query:
    """
    A QueryExecution (GetQueryExecution, BatchGetQueryExecution) as a Query.
    """
    status = item.get("Status") or {}
    stats = item.get("Statistics") or {}
    context = item.get("QueryExecutionContext") or {}
    error = status.get("AthenaError") or {}
    reuse = (stats.get("ResultReuseInformation") or {}).get("ReusedPreviousResult", False)
    engine = (item.get("EngineVersion") or {}).get("EffectiveEngineVersion", "")
    return Query(
        id=item.get("QueryExecutionId", ""),
        sql=item.get("Query", ""),
        state=status.get("State", ""),
        workgroup=item.get("WorkGroup", ""),
        database=context.get("Database", ""),
        catalog=context.get("Catalog", ""),
        statement_type=item.get("StatementType", ""),
        submitted=status.get("SubmissionDateTime"),
        completed=status.get("CompletionDateTime"),
        scanned_bytes=int(stats.get("DataScannedInBytes") or 0),
        engine_ms=int(stats.get("EngineExecutionTimeInMillis") or 0),
        total_ms=int(stats.get("TotalExecutionTimeInMillis") or 0),
        queued_ms=int(stats.get("QueryQueueTimeInMillis") or 0),
        error_message=error.get("ErrorMessage") or status.get("StateChangeReason", ""),
        error_category=int(error.get("ErrorCategory") or 0),
        error_type=int(error.get("ErrorType") or 0),
        output_location=(item.get("ResultConfiguration") or {}).get("OutputLocation", ""),
        reused=bool(reuse),
        engine_version=engine,
    )


def parse_workgroup(item: dict[str, Any]) -> Workgroup:
    """
    A WorkGroup (GetWorkGroup) as a Workgroup.
    """
    config = item.get("Configuration") or {}
    results = config.get("ResultConfiguration") or {}
    engine = config.get("EngineVersion") or {}
    return Workgroup(
        name=item.get("Name", ""),
        state=item.get("State", "ENABLED"),
        description=item.get("Description", ""),
        engine_version=engine.get("EffectiveEngineVersion") or engine.get("SelectedEngineVersion", ""),
        output_location=results.get("OutputLocation", ""),
        enforced=bool(config.get("EnforceWorkGroupConfiguration")),
        bytes_cutoff=config.get("BytesScannedCutoffPerQuery"),
        publishes_metrics=bool(config.get("PublishCloudWatchMetricsEnabled")),
        created=item.get("CreationTime"),
    )


def _columns(items: list[dict[str, Any]]) -> list[Column]:
    return [Column(c.get("Name", ""), c.get("Type", ""), c.get("Comment", "")) for c in items or []]


def _compression(parameters: dict[str, str], serde_parameters: dict[str, str]) -> str:
    for key in ("compressionType", "parquet.compression", "write.parquet.compression-codec", "orc.compress"):
        value = parameters.get(key) or serde_parameters.get(key)
        if value and value.lower() != "none":
            return value.lower()
    return ""


def _view_sql(item: dict[str, Any]) -> str:
    """
    A view's SQL: Athena keeps it as '/* Presto View: <base64 JSON> */' in ViewOriginalText, with the SQL under
    originalSql; other engines keep it as written.
    """
    original = item.get("ViewOriginalText", "")
    found = _PRESTO_VIEW_RE.match(original)
    if not found:
        return original or item.get("ViewExpandedText", "")
    try:
        return json.loads(base64.b64decode(found.group(1))).get("originalSql", "")
    except (ValueError, binascii.Error):
        return original


def parse_table(item: dict[str, Any]) -> Table:
    """
    A Glue Table (GetTable, GetTables) as a Table: its columns, partition keys, location, format and whether it uses
    partition projection.
    """
    storage = item.get("StorageDescriptor") or {}
    serde = storage.get("SerdeInfo") or {}
    parameters = dict(item.get("Parameters") or {})
    serde_parameters = serde.get("Parameters") or {}
    kind = item.get("TableType", "") or "EXTERNAL_TABLE"
    is_view = kind == "VIRTUAL_VIEW"
    return Table(
        database=item.get("DatabaseName", ""),
        name=item.get("Name", ""),
        kind=kind,
        location_uri="" if is_view else storage.get("Location", ""),
        format="" if is_view else storage_format(storage.get("InputFormat", ""),
                                                   serde.get("SerializationLibrary", ""), parameters),
        compression=_compression(parameters, serde_parameters),
        columns=_columns(storage.get("Columns", [])),
        partition_keys=_columns(item.get("PartitionKeys", [])),
        parameters=parameters,
        created=item.get("CreateTime"),
        updated=item.get("UpdateTime"),
        view_sql=_view_sql(item) if is_view else "",
        projection=parameters.get("projection.enabled", "").lower() == "true",
    )


def _targets(targets: dict[str, Any]) -> list[str]:
    paths = [t.get("Path", "") for t in targets.get("S3Targets", [])]
    paths += [f"{t.get('DatabaseName', '')}.{name}" for t in targets.get("CatalogTargets", [])
              for name in t.get("Tables", [])]
    paths += [t.get("Path", "") for t in targets.get("JdbcTargets", [])]
    paths += [path for t in targets.get("DeltaTargets", []) for path in t.get("DeltaTables", [])]
    return [p for p in paths if p]


def parse_crawler(item: dict[str, Any], metrics: dict[str, Any] | None = None) -> Crawler:
    """
    A Glue Crawler (GetCrawlers) as a Crawler, with its CrawlerMetrics (GetCrawlerMetrics) when given.
    """
    schedule = item.get("Schedule") or {}
    last = item.get("LastCrawl") or {}
    numbers = metrics or {}
    return Crawler(
        name=item.get("Name", ""),
        state=item.get("State", "READY"),
        database=item.get("DatabaseName", ""),
        targets=_targets(item.get("Targets") or {}),
        schedule=schedule.get("ScheduleExpression", ""),
        schedule_state=schedule.get("State", ""),
        last_status=last.get("Status", ""),
        last_start=last.get("StartTime"),
        last_error=last.get("ErrorMessage", ""),
        created=item.get("CreationTime"),
        tables_created=numbers.get("TablesCreated"),
        tables_updated=numbers.get("TablesUpdated"),
        tables_deleted=numbers.get("TablesDeleted"),
        last_runtime_s=numbers.get("LastRuntimeSeconds"),
        median_runtime_s=numbers.get("MedianRuntimeSeconds"),
    )


def _row_values(row: dict[str, Any]) -> list[str | None]:
    return [cell.get("VarCharValue") for cell in row.get("Data", [])]


def parse_results(pages: list[dict[str, Any]], limit: int) -> QueryResults:
    """
    GetQueryResults pages as QueryResults: the column names and types, and the rows after the header row Athena
    repeats at the top of a SELECT's results, at most `limit` (truncated when there were more).
    """
    if not pages:
        return QueryResults()
    info = (pages[0].get("ResultSet") or {}).get("ResultSetMetadata", {}).get("ColumnInfo", [])
    columns = [c.get("Label") or c.get("Name", "") for c in info]
    rows = [_row_values(r) for page in pages for r in (page.get("ResultSet") or {}).get("Rows", [])]
    if rows and rows[0] == columns:
        rows = rows[1:]
    truncated = len(rows) > limit or bool(pages[-1].get("NextToken"))
    return QueryResults(columns=columns, types=[c.get("Type", "") for c in info], rows=rows[:limit],
                        truncated=truncated)
