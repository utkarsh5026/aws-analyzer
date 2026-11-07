"""
acme's Athena and Glue Data Catalog, for the tests, the demo and the guide's figures: FakeAthena (workgroups and a
week of query history, with saved results for one query) and FakeGlue (databases, tables, partitions, crawlers),
every request and answer checked against the service model (moto's Athena has no history, failures or statistics),
and seed_buckets(), the tables' S3 folders in moto.

    primary      a dashboard's SELECT * on sales.events (CSV, no partitions) three times a day: most of the bill
    analysts     a 100 GB scan limit; a GROUP BY over sales.orders that doesn't filter on dt, and the failures
                 (TABLE_NOT_FOUND, HIVE_BAD_DATA, COLUMN_NOT_FOUND, exhausted resources, one stopped by the limit)
    legacy-etl   Athena engine version 2, no result location; MSCK REPAIR TABLE every night

    sales.events     CSV, no partitions, read in full by the dashboard
    sales.orders     Parquet, partitioned by dt, 30 partitions
    sales.clicks     JSON, partitioned by dt, files in S3 but no partitions in the catalog
    sales.returns    Parquet, its folder empty
    sales.daily_revenue   a view
    marketing.campaigns   CSV, in a bucket that no longer exists
    marketing.leads       Parquet, partition projection

    orders-crawler (daily, fine), clicks-crawler (daily, stopped 19 days ago), events-crawler (failed: S3 access),
    campaigns-crawler (schedule paused)
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

import boto3
from botocore.exceptions import ClientError
from fake_stepfunctions import _Checked


REGION = "us-east-1"

GB = 1024**3

RESULTS = "s3://acme-athena-results"

PARQUET = ("org.apache.hadoop.hive.ql.io.parquet.MapredParquetInputFormat",
           "org.apache.hadoop.hive.ql.io.parquet.serde.ParquetHiveSerDe")

CSV = ("org.apache.hadoop.mapred.TextInputFormat", "org.apache.hadoop.hive.serde2.lazy.LazySimpleSerDe")

JSON = ("org.apache.hadoop.mapred.TextInputFormat", "org.openx.data.jsonserde.JsonSerDe")

DASHBOARD = ("SELECT * FROM sales.events WHERE event_type = 'purchase' AND ts > '{day}'")

BY_CUSTOMER = "SELECT customer_id, sum(total) AS spent FROM sales.orders GROUP BY customer_id ORDER BY spent DESC"

ORDERS_TODAY = "SELECT dt, count(*) AS orders, sum(total) AS revenue FROM orders WHERE dt = '{day}' GROUP BY dt"


def query_id(number: int) -> str:
    return f"{number:08x}-0000-4000-8000-{number:012x}"


@dataclass
class FakeQuery:
    """
    One past query and how it ended.
    """

    number: int
    sql: str
    workgroup: str
    submitted: datetime
    state: str = "SUCCEEDED"
    scanned: int = 0
    seconds: float = 2.0
    database: str = "sales"
    statement: str = "DML"
    error: str = ""
    reused: bool = False

    @property
    def id(self) -> str:
        return query_id(self.number)

    def item(self, engines: dict[str, str]) -> dict[str, Any]:
        status: dict[str, Any] = {"State": self.state, "SubmissionDateTime": self.submitted,
                                  "CompletionDateTime": self.submitted + timedelta(seconds=self.seconds)}
        if self.error:
            status["StateChangeReason"] = self.error
            status["AthenaError"] = {"ErrorCategory": 2, "ErrorType": 1006, "Retryable": False,
                                     "ErrorMessage": self.error}
        engine = engines.get(self.workgroup, "Athena engine version 3")
        item = {
            "QueryExecutionId": self.id, "Query": self.sql, "StatementType": self.statement, "WorkGroup": self.workgroup,
            "QueryExecutionContext": {"Database": self.database, "Catalog": "AwsDataCatalog"},
            "ResultConfiguration": {"OutputLocation": f"{RESULTS}/{self.workgroup}/{self.id}.csv"},
            "Status": status,
            "Statistics": {"EngineExecutionTimeInMillis": int(self.seconds * 900),
                           "DataScannedInBytes": self.scanned, "TotalExecutionTimeInMillis": int(self.seconds * 1000),
                           "QueryQueueTimeInMillis": int(self.seconds * 50),
                           "ResultReuseInformation": {"ReusedPreviousResult": self.reused}},
            "EngineVersion": {"SelectedEngineVersion": "AUTO", "EffectiveEngineVersion": engine},
        }
        return item


WORKGROUPS = {
    "primary": {"output": f"{RESULTS}/primary/", "cutoff": None, "engine": "Athena engine version 3"},
    "analysts": {"output": f"{RESULTS}/analysts/", "cutoff": 100 * GB, "engine": "Athena engine version 3",
                 "enforced": True},
    "legacy-etl": {"output": "", "cutoff": None, "engine": "Athena engine version 2"},
}


def history(now: datetime) -> list[FakeQuery]:
    """
    A week of queries across the three workgroups.
    """
    queries: list[FakeQuery] = []

    def add(**kwargs: Any) -> FakeQuery:
        query = FakeQuery(number=len(queries) + 1, **kwargs)
        queries.append(query)
        return query

    for days_ago in range(7):
        day = (now - timedelta(days=days_ago)).date().isoformat()
        for hours_ago in (1, 6, 11):
            add(sql=DASHBOARD.format(day=day), workgroup="primary", scanned=220 * GB, seconds=74.0,
                submitted=now - timedelta(days=days_ago, hours=hours_ago))
        add(sql=ORDERS_TODAY.format(day=day), workgroup="primary",
            submitted=now - timedelta(days=days_ago, hours=2), scanned=int(1.2 * GB), seconds=3.1)
        add(sql="MSCK REPAIR TABLE sales.orders", workgroup="legacy-etl", statement="DDL",
            submitted=now - timedelta(days=days_ago, hours=5), seconds=12.0)
    for days_ago in (1, 2, 3, 5, 6):
        add(sql=BY_CUSTOMER, workgroup="analysts", submitted=now - timedelta(days=days_ago, hours=3),
            scanned=85 * GB, seconds=41.0)
    add(sql=BY_CUSTOMER, workgroup="analysts", submitted=now - timedelta(days=4, hours=1), reused=True, seconds=0.4)
    failures = [
        ("SELECT * FROM sales.order LIMIT 10", "TABLE_NOT_FOUND: line 1:15: Table 'awsdatacatalog.sales.order' "
                                                "does not exist", 3),
        ("SELECT * FROM sales.order LIMIT 10", "TABLE_NOT_FOUND: line 1:15: Table 'awsdatacatalog.sales.order' "
                                                "does not exist", 26),
        ("SELECT * FROM sales.order WHERE dt = '2026-11-01'", "TABLE_NOT_FOUND: line 1:15: Table "
                                                              "'awsdatacatalog.sales.order' does not exist", 50),
        ("SELECT user_id, amount FROM sales.events", "HIVE_BAD_DATA: Error parsing field value 'n/a' for field 4: "
                                                      "For input string: \"n/a\"", 8),
        ("SELECT user_id, amount FROM sales.events", "HIVE_BAD_DATA: Error parsing field value 'n/a' for field 4: "
                                                      "For input string: \"n/a\"", 30),
        ("SELECT customer, total FROM sales.orders WHERE dt = '2026-11-03'",
         "COLUMN_NOT_FOUND: line 1:8: Column 'customer' cannot be resolved or requester is not authorized to access "
         "requested resources", 70),
        ("SELECT o.*, e.* FROM sales.orders o JOIN sales.events e ON o.customer_id = e.user_id ORDER BY o.total",
         "Query exhausted resources at this scale factor", 100),
    ]
    for sql, error, hours_ago in failures:
        add(sql=sql, workgroup="analysts", state="FAILED", error=error, seconds=1.4,
            submitted=now - timedelta(hours=hours_ago))
    add(sql="SELECT * FROM sales.events", workgroup="analysts", state="CANCELLED", scanned=100 * GB,
        error="Query cancelled! : Bytes scanned limit was exceeded", submitted=now - timedelta(hours=40),
        seconds=33.0)
    for hours_ago in (6, 30, 54):
        add(sql="SELECT * FROM sales.daily_revenue LIMIT 100", workgroup="analysts",
            submitted=now - timedelta(hours=hours_ago), scanned=5 * 1024**2, seconds=1.9)
    return queries


class FakeAthena(_Checked):
    """
    Athena's read operations over `queries` and WORKGROUPS; the newest ORDERS_TODAY run has saved results.
    """

    service = "athena"

    def __init__(self, queries: list[FakeQuery], region: str = REGION):
        super().__init__(region)
        self.queries = queries
        self.engines = {name: spec["engine"] for name, spec in WORKGROUPS.items()}
        self.created = datetime(2024, 3, 1, tzinfo=timezone.utc)

    def _list_work_groups(self, **params: Any) -> dict[str, Any]:
        return {"WorkGroups": [{"Name": name, "State": "ENABLED", "CreationTime": self.created,
                                "EngineVersion": {"SelectedEngineVersion": spec["engine"],
                                                  "EffectiveEngineVersion": spec["engine"]}}
                               for name, spec in WORKGROUPS.items()]}

    def _get_work_group(self, WorkGroup: str) -> dict[str, Any]:
        spec = WORKGROUPS.get(WorkGroup)
        if spec is None:
            raise ClientError({"Error": {"Code": "InvalidRequestException",
                                         "Message": f"WorkGroup {WorkGroup} is not found."}}, "GetWorkGroup")
        config: dict[str, Any] = {"EnforceWorkGroupConfiguration": spec.get("enforced", False),
                                  "PublishCloudWatchMetricsEnabled": True,
                                  "EngineVersion": {"SelectedEngineVersion": spec["engine"],
                                                    "EffectiveEngineVersion": spec["engine"]}}
        if spec["output"]:
            config["ResultConfiguration"] = {"OutputLocation": spec["output"]}
        if spec["cutoff"]:
            config["BytesScannedCutoffPerQuery"] = spec["cutoff"]
        return {"WorkGroup": {"Name": WorkGroup, "State": "ENABLED", "Configuration": config,
                              "CreationTime": self.created}}

    def _list_query_executions(self, WorkGroup: str = "primary", MaxResults: int = 50,
                               NextToken: str | None = None) -> dict[str, Any]:
        mine = sorted((q for q in self.queries if q.workgroup == WorkGroup), key=lambda q: q.submitted, reverse=True)
        begin = int(NextToken or 0)
        page = mine[begin: begin + MaxResults]
        answer: dict[str, Any] = {"QueryExecutionIds": [q.id for q in page]} if page else {}
        if begin + MaxResults < len(mine):
            answer["NextToken"] = str(begin + MaxResults)
        return answer

    def _find(self, wanted: str, operation: str) -> FakeQuery:
        found = next((q for q in self.queries if q.id == wanted), None)
        if found is None:
            raise ClientError({"Error": {"Code": "InvalidRequestException",
                                         "Message": f"QueryExecution {wanted} was not found"}}, operation)
        return found

    def _batch_get_query_execution(self, QueryExecutionIds: list[str]) -> dict[str, Any]:
        found = [q for q in self.queries if q.id in set(QueryExecutionIds)]
        return {"QueryExecutions": [q.item(self.engines) for q in found], "UnprocessedQueryExecutionIds": []}

    def _get_query_execution(self, QueryExecutionId: str) -> dict[str, Any]:
        return {"QueryExecution": self._find(QueryExecutionId, "GetQueryExecution").item(self.engines)}

    def _get_query_runtime_statistics(self, QueryExecutionId: str) -> dict[str, Any]:
        query = self._find(QueryExecutionId, "GetQueryRuntimeStatistics")
        rows = {"InputRows": query.scanned // 120, "InputBytes": query.scanned, "OutputBytes": 512,
                "OutputRows": 0 if query.state != "SUCCEEDED" else 1 if query.sql.startswith("SELECT dt") else 1000}
        return {"QueryRuntimeStatistics": {"Rows": rows}}

    def _get_query_results(self, QueryExecutionId: str, MaxResults: int = 1000,
                           NextToken: str | None = None) -> dict[str, Any]:
        query = self._find(QueryExecutionId, "GetQueryResults")
        names = ["dt", "orders", "revenue"]
        if not query.sql.startswith("SELECT dt"):
            raise ClientError({"Error": {"Code": "InvalidRequestException", "Message": "Unable to read the query's "
                                         "results: the result file was not found in S3"}}, "GetQueryResults")
        day = query.sql.split("'")[1]
        rows = [names, [day, "1842", "96210.45"]]
        return {"ResultSet": {
            "Rows": [{"Data": [{"VarCharValue": value} for value in row]} for row in rows],
            "ResultSetMetadata": {"ColumnInfo": [{"Name": n, "Label": n, "Type": t}
                                                 for n, t in zip(names, ("varchar", "bigint", "double"))]}}}


def _table(database: str, name: str, columns: list[tuple[str, str]], *, kind: tuple[str, str] | None,
           location: str = "", keys: tuple[str, ...] = (), parameters: dict[str, str] | None = None,
           updated: datetime) -> dict[str, Any]:
    item: dict[str, Any] = {"Name": name, "DatabaseName": database, "TableType": "EXTERNAL_TABLE",
                            "CreateTime": updated - timedelta(days=200), "UpdateTime": updated,
                            "PartitionKeys": [{"Name": k, "Type": "string"} for k in keys],
                            "Parameters": dict(parameters or {})}
    storage: dict[str, Any] = {"Columns": [{"Name": n, "Type": t} for n, t in columns]}
    if kind is not None:
        storage.update({"Location": location, "InputFormat": kind[0],
                        "SerdeInfo": {"SerializationLibrary": kind[1]}})
    item["StorageDescriptor"] = storage
    return item


def tables(now: datetime) -> dict[str, list[dict[str, Any]]]:
    """
    The catalog's tables by database.
    """
    view = {"originalSql": "SELECT dt, sum(total) AS revenue FROM sales.orders GROUP BY dt", "catalog":
            "awsdatacatalog", "schema": "sales", "columns": [{"name": "dt", "type": "varchar"}]}
    revenue = _table("sales", "daily_revenue", [("dt", "string"), ("revenue", "double")], kind=None,
                     updated=now - timedelta(days=90))
    revenue["TableType"] = "VIRTUAL_VIEW"
    revenue["ViewOriginalText"] = f"/* Presto View: {base64.b64encode(json.dumps(view).encode()).decode()} */"
    revenue["ViewExpandedText"] = "/* Presto View */"
    return {
        "sales": [
            _table("sales", "events", [("ts", "string"), ("user_id", "string"), ("event_type", "string"),
                                       ("page", "string"), ("amount", "double"), ("event_date", "date")],
                   kind=CSV, location="s3://acme-raw/events/", parameters={"classification": "csv"},
                   updated=now - timedelta(days=3)),
            _table("sales", "orders", [("order_id", "string"), ("customer_id", "string"), ("total", "double"),
                                       ("items", "int")], kind=PARQUET, location="s3://acme-lake/orders/",
                   keys=("dt",), parameters={"classification": "parquet"}, updated=now - timedelta(hours=1)),
            _table("sales", "clicks", [("ts", "string"), ("user_id", "string"), ("url", "string")], kind=JSON,
                   location="s3://acme-raw/clicks/", keys=("dt",), parameters={"classification": "json"},
                   updated=now - timedelta(days=19)),
            _table("sales", "returns", [("order_id", "string"), ("reason", "string")], kind=PARQUET,
                   location="s3://acme-lake/returns/", parameters={"classification": "parquet"},
                   updated=now - timedelta(days=40)),
            revenue,
        ],
        "marketing": [
            _table("marketing", "campaigns", [("campaign_id", "string"), ("name", "string"), ("budget", "double")],
                   kind=CSV, location="s3://acme-old-marketing/campaigns/", parameters={"classification": "csv"},
                   updated=now - timedelta(days=400)),
            _table("marketing", "leads", [("lead_id", "string"), ("email_domain", "string")], kind=PARQUET,
                   location="s3://acme-lake/leads/", keys=("dt",),
                   parameters={"classification": "parquet", "projection.enabled": "true",
                               "projection.dt.type": "date", "projection.dt.range": "2025-01-01,NOW"},
                   updated=now - timedelta(days=12)),
        ],
    }


def crawlers(now: datetime) -> list[dict[str, Any]]:
    def crawler(name: str, path: str, *, schedule: str = "", state: str = "SCHEDULED", status: str = "SUCCEEDED",
                ago: timedelta, error: str = "") -> dict[str, Any]:
        item: dict[str, Any] = {"Name": name, "Role": f"arn:aws:iam::123456789012:role/glue-{name}",
                                "Targets": {"S3Targets": [{"Path": path}]}, "DatabaseName": "sales",
                                "State": "READY", "CreationTime": now - timedelta(days=300),
                                "LastCrawl": {"Status": status, "StartTime": now - ago}}
        if error:
            item["LastCrawl"]["ErrorMessage"] = error
        if schedule:
            item["Schedule"] = {"ScheduleExpression": schedule, "State": state}
        return item

    campaigns = crawler("campaigns-crawler", "s3://acme-old-marketing/campaigns/", schedule="cron(0 6 ? * MON *)",
                        state="PAUSED", ago=timedelta(days=60))
    campaigns["DatabaseName"] = "marketing"
    return [
        crawler("orders-crawler", "s3://acme-lake/orders/", schedule="cron(0 2 * * ? *)", ago=timedelta(hours=1)),
        crawler("clicks-crawler", "s3://acme-raw/clicks/", schedule="cron(30 1 * * ? *)", ago=timedelta(days=19)),
        crawler("events-crawler", "s3://acme-raw/events/", status="FAILED", ago=timedelta(days=3),
                error="Service Principal: glue.amazonaws.com is not authorized to perform: s3:GetObject on "
                      "resource: arn:aws:s3:::acme-raw/events/2026/part-0001.csv"),
        campaigns,
    ]


@dataclass
class FakeGlue(_Checked):
    """
    The Glue Data Catalog's read operations over tables(now), 30 daily partitions of sales.orders, and
    crawlers(now) with their metrics.
    """

    now: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    service = "glue"

    def __post_init__(self) -> None:
        super().__init__(REGION)
        self.tables = tables(self.now)
        self.crawlers = crawlers(self.now)
        self.partitions = {("sales", "orders"): [[(self.now - timedelta(days=d)).date().isoformat()]
                                                 for d in range(30)]}

    def _get_databases(self, **params: Any) -> dict[str, Any]:
        return {"DatabaseList": [{"Name": name, "CreateTime": self.now - timedelta(days=400)}
                                 for name in self.tables]}

    def _get_database(self, Name: str, **params: Any) -> dict[str, Any]:
        if Name not in self.tables:
            raise ClientError({"Error": {"Code": "EntityNotFoundException",
                                         "Message": f"Database {Name} not found."}}, "GetDatabase")
        return {"Database": {"Name": Name, "CreateTime": self.now - timedelta(days=400)}}

    def _get_tables(self, DatabaseName: str, **params: Any) -> dict[str, Any]:
        if DatabaseName not in self.tables:
            raise ClientError({"Error": {"Code": "EntityNotFoundException",
                                         "Message": f"Database {DatabaseName} not found."}}, "GetTables")
        return {"TableList": self.tables[DatabaseName]}

    def _get_table(self, DatabaseName: str, Name: str, **params: Any) -> dict[str, Any]:
        found = next((t for t in self.tables.get(DatabaseName, []) if t["Name"] == Name), None)
        if found is None:
            raise ClientError({"Error": {"Code": "EntityNotFoundException",
                                         "Message": f"Table {Name} not found."}}, "GetTable")
        return {"Table": found}

    def _get_partitions(self, DatabaseName: str, TableName: str, NextToken: str | None = None,
                        MaxResults: int = 20, **params: Any) -> dict[str, Any]:
        values = self.partitions.get((DatabaseName, TableName), [])
        begin = int(NextToken or 0)
        page = values[begin: begin + MaxResults]
        answer: dict[str, Any] = {"Partitions": [{"Values": v, "DatabaseName": DatabaseName,
                                                  "TableName": TableName} for v in page]}
        if begin + MaxResults < len(values):
            answer["NextToken"] = str(begin + MaxResults)
        return answer

    def _get_crawlers(self, **params: Any) -> dict[str, Any]:
        return {"Crawlers": self.crawlers}

    def _get_crawler_metrics(self, CrawlerNameList: list[str], **params: Any) -> dict[str, Any]:
        numbers = {"orders-crawler": (0, 1, 0, 64.0), "clicks-crawler": (0, 0, 0, 41.0),
                   "events-crawler": (0, 0, 0, 12.0), "campaigns-crawler": (0, 1, 0, 30.0)}
        metrics = []
        for name in CrawlerNameList:
            made, changed, dropped, seconds = numbers.get(name, (0, 0, 0, 10.0))
            metrics.append({"CrawlerName": name, "TablesCreated": made, "TablesUpdated": changed,
                            "TablesDeleted": dropped, "LastRuntimeSeconds": seconds,
                            "MedianRuntimeSeconds": seconds})
        return {"CrawlerMetricsList": metrics}


def seed_buckets(now: datetime) -> None:
    """
    The tables' folders in moto's S3 (call inside mock_aws): events' CSV files, orders' daily Parquet files,
    clicks' daily JSON files (which the catalog has no partitions for), leads' files and an empty returns folder.
    acme-old-marketing isn't made.
    """
    s3 = boto3.client("s3", region_name=REGION)
    for bucket in ("acme-raw", "acme-lake", "acme-athena-results"):
        s3.create_bucket(Bucket=bucket)
    for month in range(1, 13):
        s3.put_object(Bucket="acme-raw", Key=f"events/2026/part-{month:04d}.csv", Body=b"ts,user_id\n" * 400)
    for days_ago in range(30):
        day = (now - timedelta(days=days_ago)).date().isoformat()
        s3.put_object(Bucket="acme-lake", Key=f"orders/dt={day}/part-0000.parquet", Body=b"PAR1" + b"0" * 2048)
        s3.put_object(Bucket="acme-raw", Key=f"clicks/dt={day}/clicks.json", Body=b'{"url": "/"}\n' * 50)
    s3.put_object(Bucket="acme-lake", Key="returns/", Body=b"")
    s3.put_object(Bucket="acme-lake", Key="leads/dt=2026-11-01/part-0000.parquet", Body=b"PAR1" + b"0" * 512)


def scene(now: datetime) -> tuple[FakeAthena, FakeGlue]:
    """
    The fake Athena and Glue clients, at `now`.
    """
    return FakeAthena(history(now)), FakeGlue(now=now)
