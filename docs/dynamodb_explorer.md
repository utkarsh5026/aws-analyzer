---
title: DynamoDB Explorer Guide
description: "How to click through Amazon DynamoDB tables in a SageMaker notebook with aws-analyzer's DynamoDB explorer window: every table with what it costs and what to fix, a table's keys and indexes, its items as plain values, and what its items hold."
---

<p class="eyebrow"><img class="aws-icon" src="images/aws/dynamodb.svg" alt="" width="32" height="32"> DynamoDB explorer <code>dynamodb/explorer/</code></p>

# Click through your tables, their items and what they hold

`explore()` turns a notebook cell into a window on DynamoDB. Click a table for its keys, indexes and cost, read its items as plain values, type a partition key to see one item collection, and click an attribute to count its values.
{ .lede }

<ul class="pills">
  <li>One install, ipywidgets already on SageMaker</li>
  <li>Read-only: never writes an item</li>
  <li>Items as plain values, not DynamoDB JSON</li>
  <li>Every read says what it cost</li>
</ul>

The examples use acme's tables: `acme-app` holds each customer's profile, orders and support tickets under one partition key, `pk`, with a `by-status` index, and `acme-sessions` pays for far more capacity than it uses. The screenshots are the real window in JupyterLab on demo data. For reports you run from code, see the [DynamoDB guide](dynamodb.md).
{ .muted }

![The DynamoDB explorer in a notebook, animated: it opens on the Overview of every table; the pointer opens the Tables tab, picks the To fix chip and clicks acme-sessions, whose reads peaked at 5% of its provisioned capacity; it opens acme-app and the Items tab, types ORDER# as the sort key prefix and CUSTOMER#1042 as the partition key, and clicks order 7731 for all of it; then the Schema tab, where a click on status counts its six values](images/ddb-explorer-tour-light.webp#only-light){ width="984" height="863" loading=lazy }
![The DynamoDB explorer in a notebook, animated: it opens on the Overview of every table; the pointer opens the Tables tab, picks the To fix chip and clicks acme-sessions, whose reads peaked at 5% of its provisioned capacity; it opens acme-app and the Items tab, types ORDER# as the sort key prefix and CUSTOMER#1042 as the partition key, and clicks order 7731 for all of it; then the Schema tab, where a click on status counts its six values](images/ddb-explorer-tour-dark.webp#only-dark){ width="984" height="863" loading=lazy }
/// caption
**`ddb.explore()`**: from the table that costs too much, to one customer's orders, to what an attribute holds.
///

## Open it { #open }

<div class="steps" markdown>

1. **Install it** in a notebook cell:

    ```bash
    %pip install aws-analyzer
    ```

2. **Open the window.** It uses the notebook's IAM role and region, so there's nothing to configure.

    ```python
    from aws_analyzer import DynamoDBView

    ddb = DynamoDBView()
    x = ddb.explore()                 # the Overview: every table at a glance
    ```

</div>

??? more "Straight to a table or a tab"

    ```python
    ddb.explore("acme-app")                  # a table, open in the Tables tab
    ddb.explore("acme-app", tab="items")     # its items: a sample from across the table
    ddb.explore("acme-app", tab="schema")    # what its items hold
    ```

    `tab=` is one of `"overview"`, `"tables"`, `"items"` and `"schema"`. `height=` sets the tabs' height (pixels, or CSS like `"80vh"`). The window needs `ipywidgets`, which SageMaker notebooks already have; without it, or outside Jupyter, `explore()` shows the [reports](dynamodb.md) instead.

## Start here { #start }

Four things to try in your first minute. Nothing you click writes, deletes or changes an item or a table.
{ .gist }

<div class="quick" markdown>

Tables
{ .qgroup }

-   [Which tables need fixing?](#tables) <span class="how">Tables, then the To fix chip</span>
-   [How do I query this table?](#tables) <span class="how">Click a table</span>

Items
{ .qgroup }

-   [What does a user's data look like?](#items) <span class="how">Type its partition key, press Enter</span>
-   [Which attributes are missing or mixed?](#schema) <span class="how">Open the Schema tab</span>

</div>

## Every table { #tables }

The **Tables** tab lists every table in the region; click one for its keys, indexes and how to query each, its usage, its cost a month and what to fix.
{ .gist }

![The DynamoDB explorer on its Tables tab with the To fix chip picked: three tables with a warning, acme-sessions open on the right, provisioned at 400 read and 200 write units for $132.88 a month, and a warning that its reads peaked at 5% of that capacity, with what less capacity or on-demand would cost](images/ddb-explorer-tables-light.webp#only-light){ width="984" height="864" loading=lazy }
![The DynamoDB explorer on its Tables tab with the To fix chip picked: three tables with a warning, acme-sessions open on the right, provisioned at 400 read and 200 write units for $132.88 a month, and a warning that its reads peaked at 5% of that capacity, with what less capacity or on-demand would cost](images/ddb-explorer-tables-dark.webp#only-dark){ width="984" height="864" loading=lazy }
/// caption
`ddb.explore()`, the Tables tab, To fix: `acme-sessions` pays for capacity it doesn't use.
///

The **Overview** tab is [`tables()`](dynamodb.md#tables): every table's items, size, billing and cost, and every warning. On the **Tables** tab, chips show only the tables **To fix**, the **On-demand** or **Provisioned** ones, those with **No backups** (point-in-time recovery off) and the **Idle** ones nothing read or wrote in the last 24 hours. The table you click is [`table_info()`](dynamodb.md#tables), drawn from what the window already read, and it becomes the open table: the menu at the top shows it, and the Items and Schema tabs read it.

## Its items { #items }

The **Items** tab reads the open table's items, 50 at a time, as plain values. Type a partition key value and press Enter for that item collection.
{ .gist }

![The DynamoDB explorer on its Items tab: CUSTOMER#1042 typed as the partition key and ORDER# as the sort key prefix, the customer's orders listed on the left by their sort key with their status and size, and order 7731 open on the right with its size, read and write cost and every attribute, the lines list unfolded](images/ddb-explorer-items-light.webp#only-light){ width="984" height="864" loading=lazy }
![The DynamoDB explorer on its Items tab: CUSTOMER#1042 typed as the partition key and ORDER# as the sort key prefix, the customer's orders listed on the left by their sort key with their status and size, and order 7731 open on the right with its size, read and write cost and every attribute, the lines list unfolded](images/ddb-explorer-items-dark.webp#only-dark){ width="984" height="864" loading=lazy }
/// caption
The Items tab: `CUSTOMER#1042`'s orders, read by typing its key, and one open in full.
///

Without a key, it reads **a sample from across the table** ([`sample()`](dynamodb.md#items)), or **from the start of the table** ([`scan()`](dynamodb.md#items)), and **More** reads the next page. With a key it's [`query()`](dynamodb.md#items): the sort key box narrows it to a prefix (`ORDER#`), and the first menu reads an index instead of the table. The filter box looks through the items already read, without reading more. Click an item for all of it, maps and lists unfolded, with its size and what reading it costs ([`get()`](dynamodb.md#items)).

!!! tip "Worth knowing"

    Every read costs read units, as it would in a cell. The page on the right says what it read and cost; a sample or a page from the start reads at most 10,000 items.

## What the items hold { #schema }

The **Schema** tab profiles about 1,000 items from across the open table: every attribute's types, how many items have it, and examples. Click one to count its values.
{ .gist }

![The DynamoDB explorer on its Schema tab: acme-app's attributes listed with their types, examples and the share of items that have them, with chips for those not on every item and of mixed types; status picked, and its six values counted on the right, delivered first at 34.5% and 30.5% of items without it](images/ddb-explorer-schema-light.webp#only-light){ width="984" height="864" loading=lazy }
![The DynamoDB explorer on its Schema tab: acme-app's attributes listed with their types, examples and the share of items that have them, with chips for those not on every item and of mixed types; status picked, and its six values counted on the right, delivered first at 34.5% and 30.5% of items without it](images/ddb-explorer-schema-dark.webp#only-dark){ width="984" height="864" loading=lazy }
/// caption
The Schema tab: a click on `status` counts its values.
///

It's [`schema()`](dynamodb.md#schema), with each attribute a line and a map's fields under it. Chips show only the attributes **Not on every item**, those of **Mixed types** (a number here, text there) and the **Maps and lists**. A click is [`value_counts()`](dynamodb.md#values) over up to 10,000 items: on the partition key, each value's count is the size of its item collection, so hot partitions stand out.

<p class="part"><strong>That's the guide.</strong> Everything below is reference for when you need it: how the window reads, and every command.</p>

## From code { #from-code }

The window is an object you can drive: what a click does, a command does too.
{ .gist }

```python
x = ddb.explore()
x.table("acme-app")                     # a table in the Tables tab
x.items("CUSTOMER#1042", "ORDER#")      # a partition key's items, the sort key starting with ORDER#
x.items("failed", index="by-status")    # the same on an index
x.schema()                              # what the open table's items hold
x.refresh()                             # read every table again (the ↻ button)
```

??? more "How the window reads"

    It describes every table and reads its last 24 hours of CloudWatch usage in the background, so the window answers at once. The Items and Schema tabs read the open table the first time you open them, and an item read from an index is read again from the table by its key, since an index may hold only some attributes. `x.reports`, `x.items_read`, `x.profile` and `x.counts` hold what it read.

## Command reference { #reference }

<div class="ref" markdown>

| Command | What it does |
|---|---|
| `explore(what=None, tab=None, region=None, profile=None, height=None)` | Opens the window and returns it; `ddb.explore(...)` on a `DynamoDBView` takes `what`, `tab` and `height` |
| `x.table(name)` | Opens a table in the Tables tab: its keys, indexes and how to query each, usage, cost and what to fix |
| `x.items(partition=None, starts_with=None, table=None, index=None)` | Opens the Items tab and reads: a partition key's items, or without one a sample from across the table |
| `x.schema(table=None)` | Opens the Schema tab: every attribute of the open table's items |
| `x.refresh()` | Reads every table again (the ↻ button) |

</div>
