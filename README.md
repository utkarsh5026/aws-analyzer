# aws-analyzer

[![CI](https://github.com/utkarsh5026/aws-analyzer/actions/workflows/ci.yml/badge.svg)](https://github.com/utkarsh5026/aws-analyzer/actions/workflows/ci.yml)

Copy-paste utilities for analyzing AWS services from a SageMaker (or any Jupyter) notebook.

📖 [Guides](https://utkarsh5026.github.io/aws-analyzer/) with examples and screenshots: setting up in SageMaker, every
command, and ready-made IAM permissions.

- **[S3 guide](https://utkarsh5026.github.io/aws-analyzer/s3.html)** (source: [`docs/s3.html`](docs/s3.html))
- **[DynamoDB guide](https://utkarsh5026.github.io/aws-analyzer/dynamodb.html)** (source: [`docs/dynamodb.html`](docs/dynamodb.html))
- **[Bedrock Knowledge Bases guide](https://utkarsh5026.github.io/aws-analyzer/bedrock_kb.html)** (source: [`docs/bedrock_kb.html`](docs/bedrock_kb.html))

**One file per service, no dependencies on each other.** Drop `analyzers/<service>.py` into a
notebook cell (or upload it next to the notebook and `import` it) and start analyzing.

Every file has the same two layers:

| Layer | Class | What it does |
|---|---|---|
| Logic | `S3Analyzer`, `DynamoDBAnalyzer`, `BedrockKBAnalyzer` | Calls AWS, returns plain Python data (dataclasses, dicts, lists, DataFrames). Never prints. |
| UI | `S3View`, `DynamoDBView`, `BedrockKBView` | Wraps the analyzer and renders readable cards, bar tables and previews in the notebook (HTML in Jupyter, text in a terminal). |

Commands that take a while show a progress bar as they run: a [tqdm](https://github.com/tqdm/tqdm) bar (a widget in
Jupyter when `ipywidgets` is installed) with the rate and the time left, when tqdm is installed, as it usually is on
SageMaker. Without it you get a plain line with the same numbers. Every View takes `progress="plain"` to always use
that line, or `progress="off"` for none.

| Service | File | Status |
|---|---|---|
| S3 | [`analyzers/s3.py`](analyzers/s3.py) | ✅ |
| Bedrock Knowledge Bases | [`analyzers/bedrock_kb.py`](analyzers/bedrock_kb.py) | ✅ |
| DynamoDB | [`analyzers/dynamodb.py`](analyzers/dynamodb.py) | ✅ |

## S3 quick start

Install the packages first, in a notebook cell (in a terminal, drop the `%`). On SageMaker the first line is already
installed, so you only need the second one, and only for the file types it lists.

```python
%pip install boto3 pandas pyarrow                                  # the commands below
%pip install openpyxl xlrd pypdf zstandard python-snappy tqdm      # optional: Excel, PDF, .zst, snappy Avro, progress bars
```

| Package | Needed for |
|---|---|
| `boto3` | Every command (required) |
| `pandas` | Tables in `preview` (CSV, JSON, Avro, Excel, NumPy), `read_df`, `objects_to_df`. Installs `numpy` for `.npy` / `.npz` |
| `pyarrow` | `.parquet`, `.orc`, `.feather`, `.arrow` in `preview` and `read_df`, and `parquet_info` |
| `openpyxl` / `xlrd` | Excel `.xlsx` / `.xlsm` and old `.xls` |
| `pypdf` | PDF text in `preview`, `document`, `read_pdf` |
| `zstandard` | `.zst` files before Python 3.14 |
| `python-snappy` | Avro files compressed with snappy |
| `tqdm` | Progress bars with the time left while long commands run (`ipywidgets` makes them notebook widgets). Without it, a plain progress line |

IPython, used for the HTML output, comes with Jupyter. If a package is missing, the command tells you which one to
install instead of failing; install it and run the cell again.

```python
ui = S3View()                      # uses the notebook's execution role
ui.help()                          # every command with a one-line description

ui.overview()                      # every bucket: size, monthly cost, security warnings
ui.bucket_info("my-bucket")
ui.summary("s3://my-bucket/data/")
ui.preview("s3://my-bucket/data/part-0.parquet")
ui.what_if("s3://my-bucket/logs/", move_after=30, to="STANDARD_IA")   # preview a lifecycle rule
```

Anywhere a location is expected you can pass `s3://bucket/prefix` or `bucket/prefix`.
Sizes accept `1024`, `"10MB"`, `"1.5GB"`; times accept a `datetime`, `"2024-05-01"`, or relative `"7d"`, `"12h"`.

### What you can look at (`S3View`)

| Command | Shows |
|---|---|
| `buckets()` | All buckets with region, creation date, age |
| `overview(match=None)` | Every bucket in one table: objects, size, estimated monthly cost, versioning, encryption, public access, lifecycle rules, and a list of warnings. `match="sagemaker-*"` checks only matching names |
| `bucket_info(bucket)` | Versioning, encryption, public access (bucket and account level), bucket policy and lifecycle rules in plain English, ownership, object lock, replication, logging, inventory, tags, **plus CloudWatch object count, size and estimated monthly cost per storage type** (instant, even for billion-object buckets), and flagged risks |
| `policy(bucket)` | The bucket policy in plain English (who can do what, on which files, under which conditions), its risks (public access, other accounts, no HTTPS requirement) and the raw JSON |
| `ls(uri)` | One level of folders and files, like `aws s3 ls` |
| `summary(uri)` | Dashboard: totals, estimated monthly cost, folder breakdown, file types, storage classes, size and age histograms, largest objects, and findings (small-file problem, archived objects, cold data in STANDARD and what moving it would save, files under 128 KB billed as 128 KB, empty files) |
| `tree(uri, depth=2)` | Folder tree with count, size and share at every level |
| `find(uri, pattern=, regex=, extensions=, min_size=, max_size=, modified_after=, modified_before=, storage_classes=)` | Search |
| `largest(uri)` / `newest(uri)` / `oldest(uri)` | Top-N objects |
| `duplicates(uri, method="hash", min_size=1, max_read="10GB")` | Identical files, the space and monthly cost of their copies, which copy to keep, and folders that hold nothing but copies (e.g. a backfill of files that exist elsewhere), plus the call that gets the list as a DataFrame. Files are matched by size and ETag, and where same-size files have different ETags (copies uploaded in parts of another size, or encrypted with SSE-KMS), by the SHA-256 of their content: the first 64 KB first, the whole file only where those match, reading at most `max_read`. `method="etag"` reads nothing; `method="strict"` hashes every file that shares its size |
| `compare(uri_a, uri_b)` | Diff two prefixes: identical / different / only in A / only in B (to verify a copy or sync) |
| `versions(uri)` | Current vs noncurrent versions, delete markers, what the old versions cost per month, keys holding the most old-version data |
| `deleted(uri, deleted_after=None)` | Deleted files you can still bring back in a versioned bucket (most recent first), their size, the old versions kept, and the call that restores one. Read-only: it never restores anything itself |
| `history(uri)` | Version history of one object |
| `uploads(uri)` | Incomplete multipart uploads (billed but invisible in normal listings) and what they cost |
| `what_if(uri, move_after=, to=, delete_after=)` | Preview a lifecycle rule before adding it: how many files it would move or delete today, cost before and after, one-time cost and payback time, plus the rule's JSON. `move_after={30: "STANDARD_IA", 180: "GLACIER"}` for several moves |
| `head(uri)` | All object metadata, user metadata and tags |
| `preview(uri, n=20)` | Looks inside a file (see [file types](#file-types)): tables as a DataFrame with their schema, the files in an archive, tensors, notebook cells, pretty JSON, text, images, an audio / video player, or a hex dump. Only downloads what it needs. |
| `document(uri, pages=None)` | Full text of a PDF, Word `.docx` or PowerPoint `.pptx`, page by page or slide by slide (PDFs need `pypdf`) |
| `download(uri, path=None)` | Downloads a file, or a whole folder with its sub-folders, with a progress bar, and says where it went. Files already there with the same size and time are skipped, so running it again resumes. GLACIER files are listed as needing a restore, and it refuses when the disk hasn't room. For a table file it shows the pandas call that opens it |
| `download_zip(uri, path=None, max_size="100MB", max_files=10_000, dry_run=False)` | A file or folder as one `.zip` on the notebook's disk, but first a check of whether this notebook can make it: the files fit the size limit (100 MB by default) and file count, the disk has room, memory, and the role can read them (one 1-byte read). If a check fails nothing is downloaded, and the report says what to change (e.g. the `max_size=` that would fit). `dry_run=True` only runs the checks. GLACIER files are left out and listed; parquet, gz and images are stored as they are, the rest compressed |
| `link(uri)` | Clickable presigned download link |

### Getting the data (`S3Analyzer`)

`ui.core` is the `S3Analyzer`. Every UI command has a data method on it, and there are a few more:

```python
s3 = ui.core                                        # or S3Analyzer(region="us-east-1", profile="dev")

summary = s3.summarize("s3://my-bucket/data/")      # PrefixSummary dataclass
summary.by_extension["parquet"].size                # bytes

files = s3.find("s3://my-bucket/data/", extensions=["csv"], modified_after="7d")
df = objects_to_df(files)                           # key, size, last_modified, storage_class, ...

df = s3.read_df("s3://my-bucket/data/big.parquet", nrows=1000, columns=["id", "ts"])
df = s3.read_df("s3://my-bucket/reports/q1.xlsx", sheet_name="Summary")
df = s3.read_df("s3://my-bucket/spark/part-00000", fmt="parquet")   # no extension: say what it is
s3.parquet_info("s3://my-bucket/data/big.parquet")  # rows, row groups, schema (reads only the footer)
s3.list_archive("s3://my-bucket/job/output/model.tar.gz").entries   # files inside, without extracting
s3.read_avro("s3://my-bucket/events.avro", n=100), s3.read_npy(uri, nrows=10), s3.safetensors_info(uri)
doc = s3.read_document("s3://my-bucket/docs/policy.pdf")   # also .docx / .pptx: doc.text, doc.parts, doc.title
s3.read_pdf(uri, pages=[1, 2]), s3.read_docx(uri).headings, s3.read_pptx(uri).notes
s3.read_lines("s3://my-bucket/logs/app.log.gz", 50)
s3.read_json(...), s3.read_jsonl(..., n=100), s3.read_text(...), s3.read_bytes(uri, 0, 1023)
with s3.open("s3://my-bucket/data/x.csv.gz") as f: ...   # streaming, decompressed

for obj in s3.iter_objects("s3://my-bucket/"):      # stream a listing without holding it in memory
    ...

reports = s3.bucket_reports(match="sagemaker-*")    # BucketConfig + CloudWatch size per bucket
explain_policy(s3.bucket_policy("my-bucket"), s3.account_id())   # [PolicyStatement], plain English
impact = s3.simulate_lifecycle("s3://my-bucket/logs/", move_after=30, to="STANDARD_IA")
impact.monthly_savings, impact.rule()               # USD per month, the rule as a dict
s3.deleted_files("s3://my-bucket/data/", deleted_after="7d").files   # [DeletedObject]
dupes = s3.find_duplicates("s3://my-bucket/data/")  # DuplicateReport: groups, copies, reclaimable, monthly_cost
dupes.to_df()                                       # one row per file: group, role ('keep' / 'copy'), key, sha256, ...
s3.download_folder("s3://my-bucket/data/", "data")  # FolderDownload; s3.download(uri, path) for one file
plan = s3.plan_zip("s3://my-bucket/data/")          # ZipPlan: files, size, disk / memory free, can_download
s3.download_zip("s3://my-bucket/data/", max_size="1GB").plan.path   # ZipDownload; nothing written if a check fails
```

### File types

`preview` and `read_df` pick the reader from the file name, and from the first bytes when the name has
no extension or the wrong one (Spark's `part-00000`, a Firehose object, a `.gz` that isn't gzipped).

| Kind | Extensions | `preview` shows | `read_df` |
|---|---|---|---|
| Delimited text | `.csv` `.tsv` `.psv` | first rows | ✓ |
| JSON | `.json` `.jsonl` `.ndjson` | table of records, or pretty JSON | ✓ |
| Columnar | `.parquet` `.orc` `.feather` `.arrow` | first rows, schema, row count (reads only what it needs) | ✓ |
| Avro | `.avro` | first rows, schema, codec (built-in reader; snappy needs `python-snappy`) | ✓ |
| Excel | `.xlsx` `.xlsm` `.xls` | sheet names, first rows (needs `openpyxl`; `.xls` needs `xlrd`) | ✓ `sheet_name=` |
| NumPy | `.npy` `.npz` | shape, dtype, first rows / the arrays inside | ✓ `.npy` up to 2-D |
| Archives | `.zip` `.tar` `.tar.gz` `.tgz` | the files inside, e.g. a SageMaker `model.tar.gz` | |
| Models | `.safetensors` `.pt` `.pth` `.ckpt` `.pkl` `.joblib` | tensors, shapes, parameter count / files inside; pickles are never loaded | |
| Notebooks | `.ipynb` | kernel and cells | |
| Images, audio, video | `.png` `.jpg` `.gif` `.webp` / `.wav` `.mp3` `.flac` / `.mp4` `.webm` `.mov` | the image / a player | |
| PDF | `.pdf` | page count, title and first page's text (needs `pypdf`; without it, a link) | |
| Word | `.docx` `.docm` `.dotx` | first paragraphs, outline, first table, word count (no package needed) | |
| PowerPoint | `.pptx` `.pptm` `.ppsx` | every slide's title and text, speaker notes (no package needed) | |
| Old Office | `.doc` `.ppt` `.msg` | recognised, with how to convert them (the old binary format can't be read) | |
| Text | `.txt` `.log` `.md` `.yaml` `.xml` `.sql` `.py` and more | first lines | |

Any of them can also be compressed: `.gz`, `.bz2`, `.xz`, or `.zst` (Python 3.14+, or `pip install zstandard`).
Packages in the table are optional; without them `preview` says what to install.

The aggregation functions are pure (no AWS calls), so they also work on your own lists of `ObjectInfo`,
for example rows loaded from an S3 Inventory report: `summarize_objects`, `build_folder_tree`,
`make_filter`, `find_duplicate_groups`, `compare_objects`, `simulate_lifecycle_objects`, `summary_findings`,
`bucket_findings`, `explain_policy`, `policy_findings`, `object_monthly_cost`, `cloudwatch_cost`, the duplicate
finder's steps (`files_to_hash` says which files need reading, `group_duplicates` groups them given the hashes you
have, then `duplicate_folders` and `duplicate_findings`), `zip_checks` and `zip_findings` (on a `ZipPlan`), and the
file parsers `parse_docx`, `parse_pptx`, `parse_pdf`, `parse_avro`.

### Cost estimates

Costs are storage only (no requests, retrievals or data transfer), at us-east-1 list prices for the first
50 TB (`S3_PRICES`). They follow S3's billing rules: STANDARD_IA, ONEZONE_IA and GLACIER_IR bill at least
128 KB per object, and GLACIER / DEEP_ARCHIVE add 40 KB of index data per object. Listings don't say which
Intelligent-Tiering tier an object is in, so it's priced at the frequent-access rate; CloudWatch does, so
`bucket_info` and `overview` price each tier. For another region, pass your prices:

```python
ui = S3View(S3Analyzer(prices={"STANDARD": 0.025, "STANDARD_IA": 0.0138}))
```

### Large buckets

- `summary`, `tree`, `find`, `duplicates` and `compare` list every key under the prefix (1,000 per request),
  so expect about 1 to 3 minutes per million objects. A progress bar shows while they run. Pass `limit=` to sample.
- `duplicates` also downloads files that share a size but not an ETag: 64 KB of each, then whole files only where
  those match, 16 at a time, biggest possible saving first, and it stops at `max_read` (10 GB by default). It
  says how much it read. That download is free inside the bucket's region; from outside AWS it's billed as data
  transfer. `method="etag"` reads nothing.
- `download_zip` counts at most `max_files` keys (10,000 by default) before it decides, so it answers quickly even
  on a huge folder, and it only downloads once every check passes.
- `what_if` lists every key too; `deleted` and `versions` list every version.
- `bucket_info` reads the bucket's size from CloudWatch without listing anything. Use it first on huge buckets.
- `overview` makes about 15 read calls per bucket, 8 buckets at a time, and lists no keys.
- `ls` only lists one level, so it's fast anywhere.

### IAM permissions

Read-only. Grant what you need:
`s3:ListAllMyBuckets`, `s3:GetBucketLocation`, `s3:ListBucket`, `s3:ListBucketVersions`,
`s3:ListBucketMultipartUploads`, `s3:ListMultipartUploadParts`, `s3:GetObject` (also for `duplicates` to read
files and for `download` / `download_zip`), `s3:GetObjectTagging`, the `s3:GetBucket*` /
`s3:GetLifecycleConfiguration` / `s3:GetReplicationConfiguration` / `s3:GetEncryptionConfiguration` /
`s3:GetInventoryConfiguration` family for `bucket_info` (including `s3:GetBucketPolicy` for `policy`),
`s3:GetAccountPublicAccessBlock` for the account-level setting, and `cloudwatch:ListMetrics` +
`cloudwatch:GetMetricData` for bucket sizes. Anything you can't read shows up as a note instead of an error.

## DynamoDB quick start

Install the packages first, in a notebook cell (in a terminal, drop the `%`). On SageMaker both are already
installed.

```python
%pip install boto3               # every command (required)
%pip install pandas              # optional: only for DataFrames (page.to_df(), profile.to_df(), items_to_df)
```

Every `DynamoDBView` command works with boto3 alone; IPython, used for the HTML output, comes with Jupyter.

```python
ui = DynamoDBView()                # uses the notebook's execution role and region
ui.help()                          # every command with a one-line description

ui.tables()                        # every table in the region: key, items, size, est. cost, warnings
ui.table_info("orders")            # indexes and how to query each, capacity, usage, backups, risks
ui.scan("orders")                  # the first 20 items as a table...
ui.more()                          # ...and the next 20
ui.schema("orders")                # what the items look like
ui.get("orders", "USER#42", "ORDER#0017")
ui.query("orders", "USER#42", sort=("begins_with", "ORDER#"))
```

Tables are regional: `DynamoDBView(DynamoDBAnalyzer(region="eu-west-1"))` looks at another region.
Items are shown and returned as plain Python, not DynamoDB JSON: numbers are `int` / `float`, sets are sets,
binary is `bytes`. Nothing in the file writes to a table.

### What you can look at (`DynamoDBView`)

| Command | Shows |
|---|---|
| `tables(match=None)` | Every table in the region in one table: key, item count, size, billing mode, indexes, estimated monthly cost (including on-demand requests at the last 24 hours' rate) and a list of warnings. `match="prod-*"` checks only matching names |
| `table_info(table)` | Keys and types, every index with its projection and **the `query(...)` call that reads it**, billing and capacity, CloudWatch usage over the last 24 hours (consumed units, busiest 5 minutes, throttling), TTL, stream, point-in-time recovery, deletion protection, encryption, tags, estimated monthly cost, and flagged risks, each with what to do about it: no point-in-time recovery (with its cost and the command that turns it on), throttling, capacity near its limit, or capacity far above what's used (with what less capacity or on-demand would cost) |
| `scan(table, n=20, where=, index=, attributes=)` | Items from the start of the table (or an index) as a table: key attributes first, then the others by how many items have them, nested maps as `address.city` columns. Shows how many items were read to find them and the read units used |
| `query(table, partition, sort=None, index=, where=, descending=)` | Items sharing one partition key, in sort-key order, on the table or an index |
| `sample(table, n=20)` | About n items spread across the whole key space. `scan` shows the start of the table, which can all be one partition key; this reads a few items from many slices of it |
| `more()` | The next page of the last `scan`, `query` or `sql` |
| `get(table, *key)` | One item with every nested map and list expanded, the type of each attribute, its size and read / write cost. `as_json=True` adds a JSON copy |
| `sql(statement, *params)` | A PartiQL statement, e.g. `sql('SELECT * FROM "orders" WHERE pk = ?', "USER#42")` |
| `schema(table, n=1000)` | Every attribute and map field: type (or mix of types), share of items that have it, distinct values, examples, range. Key patterns such as `USER#<number>` and `ORDER#<date>`, which show the entity types of a single-table design. Item sizes, the largest items, and findings: mixed types, empty strings, items near the 400 KB limit, attribute names built from data |
| `value_counts(table, attribute)` | How often each value occurs, with the size of those items. On the partition key this is each item collection's size, so hot partitions stand out |
| `largest(table, n=10)` | The biggest items by DynamoDB's sizing rules, and what reading each costs |
| `count(table, where=None)` | Exact count (a full scan), next to DynamoDB's own estimate |

### Filters

`where=` works on `scan`, `query`, `sample`, `schema`, `value_counts`, `largest` and `count`. It takes a dict, and
every condition must match:

| `where=` | Means |
|---|---|
| `{"status": "failed"}` | `status = 'failed'` |
| `{"total": (">", 100)}` | also `"="`, `"!="`, `"<"`, `"<="`, `">="` |
| `{"total": ("between", 10, 100)}` | both ends included |
| `{"sk": ("begins_with", "ORDER#")}` | |
| `{"tags": ("contains", "promo")}` | a substring, or a member of a set or list |
| `{"status": ("in", ["paid", "shipped"])}` | |
| `{"deleted_at": ("not_exists",)}` | also `("exists",)`, and `("type", "N")` to check the stored type |
| `{"address.city": "Pune"}` | dots reach into maps |

For OR and NOT, pass a boto3 condition instead: `where=Attr("a").eq(1) | Attr("b").exists()`. The `sort=` argument
of `query` takes a value or one of the same tuples (`=`, `<`, `<=`, `>`, `>=`, `between`, `begins_with`). Key values
are converted to the key's type, so `"42"` works for a number key.

### Getting the data (`DynamoDBAnalyzer`)

`ui.core` is the `DynamoDBAnalyzer`. Every UI command has a data method on it:

```python
ddb = ui.core                                       # or DynamoDBAnalyzer(region="eu-west-1", profile="dev")

page = ddb.scan("orders", n=5000, where={"status": "failed"})   # ItemPage
page.items                                          # list of plain dicts
df = page.to_df()                                   # DataFrame: keys first, nested maps as 'address.city'
page.stats                                          # items read, items returned, read units, seconds
more = ddb.scan("orders", n=5000, where={"status": "failed"}, start_key=page.last_key)

ddb.query("orders", "USER#42", sort=("between", "ORDER#2024-01", "ORDER#2024-12"), descending=True)
ddb.query("orders", "failed", index="by-status").to_df()
ddb.sample("orders", 500).items                     # spread over the key space
ddb.get("orders", "USER#42", "ORDER#0017")          # dict, or None
ddb.sql('SELECT * FROM "orders" WHERE pk = ?', "USER#42").items

profile = ddb.profile("orders", 2000)               # TableProfile (what schema() shows)
profile.attributes["total"].types                   # Counter({'N': 1990, 'S': 10})
profile.to_df()                                     # one row per attribute

ddb.value_counts("orders", "status").counts         # {'paid': Stat(count=..., size=...), ...}
ddb.count("orders", where={"status": "failed"}).matched
ddb.describe("orders")                              # TableInfo: keys, indexes, capacity, TTL, backups, tags
ddb.table_metrics("orders", hours=24)               # consumed units, busiest period, throttle events
ddb.table_reports(match="prod-*")                   # [TableReport]: describe() + usage for every table

for item in ddb.iter_items("orders", limit=None):   # stream a full scan without holding it in memory
    ...
```

The analysis functions are pure (no AWS calls), so they also work on items you already have, for example a
DynamoDB export to S3: `from_dynamo_item`, `to_dynamo`, `items_to_df`, `flatten_item`, `profile_items`,
`count_values`, `item_size`, `key_pattern`, `build_filter`, `table_findings`, `profile_findings`,
`table_monthly_cost`, `capacity_cost`, `request_cost`.

```python
rows = S3Analyzer().read_jsonl("s3://my-bucket/AWSDynamoDB/01234-abcd/data/part.json.gz")   # from s3.py
profile_items([from_dynamo_item(row["Item"]) for row in rows], keys=["pk", "sk"])
```

### Large tables and cost

- DynamoDB bills every item a scan reads, and a scan competes with your application for the table's
  capacity. So the commands that scan stop early by default: `value_counts` and `largest` read the first
  10,000 items (`limit=None` reads everything), `schema` profiles about 1,000, and `ui.scan` with a filter
  reads at most 100,000 items per page (`scan_limit=`).
- `count` always reads the whole table: `Select=COUNT` returns no items but still reads, and bills, every one.
  For an instant estimate, `tables()` and `table_info()` show DynamoDB's own item count and size, which it
  refreshes about every 6 hours.
- Every report shows the read units it used and, where it matters, what they cost on-demand.
- Costs are estimates at us-east-1 list prices for the standard table class, before the free tier
  (`DYNAMODB_PRICES`): storage, provisioned capacity and point-in-time recovery per month, and on-demand
  reads and writes per million. For another region, pass your prices:
  `DynamoDBAnalyzer(prices={"storage": 0.285, "read_request": 0.1425})`.

### IAM permissions (DynamoDB)

Read-only: `dynamodb:ListTables`, `dynamodb:DescribeTable`, `dynamodb:Scan`, `dynamodb:Query`,
`dynamodb:GetItem`, `dynamodb:PartiQLSelect` for `sql`, `dynamodb:DescribeTimeToLive`,
`dynamodb:DescribeContinuousBackups`, `dynamodb:ListTagsOfResource`, and `cloudwatch:GetMetricData` for usage.
Reading an index needs the permission on its ARN too (`arn:aws:dynamodb:<region>:<account>:table/orders/index/*`),
and a table encrypted with a customer managed KMS key needs `kms:Decrypt`. Anything you can't read shows up as a
note instead of an error.

## Bedrock Knowledge Bases quick start

Install the packages first, in a notebook cell (in a terminal, drop the `%`). On SageMaker both are already
installed.

```python
%pip install boto3               # every command (required)
%pip install pandas              # optional: only for DataFrames (r.to_df(), a.to_df(), report.to_df())
```

Every `BedrockKBView` command works with boto3 alone; IPython, used for the HTML output, comes with Jupyter.
Answers are generated through Bedrock itself (RetrieveAndGenerate or Converse), so no model SDK is needed and any
Bedrock model you have access to works.

```python
ui = BedrockKBView()               # uses the notebook's execution role and region
ui.help()                          # every command with a one-line description

ui.kbs()                           # every knowledge base: status, store, embedding model, last sync, warnings
ui.use("support-docs")             # later commands use this knowledge base (a name, ID or ARN)
ui.kb_info()                       # settings in plain English, data sources, recent syncs, findings, idle cost
ui.search("how do refunds work?")  # ranked passages with source, page and the question's words highlighted
ui.chunk(2)                        # the full text and metadata of result #2
ui.ask("How long do refunds take?")          # an answer with [1][2] citations and the sources behind them
ui.follow_up("And for digital goods?")       # same conversation
```

Knowledge bases are regional: `BedrockKBView(BedrockKBAnalyzer(region="us-west-2"))` looks at another region.
Commands take `kb=` (a name in any case, the 10-character ID, or the ARN); without it they use the one set by
`use()` or `BedrockKBView(kb=...)`, else the only knowledge base in the region, else they list the ones there and
say how to pick. Nothing in the file changes a knowledge base: where a sync is needed, it shows the
`aws bedrock-agent start-ingestion-job ...` command and the boto3 call instead of running them.

### What you can look at (`BedrockKBView`)

| Command | Shows |
|---|---|
| `kbs()` | Every knowledge base in the region: status, type, vector store, embedding model, data sources, documents read by the last sync, last sync, estimated idle cost and warnings |
| `kb_info(kb=None)` | Cards (status, vector store, embedding model and dimensions, data sources, last sync, idle cost), findings, every setting in plain English (vector store, each data source's location, chunking, parsing and deletion policy), recent syncs, tags, and what to try next |
| `syncs(kb=None, data_source=None, n=10)` | Sync history: when, how long, status, scanned / new / modified / deleted / failed counts, **why syncs failed**, and the command to sync again |
| `documents(kb=None, data_source=None, status=None, n=50)` | Documents by status (indexed, failed, pending ...), the ones that aren't indexed with Bedrock's reason, and the sync command. `status="INDEXED"` or `"FAILED"` lists only those |
| `unsynced(kb=None, data_source=None)` | S3 files added or changed since each data source's last successful sync, and the command to sync them |
| `search(question, n=5, kb=, where=, search_type=, rerank=)` | Ranked passages: a score bar relative to the top result, file and page, the best part of the text with the question's words highlighted, and the passage's metadata. Findings: nothing found, one file answering everything, duplicate passages, very short chunks, and codes in the question that no passage contains (try `search_type="HYBRID"`). Time and estimated cost |
| `chunk(rank)` | The full text, metadata and IDs of result #rank from the last `search` or `ask`, and the `S3View().preview("s3://...")` call that opens its file |
| `ask(question, kb=, n=5, where=, model=, engine="kb", prompt=, temperature=, max_tokens=)` | The answer with `[1][2]` citation markers, cards (grounded share, sources used, model, tokens, cost, time), the sources table and findings (not grounded, mostly uncited, Bedrock's "unable to assist" reply, a guardrail, cut off at max_tokens) |
| `follow_up(question)` | The next question in the same RetrieveAndGenerate session (or Converse conversation). If the session has expired, starts a new one and says so |
| `compare(question, kb=, n=(5, 10), search_types=("SEMANTIC", "HYBRID"), where=)` | One row per passage and one column per setting with its rank there, how much each pair of settings overlaps, and what each found that the others missed |
| `evaluate(cases, kb=, n=5, search_type=)` | Retrieval hit rate @n and MRR on test questions, where each expected source ranked (or "missed") and what came up first instead, with the usual fixes |
| `models(match=None)` | The text models you can use for `ask()` here: the ID to pass as `model=`, provider, on demand or through an inference profile, and $ per 1M tokens in and out |

### Two ways to generate answers

- **`engine="kb"` (default)** calls Bedrock's managed RetrieveAndGenerate: Bedrock retrieves, prompts the model and
  returns the citations, and `follow_up()` keeps its session. It doesn't report token counts, so tokens and cost are
  estimated from characters (and labelled as estimates). A custom `prompt=` must contain `$search_results$`.
- **`engine="converse"`** retrieves, then calls the model through Bedrock Converse with the passages numbered as
  sources: exact token counts and cost, any Bedrock model, and your own `prompt=` template with `{sources}` and
  `{question}` (see `bedrock_kb.DEFAULT_PROMPT`). The sources are sent as data, never as instructions, and the model
  is told to cite them as `[n]` and to say when they don't hold the answer.

`model=` takes a model ID or ARN, an inference profile ID, or a short name: `"opus"`, `"sonnet"`, `"haiku"`,
`"claude-opus-5"`, `"nova-pro"`. The default is Claude Opus 5 (`bedrock_kb.DEFAULT_MODEL`), through the region's
inference profile when it needs one; `BedrockKBAnalyzer(default_model="sonnet")` changes it.

### Filters (`where=`)

`where=` filters on the documents' own metadata, which comes from a `<file>.metadata.json` next to each file (for
example `refund-policy.pdf.metadata.json` holding `{"metadataAttributes": {"team": "billing", "year": 2024}}`). It
works on `search`, `ask`, `compare` and `evaluate`, uses the same vocabulary as the DynamoDB analyzer, and every
condition must match:

| `where=` | Means |
|---|---|
| `{"team": "billing"}` | `team = 'billing'` |
| `{"team": ["billing", "support"]}` | one of these values |
| `{"year": (">=", 2024)}` | also `"="`, `"!="`, `">"`, `"<"`, `"<="` |
| `{"year": ("between", 2020, 2024)}` | both ends included |
| `{"region": ("in", ["eu", "uk"])}` | also `("not_in", [...])` |
| `{"doc_id": ("begins_with", "POL-")}` | text starting with this |
| `{"title": ("contains", "refund")}` | text containing this, or a list with an element containing it |
| `{"tags": ("list_contains", "gdpr")}` | a list attribute holding exactly this element |

Values are typed: `2024` and `"2024"` differ. For OR, pass a Bedrock `RetrievalFilter` instead, e.g.
`where={"orAll": [{"equals": {"key": "team", "value": "a"}}, {"equals": {"key": "team", "value": "b"}}]}`; it is
sent unchanged.

### Getting the data (`BedrockKBAnalyzer`)

`ui.core` is the `BedrockKBAnalyzer`. Every UI command has a data method on it, and its methods take the knowledge
base first:

```python
kb = ui.core                                        # or BedrockKBAnalyzer(region="us-west-2", profile="dev")

info = kb.describe("support-docs")                  # KnowledgeBaseInfo: settings, data sources, last syncs, tags
info.data_sources[0].chunking                       # the chunkingConfiguration AWS returned
kb.list_knowledge_bases()                           # [KnowledgeBaseInfo], described in parallel
kb.ingestion_jobs("support-docs", n=20)             # [IngestionJob], newest first, with failure reasons
docs, summary = kb.documents("support-docs", status="FAILED")   # [KBDocument], DocumentSummary
kb.unsynced("support-docs")                         # [SyncFreshness]: changed S3 files per data source

r = kb.retrieve("support-docs", "refund window", n=10, where={"team": "billing"})   # Retrieval
r.passages[0].text, r.passages[0].source, r.passages[0].metadata
df = r.to_df()                                      # one row per passage

a = kb.ask("support-docs", "How long do refunds take?")          # Answer (RetrieveAndGenerate)
a.text, a.citations, a.sources, a.grounded_share
a = kb.generate("refund window?", r.passages, model="opus", prompt=MY_TEMPLATE)   # Converse on your passages
a = kb.generate("refund window?", ["my own chunk", "another chunk"])             # ...or on plain strings
a.input_tokens, a.output_tokens                     # exact, from Converse

kb.compare("support-docs", "refund window for EU orders").overlap   # SearchComparison
report = kb.evaluate("support-docs", [("refund window?", "refund-policy.pdf")])
report.hit_rate, report.mrr, report.to_df()         # EvalReport
kb.models("claude")                                 # [ModelInfo]: what to pass as model=, and its price
```

The analysis functions are pure (no AWS calls), so they also work on responses and passages you already have:
`parse_knowledge_base`, `parse_data_source`, `parse_ingestion_job`, `parse_retrieve`, `parse_rag`, `parse_converse`,
`describe_chunking`, `describe_parsing`, `describe_vector_store`, `build_filter`, `describe_filter`, `build_prompt`
(and `DEFAULT_PROMPT`), `parse_citation_markers`, `question_terms`, `best_snippet`, `retrieval_metrics`,
`match_expected`, `compare_retrievals`, `summarize_documents`, `changed_since`, `generation_cost`,
`vector_store_monthly_cost`, `query_cost`, and the findings: `kb_findings`, `sync_findings`, `retrieval_findings`,
`answer_findings`, `eval_findings`.

### Cost and limits

- Costs are estimates at us-east-1 list prices, read from the Bedrock and OpenSearch pricing pages on 2026-09-25,
  and every report says whether it used list prices or yours. `BEDROCK_PRICES` holds the OpenSearch Serverless
  OCU-hour, its idle minimum, reranking per 1,000 queries and question embedding; `MODEL_PRICES` holds $ per 1M
  input and output tokens by model family. Pass your own:
  `BedrockKBAnalyzer(prices={"opensearch_min_ocus": 1}, model_prices={"my-model": (1.0, 5.0)})`.
- The idle cost is only estimated for OpenSearch Serverless: a classic vector collection bills 2 OCUs
  (about $350/month) even with no traffic. Collections that share a KMS key share those OCUs, dev-test collections
  bill half, and NextGen collections scale to zero, so check your collection type. Other vector stores are billed by
  their own service and show "not estimated".
- `ask()` with the default engine estimates tokens from characters, since RetrieveAndGenerate doesn't return
  them; `engine="converse"` shows exact counts. A model that isn't in `MODEL_PRICES` shows its cost as unknown.
- `evaluate()` and `compare()` only retrieve, so they cost a question embedding per search (well under a cent),
  plus reranking when you ask for it.
- `documents()` reads at most 10,000 documents and `unsynced()` lists at most 100,000 objects by default; both say
  when they stopped early (`.core.documents(..., limit=None)` reads everything).

### IAM permissions (Bedrock Knowledge Bases)

Read-only, per command:

| Permission | Used by |
|---|---|
| `bedrock:ListKnowledgeBases`, `bedrock:GetKnowledgeBase` | `kbs`, `kb_info`, and finding a knowledge base by name |
| `bedrock:ListDataSources`, `bedrock:GetDataSource` | `kb_info`, `syncs`, `documents`, `unsynced` |
| `bedrock:ListIngestionJobs`, `bedrock:GetIngestionJob` | `kbs`, `kb_info`, `syncs`, `unsynced` |
| `bedrock:ListKnowledgeBaseDocuments` | `documents` |
| `bedrock:ListTagsForResource` | `kb_info` |
| `bedrock:Retrieve` | `search`, `chunk`, `compare`, `evaluate`, `ask(engine="converse")` |
| `bedrock:RetrieveAndGenerate` plus `bedrock:InvokeModel` on the model or inference profile | `ask`, `follow_up` |
| `bedrock:InvokeModel` | `ask(engine="converse")`, `core.generate` |
| `bedrock:ListFoundationModels`, `bedrock:ListInferenceProfiles` | `models`, and turning `model="sonnet"` into an ID |
| `s3:ListBucket` on the data source's bucket | `unsynced` |

A model also has to be enabled for the account under **Model access** in the Bedrock console. Anything you can't
read shows up as a note instead of an error.

## Development

```bash
pip install -r requirements-dev.txt
pytest
ruff check .
```

Tests run against [moto](https://github.com/getmoto/moto), so no AWS account is needed. moto covers little of
Bedrock, so the Bedrock Knowledge Bases tests use botocore's `Stubber` on real clients instead, which also checks
every request against the service model.

The guides in `docs/` are plain HTML, published to GitHub Pages by [the Docs workflow](.github/workflows/pages.yml)
whenever `docs/` changes on `main`. `docs/index.html` is the home page with a card per service, and each service has
its own guide (`docs/s3.html`, `docs/dynamodb.html`, `docs/bedrock_kb.html`); a new analyzer gets a new guide and a
card on the home page. The screenshots are the tool's own output from a demo bucket, demo tables and demo
knowledge bases with synthetic data (`.claude/skills/demo/demo.py`; Bedrock's are served by a simulated Bedrock,
since moto has none).

[CI](.github/workflows/ci.yml) runs the same checks on Python 3.10 to 3.14 for every pull request and push
to `main`, and also imports each analyzer on its own with only boto3 installed. The versions in
`requirements-dev.txt` are pinned; [Dependabot](.github/dependabot.yml) opens weekly pull requests to update
them and the GitHub Actions the workflow uses.
