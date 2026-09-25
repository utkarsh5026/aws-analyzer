# aws-analyzer

[![CI](https://github.com/utkarsh5026/aws-analyzer/actions/workflows/ci.yml/badge.svg)](https://github.com/utkarsh5026/aws-analyzer/actions/workflows/ci.yml)

Copy-paste utilities for analyzing AWS services from a SageMaker (or any Jupyter) notebook.

📖 **[S3 guide with examples and screenshots](https://utkarsh5026.github.io/aws-analyzer/)**: setting up in SageMaker,
every command, and ready-made IAM permissions. Its source is [`docs/index.html`](docs/index.html).

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
| `duplicates(uri)` | Identical objects (same size + ETag) and reclaimable space |
| `compare(uri_a, uri_b)` | Diff two prefixes: identical / different / only in A / only in B (to verify a copy or sync) |
| `versions(uri)` | Current vs noncurrent versions, delete markers, what the old versions cost per month, keys holding the most old-version data |
| `deleted(uri, deleted_after=None)` | Deleted files you can still bring back in a versioned bucket (most recent first), their size, the old versions kept, and the call that restores one. Read-only: it never restores anything itself |
| `history(uri)` | Version history of one object |
| `uploads(uri)` | Incomplete multipart uploads (billed but invisible in normal listings) and what they cost |
| `what_if(uri, move_after=, to=, delete_after=)` | Preview a lifecycle rule before adding it: how many files it would move or delete today, cost before and after, one-time cost and payback time, plus the rule's JSON. `move_after={30: "STANDARD_IA", 180: "GLACIER"}` for several moves |
| `head(uri)` | All object metadata, user metadata and tags |
| `preview(uri, n=20)` | Looks inside a file (see [file types](#file-types)): tables as a DataFrame with their schema, the files in an archive, tensors, notebook cells, pretty JSON, text, images, an audio / video player, or a hex dump. Only downloads what it needs. |
| `document(uri, pages=None)` | Full text of a PDF, Word `.docx` or PowerPoint `.pptx`, page by page or slide by slide (PDFs need `pypdf`) |
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
`bucket_findings`, `explain_policy`, `policy_findings`, `object_monthly_cost`, `cloudwatch_cost`, and the file parsers
`parse_docx`, `parse_pptx`, `parse_pdf`, `parse_avro`.

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
  so expect about 1 to 3 minutes per million objects. Progress is shown while they run. Pass `limit=` to sample.
- `what_if` lists every key too; `deleted` and `versions` list every version.
- `bucket_info` reads the bucket's size from CloudWatch without listing anything. Use it first on huge buckets.
- `overview` makes about 15 read calls per bucket, 8 buckets at a time, and lists no keys.
- `ls` only lists one level, so it's fast anywhere.

### IAM permissions

Read-only. Grant what you need:
`s3:ListAllMyBuckets`, `s3:GetBucketLocation`, `s3:ListBucket`, `s3:ListBucketVersions`,
`s3:ListBucketMultipartUploads`, `s3:GetObject`, `s3:GetObjectTagging`, the `s3:GetBucket*` /
`s3:GetLifecycleConfiguration` / `s3:GetReplicationConfiguration` / `s3:GetEncryptionConfiguration` /
`s3:GetInventoryConfiguration` family for `bucket_info` (including `s3:GetBucketPolicy` for `policy`),
`s3:GetAccountPublicAccessBlock` for the account-level setting, and `cloudwatch:ListMetrics` +
`cloudwatch:GetMetricData` for bucket sizes. Anything you can't read shows up as a note instead of an error.

## Development

```bash
pip install -r requirements-dev.txt
pytest
ruff check .
```

Tests run against [moto](https://github.com/getmoto/moto), so no AWS account is needed.

The guide in `docs/` is plain HTML, published to GitHub Pages by [the Docs workflow](.github/workflows/pages.yml)
whenever `docs/` changes on `main`. Its screenshots are the tool's own output from a demo bucket.

[CI](.github/workflows/ci.yml) runs the same checks on Python 3.10 to 3.14 for every pull request and push
to `main`, and also imports each analyzer on its own with only boto3 installed. The versions in
`requirements-dev.txt` are pinned; [Dependabot](.github/dependabot.yml) opens weekly pull requests to update
them and the GitHub Actions the workflow uses.
