---
title: DynamoDB Analyzer Guide
description: "How to browse, query and understand Amazon DynamoDB tables from a SageMaker notebook with aws-analyzer's dynamodb.py, with examples."
---

<p class="eyebrow"><img class="aws-icon" src="images/aws/dynamodb.svg" alt="" width="32" height="32"> Amazon DynamoDB <code>dynamodb.py</code></p>

# Read your DynamoDB tables as plain tables

No more `{"S": ...}` and `{"N": ...}`. See items as rows, one item with every nested field laid out, which attributes the items really hold, and what each read cost, so a scan never surprises you on the bill.
{ .lede }

<ul class="pills">
  <li>One install, boto3 only</li>
  <li>Read-only: never writes to a table</li>
  <li>Scans stop early by default</li>
  <li>Plain text outside Jupyter</li>
</ul>

The examples use `acme-app`, a single-table design: customers (`sk = "PROFILE"`), their orders (`sk = "ORDER#<date>#<id>"`) and support tickets under one partition key, `CUSTOMER#<id>`. The screenshots are the tool's real output on demo tables.
{ .muted }

## Set up in SageMaker { #setup }

<div class="steps" markdown>

1. **Install it** in a notebook cell:

    ```bash
    %pip install aws-analyzer
    ```

2. **Make the view.** It uses the notebook's IAM role and region, so there's nothing to configure.

    ```python
    from aws_analyzer import DynamoDBView

    ui = DynamoDBView()
    ui.help()             # every command, grouped by task; ui.help("scan") shows one in full
    ```

</div>

??? more "No internet in the notebook (VPC-only mode)?"

    On a computer that has internet, download the package and copy it to a bucket the notebook can read (`pip download aws-analyzer --no-deps -d wheels`, then `aws s3 cp --recursive wheels/ s3://acme-ml-data/tools/wheels/`), and install it from there:

    ```bash
    !aws s3 cp --recursive s3://acme-ml-data/tools/wheels/ wheels/
    %pip install --no-index --find-links wheels aws-analyzer
    ```

??? more "Another region or profile, plain text, more columns"

    Tables are regional, so if `tables()` comes back empty, check the region first. Only boto3 is required; pandas is used only when you ask for a DataFrame.

    ```python
    from aws_analyzer import DynamoDBAnalyzer, DynamoDBView

    ui = DynamoDBView(DynamoDBAnalyzer(region="eu-west-1", profile="dev"))
    ui = DynamoDBView(mode="text")          # plain text, e.g. in a terminal or a script
    ui = DynamoDBView(max_columns=0)        # show every attribute side by side (default: 30)
    ui = DynamoDBView(progress="plain")     # a plain progress line on long scans instead of tqdm bars ("off": none)
    ```

## Start here { #tour }

Five questions people bring to a table, and the one line that answers each. None of them write anything.
{ .gist }

<div class="quick" markdown>

-   **Which tables do I have, and what do they cost?**

    ```python
    ui.tables()
    ```

    [Your tables](#tables)

-   **What's in this table? Show me some items.**

    ```python
    ui.sample("acme-app", 20)
    ```

    [Look at items](#items)

-   **Everything for one customer.**

    ```python
    ui.query("acme-app", "CUSTOMER#1042")
    ```

    [One partition key](#query)

-   **What attributes do the items really have?**

    ```python
    ui.schema("acme-app")
    ```

    [What the items look like](#schema)

-   **Which items match a condition?**

    ```python
    ui.scan("acme-app", where={"status": "failed"})
    ```

    [Filter items](#filters)

-   **I want them in pandas.**

    ```python
    df = ui.core.scan("acme-app", n=5000).to_df()
    ```

    [Use the data in Python](#python)

</div>

Items come back as plain Python, not DynamoDB JSON: numbers are `int` or `float`, sets are sets, maps and lists are dicts and lists. A key value is converted to the key's type, so `"42"` works for a number key.

??? more "How to read a report"

    Every report puts the answer first: cards with the numbers that matter (a card turns amber or red when a finding is about it), then the findings, warnings first, each a bold headline with why it matters and what to do underneath. The tables of detail come after.

    - **Sort** by clicking a column's header (again for the other way, a third time for the original order).
    - **Filter** a column with a few repeated values (a status, a billing mode) to see only those rows.
    - **Columns** lets you untick the columns you don't need.
    - **Next** at the bottom lists two or three commands with the arguments filled in, such as `get('acme-app', 'USER#42', 'ORDER#0017')`.

    One click on a command anywhere in a report, or on a code block, selects all of it, ready to copy. It's all plain HTML and CSS, so it still works when you reopen the notebook.

## Your tables { #tables }

### Every table at a glance

`tables()` lists every table in the region with its size, key, billing mode and monthly cost, in seconds: it reads no items.
{ .gist }

```python
ui.tables()
```

![tables(): four tables with status, item count, size, key attributes and types, billing mode, index count, estimated monthly cost and age](images/dynamodb-tables-light.webp#only-light){ width="984" height="764" loading=lazy }
![tables(): four tables with status, item count, size, key attributes and types, billing mode, index count, estimated monthly cost and age](images/dynamodb-tables-dark.webp#only-dark){ width="984" height="764" loading=lazy }
/// caption
`ui.tables()`: item counts and sizes are DynamoDB's own estimates, which it refreshes about every 6 hours.
///

### One table in detail

`table_info()` tells you how to read a table (every index with the `query(...)` call for it) and what's risky or wasteful about it.
{ .gist }

Its findings catch no point-in-time recovery, throttled requests, and provisioned capacity far above or close to what's used. It also shows the last 24 hours of usage from CloudWatch, TTL, the stream, encryption and tags.

```python
ui.table_info("acme-app")
ui.table_info("acme-app", hours=24 * 7)       # a week of usage
ui.table_info("acme-app", metrics=False)      # skip CloudWatch
```

![table_info(): cards for status, items, size, keys, billing, cost, stream, TTL, backups and encryption, a warning that point-in-time recovery is off, a table of the table and its two indexes with the query call for each, the monthly cost, and 24 hours of CloudWatch usage](images/dynamodb-table-info-light.webp#only-light){ width="984" height="965" loading=lazy }
![table_info(): cards for status, items, size, keys, billing, cost, stream, TTL, backups and encryption, a warning that point-in-time recovery is off, a table of the table and its two indexes with the query call for each, the monthly cost, and 24 hours of CloudWatch usage](images/dynamodb-table-info-dark.webp#only-dark){ width="984" height="965" loading=lazy }
/// caption
`ui.table_info("acme-app")`: for an on-demand table the monthly cost includes reads and writes at the last 24 hours' rate.
///

## Look at items { #items }

### Scan and page through

`scan()` shows items as a table, 20 at a time; `more()` shows the next 20. For a first look, `sample()` is better: it reads from all over the table.
{ .gist }

Keys come first, then the other attributes by how many items have them, with nested maps as `address.city` columns. A missing attribute is a blank cell and an empty string shows as `""`, so you can tell them apart.

```python
ui.scan("acme-app")                                   # first 20 items
ui.more()                                             # next 20; works after scan, query and sql
ui.scan("acme-app", n=50, attributes=["status", "total"])   # only some attributes (keys are always included)
ui.scan("acme-app", index="by-status")                # read an index instead of the table
ui.sample("acme-app", 20)                             # 20 items spread across the whole table
```

### Everything under one partition key { #query }

`query()` reads everything stored under one partition key, in sort-key order. It's the cheapest way to read a table.
{ .gist }

`sort=` narrows the sort key with a value or a condition, `index=` queries a secondary index, and `descending=True` starts from the newest, for date-based keys.

```python
ui.query("acme-app", "CUSTOMER#1042")                                 # profile, orders and tickets
ui.query("acme-app", "CUSTOMER#1042", sort=("begins_with", "ORDER#"))  # just the orders
ui.query("acme-app", "CUSTOMER#1042", sort=("between", "ORDER#2026-06", "ORDER#2026-09"))
ui.query("acme-app", "CUSTOMER#1042", sort=("begins_with", "ORDER#"), descending=True, n=5)   # latest 5
ui.query("acme-app", "failed", index="by-status")                     # on a global index
```

![query(): eight orders of one customer in sort-key order with status, creation time, total, currency and coupon, where two coupons are empty strings](images/dynamodb-query-light.webp#only-light){ width="984" height="444" loading=lazy }
![query(): eight orders of one customer in sort-key order with status, creation time, total, currency and coupon, where two coupons are empty strings](images/dynamodb-query-dark.webp#only-dark){ width="984" height="444" loading=lazy }
/// caption
`ui.query("acme-app", "CUSTOMER#1042", sort=("begins_with", "ORDER#"), n=8, attributes=["status", "created_at", "total", "currency", "coupon"])`
///

### One item, every field

`get()` lays out one item as a tree, every nested field expanded, with what it costs to read and write.
{ .gist }

Pass the key values in order, or a dict.

```python
ui.get("acme-app", "CUSTOMER#1042", "ORDER#2026-08-14#7731")
ui.get("acme-app", {"pk": "CUSTOMER#1042", "sk": "PROFILE"})
ui.get("acme-app", "CUSTOMER#1042", "PROFILE", as_json=True)   # add a JSON copy
```

![get(): one order with its size and read and write cost, and a tree of its attributes and types where the list of line items is expanded into three maps of sku, quantity and price](images/dynamodb-get-light.webp#only-light){ width="984" height="821" loading=lazy }
![get(): one order with its size and read and write cost, and a tree of its attributes and types where the list of line items is expanded into three maps of sku, quantity and price](images/dynamodb-get-dark.webp#only-dark){ width="984" height="821" loading=lazy }
/// caption
`ui.get("acme-app", "CUSTOMER#1042", "ORDER#2026-08-14#7731")`
///

### PartiQL

If you'd rather write SQL, `sql()` runs a PartiQL statement, with `?` placeholders filled from the extra arguments.
{ .gist }

```python
ui.sql('SELECT * FROM "acme-app" WHERE pk = ?', "CUSTOMER#1042")
ui.sql('SELECT pk, sk, total FROM "acme-app"."by-status" WHERE status = ?', "failed")
```

!!! tip "Worth knowing"

    A `SELECT` without the partition key in its `WHERE` clause scans the whole table, and you pay for every item it reads.

## Filter items { #filters }

`where=` filters items with a plain dict, on `scan`, `query`, `sample`, `schema`, `value_counts`, `largest` and `count`.
{ .gist }

```python
ui.scan("acme-app", 10, where={"status": "failed", "total": (">", 300)},
        attributes=["status", "created_at", "total", "failure_reason"])
```

![scan() with a filter: 10 items returned after reading 1,619, the read units used, a note that filters run after the read so every item read is billed, and the ten failed orders over 300](images/dynamodb-scan-filter-light.webp#only-light){ width="984" height="566" loading=lazy }
![scan() with a filter: 10 items returned after reading 1,619, the read units used, a note that filters run after the read so every item read is billed, and the ten failed orders over 300](images/dynamodb-scan-filter-dark.webp#only-dark){ width="984" height="566" loading=lazy }
/// caption
A filter runs after DynamoDB reads the items, so you pay for everything read, not just what matches.
///

!!! tip "Worth knowing"

    A filter runs after DynamoDB reads the items, so you pay for everything read, not just what matches. If you search by an attribute often, a `query` on a key or a global index is far cheaper.

??? more "Every condition `where=` takes, and OR / NOT"

    Every condition in the dict must match:

    | `where=` | Means |
    |---|---|
    | `{"status": "failed"}` | `status` equals `"failed"` |
    | `{"total": (">", 100)}` | Also `"="`, `"!="`, `"<"`, `"<="` and `">="` |
    | `{"total": ("between", 10, 100)}` | Both ends included |
    | `{"sk": ("begins_with", "ORDER#")}` | Starts with |
    | `{"tags": ("contains", "vip")}` | A substring, or a member of a set or list |
    | `{"status": ("in", ["pending", "failed"])}` | Any of these values |
    | `{"shipped_at": ("not_exists",)}` | The attribute is missing. Also `("exists",)`, and `("type", "N")` to check the stored type |
    | `{"address.city": "Pune"}` | Dots reach into maps |

    For `OR` and `NOT`, pass a boto3 condition instead: `where=Attr("status").eq("failed") | Attr("total").gt(500)`, with `from boto3.dynamodb.conditions import Attr`. A filtered `ui.scan` reads at most 100,000 items per page (`scan_limit=`); `more()` continues from there.

## What the items look like { #schema }

DynamoDB doesn't store a schema, so `schema()` works one out from about 1,000 items: every attribute, its types, and how many items have it.
{ .gist }

- **Data problems** are findings: one attribute stored with different types, empty strings, items close to the 400 KB limit, and attribute names built from data.
- **Key patterns** turn key values into shapes, such as `ORDER#<date>#<number>`, so the entity types of a single-table design stand out.
- **Each attribute** shows its distinct values, a few examples, and the range of numbers or lengths, down into maps.

```python
ui.schema("acme-app")
ui.schema("acme-app", where={"sk": ("begins_with", "ORDER#")})   # just the orders
ui.schema("acme-app", n=5000, max_depth=3)                        # more items, deeper maps
```

![schema(): 1,000 items profiled, a warning that address.zip holds both strings and numbers, a note about empty coupon strings, a table of 23 attributes with type, distinct values, examples, range and how many items have each, key patterns for the partition and sort keys, item sizes and the largest items](images/dynamodb-schema-light.webp#only-light){ width="984" height="2346" loading=lazy }
![schema(): 1,000 items profiled, a warning that address.zip holds both strings and numbers, a note about empty coupon strings, a table of 23 attributes with type, distinct values, examples, range and how many items have each, key patterns for the partition and sort keys, item sizes and the largest items](images/dynamodb-schema-dark.webp#only-dark){ width="984" height="2346" loading=lazy }
/// caption
`ui.schema("acme-app")`: an old import stored some US zip codes as numbers, so a filter on `address.zip` silently misses them.
///

## Values and sizes { #values }

`value_counts()` counts each value of an attribute. On the partition key, it shows hot or oversized partitions.
{ .gist }

It reads the first 10,000 items unless you pass `limit=None`, and shows the size of the items holding each value.

```python
ui.value_counts("acme-app", "status")
ui.value_counts("acme-app", "address.country")          # dots reach into maps
ui.value_counts("acme-app", "pk", limit=None)           # items per partition key, whole table
```

![value_counts(): items read, distinct values, items without the attribute, read units and cost, and a bar table of order statuses with their item counts and sizes](images/dynamodb-value-counts-light.webp#only-light){ width="984" height="429" loading=lazy }
![value_counts(): items read, distinct values, items without the attribute, read units and cost, and a bar table of order statuses with their item counts and sizes](images/dynamodb-value-counts-dark.webp#only-dark){ width="984" height="429" loading=lazy }
/// caption
`ui.value_counts("acme-app", "status")`: “(not set)” is customers and tickets, which have no status.
///

```python
ui.largest("acme-app")                                  # the 10 biggest items, by DynamoDB's sizing rules
ui.count("acme-app", where={"status": "pending"})       # exact count: a full scan
ui.count("acme-app")                                    # next to DynamoDB's own estimate
```

<p class="part"><strong>That's the guide.</strong> Everything below is reference for when you need it: the data in Python, large tables and cost, permissions, troubleshooting and every command.</p>

## Use the data in Python { #python }

Every report has a data version on `ui.core` (a `DynamoDBAnalyzer`) that returns dataclasses, dicts and DataFrames instead of printing.

```python
ddb = ui.core

page = ddb.scan("acme-app", n=5000, where={"status": "failed"})   # ItemPage
page.items                                   # list of plain dicts
df = page.to_df()                            # DataFrame: keys first, nested maps as address.city
page.stats.scanned, page.stats.read_units    # what the read cost
next_page = ddb.scan("acme-app", n=5000, where={"status": "failed"}, start_key=page.last_key)

orders = ddb.query("acme-app", "CUSTOMER#1042", sort=("begins_with", "ORDER#"), n=None).to_df()
ddb.get("acme-app", "CUSTOMER#1042", "PROFILE")              # dict, or None
ddb.sample("acme-app", 500).items                           # spread across the table

profile = ddb.profile("acme-app", 2000)                     # TableProfile, what schema() shows
profile.attributes["address.zip"].types                     # Counter({'S': 145, 'N': 17})
profile.to_df()                                             # one row per attribute

ddb.value_counts("acme-app", "status").counts               # {'delivered': Stat(count=..., size=...), ...}
ddb.count("acme-app", where={"status": "failed"}).matched
ddb.describe("acme-app")                                    # TableInfo: keys, indexes, capacity, TTL, backups, tags
ddb.table_metrics("acme-app", hours=24)                     # consumed units, busiest period, throttles

for item in ddb.iter_items("acme-app", limit=None):         # stream a full scan without holding it in memory
    pass
```

The analysis functions don't call AWS, so they also work on items you already have, for example a [DynamoDB export to S3](https://docs.aws.amazon.com/amazondynamodb/latest/developerguide/S3DataExport.HowItWorks.html). Read the export with `s3.py` and profile it without touching the table:

```python
from aws_analyzer.dynamodb import from_dynamo_item, items_to_df, profile_items
from aws_analyzer import S3Analyzer

rows = S3Analyzer().read_jsonl("s3://acme-ml-data/exports/AWSDynamoDB/01234-abcd/data/part.json.gz")
items = [from_dynamo_item(row["Item"]) for row in rows]
profile_items(items, keys=["pk", "sk"]).to_df()
items_to_df(items, keys=["pk", "sk"])
```

Also pure: `to_dynamo`, `flatten_item`, `count_values`, `item_size`, `key_pattern`, `build_filter`, `table_findings`, `profile_findings`, `table_monthly_cost` and `request_cost`.

## Large tables and cost { #big }

<div class="grid cards" markdown>

- **Start without reading items**

    `tables()` and `table_info()` use DynamoDB's own counts and sizes and CloudWatch's usage, so they take seconds on any table.

- **Scans stop early**

    Every item a scan reads is billed and uses the table's capacity. `value_counts` and `largest` read 10,000 items and `schema` about 1,000; pass `limit=None` to read everything.

- **Queries are cheap**

    A `query` reads only one partition key's items. Prefer it, or a global index, to a filtered scan.

- **`count` reads everything**

    It returns no items, but still reads and pays for all of them. The estimate in `tables()` is free.

</div>

Every report shows the read units it used. Costs are estimates at us-east-1 list prices for the standard table class, before the free tier (`DYNAMODB_PRICES`): storage, provisioned capacity and point-in-time recovery per month, and on-demand reads and writes per million. For another region, pass your prices:

```python
ui = DynamoDBView(DynamoDBAnalyzer(prices={"storage": 0.285, "read_request": 0.1425, "write_request": 0.7125}))
```

## Permissions { #permissions }

Everything is read-only. Anything the notebook's role can't read shows up as a note (or a `?` on a card) instead of an error, so you can start with less and add what you need. This policy covers every command:

```json title="IAM policy"
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "ListTables",
      "Effect": "Allow",
      "Action": "dynamodb:ListTables",
      "Resource": "*"
    },
    {
      "Sid": "ReadTables",
      "Effect": "Allow",
      "Action": [
        "dynamodb:DescribeTable", "dynamodb:Scan", "dynamodb:Query", "dynamodb:GetItem",
        "dynamodb:PartiQLSelect", "dynamodb:DescribeTimeToLive", "dynamodb:DescribeContinuousBackups",
        "dynamodb:ListTagsOfResource"
      ],
      "Resource": ["arn:aws:dynamodb:*:*:table/*", "arn:aws:dynamodb:*:*:table/*/index/*"]
    },
    {
      "Sid": "ReadUsage",
      "Effect": "Allow",
      "Action": "cloudwatch:GetMetricData",
      "Resource": "*"
    }
  ]
}
```

To scope it down, replace `table/*` with your tables, for example `arn:aws:dynamodb:us-east-1:123456789012:table/acme-app` and `…:table/acme-app/index/*`. A table encrypted with a customer managed KMS key also needs `kms:Decrypt` on that key.

## Troubleshooting { #troubleshooting }

??? question "“ResourceNotFoundException: table not found”"

    Table names are case-sensitive, and tables are regional: the notebook's region may not be the table's. Use `DynamoDBView(DynamoDBAnalyzer(region="eu-west-1"))`.

??? question "A query returns nothing, but the item exists"

    Keys are typed. `"42"` (a string) and `42` (a number) are different keys. The tool converts what you pass to the key's declared type, but a filter on an ordinary attribute can't know what type you meant. `schema()` shows which types each attribute really holds.

??? question "A card shows “?” or a note says “Couldn't read … (AccessDeniedException)”"

    The notebook's role is missing that permission. The rest of the report still works. Compare the role with the [policy above](#permissions).

??? question "“No reads or writes recorded in CloudWatch”"

    The table had no traffic in that window, or the role can't call `cloudwatch:GetMetricData`. Try `ui.table_info(name, hours=24 * 7)`, or `metrics=False` to skip CloudWatch.

??? question "The item count in `tables()` doesn't match `count()`"

    DynamoDB refreshes the item count and size it reports about every 6 hours. `count()` is exact because it reads every item.

??? question "The report lost its formatting after I reopened the notebook"

    JupyterLab strips the report's styles from saved output when a notebook is reopened. Run the cell again to get the formatted report back.

## Command reference { #reference }

Every `DynamoDBView` command. `ui.help()` prints the same list grouped by task, and `ui.help("scan")` shows one command's full description.

<div class="ref" markdown>

| Command | What it shows |
|---|---|
| `tables()` | Every table in the region: key, items, size, billing, indexes, estimated cost |
| `table_info(table, metrics=True, hours=24)` | Keys, indexes and how to query each, capacity, usage, TTL, backups, encryption, tags, cost, risks |
| `scan(table, n=20, where=, index=, attributes=, scan_limit=100_000)` | Items from the start of the table or an index |
| `query(table, partition, sort=None, n=20, where=, index=, attributes=, descending=False)` | Items sharing one partition key, in sort-key order |
| `sample(table, n=20, where=, index=, attributes=)` | Items spread across the whole table |
| `more()` | The next page of the last scan, query or sql |
| `get(table, *key, as_json=False)` | One item as a tree, with its size and read and write cost |
| `sql(statement, *parameters, n=50)` | A PartiQL statement |
| `schema(table, n=1000, where=, index=, spread=True, max_depth=2)` | Attributes, types, fill rate, examples, ranges, key patterns, item sizes, findings |
| `value_counts(table, attribute, limit=10_000, where=, index=, top=30)` | How often each value occurs, and the size of those items |
| `largest(table, n=10, limit=10_000, where=, index=)` | The biggest items |
| `count(table, where=, index=)` | Exact item count, next to DynamoDB's estimate |
| `help(command=None)` | This list, grouped by task; `help("name")` shows one command in full |

</div>
