"""
Athena and the Glue Data Catalog: the pure functions on their own, the analyzer on fake_athena's acme (Athena and
Glue answered by checked fakes, the tables' folders in moto's S3), and the View's reports in text.
"""

import asyncio
import html as html_lib
import re
import threading
from datetime import datetime, timedelta, timezone

import pytest
from fake_athena import query_id, scene, seed_buckets
from moto import mock_aws

from aws_analyzer import athena as athenamod
from aws_analyzer._kit.errors import _Hint
from aws_analyzer._kit.view import _commands
from aws_analyzer.athena import (
    ATHENA_PRICES, AthenaAnalyzer, AthenaView, Column, Crawler, Location, Query, QueryHistory, Table, billed_bytes,
    crawler_findings, ctas_sql, date_column, failure_kind, filters_on, group_failures, normalize_sql,
    parse_table_ref, query_cost, query_shapes, run_code, sample_sql, schedule_period, selects_every_column,
    split_s3, storage_format, suggested_cutoff, table_findings, tables_in_sql,
)


GB = 1024**3

NOW = datetime(2026, 11, 7, 15, 0, tzinfo=timezone.utc)




def test_table_references_are_read_forgivingly():
    assert parse_table_ref("sales.orders") == ("sales", "orders")
    assert parse_table_ref('"Sales"."Orders"') == ("sales", "orders")
    assert parse_table_ref("AwsDataCatalog.sales.orders") == ("sales", "orders")
    assert parse_table_ref("sales/orders") == ("sales", "orders")
    assert parse_table_ref("orders", "sales") == ("sales", "orders")
    with pytest.raises(ValueError, match="database.table"):
        parse_table_ref("orders")


def test_s3_locations_split_into_bucket_and_folder():
    assert split_s3("s3://acme-raw/events") == ("acme-raw", "events/")
    assert split_s3("s3a://acme-raw/") == ("acme-raw", "")
    assert split_s3("hdfs://x/y") == ("", "")


def test_a_tables_format_in_a_word():
    assert storage_format("org.apache.hadoop.mapred.TextInputFormat",
                          "org.apache.hadoop.hive.serde2.lazy.LazySimpleSerDe", {}) == "CSV"
    assert storage_format("", "org.openx.data.jsonserde.JsonSerDe", {}) == "JSON"
    assert storage_format("x.MapredParquetInputFormat", "", {}) == "Parquet"
    assert storage_format("", "", {"table_type": "ICEBERG"}) == "Iceberg"


def test_crawler_schedules_read_as_a_period():
    assert schedule_period("cron(0 2 * * ? *)") == timedelta(days=1)
    assert schedule_period("cron(0 6 ? * MON *)") == timedelta(days=7)
    assert schedule_period("cron(0 6 1 * ? *)") == timedelta(days=31)
    assert schedule_period("cron(15 * * * ? *)") == timedelta(hours=1)
    assert schedule_period("") is None




def q(sql="SELECT 1", *, state="SUCCEEDED", scanned=0, statement="DML", **kwargs):
    return Query(id=kwargs.pop("id", query_id(1)), sql=sql, state=state, scanned_bytes=scanned,
                 statement_type=statement, submitted=kwargs.pop("submitted", NOW), **kwargs)


def test_cost_rounds_up_to_the_megabyte_with_a_10_mb_minimum():
    assert billed_bytes(q(scanned=1), ATHENA_PRICES) == 10 * 1024**2
    assert billed_bytes(q(scanned=11 * 1024**2 + 1), ATHENA_PRICES) == 12 * 1024**2
    assert query_cost(q(scanned=1024**4), ATHENA_PRICES) == pytest.approx(5.0)
    assert query_cost(q(scanned=1024**4), {**ATHENA_PRICES, "scanned_tb": 6.75}) == pytest.approx(6.75)


def test_failed_ddl_and_reused_queries_cost_nothing():
    assert query_cost(q(scanned=GB, state="FAILED"), ATHENA_PRICES) == 0
    assert query_cost(q(statement="DDL"), ATHENA_PRICES) == 0
    assert query_cost(q(scanned=GB, reused=True), ATHENA_PRICES) == 0
    assert query_cost(q(scanned=GB, state="CANCELLED"), ATHENA_PRICES) > 0


def test_a_query_with_other_values_has_the_same_shape():
    one = normalize_sql("SELECT *\nFROM t WHERE day = '2026-11-01' AND n IN (1, 2, 3) -- note")
    two = normalize_sql("select * from t where day = '2026-11-02' and n in (4,5)")
    assert one == two
    shapes = query_shapes([q("SELECT * FROM t WHERE a = 1", scanned=GB), q("SELECT * FROM t WHERE a = 2",
                                                                         scanned=GB), q("SELECT 1")],
                          ATHENA_PRICES)
    assert shapes[0].runs == 2 and shapes[0].select_star


def test_the_tables_a_query_reads():
    sql = ('WITH recent AS (SELECT * FROM "sales"."orders" WHERE dt > \'x\') SELECT * FROM recent r '
           "JOIN customers c ON r.id = c.id JOIN AwsDataCatalog.crm.leads l ON l.id = c.id")
    assert tables_in_sql(sql, "sales") == {"sales.orders", "sales.customers", "crm.leads"}
    assert tables_in_sql("SELECT * FROM orders") == set()
    assert tables_in_sql("SELECT 'from x' AS a FROM t", "db") == {"db.t"}


def test_select_star_and_partition_filters():
    assert selects_every_column("SELECT * FROM t")
    assert selects_every_column("select distinct e.* from t e")
    assert not selects_every_column("SELECT count(*) FROM t")
    assert filters_on("SELECT a FROM t WHERE dt = '2026-11-01'", ["dt"])
    assert filters_on('SELECT a FROM t WHERE "dt" > \'x\'', ["dt"])
    assert not filters_on("SELECT dt FROM t WHERE a = 1", ["dt"])
    assert not filters_on("SELECT a FROM t WHERE b = 'dt'", ["dt"])


def test_failures_are_sorted_into_kinds_with_a_fix():
    assert failure_kind("TABLE_NOT_FOUND: line 1:15: Table 'x' does not exist") == "TABLE_NOT_FOUND"
    assert failure_kind("Access Denied (Service: Amazon S3; Status Code: 403)") == "S3_ACCESS_DENIED"
    assert failure_kind("Query exhausted resources at this scale factor") == "EXHAUSTED_RESOURCES"
    assert failure_kind("something odd") == "OTHER"
    groups = group_failures([
        q(state="FAILED", error_message="HIVE_BAD_DATA: x", id=query_id(1)),
        q(state="FAILED", error_message="HIVE_BAD_DATA: y", id=query_id(2), submitted=NOW + timedelta(hours=1)),
        q(state="CANCELLED", error_message="Query cancelled! : Bytes scanned limit was exceeded", id=query_id(3)),
        q(state="CANCELLED", error_message="cancelled by user", id=query_id(4)),
        q(id=query_id(5)),
    ])
    assert [(g.kind, g.count) for g in groups] == [("HIVE_BAD_DATA", 2), ("SCAN_LIMIT", 1)]
    assert groups[0].last_id == query_id(2) and groups[0].message == "HIVE_BAD_DATA: y"
    assert "type" in groups[0].meaning and groups[0].fix


def test_a_scan_limit_above_the_largest_query():
    assert suggested_cutoff(0) == 16 * GB
    assert suggested_cutoff(220 * GB) == 512 * GB


def table(**kwargs):
    defaults = {"database": "sales", "name": "events", "location_uri": "s3://acme-raw/events/", "format": "CSV",
                "columns": [Column("ts", "string"), Column("event_date", "date")]}
    return Table(**{**defaults, **kwargs})


def test_table_findings_say_what_is_wrong_and_what_to_do():
    gone = table(location=Location("s3://x/", state="no bucket"))
    assert "doesn't exist" in table_findings(gone, [], ATHENA_PRICES)[0][1]
    unloaded = table(partition_keys=[Column("dt", "string")], partitions=0, location=Location("s3://x/", "ok"))
    assert "MSCK REPAIR TABLE" in table_findings(unloaded, [], ATHENA_PRICES)[0][1]
    projected = table(partition_keys=[Column("dt", "string")], partitions=0, projection=True)
    assert not [f for f in table_findings(projected, [], ATHENA_PRICES) if "MSCK" in f[1]]
    full = [q("SELECT * FROM sales.events", scanned=300 * GB) for _ in range(2)]
    level, text = table_findings(table(format="Parquet"), full, ATHENA_PRICES)[0]
    assert level == "warn" and "no partitions" in text and "$2.93" in text
    partitioned = table(format="Parquet", partition_keys=[Column("dt", "string")], partitions=30)
    found = table_findings(partitioned, full, ATHENA_PRICES)
    assert "didn't filter on dt" in found[0][1]


def test_crawler_findings():
    crawlers = [
        Crawler("ok", schedule="cron(0 2 * * ? *)", schedule_state="SCHEDULED", last_status="SUCCEEDED",
                last_start=NOW - timedelta(hours=3)),
        Crawler("stalled", schedule="cron(0 2 * * ? *)", schedule_state="SCHEDULED", last_status="SUCCEEDED",
                last_start=NOW - timedelta(days=9)),
        Crawler("broken", last_status="FAILED", last_start=NOW - timedelta(days=1), last_error="no access"),
        Crawler("paused", schedule="cron(0 2 * * ? *)", schedule_state="PAUSED", last_start=NOW - timedelta(days=9)),
        Crawler("never"),
    ]
    found = crawler_findings(crawlers, NOW)
    assert [f[1].split()[1] for f in found] == ["stalled", "broken's", "paused's", "never"]
    assert "aws glue start-crawler --name broken" in found[1][1]


def test_sql_to_copy():
    events = table()
    assert date_column(events) == "event_date"
    assert "partitioned_by = ARRAY['event_date']" in ctas_sql(events)
    assert "s3://acme-raw/events_parquet/" in ctas_sql(events)
    orders = table(name="orders", partition_keys=[Column("dt", "string")])
    assert "WHERE \"dt\" = '2026-11-07'" in sample_sql(orders, ["2026-11-07"])
    code = run_code('SELECT """x"""', database="sales", workgroup="analysts", region="eu-west-1")
    compile(code, "run_code", "exec")
    assert 'WorkGroup="analysts"' in code and 'region_name="eu-west-1"' in code




@pytest.fixture
def aws():
    with mock_aws():
        yield


@pytest.fixture
def fakes(aws):
    now = datetime.now(timezone.utc)
    seed_buckets(now)
    return scene(now)


@pytest.fixture
def core(fakes):
    athena, glue = fakes
    analyzer = AthenaAnalyzer(region="us-east-1", clients={"athena": athena, "glue": glue})
    analyzer.max_workers = 1
    return analyzer


def test_history_reads_every_workgroup_and_stops_at_since(core, fakes):
    history = core.history("7d")
    assert set(history.workgroups) == {"primary", "analysts", "legacy-etl"}
    assert history.queries == sorted(history.queries, key=lambda x: x.submitted, reverse=True)
    assert all(x.submitted >= history.since for x in history.queries)
    short = core.history("6h", workgroup="primary")
    assert short.workgroups == ["primary"] and all(x.workgroup == "primary" for x in short.queries)
    assert len(fakes[0].called("list_query_executions")) >= 1


def test_history_says_when_it_stopped_at_the_limit(core):
    history = core.history("7d", workgroup="primary", limit=5)
    assert len(history.queries) == 5 and history.truncated


def test_one_query_with_its_saved_results(core):
    newest = core.history("7d", workgroup="primary").queries
    orders = next(x for x in newest if x.sql.startswith("SELECT dt"))
    detail = core.query(f"https://console.aws.amazon.com/athena/home#/query-editor/history/{orders.id}")
    assert detail.results.columns == ["dt", "orders", "revenue"]
    assert detail.results.rows[0][1] == "1842" and detail.rows_out == 1


def test_a_query_whose_results_are_gone_says_so(core):
    dashboard = next(x for x in core.history("7d", workgroup="primary").queries if "events" in x.sql)
    detail = core.query(dashboard.id)
    assert detail.results is None and detail.errors == {"results": "InvalidRequestException"}


def test_catalog_checks_each_tables_folder(core):
    catalog = core.catalog()
    states = {t.full_name: t.location.state for t in catalog.tables if t.location}
    assert states["sales.returns"] == "empty"
    assert states["marketing.campaigns"] == "no bucket"
    assert states["sales.orders"] == "ok"
    tables = {t.full_name: t for t in catalog.tables}
    assert tables["sales.clicks"].partitions == 0 and tables["sales.orders"].partitions == 1
    assert tables["sales.daily_revenue"].is_view and "GROUP BY dt" in tables["sales.daily_revenue"].view_sql
    assert catalog.checked == 6 and catalog.s3_requests == 6


def test_one_table_counts_partitions_and_lists_its_folder(core):
    orders = core.table("sales.orders")
    assert orders.partitions == 30 and orders.partitions_complete
    assert orders.location.files == 30 and orders.location.complete
    assert len(orders.newest_partitions) == 3
    capped = core.table("sales.orders", partition_limit=10, file_limit=5)
    assert capped.partitions == 10 and not capped.partitions_complete and not capped.location.complete


def test_an_unknown_table_names_the_closest(core):
    with pytest.raises(_Hint, match="sales.orders"):
        core.table("sales.ordrs")
    with pytest.raises(_Hint, match="No database"):
        core.table("nowhere.x")


def test_a_denied_part_is_recorded_not_raised(core, fakes):
    fakes[1].denied.add("get_crawlers")
    catalog = core.catalog()
    assert catalog.errors == {"crawlers": "AccessDeniedException"} and catalog.tables


def test_nothing_writes(core, fakes):
    core.overview()
    core.table_report("sales.events")
    for fake in fakes:
        operations = {name for name, _ in fake.calls}
        assert all(op.startswith(("list_", "get_", "batch_get_")) for op in operations), operations


def test_prices_given_to_one_analyzer_change_no_other(fakes):
    athena, glue = fakes
    mine = AthenaAnalyzer(region="us-east-1", clients={"athena": athena, "glue": glue}, prices={"scanned_tb": 6.0})
    assert mine.prices["scanned_tb"] == 6.0 and ATHENA_PRICES["scanned_tb"] == 5.0




def run(capsys, fn, *args, **kwargs):
    fn(*args, **kwargs)
    return capsys.readouterr().out


@pytest.fixture
def ui(core):
    return AthenaView(core, mode="text", progress="off", max_rows=0)


def test_help_lists_every_command(ui, capsys):
    assert set(_commands(AthenaView)) == {n for names in AthenaView._GROUPS.values() for n in names}
    out = run(capsys, ui.help)
    assert "overview()" in out and "what the last 7 days of queries scanned and cost" in out


def test_overview_puts_the_answer_first(ui, capsys):
    out = run(capsys, ui.overview)
    assert out.index("Cost · 7d") < out.index("Findings") < out.index("Workgroups")
    assert "Failed: 8 (15%) (!)" in out
    assert "The same query ran" in out and "query result reuse" in out
    assert "BytesScannedCutoffPerQuery=549755813888" in out
    assert "marketing.campaigns points at a bucket that doesn't exist" in out
    assert "sales.clicks is partitioned by dt but the catalog has no partitions" in out
    assert "aws glue start-crawler --name events-crawler" in out
    assert "Athena engine version 2" in out
    assert "failures()" in out


def test_queries_filters_by_status_and_database(ui, capsys):
    out = run(capsys, ui.queries, status="failed")
    assert "succeeded" not in out.split("Query ID")[1] and "TABLE_NOT_FOUND" in out
    assert "status= is 'failed'" in run(capsys, ui.queries, status="weird")
    assert "Queries: 0" in run(capsys, ui.queries, database="nowhere")


def test_expensive_adds_up_a_querys_runs(ui, capsys):
    out = run(capsys, ui.expensive)
    shapes = out.split("By kind of query")[1]
    first = shapes.splitlines()[3]
    assert "SELECT * FROM sales.events" in first and "$22.56" in first
    assert "$5.00 per TB" in out and "table_info('sales.events')" in out


def test_failures_explain_each_kind(ui, capsys):
    out = run(capsys, ui.failures)
    assert out.index("TABLE_NOT_FOUND") < out.index("HIVE_BAD_DATA") < out.index("SCAN_LIMIT")
    assert "use TRY_CAST" not in out and "doesn't fit its column's type" in out


def test_one_query_report(ui, capsys, core):
    failed = next(x for x in core.history("7d", workgroup="analysts").queries if x.failed)
    out = run(capsys, ui.query, failed.id)
    assert "State: failed (!)" in out and "It failed with" in out and "Athena's message" in out
    orders = next(x for x in core.history("7d", workgroup="primary").queries if x.sql.startswith("SELECT dt"))
    out = run(capsys, ui.query, orders.id)
    assert "Its saved results" in out and "96210.45" in out and "query_code(" in out


def test_catalog_report(ui, capsys):
    out = run(capsys, ui.catalog)
    assert "Empty or gone: 2 (!)" in out and "no bucket" in out and "dt (projected)" in out
    assert "Crawlers" in out and "weekly · paused" in out
    assert "sales" in run(capsys, ui.catalog, "sales")
    assert "Did you mean 'sales'" in run(capsys, ui.catalog, "sale")


def test_table_info_shows_its_queries_and_the_sql_to_copy(ui, capsys):
    out = run(capsys, ui.table_info, "sales.events")
    assert "Partitions: none (!)" in out and "(the SQL is below)" in out
    assert "The queries that read it" in out
    assert "CREATE TABLE \"sales\".\"events_parquet\"" in out
    clicks = run(capsys, ui.table_info, "sales.clicks")
    assert "MSCK REPAIR TABLE \"sales\".\"clicks\"" in clicks and "Partitions: 0 (!)" in clicks
    assert "Did you mean 'sales.orders'" in run(capsys, ui.table_info, "sales.ordrs")


def test_crawlers_report(ui, capsys):
    out = run(capsys, ui.crawlers)
    assert "Last crawl failed: 1 (!)" in out and "clicks-crawler is scheduled" in out


def test_query_code_never_runs_a_query(ui, capsys, fakes):
    out = run(capsys, ui.query_code, "SELECT * FROM orders", database="sales")
    assert "start_query_execution" in out and "doesn't filter on the partition keys of sales.orders" in out
    assert "Reads at most" in out
    assert all(op.startswith(("list_", "get_", "batch_get_")) for op, _ in fakes[0].calls + fakes[1].calls)


def test_errors_become_notes(ui, capsys, fakes):
    out = run(capsys, ui.query, "not-an-id")
    assert "isn't a query ID" in out and "Traceback" not in out
    out = run(capsys, ui.query, query_id(99_999))
    assert "45 days of query history" in out
    fakes[0].denied.add("list_work_groups")
    out = run(capsys, ui.overview)
    assert "workgroups couldn't be read (AccessDeniedException; needs athena:ListWorkGroups)" in out


def test_an_empty_region(aws, capsys):
    athena, glue = scene(datetime.now(timezone.utc))
    athena.queries = []
    glue.tables, glue.crawlers = {}, []
    view = AthenaView(AthenaAnalyzer(region="us-east-1", clients={"athena": athena, "glue": glue}), mode="text",
                      progress="off")
    out = run(capsys, view.catalog)
    assert "No tables in us-east-1" in out
    assert "Queries: 0" in run(capsys, view.queries)


def test_the_module_shows_html(core):
    view = AthenaView(core, mode="html", progress="off")
    html = view._render_html(view._overview_blocks(core.overview()), 50)
    assert 'class="ath' in html and "🏛️ Athena" in html


def test_history_to_df(core):
    pytest.importorskip("pandas")
    frame = core.history("7d").to_df()
    assert {"sql", "scanned_bytes", "state"} <= set(frame.columns)


def test_module_has_its_public_names():
    for name in athenamod.__all__:
        assert hasattr(athenamod, name)
    assert QueryHistory(NOW, NOW).queries == []


def test_a_client_error_inside_a_report_is_a_note(ui, capsys, fakes):
    fakes[1].denied.add("get_table")
    out = run(capsys, ui.table_info, "sales.orders")
    assert "AccessDeniedException" in out and "Traceback" not in out


def explorer(core, what=None, *, mode="widgets", **kwargs):
    return athenamod.AthenaExplorer(what, core=core, mode=mode, **kwargs)


def plain(html):
    return re.sub(r"<[^>]+>", "", html_lib.unescape(html or ""))


def titles(blocks):
    return [b.text for b in blocks if type(b).__name__ == "_Title"]


def test_explorer_opens_on_the_overview_with_every_tab_filled(core):
    x = explorer(core)
    assert x._tab == "overview" and titles(x.shown["overview"]) == ["Athena in us-east-1"]
    assert len(x.query_list.keys) == len(x.history.queries)
    assert x.cost_list.keys[0] == x.cost_list.selected and titles(x.shown["costs"])[0].startswith("Query ")
    assert x.failure_list.keys and titles(x.shown["failures"])[0].startswith("Query ")
    assert set(x.table_list.keys) == {t.full_name for t in x.catalog.tables}
    assert x.crawler_list.keys[0] != "orders-crawler"
    stats = plain(x.stats.value)
    assert "Failed8" in stats.replace(" ", "") and "Table warnings" in stats
    assert x.focus.kind == "overview" and x.focus.chips() == ["Overview tab", "last 7 days"]


def test_explorer_queries_search_chips_and_a_click(core):
    x = explorer(core)
    x._show_tab("queries")
    page = x.query_list
    page.find.value = "sales.order"
    sqls = {q.id: q.sql for q in x.history.queries}
    assert page.keys and all("sales.order" in sqls[key] for key in page.keys)
    page.find.value = ""
    failed = next(b for b in page.chips_box.children if b.description.startswith("Failed"))
    failed.click()
    assert page.chip == "failed" and len(page.keys) == 8
    page.rows[0].button.click()
    assert x.query_id == page.keys[0] and titles(x.shown["queries"]) == [f"Query {page.keys[0][:8]}"]
    assert x.focus.kind == "query" and x.focus.value("query_id") == page.keys[0]
    failed.click()
    assert page.chip == ""


def test_explorer_workgroup_and_time_range(core):
    x = explorer(core)
    every = len(x._queries_shown())
    x.workgroup_pick.value = "analysts"
    assert 0 < len(x._queries_shown()) < every and all(q.workgroup == "analysts" for q in x._queries_shown())
    assert "workgroup analysts" in x.focus.chips()
    x.since_pick.value = "24h"
    assert x.history.since > datetime.now(timezone.utc) - timedelta(hours=25)
    assert "last 24 hours" in x.focus.chips()


def test_explorer_catalog_and_crawlers(core):
    x = explorer(core)
    x._show_tab("catalog")
    page = x.table_list
    fix = next(b for b in page.chips_box.children if b.description.startswith("To fix"))
    fix.click()
    assert "sales.clicks" in page.keys and "marketing.leads" not in page.keys
    clicks = page.rows[page.keys.index("sales.clicks")]
    clicks.button.click()
    assert x.table_name == "sales.clicks" and x.tables["sales.clicks"].partitions == 0
    assert "the catalog has no partitions" in plain(x.table_list.view.value)
    assert x.focus.kind == "table" and x.focus.value("table") == "sales.clicks"
    x._show_tab("crawlers")
    events = x.crawler_list.rows[x.crawler_list.keys.index("events-crawler")]
    events.button.click()
    shown = plain(x.crawler_list.view.value)
    assert "Crawler events-crawler" in shown and "not authorized to perform: s3:GetObject" in shown
    assert x.focus.kind == "crawler"


def test_explorer_commands_and_mistakes(core, fakes):
    x = explorer(core, "sales.events")
    assert x._tab == "catalog" and x.table_name == "sales.events"
    first = x.history.queries[0].id
    x.query(first)
    assert x._tab == "queries" and x.query_list.selected == first
    x.query("not an id")
    assert "isn't a query ID" in plain(x.status.value)
    x.table("sales.ordrs")
    assert "Did you mean 'sales.orders'" in plain(x.table_list.view.value)
    listed = len(fakes[0].called("list_work_groups"))
    x.refresh()
    assert len(fakes[0].called("list_work_groups")) > listed and x.history is not None
    with pytest.raises(ValueError, match="tab is one of"):
        explorer(core, tab="graphs")


def test_explorer_window_text_says_where_the_window_shows_it():
    from aws_analyzer.athena.explorer.frame import _window_text

    assert _window_text("see query('a1b2')") == "see the Queries tab"
    assert _window_text("table_info('sales.events') shows it") == "The Catalog tab shows it"
    assert "query_code(" in _window_text("run query_code('SELECT 1')")


def test_explorer_reads_in_the_background(core, fakes):
    athena, _ = fakes
    hold = athena.holds["list_query_executions"] = threading.Event()

    async def settle(x):
        while x._tasks:
            await asyncio.gather(*list(x._tasks.values()))

    async def main():
        x = explorer(core)
        assert {"history", "catalog"} <= set(x._tasks) and x.history is None
        assert "Reading Athena" in plain(x.status.value)
        hold.set()
        await settle(x)
        assert x.history is not None and x.catalog is not None and titles(x.shown["overview"])
        assert x.cost_list.selected in x.details

    asyncio.run(main())


def test_explorer_without_widgets_shows_the_reports(core, capsys):
    explorer(core, mode="text")
    assert "Athena in us-east-1" in capsys.readouterr().out
    view = AthenaView(core, mode="text", progress="off")
    assert "needs Jupyter" in run(capsys, view.explore)
