# Changelog

What's new in each release of [aws-analyzer](https://pypi.org/project/aws-analyzer/). The files in
[`analyzers/`](analyzers/) are the same code as the package, so this also tells you when a copy next to your notebook
is worth replacing.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html). Before 1.0, a minor version (0.2.0) adds commands or
changes what one shows, and a patch (0.1.1) only fixes things. A change that could break a notebook starts with
**Breaking** and says what to change. To upgrade: `pip install -U aws-analyzer`.

## [Unreleased]

## [0.3.0] - 2026-10-04

### Added

- `S3View` tables: click a column's header to sort by it (largest, newest or A to Z first, again for the other way, a
  third time for the original order), pick a value in a column's **Filter** to see only those rows (a storage class,
  a region, a bucket), and untick columns under **Columns** to hide the ones you don't need. It's plain HTML and CSS,
  so it still works after the notebook is saved and reopened.
  ([#21](https://github.com/utkarsh5026/aws-analyzer/pull/21))

### Changed

- `S3View`: long keys in tables keep the file name in view. The folder is dimmed and shortened from the left, and the
  whole key shows when you hover over it; in text mode the start of the folder goes, never the file name.
  ([#21](https://github.com/utkarsh5026/aws-analyzer/pull/21))
- `S3View` findings lead with a bold headline, with why it matters and what to do underneath as points, and amounts
  of money stand out. Notes start with their point in bold.
  ([#21](https://github.com/utkarsh5026/aws-analyzer/pull/21))

## [0.2.0] - 2026-10-04

### Added

- `S3Explorer`: **Read all** on a PDF shows its pages as they look, 20 at a time, with buttons under the report for
  the pages before and after, and each page's text folded underneath.
  ([#18](https://github.com/utkarsh5026/aws-analyzer/pull/18))
- A click on a drawn PDF page shows it as big as the notebook, with **‹** **›** to step through the pages and **✕**
  to go back: in `S3Explorer`, and in `S3View.preview()` and `document()`. It's plain HTML and CSS, so it still
  works after the notebook is saved and reopened. ([#18](https://github.com/utkarsh5026/aws-analyzer/pull/18))
- `S3Explorer`: **⬇ Download .zip** saves a folder as one `.zip` next to the notebook, after the same disk-space and
  read-access checks as `S3View.download_zip()`, up to 100 MB and 10,000 files. The **⚙** settings panel changes
  those limits and the folder the zips go to, and so does `S3Explorer(zip_max_size="2GB")`.
  ([#18](https://github.com/utkarsh5026/aws-analyzer/pull/18))

### Changed

- `S3Explorer`: clicking through files no longer waits for each one to load. In a notebook, previews load in the
  background, the file you clicked is highlighted at once, and files you clicked past are skipped.
  ([#18](https://github.com/utkarsh5026/aws-analyzer/pull/18))

## [0.1.0] - 2026-10-04

The first release on PyPI: `pip install aws-analyzer` (or `"aws-analyzer[all]"` for pandas, the file readers and the
notebook extras), then `from aws_analyzer import S3View`. Each analyzer still works on its own, as one file next to a
notebook with only boto3.

### Added

- **Amazon S3** (`s3.py`, `S3View`): every bucket's size, estimated monthly cost and security settings (`overview`,
  `bucket_info`, and `policy` in plain English); folders added up and searched (`ls`, `tree`, `summary`, `find`,
  `largest`, `compare`); savings from `duplicates`, lifecycle rules (`what_if`) and unfinished `uploads`;
  `versions`, `history` and `deleted` files with the call that restores them; and a look inside files without
  downloading them (`preview`, `document`, `file_details`): tables, archives, notebooks, images, PDFs, Word and
  PowerPoint. `download`, `download_zip` and `link` get a copy.
- **S3 explorer** (`s3_explorer.py`, `S3Explorer`): click through buckets and folders in the notebook, with a file's
  preview and details on the right.
- **Amazon DynamoDB** (`dynamodb.py`, `DynamoDBView`): `tables` and `table_info`, with the cost, CloudWatch usage
  and the `query(...)` call for each index; items as plain tables with `sample`, `scan`, `query`, `get`, PartiQL
  `sql` and `more`; and what the items hold with `schema`, `value_counts`, `largest` and `count`.
- **Amazon Bedrock Knowledge Bases** (`bedrock_kb.py`, `BedrockKBView`): every knowledge base and its settings in
  plain English (`kbs`, `kb_info`), sync health (`syncs`, `documents`, `unsynced`), `search` with highlighted
  passages, `ask` and `follow_up` with each claim linked to its source, and retrieval measured with `compare` and
  `evaluate`.
- **Bedrock knowledge base chat** (`bedrock_chat.py`, `chat()`): a chat window on a knowledge base with every
  RetrieveAndGenerate setting in reach, the request as JSON you can edit, and a `transcript()` that stays in the
  saved notebook.
- **Amazon SageMaker** (`sagemaker_env.py`, `SageMakerView`): the notebook you're in (`instance`: type, cost so far,
  CPU, memory and GPU use, idle shutdown), what fills its `disk` and what's safe to clear, and everything `running`
  and billing in the region.
- Every report starts with the numbers that matter, explains its findings in plain English with the command to run
  next, and shows a short note instead of a traceback. Nothing writes to AWS.

[Unreleased]: https://github.com/utkarsh5026/aws-analyzer/compare/v0.3.0...HEAD
[0.3.0]: https://github.com/utkarsh5026/aws-analyzer/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/utkarsh5026/aws-analyzer/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/utkarsh5026/aws-analyzer/releases/tag/v0.1.0
