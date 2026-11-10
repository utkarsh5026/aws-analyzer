---
title: Athena Analyzer Guide
description: "How to see what your Athena queries cost and why they fail, and find the Glue tables and crawlers to fix, from a SageMaker notebook with aws-analyzer's athena module."
---

<p class="eyebrow"><img class="aws-icon" src="images/aws/athena.svg" alt="" width="32" height="32"> Amazon Athena <code>athena</code></p>

# See what your SQL over S3 costs, why it fails, and which tables to fix

Find the queries that cost the most and the ones that fail, read why in plain words, and find the Glue tables whose S3 folder is empty or gone, or that every query reads in full, without opening the console.
{ .lede }

<ul class="pills">
  <li>Athena and the Glue Data Catalog</li>
  <li>Read-only: never runs a query</li>
  <li>Cost per query, at $5 per TB</li>
  <li>Plain text outside Jupyter</li>
</ul>

The examples use acme's data lake: a dashboard runs `SELECT *` on `sales.events`, a CSV table with no partitions, three times a day, and analysts query `sales.orders`, a Parquet table partitioned by day.
{ .muted }

![overview(): cards with the week's queries, scanned bytes, cost, failed share, tables and crawlers, then findings about a repeated costly query, a workgroup with no scan limit, tables pointing at a missing bucket or an empty folder, a table with no partitions loaded, and crawlers that failed or stopped](images/athena-overview-light.webp#only-light){ width="984" height="2065" loading=lazy }
![overview(): cards with the week's queries, scanned bytes, cost, failed share, tables and crawlers, then findings about a repeated costly query, a workgroup with no scan limit, tables pointing at a missing bucket or an empty folder, a table with no partitions loaded, and crawlers that failed or stopped](images/athena-overview-dark.webp#only-dark){ width="984" height="2065" loading=lazy }
/// caption
**`athena.overview()`**: one dashboard query is 90% of the bill, two tables point at nothing, and two crawlers stopped.
///

## Watch it run { #video }

Six calls in a notebook, from a week's bill to the code for a query: each call typed and run, and the report it draws, with what to notice. It plays as you scroll to it; click it to pause, or pick a chapter to jump to a call.
{ .gist }

<div class="aa-tour" data-tour="athena">
  <div class="tour-screen"></div>
  <div class="tour-controls"></div>
  <div class="tour-chapters"></div>
  <p class="tour-links"><a href="tour.html?service=athena">Full screen</a> · <a href="videos/athena-tour-light.mp4" data-tour-mp4 download>Download as MP4</a>, for Slack or a slide (light or dark, as this page is)</p>
</div>

## Set up in SageMaker { #setup }

<div class="steps" markdown>

1. **Install it** in a notebook cell:

    ```bash
    %pip install aws-analyzer
    ```

2. **Make the view.** It uses the notebook's IAM role and region, so there's nothing to configure.

    ```python
    from aws_analyzer import AthenaView

    athena = AthenaView()
    athena.help()            # every command, grouped by task; athena.help("expensive") shows one in full
    ```

</div>

??? more "Another region or profile, plain text"

    Athena and the Glue Data Catalog are regional, so if `overview()` comes back empty, check the region first. Only boto3 is required.

    ```python
    from aws_analyzer import AthenaAnalyzer, AthenaView

    athena = AthenaView(AthenaAnalyzer(region="eu-west-1", profile="dev"))
    athena = AthenaView(mode="text")    # plain text, also what you get outside Jupyter
    ```

## Start here { #tour }

Five questions people bring to Athena, and the one line that answers each. None of them run a query.
{ .gist }

<div class="quick" markdown>

What it costs
{ .qgroup }

-   [What are my queries costing?](#expensive) `athena.expensive()`
-   [What would this new query cost?](#query-code) `athena.query_code("SELECT ...", database="sales")`{ .long }

What's broken
{ .qgroup }

-   [Why did my query fail?](#failures) `athena.failures()`
-   [Which tables are broken or costly to read?](#catalog) `athena.catalog()`

Rather click?
{ .qgroup }

-   [Can I just click through it?](#explorer) `athena.explore()`

</div>

A table is named `"database.table"`; `"sales/events"`, `'"sales"."events"'` and `"AwsDataCatalog.sales.events"` work too. Times are `"7d"`, `"24h"` or `"2026-11-01"`.

!!! tip "Worth knowing"
    aws-analyzer never runs a query: Athena writes every query's results to S3, and this tool only reads. It reads back past queries and the results they saved, and for a new one shows the Python to run it yourself.

## The costliest queries { #expensive }

`expensive()` adds up the runs of the same query, the same SQL with other values, so a dashboard that runs 20 times a day shows once with what it costs in all.
{ .gist }

```python
athena.expensive()               # or expensive(since="30d", workgroup="primary")
```

![expensive(): the week's cost and a monthly estimate, a finding that the same SELECT * query ran 21 times for 90% of the bill, the queries grouped by kind with their share of cost, then the single costliest runs](images/athena-expensive-light.webp#only-light){ width="984" height="1321" loading=lazy }
![expensive(): the week's cost and a monthly estimate, a finding that the same SELECT * query ran 21 times for 90% of the bill, the queries grouped by kind with their share of cost, then the single costliest runs](images/athena-expensive-dark.webp#only-dark){ width="984" height="1321" loading=lazy }
/// caption
`athena.expensive()`: the dashboard's `SELECT *` ran 21 times and cost $22.56.
///

Athena bills **$5 per TB a query scans**, rounded up to the MB, at least 10 MB a query. Failed queries, DDL (`CREATE`, `ALTER`, `MSCK REPAIR TABLE`) and results reused from an earlier run are free. The findings say what makes a query cheaper: **result reuse** for a query that runs again and again, naming columns instead of `SELECT *`, and partitions. `queries()` lists every query one by one, and `query(id)` shows one with its SQL and the first rows of the results it saved.

## Why queries fail { #failures }

`failures()` sorts failed queries by kind, the most common first, and says what each kind means and how to fix it.
{ .gist }

```python
athena.failures()
```

![failures(): eight failed queries in five kinds, TABLE_NOT_FOUND first, then HIVE_BAD_DATA, COLUMN_NOT_FOUND, exhausted resources and the scan limit, each with what it means, how to fix it and the newest message](images/athena-failures-light.webp#only-light){ width="984" height="884" loading=lazy }
![failures(): eight failed queries in five kinds, TABLE_NOT_FOUND first, then HIVE_BAD_DATA, COLUMN_NOT_FOUND, exhausted resources and the scan limit, each with what it means, how to fix it and the newest message](images/athena-failures-dark.webp#only-dark){ width="984" height="884" loading=lazy }
/// caption
`athena.failures()`: a typo'd table name, a bad value in a CSV file, and a query the scan limit stopped.
///

A kind is Athena's own code when the message starts with one (`TABLE_NOT_FOUND`, `HIVE_BAD_DATA`, `HIVE_PARTITION_SCHEMA_MISMATCH`, ...), else it's read from the words: an S3 permission, exhausted resources, a timeout, the workgroup's scan limit. `query(id)` shows the whole message and the SQL.

## The Data Catalog { #catalog }

`catalog()` lists every Glue table with its format, partition keys and location, and checks with one small S3 request whether the folder holds files.
{ .gist }

```python
athena.catalog()                 # or catalog("sales") for one database
```

![catalog(): two databases and seven tables, findings about a table in a bucket that doesn't exist, one with an empty folder, one partitioned table with no partitions, a table every query reads in full, and three crawlers, then the tables and crawlers](images/athena-catalog-light.webp#only-light){ width="984" height="1419" loading=lazy }
![catalog(): two databases and seven tables, findings about a table in a bucket that doesn't exist, one with an empty folder, one partitioned table with no partitions, a table every query reads in full, and three crawlers, then the tables and crawlers](images/athena-catalog-dark.webp#only-dark){ width="984" height="1419" loading=lazy }
/// caption
`athena.catalog()`: `marketing.campaigns` points at a bucket that's gone, `sales.clicks` has files but no partitions.
///

The findings name what to fix: a table in a **missing bucket or an empty folder** returns no rows, a partitioned table with **no partitions in the catalog** returns no rows until `MSCK REPAIR TABLE` loads them, and a table with **no partitions** is read in full by every query. `crawlers()` shows each crawler's schedule and last crawl: **a failed crawl**, **a paused schedule**, and **a scheduled crawler that stopped running**.

## One table { #table-info }

`table_info()` shows one table's format, partitions, size in S3, its columns and the queries that read it, with the SQL to copy.
{ .gist }

```python
athena.table_info("sales.events")    # or table_info("sales.events", since="30d")
```

![The top of table_info('sales.events'): a CSV table with no partitions, 23 queries that cost $20.90, findings that every query read all of it and that it's stored row by row, and its columns](images/athena-table-info-light.webp#only-light){ width="984" height="1000" loading=lazy }
![The top of table_info('sales.events'): a CSV table with no partitions, 23 queries that cost $20.90, findings that every query read all of it and that it's stored row by row, and its columns](images/athena-table-info-dark.webp#only-dark){ width="984" height="1000" loading=lazy }
/// caption
The top of `athena.table_info("sales.events")`: every query reads all of it, so partitioning it by date is the fix.
///

Under the queries come the SQL statements to copy: a cheap first query (one partition, `LIMIT 10`), `CREATE TABLE AS` that copies the table to Parquet partitioned by its date column, and `MSCK REPAIR TABLE` when its partitions aren't loaded.

## Run a query yourself { #query-code }

`query_code()` writes the Python that runs a query, and says the most it could cost before you run it.
{ .gist }

```python
athena.query_code("SELECT * FROM orders WHERE total > 100", database="sales")
```

![query_code(): the tables the query reads, the most it could read and cost, a warning that it doesn't filter on the partition key dt, and the boto3 code to run it, with an awswrangler version folded](images/athena-query-code-light.webp#only-light){ width="984" height="899" loading=lazy }
![query_code(): the tables the query reads, the most it could read and cost, a warning that it doesn't filter on the partition key dt, and the boto3 code to run it, with an awswrangler version folded](images/athena-query-code-dark.webp#only-dark){ width="984" height="899" loading=lazy }
/// caption
`athena.query_code(...)`: it doesn't filter on `dt`, so it reads every partition.
///

The most it could read is the size of its tables' folders; partition filters, and naming only the columns you need on a Parquet or ORC table, read less.

## Explore by clicking { #explorer }

`explore()` opens a window in the notebook to click through it all: the Overview, every query, the costliest ones, the failures, the tables and the crawlers.
{ .gist }

```python
athena.explore()                         # or explore(tab="costs", since="30d"), explore("sales.events")
```

The [Athena explorer guide](athena_explorer.md) covers every tab, with its own video tour.

<p class="part"><strong>That's the guide.</strong> Everything below is reference for when you need it: the data in Python, cost, permissions, troubleshooting and every command.</p>

## Use the data in Python { #python }

`athena.core` is the `AthenaAnalyzer`. Every command has a data method on it:

```python
core = athena.core

history = core.history(since="7d")                  # QueryHistory: .queries, .truncated, .errors
history.to_df()                                     # every query as a DataFrame
core.results("a1b2c3d4-...", limit=10_000).to_df()  # a past query's saved results
catalog = core.catalog("sales")                     # Catalog: .databases, .tables, .crawlers
table = core.table("sales.events")                  # Table: .format, .partition_keys, .partitions, .location
```

The analysis functions are pure, so they work on data you already have: `query_cost`, `query_shapes`, `group_failures`, `failure_kind`, `tables_in_sql`, `filters_on`, `ctas_sql`, `run_code` and the `*_findings` functions.

## Cost { #cost }

Costs are at us-east-1 list prices (`ATHENA_PRICES`): $5.00 per TB scanned, at least 10 MB a query. A workgroup on provisioned capacity pays $0.30 per DPU-hour instead, which the reports don't show. Pass your own: `AthenaAnalyzer(prices={"scanned_tb": 6.75})`.

Reading queries, workgroups and the catalog costs nothing. `catalog()` makes one S3 LIST request per table ($0.005 per 1,000) and says how many; `table_info()` lists the table's folder, up to 10,000 keys.

## Permissions { #permissions }

Everything is read-only. Anything the notebook's role can't read shows up as a note instead of an error. This policy covers every command:

```json title="IAM policy"
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "ReadAthena",
      "Effect": "Allow",
      "Action": [
        "athena:ListWorkGroups", "athena:GetWorkGroup", "athena:ListQueryExecutions",
        "athena:BatchGetQueryExecution", "athena:GetQueryExecution", "athena:GetQueryRuntimeStatistics",
        "athena:GetQueryResults"
      ],
      "Resource": "*"
    },
    {
      "Sid": "ReadCatalog",
      "Effect": "Allow",
      "Action": [
        "glue:GetDatabases", "glue:GetDatabase", "glue:GetTables", "glue:GetTable", "glue:GetPartitions",
        "glue:GetCrawlers", "glue:GetCrawlerMetrics"
      ],
      "Resource": "*"
    },
    {
      "Sid": "ReadTableFoldersAndResults",
      "Effect": "Allow",
      "Action": ["s3:ListBucket", "s3:GetObject"],
      "Resource": "*"
    }
  ]
}
```

The last statement checks the tables' folders and reads past queries' saved results; narrow its `Resource` to your data and result buckets.

## Troubleshooting { #troubleshooting }

??? question "“No table … in database …”"

    The name is read as `database.table`, in lower case, as Glue keeps it. The note lists the closest names; `catalog("<database>")` lists them all.

??? question "A query's results say they couldn't be read"

    Athena reads results back from where it saved them. A lifecycle rule on the result bucket may have deleted them, or the role may lack `s3:GetObject` there.

??? question "“Stopped at the limit”"

    `queries()` reads at most 2,000 queries per workgroup. Pass `limit=` for more, or a shorter `since=`. Athena keeps 45 days of history.

??? question "A table's queries aren't counted"

    The tables a query reads come from its SQL, so a query through a view counts for the view, not the tables under it.

## Command reference { #reference }

<div class="ref" markdown>

| Command | What it shows |
|---|---|
| `overview(days=7)` | What the week's queries cost, which fail, each workgroup's limits, and the tables and crawlers to fix |
| `queries(since="7d", workgroup=None, status=None, database=None, limit=2000)` | Every query, newest first, with what it scanned and cost |
| `expensive(since="7d", top=10, workgroup=None)` | The costliest queries, the same query's runs added up |
| `failures(since="7d", workgroup=None)` | Failed queries by kind, with what each means and how to fix it |
| `query(query_id, rows=20)` | One query: its SQL, numbers and saved results |
| `catalog(database=None)` | Every table, its format, partitions and whether its folder holds files, and the crawlers |
| `table_info(table, since="7d")` | One table: partitions, size, its queries, and SQL to copy |
| `crawlers()` | Every crawler's schedule and last crawl |
| `query_code(sql, database=None, workgroup=None)` | The Python to run a query yourself, and the most it could cost |
| `explore(what=None, tab=None, since="7d", workgroup=None, height=None)` | The explorer window: the Overview, every query, the costliest, the failures, the tables and the crawlers, by clicking |
| `help(command=None)` | This list, grouped by task; `help("name")` shows one command in full |

</div>
