---
title: Athena Explorer Guide
description: "How to click through Amazon Athena's past queries and the Glue Data Catalog in a SageMaker notebook with aws-analyzer's Athena explorer window: what queries cost and why they fail, each query's SQL and saved rows, every table with what's wrong with it, and the crawlers."
---

<p class="eyebrow"><img class="aws-icon" src="images/aws/athena.svg" alt="" width="32" height="32"> Athena explorer <code>athena/explorer/</code></p>

# Click through what your queries cost, why they fail, and which tables to fix

`explore()` turns a notebook cell into a window on Athena and the Glue Data Catalog. Pick a time range and a workgroup, then click through the queries, what they cost, why they failed, the tables and the crawlers. Each one opens on the right with what's wrong and the fix.
{ .lede }

<ul class="pills">
  <li>One install, ipywidgets already on SageMaker</li>
  <li>Read-only: never runs a query</li>
  <li>The same query's runs added up</li>
  <li>Every Glue table checked against S3</li>
</ul>

The examples use acme's data lake: a dashboard runs `SELECT *` on `sales.events`, a CSV table with no partitions, three times a day, and analysts query `sales.orders`, a Parquet table partitioned by day. The screenshots are the real window in JupyterLab on demo data. For reports you run from code, see the [Athena guide](athena.md).
{ .muted }

## Watch it run { #video }

Two steps in a notebook, from the costliest query to a table that returns no rows: each command typed and run, and the window it shows, with what to notice. It plays as you scroll to it; click it to pause, or pick a chapter to jump to a step.
{ .gist }

<div class="aa-tour" data-tour="athena_explorer">
  <div class="tour-screen"></div>
  <div class="tour-controls"></div>
  <div class="tour-chapters"></div>
  <p class="tour-links"><a href="tour.html?service=athena_explorer">Full screen</a> · <a href="videos/athena_explorer-tour-light.mp4" data-tour-mp4 download>Download as MP4</a>, for Slack or a slide (light or dark, as this page is)</p>
</div>

## Open it { #open }

<div class="steps" markdown>

1. **Install it** in a notebook cell:

    ```bash
    %pip install aws-analyzer
    ```

2. **Open the window.** It uses the notebook's IAM role and region, so there's nothing to configure.

    ```python
    from aws_analyzer import AthenaView

    athena = AthenaView()
    x = athena.explore()             # the Overview of the last 7 days
    ```

</div>

??? more "Straight to a tab, a time range, a workgroup or a table"

    ```python
    athena.explore(tab="costs", since="30d")     # the Costs tab, over the last 30 days
    athena.explore("sales.events")               # a table, open in the Catalog tab
    athena.explore("a1b2c3d4-...")               # a query by its ID, open in the Queries tab
    athena.explore(workgroup="primary")          # one workgroup's queries
    ```

    `tab=` is one of `"overview"`, `"queries"`, `"costs"`, `"failures"`, `"catalog"` and `"crawlers"`, and `since=` one of `"24h"`, `"7d"` (the default) and `"30d"`. `region=`, `profile=` and `height=` work as for the reports. The window needs `ipywidgets`, which SageMaker notebooks already have; without it, or outside Jupyter, `explore()` shows the [reports](athena.md) instead.

## Start here { #start }

Four things to try in your first minute. Nothing you click runs a query, starts a crawler or changes a table.
{ .gist }

<div class="quick" markdown>

Queries
{ .qgroup }

-   [Which queries cost the most?](#costs) <span class="how">Open the Costs tab</span>
-   [Why did my query fail?](#failures) <span class="how">Open the Failures tab</span>

Tables
{ .qgroup }

-   [Which tables return nothing?](#catalog) <span class="how">Catalog, then the To fix chip</span>
-   [Which crawler stopped?](#crawlers) <span class="how">Open the Crawlers tab</span>

</div>

## Every query { #queries }

The menus at the top pick the time range and one workgroup; the cards under them, and every tab, follow.
{ .gist }

The time range is the last 24 hours, 7 days or 30 days. The **Overview** tab is [`overview()`](athena.md#tour): what queries cost and which fail, each workgroup's limits, and what to fix first. The **Queries** tab lists every query run in that time, with chips for the **Failed**, **Costly**, **Cancelled** and **Running** ones and a search box. Click one for its SQL, its numbers (what it scanned and cost) and the rows it saved.

## What they cost { #costs }

The **Costs** tab adds up the same query's runs, whatever its literal values, the costliest first. Click one for its newest run.
{ .gist }

![The Athena explorer on its Costs tab: the cards over the tabs say 52 queries cost $25.16 and 8 failed; on the left the five kinds of query, the dashboard's SELECT * on sales.events first at $22.56 and 90% of the bill; on the right its newest run, 220 GB scanned for $1.07, with a note that it read every column](images/athena-explorer-costs-light.webp#only-light){ width="984" height="864" loading=lazy }
![The Athena explorer on its Costs tab: the cards over the tabs say 52 queries cost $25.16 and 8 failed; on the left the five kinds of query, the dashboard's SELECT * on sales.events first at $22.56 and 90% of the bill; on the right its newest run, 220 GB scanned for $1.07, with a note that it read every column](images/athena-explorer-costs-dark.webp#only-dark){ width="984" height="864" loading=lazy }
/// caption
`athena.explore()`, the Costs tab: a click on a kind of query shows its newest run.
///

Chips show only the queries **Run again and again** or the ones that read every column (**SELECT \***). It's the window's [`expensive()`](athena.md#expensive).

## Why they fail { #failures }

The **Failures** tab groups the failed queries by kind, with what each kind means and the fix. Click one for its newest failed query.
{ .gist }

It's the window's [`failures()`](athena.md#failures): a typo'd table name, a bad value in a CSV file, a query the workgroup's scan limit stopped, each said in plain words.

## The tables { #catalog }

The **Catalog** tab is every Glue table and view, each checked against its S3 folder. Click one for its partitions, its size, the queries that read it and the SQL that fixes it.
{ .gist }

![The Athena explorer on its Catalog tab with the To fix chip picked: five tables with a warning, sales.clicks open on the right, partitioned by dt with 0 partitions in the catalog, a warning that its queries return no rows and the MSCK REPAIR TABLE statement that loads them](images/athena-explorer-catalog-light.webp#only-light){ width="984" height="864" loading=lazy }
![The Athena explorer on its Catalog tab with the To fix chip picked: five tables with a warning, sales.clicks open on the right, partitioned by dt with 0 partitions in the catalog, a warning that its queries return no rows and the MSCK REPAIR TABLE statement that loads them](images/athena-explorer-catalog-dark.webp#only-dark){ width="984" height="864" loading=lazy }
/// caption
The Catalog tab, To fix: `sales.clicks` has files in S3 but no partitions in the catalog.
///

Chips show only the tables **To fix**, those **Empty or gone** (the folder is empty, or the bucket doesn't exist), those with **No partitions loaded**, those with **No partitions** at all (every query reads them in full), those stored as **CSV / JSON** rather than Parquet or ORC, and the **Views**. A table's partitions and size are read when you click it.

## The crawlers { #crawlers }

The **Crawlers** tab lists every crawler, the failed or stopped ones first; click one for what's wrong with it.
{ .gist }

<p class="part"><strong>That's the guide.</strong> Everything below is reference for when you need it: how the window reads, and every command.</p>

## From code { #from-code }

The window is an object you can drive: what a click does, a command does too.
{ .gist }

```python
x = athena.explore()
x.query(query_id)                 # a query by its ID, in the Queries tab
x.table("sales.events")           # a table in the Catalog tab
x.refresh()                       # read everything again (the ↻ button)
```

??? more "How the window reads"

    It reads the workgroups and queries, and the catalog with each table's folder checked, side by side in the background, so the window answers at once. A table's partitions and size are read when you click it, and a query's statistics and saved rows when you open it, once each. It only reads, like the reports: `x.query("a1b2...")` and `x.table("sales.events")` open one from code, and `x.history`, `x.catalog` hold what it read.

## Command reference { #reference }

<div class="ref" markdown>

| Command | What it does |
|---|---|
| `explore(what=None, tab=None, since="7d", workgroup=None, region=None, profile=None, height=None)` | Opens the window and returns it; `athena.explore(...)` on an `AthenaView` takes the same |
| `x.query(query_id)` | Opens a query in the Queries tab: its SQL, numbers and saved results |
| `x.table(table)` | Opens a table in the Catalog tab (`"sales.events"`): its partitions, size, the queries that read it and the SQL to copy |
| `x.refresh()` | Reads everything again (the ↻ button) |

</div>
