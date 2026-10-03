# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Copy-paste AWS analysis utilities for SageMaker / Jupyter notebooks. Each service is **one self-contained file**
in `analyzers/` (`s3.py`, `dynamodb.py`, `bedrock_kb.py` for Bedrock Knowledge Bases, `sagemaker_env.py` for the
SageMaker notebook itself and what's running) that a user pastes into a notebook cell or uploads next to a notebook
and `import`s. `sagemaker_env.py` isn't `sagemaker.py` because that would hide the SageMaker Python SDK. The same
files are also on PyPI as `aws-analyzer` (`from aws_analyzer import S3View`; see "The PyPI package" below), but the
build only copies them into the wheel unchanged, so they stay standalone and nothing in them depends on it.
The one exception to "one file" is `s3_explorer.py`, a **companion** to `s3.py`: a clickable file explorer (ipywidgets)
that imports `s3.py` for its previews, formatting and AWS calls, so users put both files next to the notebook.

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
  `what_if` shows the rule's JSON. Views show findings as one `_Findings` panel (warnings first; `empty=` says so
  when every check passed), and a main report ends with a `_Next` block: two or three calls worth running next,
  with the arguments filled in from the report by `_call(...)` (`get('orders', 'USER#0', 'ORDER#0000')`).
- **Put the answer first.** Start with a title and cards holding the few numbers that matter, then the tables of
  detail. A card gets a tone (`("Encryption", "none", "warn")`) only when a warning in the same report is about it. Use units people know: `human_size`, `human_money` (USD per month), `human_age`, and comma-separated
  counts. Avoid raw bytes, epoch times and DynamoDB JSON.
- **Be forgiving about input.** Accept `s3://bucket/prefix` or `bucket/prefix`, `"10MB"`, `"7d"` or
  `"2024-05-01"`, and key values of the wrong type (`"42"` for a number key). Pick sensible defaults so the
  one-argument call is useful.
- **Be honest about limits and cost.** Label estimates as estimates and show which prices were used. Say when a
  result is partial ("Scan stopped at the limit..."). Stop early on expensive scans by default, and show what the
  command itself read or cost.
- **Never show a traceback.** A missing permission, a missing optional package or a broken file becomes a short note
  that says what's missing and how to fix it. The rest of the report still renders.
- **Keep it discoverable.** `help()` is the entry point, so the first paragraph of each command's docstring should
  read as a plain description of what the user will see. Every public View command goes in one of the View's
  `_GROUPS` (a test checks it), and `help("name")` shows the whole docstring.

## Commands

```bash
pip install -r requirements-dev.txt            # pinned versions; moto, pytest, ruff, fastavro, openpyxl, pypdf, pypdfium2, pillow, tqdm
python -m pytest                               # all tests (moto, no AWS account needed)
python -m pytest tests/test_dynamodb.py        # one file
python -m pytest tests/test_s3.py::test_ls     # one test
python -m pytest -k "policy"                   # by name
ruff check .                                   # lint (errors only, see ruff.toml)
python -m build                                # the PyPI sdist and wheel, in dist/ (twine check --strict dist/*)
pip install -r requirements-docs.txt           # the guide site: mkdocs, mkdocs-material
mkdocs serve                                   # preview docs/ at http://127.0.0.1:8000
mkdocs build --strict                          # what the Docs workflow runs: broken links and anchors fail
```

CI (`.github/workflows/ci.yml`) also checks that each analyzer imports on its own with only boto3 installed, and
its Package job builds the wheel, runs `twine check` and imports the installed package with only boto3
(`.claude/skills/check/run.py` does both). To reproduce the first locally:

```bash
for f in analyzers/*.py; do d=$(mktemp -d); cp "$f" "$d/"; (cd "$d" && python -c "import $(basename "$f" .py)") && echo "ok: $f"; done
```

## Hard constraints

- **No imports between analyzers and no shared module.** Each file must work alone in a notebook. Helpers that
  every file needs (`human_size`, `human_money`, `_require`, `_in_notebook`, `_esc`, `_prose`, `_call`,
  `_signature`, the render blocks and `_render_html` / `_render_text` with their small helpers, `_friendly_errors`,
  `View._progress` with `_progress_bar_class` / `_progress_bar` / `_progress_text` / `_duration`, `View.help`) are
  deliberately duplicated in all four analyzers. Only the CSS root class, `_BADGE` and the View's `_GROUPS` /
  `_START` differ between the copies. When you fix or change one of them, check the copies in the others.
  The exception is a companion (`COMPANIONS` in `.claude/skills/check/rules.py`): `s3_explorer.py` imports `s3`
  (lazily, inside `_s3_module()`, so it still imports alone), reuses its helpers instead of copying them, and is
  left out of `drift.py`. Nothing imports a companion.
- **boto3 + stdlib only at import time.** pandas, pyarrow, IPython, pypdf, pypdfium2, pillow, openpyxl, etc. are
  optional and are imported lazily inside the function that needs them, via `_require(module, purpose)` (raises an
  ImportError that says what to `pip install`; pass `package=` when the pip name differs, `_require("PIL.Image",
  ..., "pillow")`) or a local `from IPython.display import ...`. tqdm (and ipywidgets for its
  notebook widget) is the exception that fails quietly: `_progress_bar_class` loads it with `importlib`, and without
  it the progress line is plain text.
- **Read-only against AWS.** Nothing writes to a bucket, table or knowledge base (e.g. S3 `deleted()` shows the
  restore call but never runs it, and Bedrock findings show the `start-ingestion-job` command instead of syncing),
  and nothing stops a notebook, deletes an app or endpoint, or deletes a local file (`sagemaker_env` shows the
  `aws sagemaker stop-notebook-instance ...` / `rm -rf ~/.../.Trash-1000/*` command instead).
  Bedrock `Converse` generates text and changes nothing, so its call line carries a `# read-only:` comment for
  `rules.py`. Keep it that way; README lists the read-only IAM permissions per service, so update that list
  when a new AWS API call is added. `rules.py` finds the services from `session.client("<literal name>", ...)`
  calls, so create each client with its service name spelled out (see `SageMakerAnalyzer._service`), or its
  operations go unchecked.
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

- Methods build a list of render blocks (`_Title`, `_Cards`, `_Findings`, `_Table`, `_Note`, `_Text`, `_Next`, in
  S3 also `_Frame`, `_Image`, `_Link`, `_Media`, `_Pages` (PDF pages drawn as pictures) and `_Flow` (a Word
  document laid out: headings, lists, tables and its pictures in place), and in Bedrock `_Passage` (a retrieved
  passage with `<mark>` highlights) and `_Answer` (an answer with shaded cited spans and `[n]` superscripts)) and pass them to
  `self._show(blocks)`, which renders HTML in Jupyter or plain text elsewhere (`mode="auto" | "html" | "text"`).
  Don't emit HTML or print directly; add to the block list so both renderers handle it.
- The HTML is plain HTML and CSS, never JavaScript (Jupyter drops scripts from reopened notebooks), so anything
  interactive uses CSS or `<details>`. What the blocks offer:
  - Cards take an optional tone: `(label, value, "warn" | "bad" | "ok")`. Text mode adds ` (!)` to warn and bad.
  - A table cell can be `_Tone(text, tone)`, a coloured pill in HTML and plain text elsewhere. `code_cols` shows
    columns of calls as code, and `prose_cols` passes columns of tool-written sentences (the Warnings tables)
    through `_prose`. Tables over 30 rows scroll under a sticky header.
  - `_Table(collapsed=True)` / `_Text(collapsed=True)` fold a secondary view (tags, raw JSON) under its title, and
    `_Text(code=True)` marks a snippet to copy: one click selects all of it.
  - `_prose` renders every tool-written sentence (notes, findings, table titles, subtitles): it escapes the text
    and shows the calls in it (`documents(status='FAILED')`) and AWS CLI commands as code that one click selects.
    Never use it on table cells that hold data.
- Every public View method is decorated with `@_friendly_errors`, which turns `ClientError` / `BotoCoreError` /
  data-decoding errors into a warning note instead of a traceback.
- `help()` lists public View methods by introspection, grouped by the View's `_GROUPS` (anything missing lands
  in "Other"), with their signatures without type hints and the **first paragraph of each docstring** as the
  description, after a "Start here" `_Next` from `_START`. `help("name")` shows one command's whole docstring.
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
- `sagemaker_env` also reads the machine it runs on: SageMaker's `/opt/ml/metadata/resource-metadata.json` (which
  says whether this is a notebook instance or a Studio app, and which), `/proc` (load, memory, uptime,
  processes and which are Jupyter kernels), the disks and `nvidia-smi`. `SageMakerAnalyzer(root=...)` points all of
  that at a folder of fake files, and `_shown()` / `_real()` make paths look and work as the machine sees them
  (`/home/sagemaker-user`, not the temp folder). `_cpu_count`, `_disk_usage` and `_gpu_query` are the other
  hooks tests and the demo replace. Local reads that fail go in `Machine.errors`, like AWS sections in `errors`.

Cost estimates come from module-level price tables (`S3_PRICES`, `DYNAMODB_PRICES`, `BEDROCK_PRICES`, and
`MODEL_PRICES` for $ per 1M tokens by model family, with `GLOBAL_MODEL_PRICES` for the cheaper `global.` inference
profiles, and `SAGEMAKER_PRICES`, storage plus the hourly price of each type in `INSTANCE_TYPES`, which also holds
its vCPUs, memory and GPUs; us-east-1 list prices with the date they were read) that callers override with `prices={...}` (and
`model_prices={...}`); the View shows whether list prices or the caller's prices were used. Check them against the
AWS Price List API (`pricing.us-east-1.amazonaws.com/offers/v1.0/aws/<AmazonS3|AmazonBedrock|
AmazonBedrockFoundationModels|AmazonES|AmazonSageMaker>/current/us-east-1/index.json`; `index.csv` is easier to
grep), which is what AWS bills from. A model missing from `MODEL_PRICES` shows its cost as unknown rather than a guess.

## The S3 explorer (`s3_explorer.py`)

Same five sections: pure helpers (`parse_location`, which also takes S3 console links and object URLs,
`breadcrumbs`, `sort_entries`, `filter_entries`, `folder_stats`), `S3Navigator` as the logic layer (one
`list_objects_v2` level per page, back / forward / up history, a folder cache, listing errors in `Folder.error`,
never prints), and `S3Explorer` as the UI. How the UI works:

- It finds `s3.py` with `_s3_module()`: the module `core` came from, else `import s3`, else `__main__` (s3.py pasted
  into a cell). Without it, a note; without ipywidgets, a text listing (`mode="text"` forces that).
- The right pane is a private `S3View` whose `_show` is replaced by `_capture`: its reports (`preview`, `head`,
  `document`, `download`, `link`, `summary`, `bucket_info`, `overview`) land in an `HTML` widget, with the `_Next`
  block dropped and the file's path shortened to its name. Progress bars go into an `Output` above it. `x.ui` is a
  normal `S3View` for the user's own cells.
- Still no JavaScript: every click is an ipywidgets `Button`, styled by the `<style>` in a hidden `HTML` widget
  (`.s3x` classes, overriding ipywidgets' own hover / focus shadows). A row is a full-width button under its size and
  age labels (`pointer-events:none`), so the whole row is the click target. Rows are pooled and reused.
- Widgets can't scroll, so `_renew()` puts the list or the report in a new box, which starts at the top.
- A click within `_CLICK_GRACE` seconds after the rows changed is dropped: it was aimed at the old rows (a double
  click on a folder would otherwise open whatever took its place).
- The path box navigates on Enter only: it listens for the `submit` message the text box sends (`on_submit` is
  deprecated), so leaving the box or clicking ✕ doesn't navigate.
- Callbacks go through `_guard()`, which turns any exception into a note on the right; an exception in a widget
  callback would otherwise go to Jupyter's log, and the click would seem to do nothing.

## The PyPI package

- `pyproject.toml` (hatchling) force-includes each `analyzers/*.py` unchanged into the wheel as
  `aws_analyzer/<name>.py`, next to `src/aws_analyzer/__init__.py`, which holds `__version__` and re-exports the
  Analyzer / View / Explorer classes lazily through a module `__getattr__` (importing `aws_analyzer` loads no
  analyzer). A new analyzer needs its `force-include` line (`tests/test_package.py` checks every file is listed) and
  its classes in `__init__.py`'s `__all__`, `_EXPORTS`, `_MODULES` and `TYPE_CHECKING` imports.
- The only code that knows about the package is `s3_explorer._s3_module()`, which looks for `s3` next to itself
  (`{__package__}.s3`) before `import s3`.
- The one dependency is `boto3>=1.35.72`, the first release whose service models have every AWS operation the
  analyzers call (Bedrock's `ListKnowledgeBaseDocuments`). A call to a newer operation means raising it; otherwise
  keep it low, so installing doesn't upgrade the boto3 a SageMaker image ships with. Dependabot's
  `versioning-strategy: increase-if-necessary` bumps the `==` pins in requirements files but leaves `>=` floors alone.
- Optional packages are extras: `data` (pandas, pyarrow), `files` (the S3 file readers), `notebook` (IPython,
  ipywidgets, tqdm), and `all`. A new optional package goes in one of them as well as in README's install lines.
- PyPI shows README.md, through `hatch-fancy-pypi-readme`, which points its relative links and pictures at GitHub
  and turns `> [!NOTE]` callouts into bold labels (PyPI renders neither). `twine check --strict` with
  `readme-renderer[md]` checks that it renders.
- Releasing: bump `__version__`, then publish a GitHub release tagged `v<version>`. `.github/workflows/release.yml`
  builds, checks, imports the wheel with only boto3, refuses a tag that doesn't match `__version__`, and uploads
  with PyPI trusted publishing from the `pypi` environment (no token stored).

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
- SageMaker: moto covers notebook instances (with an old instance-type list: no `ml.g5`), lifecycle configs,
  domains and STS, so `tests/test_sagemaker_env.py` uses it for notebook instances. moto has no `ListApps`,
  `DescribeApp` or spaces, so the Studio and `running()` tests use `Stubber` on injected clients
  (`SageMakerAnalyzer(clients={"sagemaker": ..., "sts": ..., "cloudwatch": ...})`, `max_workers = 1`), and
  `write_root()` / `fake_machine()` build the fake machine. demo.py's `seed_sagemaker_env()` returns fake clients
  and a fake root with sparse files, so the disk shows gigabytes without writing them.
- UI tests build the View with `mode="text"` and assert on `capsys` output through a small `run(capsys, fn, ...)`
  helper.
- `tests/test_s3_explorer.py` builds `S3Explorer(mode="widgets")` without a kernel (ipywidgets works without one),
  clicks with `button.click()`, sets text boxes' `.value`, sends the path box's Enter with
  `_handle_custom_msg({"event": "submit"}, [])`, sets `_CLICK_GRACE` to 0, and reads the right pane as blocks in
  `x.shown`.

## Docs and dependencies

- `README.md` is the user documentation: per service, a quick start, a command table, the pure functions, cost
  notes and IAM permissions. Update it together with the analyzer.
- `docs/` is the guide site: Markdown built by MkDocs with the Material theme (`mkdocs.yml`, versions pinned in
  `requirements-docs.txt`) and published to GitHub Pages by `.github/workflows/pages.yml` on pushes to `main`;
  pull requests only build it, with `--strict`. `use_directory_urls: false` keeps the pages at `s3.html`, ... so
  README links and old links still work. `index.md` is the home page with one card per service (Material grid
  cards); each service has its own guide (`s3.md`, `dynamodb.md`, `bedrock_kb.md`, `sagemaker_env.md`). A new
  analyzer gets its own `docs/<service>.md`, a card on `index.md`, an entry in `mkdocs.yml`'s `nav` and a link in
  README. `index.md` ends with a script that forwards old `/#section` links (from when it was the S3 guide) to
  `s3.html` when the id isn't on the home page. A guide's building blocks: section headings keep explicit ids
  (`## Permissions { #permissions }`) because README and other pages link to them; code blocks are fenced with a
  language and an optional `title="IAM policy"`; callouts are `!!! note ""` / `!!! warning ""`, troubleshooting
  entries `??? question "..."`, card grids `<div class="grid cards" markdown>`, the set-up steps
  `<div class="steps" markdown>` and the command reference `<div class="ref" markdown>`; `docs/stylesheets/extra.css`
  styles them. A screenshot is two images and a caption:
  `![alt](images/<name>-light.webp#only-light){ width="984" height="..." loading=lazy }`, the same line with
  `-dark` / `#only-dark` (Material shows the one for the reader's theme), then `/// caption` ... `///`.
  The screenshots (`docs/images/*-{light,dark}.webp`) are the tool's own output, made by
  `.claude/skills/demo/shots.py` from the "acme" scenes the guides are written around (S3, DynamoDB) and demo.py's
  fake Bedrock and SageMaker: `shots.py <name>` remakes one figure and sets the `height=` of both its images.
  The explorer is a live widget, so its figures (`explorer`, `explorer-docx`, `explorer-buckets`, and `explorer-tour`,
  an animated WebP of a pointer clicking through it) come from `explorer_shots.py`, which runs it in a real JupyterLab
  with Playwright (`pip install jupyterlab playwright`). Remake the affected figures when a report's look changes, and check
  their captions and alt text still match, in the guides and in README, which shows seven of them (`overview`,
  `dynamodb-table-info`, `preview-parquet`, `explorer-tour`, `dynamodb-scan-filter`, `bedrock-ask`, `sagemaker-instance`) as `<picture>`s that switch to
  the `-dark` file in dark mode.
- Versions in `requirements-dev.txt` (which also pins `build`, `twine` and `readme-renderer[md]` for the package
  checks) and `requirements-docs.txt` are pinned and updated by Dependabot; the
  `python_version < "3.11"` lines are intentionally held back, and so is mkdocs at 1.x (2.0 drops the plugins and
  themes Material needs). `ruff.toml` selects only `E4`, `E7`, `E9`, `F` (real errors, not style), listed
  explicitly so ruff upgrades don't change them; there is no formatter, and lines run to about 120 characters.
