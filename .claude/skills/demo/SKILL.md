---
name: demo
description: Run analyzer View commands against realistic synthetic data in moto or a fake Bedrock (no AWS account needed) and show exactly what a user would see, as text and optionally as the notebook HTML. Use to check how a new or changed report reads, to reproduce an output bug, or to prepare pages for the docs screenshots.
argument-hint: "<service> [python to run, e.g. ui.summary(\"s3://demo-lake/\")] [--html path] [--theme light|dark]"
allowed-tools: Bash(.venv/bin/python .claude/skills/demo/demo.py:*), Bash(python .claude/skills/demo/demo.py:*)
---

# Demo

`demo.py` starts moto, fills it with demo data built to set off most findings, builds the service's View in
text mode, and runs the Python you pass. `ui`, `core`, `mod` and the module under its own name (`s3`,
`dynamodb`, `bedrock_kb`) are in scope. moto has no Bedrock, so for `bedrock_kb` the seeder also hands the
analyzer fake Bedrock clients (see "Bedrock" below).

```bash
.venv/bin/python .claude/skills/demo/demo.py s3 'ui.summary("s3://demo-lake/")'
.venv/bin/python .claude/skills/demo/demo.py dynamodb 'ui.scan("orders", where={"status": "failed"}); ui.more()'
.venv/bin/python .claude/skills/demo/demo.py dynamodb 'ui.table_info("sessions")' --html <scratchpad>/t.html --theme dark
.venv/bin/python .claude/skills/demo/demo.py s3 'print(core.summarize("s3://demo-lake/raw/").object_count)'
.venv/bin/python .claude/skills/demo/demo.py bedrock_kb 'ui.use("support-docs"); ui.ask("How long do refunds take?")'
```

Arguments: `$ARGUMENTS`. The first word is the service. The rest is the code to run, plus any flags. If only a
service is given, run a short tour:

- s3: `ui.overview(); ui.bucket_info("demo-lake"); ui.summary("s3://demo-lake/")`
- dynamodb: `ui.tables(); ui.table_info("orders"); ui.schema("orders")`
- bedrock_kb: `ui.kbs(); ui.kb_info("support-docs"); ui.search("How long do refunds take?", kb="support-docs")`
- other services: `ui.help()` and then the service's overview command

`--help` describes the demo data. Most useful:

- **S3**, bucket `demo-lake` (versioned):
  - `raw/events/` has about 1,000 small files, and `raw/backfill/` duplicates a week of them.
  - `curated/` has parquet files and `customers.csv` with metadata and tags.
  - `exports/` holds one of those parquet files uploaded again in parts: same content, different ETag.
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
- **Bedrock** (knowledge base IDs are fixed, so calls can be copied):
  - `support-docs` (OpenSearch Serverless) has two data sources. `docs-s3` reads moto's `support-docs-bucket`
    and has a failed sync, 2 failed documents, 1 ignored one, and two files changed after its last sync.
    `help-center` is a WEB source with semantic chunking, a model parser and the RETAIN deletion policy.
  - `sales-playbooks` was never synced, `hr-policies` doesn't chunk, and `legacy-faq` is FAILED.
  - Retrieval ranks a small corpus of support passages. SEMANTIC matches concepts but not error codes, and
    HYBRID also matches exact words, so `compare("what does error E1234 mean?")` shows a difference.
    `holiday-shipping.md` isn't indexed yet, so a question about it misses in `evaluate()`.
  - `ask()` (the default engine) cites every sentence but the last one, so answers come out partly grounded.
    `engine="converse"` returns exact token counts.
  - `models()` lists Claude, Llama, Nova and Mistral text models, on demand or through `us.` profiles.

## Reading the output

This output is what a data scientist sees in the notebook. When you run a demo to review a command, check it
against the product rules in CLAUDE.md:

- Does the answer come first?
- Are all values in human units?
- Does every finding name a next step?
- Do the Next calls use this report's real arguments, and do they run as written?
- Are estimates and partial results labelled?

Say what you'd change, with the line of output that shows it.

moto is not real AWS. Don't report these as bugs:

- table sizes show as 0 B
- S3's CloudWatch sizes are approximate
- timestamps show "just now" unless the seed backdates them
- IAM isn't enforced, so a missing-permission path can't be shown here (tests cover it with a stubbed error)
- Bedrock's scores, answers and latencies come from the fake clients, not from a model. Judge how they're
  presented, not the retrieval quality. The fake checks each request against botocore's service model, so a
  malformed call still fails as it would against AWS.

## HTML and screenshots

`--html PATH` also writes the notebook rendering of every report to a standalone page. Put it in the scratchpad
unless the user names a place, and give them the path. `--theme light|dark` sets the page theme. The
docs screenshots are `docs/images/<command>-{light,dark}.webp` (Bedrock's are `bedrock-<command>-...`).

`shots.py`, next to `demo.py`, makes them: for every figure in the guides it runs the figure's command against
a scene in moto, renders the notebook HTML, screenshots it in headless Chrome (984 CSS px wide at 1.5x, so
1476 px), trims the empty space below, writes the light and dark WebP and sets the `<img height=>` in the
guide. It needs Pillow (`pip install pillow`) and Chrome (`$CHROME`, or Playwright's headless shell).

```bash
.venv/bin/python .claude/skills/demo/shots.py --list                 # every figure and the command behind it
.venv/bin/python .claude/skills/demo/shots.py summary what-if        # remake these
.venv/bin/python .claude/skills/demo/shots.py                        # remake all of them (about 2 minutes)
```

The S3 and DynamoDB scenes are the "acme" ones the guides are written around (`acme-ml-data`, `acme-app`),
at production scale: moto holds the files a figure opens, and the scene adds the listing, CloudWatch numbers and
DescribeTable counts around them. Bedrock's figures use `seed_bedrock_kb()`. After remaking a figure, look at it,
and check that its caption and `alt` text in the guide still say what it shows (counts in them come from the
scene). A new figure goes in `FIGURES` in `shots.py` and in the guide as a `<figure>` like the others.

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
