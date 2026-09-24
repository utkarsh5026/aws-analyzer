# aws-analyzer

[![CI](https://github.com/utkarsh5026/aws-analyzer/actions/workflows/ci.yml/badge.svg)](https://github.com/utkarsh5026/aws-analyzer/actions/workflows/ci.yml)

Copy-paste utilities for analyzing AWS services from a SageMaker (or any Jupyter) notebook.

**One file per service, no dependencies on each other.** Drop `analyzers/<service>.py` into a
notebook cell (or upload it next to the notebook and `import` it) and start analyzing.

Every file has the same two layers:

| Layer | Class | What it does |
|---|---|---|
| Logic | `S3Analyzer` | Calls AWS, returns plain Python data (dataclasses, dicts, lists, DataFrames). Never prints. |
| UI | `S3View` | Wraps the analyzer and renders readable cards, bar tables and previews in the notebook (HTML in Jupyter, text in a terminal). |

| Service | File | Status |
|---|---|---|
| S3 | [`analyzers/s3.py`](analyzers/s3.py) | ✅ |
| Bedrock Knowledge Bases | `analyzers/bedrock_kb.py` | planned |
| DynamoDB | `analyzers/dynamodb.py` | planned |

## S3 quick start

```python
ui = S3View()                      # uses the notebook's execution role
ui.help()                          # every command with a one-line description

ui.buckets()
ui.bucket_info("my-bucket")
ui.summary("s3://my-bucket/data/")
ui.preview("s3://my-bucket/data/part-0.parquet")
```

Anywhere a location is expected you can pass `s3://bucket/prefix` or `bucket/prefix`.
Sizes accept `1024`, `"10MB"`, `"1.5GB"`; times accept a `datetime`, `"2024-05-01"`, or relative `"7d"`, `"12h"`.

### What you can look at (`S3View`)

| Command | Shows |
|---|---|
| `buckets()` | All buckets with region, creation date, age |
| `bucket_info(bucket)` | Versioning, encryption, public access, policy, ownership, object lock, lifecycle rules (in plain English), replication, logging, inventory, tags, **plus CloudWatch object count and size per storage class** (instant, even for billion-object buckets), and flagged risks |
| `ls(uri)` | One level of folders and files, like `aws s3 ls` |
| `summary(uri)` | Dashboard: totals, folder breakdown, file types, storage classes, size and age histograms, largest objects, and findings (small-file problem, archived objects, cold data in STANDARD, empty files) |
| `tree(uri, depth=2)` | Folder tree with count, size and share at every level |
| `find(uri, pattern=, regex=, extensions=, min_size=, max_size=, modified_after=, modified_before=, storage_classes=)` | Search |
| `largest(uri)` / `newest(uri)` / `oldest(uri)` | Top-N objects |
| `duplicates(uri)` | Identical objects (same size + ETag) and reclaimable space |
| `compare(uri_a, uri_b)` | Diff two prefixes: identical / different / only in A / only in B (to verify a copy or sync) |
| `versions(uri)` | Current vs noncurrent versions, delete markers, keys holding the most old-version data |
| `history(uri)` | Version history of one object |
| `uploads(uri)` | Incomplete multipart uploads (billed but invisible in normal listings) |
| `head(uri)` | All object metadata, user metadata and tags |
| `preview(uri, n=20)` | CSV/TSV/JSON/JSONL/Parquet as a DataFrame (+ parquet schema and row count), pretty JSON, text lines, images, hex dump. `.gz`/`.bz2`/`.xz` are decompressed on the fly. Only downloads what it needs. |
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
s3.parquet_info("s3://my-bucket/data/big.parquet")  # rows, row groups, schema (reads only the footer)
s3.read_lines("s3://my-bucket/logs/app.log.gz", 50)
s3.read_json(...), s3.read_jsonl(..., n=100), s3.read_text(...), s3.read_bytes(uri, 0, 1023)
with s3.open("s3://my-bucket/data/x.csv.gz") as f: ...   # streaming, decompressed

for obj in s3.iter_objects("s3://my-bucket/"):      # stream a listing without holding it in memory
    ...
```

The aggregation functions are pure (no AWS calls), so they also work on your own lists of `ObjectInfo`,
for example rows loaded from an S3 Inventory report: `summarize_objects`, `build_folder_tree`,
`make_filter`, `find_duplicate_groups`, `compare_objects`, `summary_findings`, `bucket_findings`.

### Large buckets

- `summary`, `tree`, `find`, `duplicates` and `compare` list every key under the prefix (1,000 per request),
  so expect about 1 to 3 minutes per million objects. Progress is shown while they run. Pass `limit=` to sample.
- `bucket_info` reads the bucket's size from CloudWatch without listing anything. Use it first on huge buckets.
- `ls` only lists one level, so it's fast anywhere.

### IAM permissions

Read-only. Grant what you need:
`s3:ListAllMyBuckets`, `s3:GetBucketLocation`, `s3:ListBucket`, `s3:ListBucketVersions`,
`s3:ListBucketMultipartUploads`, `s3:GetObject`, `s3:GetObjectTagging`, the `s3:GetBucket*` /
`s3:GetLifecycleConfiguration` / `s3:GetReplicationConfiguration` / `s3:GetEncryptionConfiguration` /
`s3:GetInventoryConfiguration` family for `bucket_info`, and `cloudwatch:ListMetrics` + `cloudwatch:GetMetricData`
for bucket sizes. Anything you can't read shows up as a note instead of an error.

## Development

```bash
pip install -r requirements-dev.txt
pytest
ruff check .
```

Tests run against [moto](https://github.com/getmoto/moto), so no AWS account is needed.

[CI](.github/workflows/ci.yml) runs the same checks on Python 3.10 to 3.14 for every pull request and push
to `main`, and also imports each analyzer on its own with only boto3 installed. The versions in
`requirements-dev.txt` are pinned; [Dependabot](.github/dependabot.yml) opens weekly pull requests to update
them and the GitHub Actions the workflow uses.
