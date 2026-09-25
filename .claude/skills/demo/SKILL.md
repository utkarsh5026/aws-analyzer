---
name: demo
description: Run analyzer View commands against realistic synthetic data in moto (no AWS account needed) and show exactly what a user would see, as text and optionally as the notebook HTML. Use to check how a new or changed report reads, to reproduce an output bug, or to prepare pages for the docs screenshots.
argument-hint: "<service> [python to run, e.g. ui.summary(\"s3://demo-lake/\")] [--html path] [--theme light|dark]"
allowed-tools: Bash(.venv/bin/python .claude/skills/demo/demo.py:*), Bash(python .claude/skills/demo/demo.py:*)
---

# Demo

`demo.py` starts moto, fills it with demo data built to set off most findings, builds the service's View in
text mode, and runs the Python you pass. `ui`, `core`, `mod` and the module under its own name (`s3`,
`dynamodb`) are in scope.

```bash
.venv/bin/python .claude/skills/demo/demo.py s3 'ui.summary("s3://demo-lake/")'
.venv/bin/python .claude/skills/demo/demo.py dynamodb 'ui.scan("orders", where={"status": "failed"}); ui.more()'
.venv/bin/python .claude/skills/demo/demo.py dynamodb 'ui.table_info("sessions")' --html <scratchpad>/t.html --theme dark
.venv/bin/python .claude/skills/demo/demo.py s3 'print(core.summarize("s3://demo-lake/raw/").object_count)'
```

Arguments: `$ARGUMENTS`. The first word is the service. The rest is the code to run, plus any flags. If only a
service is given, run a short tour:

- s3: `ui.overview(); ui.bucket_info("demo-lake"); ui.summary("s3://demo-lake/")`
- dynamodb: `ui.tables(); ui.table_info("orders"); ui.schema("orders")`
- other services: `ui.help()` and then the service's overview command

`--help` describes the demo data. Most useful:

- **S3**, bucket `demo-lake` (versioned):
  - `raw/events/` has about 1,000 small files, and `raw/backfill/` duplicates a week of them.
  - `curated/` has parquet files and `customers.csv` with metadata and tags.
  - `logs/app/` is 1–3 years old, `archive/` is in GLACIER, and `reports/monthly/` holds small STANDARD_IA files.
  - `reports/` also holds an overwritten file and 3 deleted ones.
  - `tmp/` has an unfinished multipart upload.
  - The bucket policy shares `curated/` with another account.
  - Buckets `demo-models` (holding a `model.tar.gz`) and `demo-scratch` (empty) are also there.
- **DynamoDB**:
  - `orders` has pk/sk, the GSI `by-status`, USER#/ORDER# keys, nested maps, sets, one mixed-type attribute,
    empty strings and a ~330 KB item.
  - `sessions` is provisioned far above its seeded CloudWatch usage and has throttles.
  - `counters` has a numeric key.

## Reading the output

This output is what a data scientist sees in the notebook. When you run a demo to review a command, check it
against the product rules in CLAUDE.md:

- Does the answer come first?
- Are all values in human units?
- Does every finding name a next step?
- Are estimates and partial results labelled?

Say what you'd change, with the line of output that shows it.

moto is not real AWS. Don't report these as bugs:

- table sizes show as 0 B
- S3's CloudWatch sizes are approximate
- timestamps show "just now" unless the seed backdates them
- IAM isn't enforced, so a missing-permission path can't be shown here (tests cover it with a stubbed error)

## HTML and screenshots

`--html PATH` also writes the notebook rendering of every report to a standalone page. Put it in the scratchpad
unless the user names a place, and give them the path. `--theme light|dark` sets the page theme. The
docs screenshots are `docs/images/<command>-{light,dark}.webp`, and each is taken from such a page in a browser.
This script makes the pages but doesn't take screenshots.

## Real AWS

Use `--live` (with optional `--region` / `--profile`) only when the user explicitly asks for it. It runs against
their real account with their credentials. The analyzers are read-only, but scans and reads are billed, so say
what the command will read before you run it.

## Adding demo data

A new command often needs data that sets off its findings. Add it to `seed_<service>()` in `demo.py`:

- Keep it deterministic (`random.Random(7)`) and small, so the demo runs in a few seconds.
- Backdate S3 objects with `put(..., age_days=N)`.
- Update the module docstring's list of demo data.

A new analyzer needs its own `seed_<service>()` and an entry in `SEEDERS`.
