# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Copy-paste AWS analysis utilities for SageMaker / Jupyter notebooks. Each service is **one self-contained file**
in `analyzers/` (`s3.py`, `dynamodb.py`, `bedrock_kb.py` for Bedrock Knowledge Bases) that a user pastes into a
notebook cell or uploads next to a notebook and `import`s. There is no package, no `setup.py` / `pyproject.toml`, and no build step.

## Product goal: help the user decide what to do

The tool should be simple to use, and its output should help the user decide what to do next. It should not just
dump AWS data. Assume the reader is a data scientist in a notebook, not an AWS expert. Judge every new command and
report against these rules (the existing code follows them, so match it):

- **Say what it means, not only what it is.** Explain things in plain English, and keep the raw JSON as a secondary
  view. `policy()` and `bucket_info()` describe bucket policies and lifecycle rules as sentences, and `get()`
  expands nested items.
- **Findings should lead to an action.** A `*_findings` function returns `("warn" | "info", message)` pairs. Each
  message says what's wrong and why it matters, puts a price on it when it can ("costing $12.40/month"), and
  names the next step: a command to run (`"... (see what_if)"`), a setting to change, or the exact call to copy.
  For example, `table_info` shows the `query(...)` call for each index, `deleted` shows the restore call, and
  `what_if` shows the rule's JSON.
- **Put the answer first.** Start with a title and cards holding the few numbers that matter, then the tables of
  detail. Use units people know: `human_size`, `human_money` (USD per month), `human_age`, and comma-separated
  counts. Avoid raw bytes, epoch times and DynamoDB JSON.
- **Be forgiving about input.** Accept `s3://bucket/prefix` or `bucket/prefix`, `"10MB"`, `"7d"` or
  `"2024-05-01"`, and key values of the wrong type (`"42"` for a number key). Pick sensible defaults so the
  one-argument call is useful.
- **Be honest about limits and cost.** Label estimates as estimates and show which prices were used. Say when a
  result is partial ("Scan stopped at the limit..."). Stop early on expensive scans by default, and show what the
  command itself read or cost.
- **Never show a traceback.** A missing permission, a missing optional package or a broken file becomes a short note
  that says what's missing and how to fix it. The rest of the report still renders.
- **Keep it discoverable.** `help()` is the entry point, so the first line of each command's docstring should read
  as a plain description of what the user will see.

## Commands

```bash
pip install -r requirements-dev.txt            # pinned versions; moto, pytest, ruff, fastavro, openpyxl, pypdf, tqdm
python -m pytest                               # all tests (moto, no AWS account needed)
python -m pytest tests/test_dynamodb.py        # one file
python -m pytest tests/test_s3.py::test_ls     # one test
python -m pytest -k "policy"                   # by name
ruff check .                                   # lint (errors only, see ruff.toml)
```

CI (`.github/workflows/ci.yml`) also checks that each analyzer imports on its own with only boto3 installed.
To reproduce that locally:

```bash
for f in analyzers/*.py; do d=$(mktemp -d); cp "$f" "$d/"; (cd "$d" && python -c "import $(basename "$f" .py)") && echo "ok: $f"; done
```

## Hard constraints

- **No imports between analyzers and no shared module.** Each file must work alone in a notebook. Helpers that
  every file needs (`human_size`, `human_money`, `_require`, `_in_notebook`, `_esc`, the render blocks and
  `_render_html` / `_render_text`, `_friendly_errors`, `View._progress` with `_progress_bar_class` /
  `_progress_bar` / `_progress_text` / `_duration`, `View.help`) are deliberately duplicated in all three
  analyzers. When you fix or change one of them, check the copies in the other two.
- **boto3 + stdlib only at import time.** pandas, pyarrow, IPython, pypdf, openpyxl, etc. are optional and are
  imported lazily inside the function that needs them, via `_require(module, purpose)` (raises an ImportError
  that says what to `pip install`) or a local `from IPython.display import ...`. tqdm (and ipywidgets for its
  notebook widget) is the exception that fails quietly: `_progress_bar_class` loads it with `importlib`, and without
  it the progress line is plain text.
- **Read-only against AWS.** Nothing writes to a bucket, table or knowledge base (e.g. S3 `deleted()` shows the
  restore call but never runs it, and Bedrock findings show the `start-ingestion-job` command instead of syncing).
  Bedrock `Converse` generates text and changes nothing, so its call line carries a `# read-only:` comment for
  `rules.py`. Keep it that way; README lists the read-only IAM permissions per service, so update that list
  when a new AWS API call is added.
- **Python 3.10 floor.** CI runs 3.10–3.14; ruff `target-version = "py310"`. On 3.10, `requirements-dev.txt`
  installs pandas 2 / IPython 8, on 3.11+ pandas 3 / IPython 9, so code must work with both majors.
  Files use `from __future__ import annotations`.

## Architecture of an analyzer file

Every analyzer has the same five numbered sections, marked by `# ====` banner comments, in this order:

1. **Helpers**: parsing and formatting (`parse_size`, `parse_time` accepting `"7d"` / `"2024-05-01"`, `human_*`,
   DynamoDB JSON ↔ plain Python conversion, S3 format sniffing and the stdlib-only Avro / DOCX / PPTX parsers).
2. **Data models**: `@dataclass`es that the analyzer returns (`ObjectInfo`, `PrefixSummary`, `BucketConfig`,
   `ItemPage`, `TableInfo`, `TableProfile`, `Retrieval`, `Answer`, ...). Some have a `to_df()` that lazily requires
   pandas.
3. **Pure analysis**: module-level functions with **no AWS calls** (`summarize_objects`, `simulate_lifecycle_objects`,
   `explain_policy`, `profile_items`, `build_filter`, `build_prompt`, `*_findings`, `*_monthly_cost`, ...). They
   are public API: users run them on S3 Inventory rows, DynamoDB exports or their own passages. Put new analysis logic here when it doesn't need AWS,
   so it can be unit-tested without moto.
4. **`<Service>Analyzer`**: the logic layer. Talks to AWS, returns section-2 data, **never prints**. Takes
   `session` / `region` / `profile` / `client` / `prices`. Long scans accept `limit=` and a `progress=` callback.
   Config sections the caller can't read are recorded in an `errors` dict on the result (section → error code)
   instead of raising, so missing IAM permissions degrade to a note.
5. **`<Service>View`**: the notebook UI. Wraps an analyzer as `self.core`; each public method renders a report and
   returns `None`. Many share a name with their data method (`ls`, `find`, `scan`, `query`), but not all
   (`summary` → `summarize`, `what_if` → `simulate_lifecycle`, `schema` → `profile`, `table_info` → `describe`).

How the View layer works:

- Methods build a list of render blocks (`_Title`, `_Cards`, `_Table`, `_Note`, `_Text`, in S3 also `_Frame`,
  `_Image`, `_Link`, `_Media`, and in Bedrock `_Passage` (a retrieved passage with `<mark>` highlights) and `_Answer`
  (an answer with shaded cited spans and `[n]` superscripts)) and pass them to `self._show(blocks)`, which renders HTML in Jupyter or plain text
  elsewhere (`mode="auto" | "html" | "text"`). Don't emit HTML or print directly; add to the block list so both
  renderers handle it.
- Every public View method is decorated with `@_friendly_errors`, which turns `ClientError` / `BotoCoreError` /
  data-decoding errors into a warning note instead of a traceback.
- `help()` lists public View methods by introspection, using the **first line of each docstring** as the
  description.
- Long operations wrap the analyzer call in `with self._progress(label, unit) as tick:` and pass `progress=tick`.
  The analyzer calls `progress(count)` with a running count, or `progress(done, total)` when it knows the total
  (a new total starts a new bar; `unit="B"` counts bytes). `_progress` shows a tqdm bar when tqdm is installed and a
  plain line with the rate and time left otherwise, only one at a time (a nested `_progress` replaces the outer
  bar), and nothing with `View(progress="off")`. Work spread over threads reports progress from the calling thread
  only (S3's `_run_in_threads`), never from a worker, so notebook widgets aren't touched from other threads.
- `DynamoDBView` keeps `self._pager` so `more()` continues the last `scan` / `query` / `sql`. `BedrockKBView` keeps
  `self._last` (the last search or answer, for `chunk()`) and `self._conversation` (for `follow_up()`), and
  `self.kb`, the default knowledge base that `use()` sets.
- Text from a knowledge base is untrusted: HTML blocks escape every piece before wrapping it in markup, and
  `build_prompt` sends passages to a model as data inside `<source>` tags, never as instructions.

Cost estimates come from module-level price tables (`S3_PRICES`, `DYNAMODB_PRICES`, `BEDROCK_PRICES`, and
`MODEL_PRICES` for $ per 1M tokens by model family; us-east-1 list prices with the date they were read) that callers
override with `prices={...}` (and `model_prices={...}`); the View shows whether list prices or the caller's prices
were used. A model missing from `MODEL_PRICES` shows its cost as unknown rather than a guess.

## Tests

- `tests/conftest.py` puts `analyzers/` on `sys.path` so tests `import s3` / `import dynamodb` the way a notebook
  would, and sets fake AWS credentials plus `AWS_DEFAULT_REGION=us-east-1`.
- One test file per analyzer. Pure functions are tested directly; AWS-backed methods use an `aws` fixture wrapping
  `moto.mock_aws()` and fixtures that seed a bucket / table. A new AWS service needs its moto extra added to
  `moto[...]` in `requirements-dev.txt`.
- Bedrock has little moto support (moto 5.2 only creates / gets / lists / deletes knowledge bases), so
  `tests/test_bedrock_kb.py` uses botocore `Stubber` with injected clients:
  `BedrockKBAnalyzer(client=agent, clients={"bedrock-agent-runtime": ..., "bedrock-runtime": ..., "bedrock": ...})`.
  Stubber answers in the order calls are queued and checks each request against the service model; set
  `core.max_workers = 1` when a test lists several knowledge bases. moto is only used for the S3 bucket behind
  `unsynced()`. For the same reason the Bedrock seeder in `.claude/skills/demo/demo.py` returns fake clients
  (`_FakeAWS`, which validates requests and responses against the service model) that `demo.py` passes to the
  analyzer; only the bucket is moto.
- UI tests build the View with `mode="text"` and assert on `capsys` output through a small `run(capsys, fn, ...)`
  helper.

## Docs and dependencies

- `README.md` is the user documentation: per service, a quick start, a command table, the pure functions, cost
  notes and IAM permissions. Update it together with the analyzer.
- `docs/` is the guide site: plain static HTML published to GitHub Pages by `.github/workflows/pages.yml` on pushes
  to `main` that touch `docs/`. `index.html` is the home page with one card per service; each service has its own
  guide (`s3.html`, `dynamodb.html`, `bedrock_kb.html`) that links back to it. A new analyzer gets its own
  `docs/<service>.html`, a card on `index.html` and a link in README. `index.html` also forwards old `/#section`
  links (from when it was the S3 guide) to `s3.html`, so keep its own ids in the `own` list there. The screenshots
  (`docs/images/*-{light,dark}.webp`) are the tool's own output from a demo bucket, demo tables and demo knowledge
  bases (`/demo --html`, then 1476 px wide WebP; `bedrock-*` for the Bedrock guide).
- Versions in `requirements-dev.txt` are pinned and updated by Dependabot; the `python_version < "3.11"` lines are
  intentionally held back. `ruff.toml` selects only `E4`, `E7`, `E9`, `F` (real errors, not style), listed
  explicitly so ruff upgrades don't change them; there is no formatter, and lines run to about 120 characters.
