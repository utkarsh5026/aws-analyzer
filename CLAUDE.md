# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

AWS analysis utilities for SageMaker / Jupyter notebooks, installed with pip as `aws-analyzer`
(`from aws_analyzer import S3View`; see "The PyPI package" below). Each service is **one module** in
`src/aws_analyzer/` (`s3/` (a package, split by layer: see "Architecture of a service package"), `dynamodb/` (a package too), `bedrock_kb/` (a package too) for Bedrock Knowledge Bases and `explore()`, a window to look
through one by clicking, `bedrock_chat/` (a package too) for a chat window on a knowledge base, `sagemaker_env/` (a package too) for the SageMaker notebook itself, its Python environments and what's running, `opensearch/` (a package too) for
OpenSearch vector indexes in Service domains and Serverless collections, `lambda_functions/` (a package too) for Lambda functions in one
region or all of them, and `explore()`, a window to click through them and read their logs run by run). The code they
all share lives once in `src/aws_analyzer/_kit/`, and what the two Bedrock modules share in
`src/aws_analyzer/_bedrock/` (see "Hard constraints"). `sagemaker_env` isn't `sagemaker` so it's
never mixed up with the SageMaker Python SDK (when each analyzer was a file next to the notebook, a `sagemaker.py`
would have hidden it), and `lambda_functions` isn't `lambda` because `lambda` is a Python keyword
(`from aws_analyzer import lambda` is a syntax error). Up to 0.13.0 each analyzer was also a standalone file to copy
next to a notebook; that's gone, and the package is the one way in.
`s3/explorer/` is S3's window: a clickable file explorer (ipywidgets) on the package's previews, formatting and AWS
calls; `s3_explorer.py`, where it was until 0.20.0, only re-exports it now (an alias: `ALIASES` in `rules.py`).
`agent/` is a companion to `lambda_functions` and `s3`: `AgentView().ask("Which
Lambda failed last night?")`, an agent (LangChain's deepagents, on a model the user picks: Amazon Bedrock, OpenAI, Azure
OpenAI or any LangChain provider) answering questions from
read-only tools built on `LambdaAnalyzer` and `S3Analyzer` (see "The agent" below). It's the one analyzer that is a package rather
than a file, because it grows by toolkits (one module per AWS service it has tools for) and by capabilities of the
agent itself, each in its own module.

## In progress: each service becomes a package

The service modules are being split into packages (core: `helpers`, `models`, `analysis`, `analyzer`; UI: `style`,
`blocks`, `view`, `explorer`), one service per PR, with no change a user can see. The plan, the rules each step keeps,
the recipe and the progress table are in `.claude/skills/modularize/SKILL.md` (`/modularize`). Read it before
changing a service that the table shows as started.

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
ruff check .                                   # lint: real errors and the style rules ruff can check (ruff.toml)
python -m build                                # the PyPI sdist and wheel, in dist/ (twine check --strict dist/*)
pip install -r requirements-docs.txt           # the guide site: mkdocs, mkdocs-material
mkdocs serve                                   # preview docs/ at http://127.0.0.1:8000
mkdocs build --strict                          # what the Docs workflow runs: broken links and anchors fail
python .claude/skills/check/snapshot.py run OUT [--root CHECKOUT]  # every report as text and HTML (pip install time-machine)
python .claude/skills/check/snapshot.py compare BASE NEW           # what a refactor changed in them (see /check)
```

CI (`.github/workflows/ci.yml`) also checks that each module of the package (and the `agent/` package) imports with
only boto3 installed, each in a fresh interpreter, and its Package job builds the wheel, runs `twine check` and imports the installed package
with only boto3 (`.claude/skills/check/run.py` does both, blocking every other package). To reproduce the first
locally, in an environment with only boto3:

```bash
cd src && for f in aws_analyzer/[!_]*.py aws_analyzer/[!_]*/__init__.py; do m="${f%/__init__.py}"; python -c "import aws_analyzer.$(basename "$m" .py)" && echo "ok: $m"; done
```

## Hard constraints

- **No imports between analyzers; shared code lives in `_kit/`.** An analyzer imports only the standard library,
  boto3 / botocore and `._kit` (a companion also its parent, and the two Bedrock modules `._bedrock`), so importing
  one loads no other
  (`tests/test_package.py` checks). `src/aws_analyzer/_kit/` holds what they all need, written once: `fmt.py`
  (`human_size`, `human_money`, `human_age`, `parse_size`, `parse_time`, `_plural`, `_duration`, `_fmt_dt`,
  `_as_int`, `HOURS_PER_MONTH`, `file_extension`, `Stat`, ..., and the time zone reports show times in:
  `set_timezone`, public as `aws_analyzer.set_timezone`, or the `AWS_ANALYZER_TZ` environment variable, UTC by default;
  `_in_zone` converts a moment, `_fmt_dt(moment, zone=True)` names its zone in a sentence and `_tz_label()` in a
  column's header, so never write "UTC" after a time; CloudWatch's daily numbers stay in UTC days and say so, and naive
  times `parse_time` reads are in that zone; `tests/conftest.py` sets UTC back after each test), `text.py` (`_esc`,
  `_clip`, `_pad`, `_width`, `_text_bar`, and the windows' search: `search_rank`, `_marked`), `deps.py` (`_require`, `_in_notebook`), `errors.py`
  (`_error_code`, `_error_name`, `_why`, `_Hint`), `blocks.py` (the
  render blocks every service uses, `_Title`, `_Cards`, `_Table`, `_Note`, `_Text`, `_Findings`, `_Next` and the
  `_Tone` cell, and the registry of how each kind is drawn: `_Report`, `@_draws`) and `render.py` (`_base_css`,
  `_prose`, `_lead`, `_call`, `_signature`, the shared blocks' drawers with the tables' sorting, filters and key
  columns, `_report_html`, `_render_text`, `_blocks_html`),
  `progress.py` (`_progress_bar_class`, `_progress_bar`, `_progress_text`), `view.py` (`_BaseView`, which every
  `<Service>View` subclasses: `_show`, `_progress`, `help()`, `_price_basis`; `@_friendly_errors`; `_commands`) and
  `analyzer.py` (`_BaseAnalyzer`, which every `<Service>Analyzer` subclasses: the session, `_config`, `prices`,
  `region`, `_cached_client`, `_paginate`, `_map`; see "Architecture of an analyzer file"), `highlight.py`
  (`_python_html`, `_json_source_html`: code in colour, for the chat's code to copy and the Lambda explorer's Code tab),
  `jsontree.py` (the `_JsonTree` block, JSON you can fold: S3's .json previews and Lambda's configuration; a service
  that shows one adds `_json_tree_css(_ROOT)` to its `_EXTRA_CSS` in `_base_css`), `themes.py` (how a View's reports
  look: `set_theme`, public as `aws_analyzer.set_theme`, or the `AWS_ANALYZER_THEME` environment variable, `console`
  by default; `THEMES` are console, soft, editorial, brand and classic, the stylesheet's own look with no rules of its
  own; `_theme_css(root, name)` scopes every rule of a theme to `.<root>.theme-<name>`, the class `_report_html` puts
  on the report's box, because a notebook applies every output's `<style>` to the whole page and an earlier output
  must keep its theme; the tones are custom properties, lighter on a dark page (`_DARK_PAGES`, `_UNSET_PAGE`, which
  the windows' terminal theme reads too); `_BaseView._show` passes `theme=_theme_name()`, and the windows call
  `_render_html` with none, so they keep their own look; a new rule in `_BASE_RULES` for a shared block may need a
  line in each theme, and `demo.py --look` / a `Figure(theme=)` in `shots.py` show one)
  and `window.py`, what the four notebook windows share (see "The windows' kit" under "How the View layer works").
  Each analyzer imports the names it uses (`from ._kit.fmt import human_size, ...`), so `aws_analyzer.s3.human_size`
  still works, and `rules.py` fails when an analyzer defines a name `_kit` has: a copy. `_kit` never imports an
  analyzer, and it follows the same import-time rule as they do. Each service sets its look: `_ROOT` (its CSS root
  class), `_CSS = _base_css(_ROOT, _EXTRA_CSS)` (the shared rules, then its own blocks' rules; the chat also passes
  `theme=`, `restyle=` and `after=`, because its reports match its window), `_BADGE`, `_LOOK = _Look(_CSS, _ROOT,
  _BADGE)`, and `_render_html(blocks, max_rows, theme="")`, which is `_report_html` with its `_LOOK` and the theme to
  draw in (its View's `_render_html` attribute points at it).
  What `bedrock_kb/` and `bedrock_chat/` share is written once in `src/aws_analyzer/_bedrock/`, a package by
  topic: the Bedrock price tables, `DEFAULT_MODEL`, `model_price` and `short_model` (`prices.py`), the region's model
  list (`parse_models` in `foundation_models.py`, into `ModelInfo`), what Bedrock returns (`Passage`, `Citation`,
  `KBDocument`, `ModelInfo` in `models.py`; `parse_retrieve`, `parse_document`, ... in `parsing.py`), knowledge base
  IDs, source names and name matching (`helpers.py`), the filter helpers (`build_filter`, `with_data_sources`,
  `describe_sources` in `filters.py`), the cost and token estimates (`estimates.py`), the question's words and
  snippets (`words.py`), the answer's markdown (`_Markdown`, `_with_markers`, `_answer_lines` in `markdown.py`), the
  `_Answer`, `_Json` and `_Download` blocks with their drawings and `_file_list_blocks` (`blocks.py`, the one UI
  module), and a file's link and the list of files saved as text (`presigned_url`, `parse_list_link`,
  `file_list_lines`, `write_file_list`, `SavedFileList`, `_notebook_href` in `files.py`). It's Bedrock's, so
  it isn't in `_kit`: only those two import it (`SHARED` in `rules.py`), it imports `_kit` and never an analyzer, and
  `rules.py` fails when either defines one of its names. Each imports the names it uses from the module that defines
  them, and the public ones it doesn't as `from ._bedrock.prices import model_price as model_price` (a re-export ruff
  accepts), so `bedrock_chat.model_price` still works; `_bedrock/__init__.py` re-exports only the public names. The price tables are one dict for both: `prices=` / `model_prices=` copy them per analyzer, so nothing edits
  them. Some names are the same in both files but mean different things, and stay in each: `Answer`, `DEFAULT_PROMPT`,
  `parse_rag`, `describe_filter`, `answer_findings`, `_question_text` and the View's `_answer_blocks`.
  A test that patches a helper patches it where it's looked up: on the analyzer module for a call the analyzer
  makes (`monkeypatch.setattr(s3mod, "_require", ...)`, `monkeypatch.setattr(chatmod, "_cell_number", ...)`: the
  chat's View asks it), on the `_kit` (or `_bedrock`) module for a call it makes
  (`monkeypatch.setattr(viewmod, "_progress_bar_class", ...)` with `from aws_analyzer._kit import view as viewmod`:
  `_ProgressLine`, the line `_BaseView._progress` shows, makes that call, and `_BaseView.__init__` asks
  `_in_notebook` there too; an explorer's once-per-cell display asks `_cell_number` on `_kit.window`).
  The exception is a companion (`COMPANIONS` in `.claude/skills/check/rules.py`): the `agent/` package imports
  `lambda_functions` (its Lambda toolkit's `_lambda_module()`, imported when the first tool runs; its type hints
  import it under `TYPE_CHECKING`) and reuses it instead of copying it, and so it does with every analyzer a later
  toolkit reads through (`COMPANIONS["agent"]` lists them), and the third user of `_bedrock/` (`SHARED`), for the
  `_Answer` block its answers are drawn with. `rules.py` checks each of its modules like an analyzer file, reading
  relative imports from the module's own package (`from ..budget import` is the agent's, `from ...lambda_functions
  import` the analyzer). Nothing imports a companion.
- **boto3 + stdlib only at import time.** pandas, pyarrow, IPython, pypdf, pypdfium2, pillow, openpyxl, etc. are
  optional and are imported lazily inside the function that needs them, via `_require(module, purpose)` (raises an
  ImportError that says what to `pip install`; pass `package=` when the pip name differs, `_require("PIL.Image", ...,
  "pillow")`) or a local `from IPython.display import ...`. tqdm (and ipywidgets for its notebook widget) is the
  exception that fails quietly: `_progress_bar_class` (`_kit/progress.py`) loads it with `importlib`, and without it the
  progress line is plain text. `bedrock_chat`'s window loads ipywidgets with `_require` in `app()`. The agent's
  packages (deepagents, langchain-openai, langgraph, langchain's middleware types) load with `_require` in
  `AgentAnalyzer._harness()`, `_chat()` and `_hide_tools()`, the first time `ask()` runs.
- **Read-only against AWS.** Nothing writes to a bucket, table or knowledge base (e.g. S3 `deleted()` shows the
  restore call but never runs it, and Bedrock findings show the `start-ingestion-job` command instead of syncing),
  and nothing stops a notebook, deletes an app or endpoint, or deletes a local file (`sagemaker_env` shows the
  `aws sagemaker stop-notebook-instance ...` / `rm -rf ~/.../.Trash-1000/*` command instead). The one exception is
  `s3`'s `clean_downloads()` (`s3/analyzer/downloads.py`, with every guard below), which deletes what the user
  downloaded, and only from a downloads folder s3 made:
  `S3Analyzer._make_downloads_folder` writes `_DOWNLOADS_MARK` (a `.gitignore` of `*`) when the folder is new or
  empty, `_made_for_downloads` checks for it, and `_protected_folder` refuses the notebook's own folder, the home
  folder and the ones above them, even when marked. Local files are only written where the user asks: S3 downloads,
  `lambda_functions`' `download()` and the explorer's Download .zip (`LambdaAnalyzer._save_package`: one .zip, written
  to `<name>.zip.part` then renamed, in `lambda-downloads/` or the path given; a downloads folder it makes gets its own
  `_DOWNLOADS_MARK`, and nothing in Lambda's module ever deletes a download) and `how_it_works_page()` / the explorer's
  ↗ Open in a tab (`_save_page`, through the same `_write_download`: `<name>-how-it-works.html`, the report drawn by
  `style._render_page` as a whole document, `_kit.render._page_document`), `bedrock_chat`'s `save_runs()` /
  `log=`, which only append lines, and both Bedrock modules' `save_file_list()`, the KB explorer's ⬇ List and the
  chat's Files list's ⬇ List (`_bedrock.write_file_list`: one .txt of the files' paths or links, written to
  `<name>.part` then renamed, beside the notebook or at the path given; it replaces a file of that name and never
  deletes one), and `sagemaker_env`'s `new_env()`, which makes a Python environment and its Jupyter kernel
  (`SageMakerAnalyzer.create_env`, in `sagemaker_env/envs.py`'s `_EnvMaking` with nothing else, runs an `env_plan`'s commands, conda / mamba / micromamba or uv, pip and
  `ipykernel install --user`, through `_run`, a subprocess without a shell; it writes the environment's folder and its
  kernel, refuses a folder that's there and isn't an environment or has another Python, and never removes anything:
  findings show `rm -rf ...` instead).
  Bedrock `Converse` generates text and changes nothing, so its call line carries a `# read-only:` comment for
  `rules.py` (RetrieveAndGenerate and RetrieveAndGenerateStream pass as `Retrieve*`; `rules.py` maps the stream to
  the `bedrock:RetrieveAndGenerate` permission), and so does `opensearch`'s `InvokeModel` (in `opensearch/analyzer.py`), which only
  embeds a question. `opensearch` also talks to each domain's or collection's own REST API, signing requests with botocore's
  `SigV4Auth` (service `es` or `aoss`) and sending them with botocore's `URLLib3Session`, so no OpenSearch client
  library is needed. `rules.py` can't see those calls: `OpenSearchAnalyzer.request()` sends GET, and POSTs only to
  `_search` / `_count` (`_READ_POSTS`), refusing anything else before it's sent, and `tests/fake_opensearch.py` fails a
  test on any other request. Index fixes (replicas, force merge) are shown as opensearch-py calls, never run.
  `lambda_functions` never invokes, changes or deletes a function (findings show the `aws lambda ...` /
  `aws logs put-retention-policy ...` command instead); its `filter_log_events` call lines carry `# read-only:`
  comments (`FilterLogEvents` isn't a Get/List/Describe name), and `code()` and `download()` download the deployment
  package with urllib from the short-lived S3 link `GetFunction` returns (`LambdaAnalyzer._download`, https only),
  which `rules.py` can't see. `how_it_works()` reads the package's Python with `ast` and never runs, imports or
  `exec`s any of it. Environment variable values never reach a report: `parse_function` keeps their names, and
  `Function.raw` (the configuration shown folded) has the values replaced by `(hidden)`. The agent only calls the
  analyzers' read methods, and `_Guard` hooks every client a toolkit's analyzer makes for it (`attach` swaps its
  `session` for a `_GuardedSession`, whose `client()` hooks what it makes, and wraps Lambda's `_service`, which also
  hands out clients passed in) on `before-parameter-build`, refusing any operation outside `READ_PREFIXES` (Get, List,
  Describe, Head, Filter) with `ReadOnlyError` before its request is built; the S3 tools read names, sizes, dates and
  headers, never an object's body; the model is reached through LangChain, not boto3, so
  `rules.py` sees none of it. Keep it that way; README lists the read-only IAM permissions per service, so update that list
  when a new AWS API call is added. `rules.py` finds the services from `session.client("<literal name>", ...)`
  calls, so create each client with its service name spelled out (see `SageMakerAnalyzer._service`), or its
  operations go unchecked.
- **Python 3.10 floor.** CI runs 3.10–3.14; ruff `target-version = "py310"`. On 3.10, `requirements-dev.txt`
  installs pandas 2 / IPython 8, on 3.11+ pandas 3 / IPython 9, so code must work with both majors. The agent's
  packages need 3.11 (`harness._MIN_PYTHON`): `agent/` still imports on 3.10, `ask()` says what it needs, the `[agent]`
  extra and the dev pins carry `python_version >= '3.11'`, and its tests that need them skip there.
  Files use `from __future__ import annotations`.

## Code style

These apply to every line you write or change. Every docstring already follows them; the comments and the
structural rules are being brought in one module at a time, so bring a function in line when you're editing it
anyway, but don't reformat files you aren't otherwise touching.

- **No `#` comments to explain code.** Make the code say it: a well-named function, variable or constant instead of
  a comment above it, and the reasoning in the docstring of the function it belongs to. Only these stay:
  - the `# ====` section banners in an analyzer file,
  - the `# read-only:` markers `rules.py` reads on `Converse`, `InvokeModel` and `filter_log_events` call lines,
  - tool directives: `# noqa: ...`, `# type: ignore[...]`, `# pragma: no cover`.
- **Docstrings open and close on their own lines**, the text between them, even when it's one sentence:

  ```python
  def human_size(n: int) -> str:
      """
      1536 -> '1.5 KB' (binary units, like the AWS console).
      """
  ```

  Never `"""1536 -> '1.5 KB'."""` on one line, and never text on the line with the opening or closing quotes. The
  first paragraph is still what `help()` and the agent's tool list show (they read docstrings through
  `inspect.getdoc` and whitespace-collapsing, so the blank lines don't change what users see), so keep it a plain
  description of what the user gets. Module docstrings follow the same shape.
- **What checks them.** `tests/test_style.py` fails on a docstring with text beside its quotes anywhere in `src/`,
  `tests/` or `.claude/skills/`, and on a file with more `#` comments than its number in `COMMENTS_LEFT`; when you
  remove comments it fails until you lower that number, so they can't come back. `ruff.toml` checks the rest it can
  (mutable defaults, broad excepts, commented-out code, complexity, too many arguments, long functions); files
  written before these rules are listed in its `per-file-ignores` for what they already broke. Take a rule off a
  file once the file passes it, and never add a file or a rule to that list.

### Writing code that reads easily

The reader should understand a function from top to bottom in one pass, without jumping around or decoding anything.

#### Names

- Name things by what they hold or do, in full words: `failed_runs`, not `fr`, `res` or `tmp`; `bucket_name`, not
  `bn`. Short names are fine only for a loop index (`i`) or a tiny lambda.
- Functions are verbs (`read_policy`, `count_arrivals`), values are nouns (`policy`, `arrivals`), booleans read as
  a yes/no question (`is_empty`, `has_versions`, `should_retry`).
- No magic numbers or strings in logic: give them a module-level constant with a name that says what they mean
  (`_MAX_PICTURES = 20`, `_LIVE_SECONDS = 5`), not `if len(pages) > 20`.
- Keep the shared names `_kit` already has (`_esc`, `_clip`, `_fmt_dt`, ...); this is about new names.

#### Functions

- One function, one job. If you need "and" to describe it, or it's longer than about 40 lines, split it into
  well-named helpers that read like the steps of the job.
- Return early. Handle the edge cases and errors first and `return`, so the main path isn't indented. Never nest
  more than three levels deep (`for` inside `if` inside `with` is already three).
- Few parameters. More than four or five is a sign they belong together in a dataclass; past the first one or two,
  make them keyword-only (`*,`) so a call reads `ls(bucket, limit=100, deep=True)`, not `ls(bucket, 100, True)`.
- No boolean flag that makes a function do two different things; write two functions instead.
- Prefer pure functions (inputs in, a value out, nothing changed) for logic, and keep AWS calls, file writes and
  widget updates at the edges, as the five-section layout already does.

#### Expressions

- Give a long condition a name: `is_stale = last_sync < cutoff and not job.running` then `if is_stale:`, not one
  `if` that wraps over three lines.
- A comprehension does one thing: one `for`, at most one `if`. Nested comprehensions, a ternary inside a
  comprehension, or a chain of `and`/`or` used for its value become a plain loop or a helper function.
- No clever one-liners. Two clear lines beat one dense one; don't chain more than about three calls on one line.
- No ternaries inside ternaries.
- Use f-strings for building text, never `%` or `+` across many pieces.

#### Structure

- Use a `@dataclass` (or `NamedTuple`) for data with named parts, not a dict with string keys or a tuple read by
  position (`run.cost`, not `run[3]` or `run["cost"]`). The `("warn" | "info", message)` findings pairs stay as
  they are.
- Separate the steps of a function with a blank line, like paragraphs: gather, then decide, then build the result.
- Put helpers above the code that uses them in the same section, so a file reads top-down.
- No dead code: no commented-out code, no unused functions, variables or imports, no `if False:` blocks. Git
  remembers what was there.
- Catch the specific exception you expect (`except ClientError`, `except KeyError`), never a bare `except:` or a
  wide `except Exception:` that silently swallows errors (the `@_friendly_errors` and `_safely` guards are the
  places that catch broadly, on purpose).
- No mutable default arguments (`def f(items=[])`); use `None` and make the list inside.
- Type-hint every function's parameters and return value.

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
   `session` / `region` / `profile` / `client` / `prices`. It subclasses `_BaseAnalyzer` (`_kit/analyzer.py`): it
   sets `_LIST_PRICES` (its price table) and passes `session`, `region`, `profile` and `prices` to
   `super().__init__()`, which makes the session (or keeps the one given), the clients' retry `_config` and
   `self.prices`, a copy of the list prices with `prices=` over them (the module's table never changes; a test
   checks). The base also gives `region` (the main client's), `_paginate(operation, key, **params)`, `_map(fn,
   items)` over `self.max_workers` threads, and `_cached_client(name, make)` (once, under `self._lock`, into
   `self._clients`). The service makes its own clients, each with its service name spelled out. Long scans accept
   `limit=` and a `progress=` callback.
   Config sections the caller can't read are recorded in an `errors` dict on the result (section → error code)
   instead of raising, so missing IAM permissions degrade to a note.
5. **`<Service>View`**: the notebook UI. Wraps an analyzer as `self.core`; each public method renders a report and
   returns `None`. Many share a name with their data method (`ls`, `find`, `scan`, `query`), but not all
   (`summary` → `summarize`, `what_if` → `simulate_lifecycle`, `schema` → `profile`, `table_info` → `describe`).
   It subclasses `_BaseView` (`_kit/view.py`): `__init__` passes `mode`, `max_rows` and `progress` to
   `super().__init__()` and then sets `self.core: <Service>Analyzer = core or <Service>Analyzer()`, and the class
   says what's its own: `_render_html = staticmethod(_render_html)` (its look), `_GROUPS` and `_START` (help()),
   and where it differs, `_PROGRESS` (what `_progress()` says without a label: S3's `("Scanning", "objects")`) and
   `_DATA_ERRORS` (S3's decoding errors). The base defines no command but `help()`.

How the View layer works:

- Methods build a list of render blocks (`_Title`, `_Cards`, `_Findings`, `_Table`, `_Note`, `_Text`, `_Next`, in
  S3 also `_Frame`, `_Image`, `_Link`, `_Media`, `_Pages` (PDF pages drawn as pictures; `_Zoom` gives each a hidden
  radio button that its picture's `<label>` checks, and a checked one makes the page a fixed overlay whose ‹ › ✕
  are labels for the neighbouring pages' radios and the report's "none" radio, so a click shows the page full size
  and steps through every drawn page in the report; JupyterLab confines fixed elements to the notebook panel, so
  it fills the notebook, not the window), `_Flow` (a Word
  document laid out: headings, lists, tables and its pictures in place), in S3 and Lambda `_JsonTree` (`_kit`'s: a
  JSON file or Lambda's configuration as nested `<details>` with coloured tokens, capped and opened breadth-first,
  `collapsed=True` folding it all under its title; not `bedrock_chat`'s `_Json`, which shows a request with its
  settings marked), in Lambda `_Outline` (`how_it_works()`'s `CodeStep`s as a tree of code lines in colour, each
  with coloured tags for what it does, and each of its own functions a line calls folded under that line in a
  `<details>`) and `_Graph` (a `CodeGraph` as an inline SVG of cards under column captions: `_GCard`s, an icon badge per
  kind, sized from `_G_SANS`'s font widths; `_graph_layout` makes each column as wide as its cards want, orders it by
  the barycenter of its neighbours and gives an arrow that skips columns, either way, a waypoint in each, so it runs
  between boxes; `_g_anchors` lands an arrow on the row of the operation it calls, or starts it at the variable it
  carries, else spreads it down the box's side; a warning's arrow keeps its colour under an amber halo, and its loop and
  warning marks sit on its straight run, clear of other arrows' marks; pointing at a box is per-node `:has()` rules in `report.rules`, and
  arrowheads are paths, not markers), and in Bedrock `_Passage` (a retrieved
  passage with `<mark>` highlights) and `_Answer` (an answer laid out from its markdown by `_Markdown`, with shaded
  cited spans and `[n]` superscripts), in `bedrock_kb` also `_Steps` (a dot per step on a line: how a file was
  indexed, the Syncs timeline), `_Pipeline` (each data source's stages, left to right), `_Chunks` (a file's chunks in
  document order: a bar of their sizes, and of where each sits in the file when its text was read, then each folded,
  the text it repeats from the one before in `<mark class="ov">`) and `_Shares` (parts of a whole in one bar: files by
  state), and in both `_Json` and `_Download` (from `_bedrock/blocks.py`; a saved file's ⬇ Download link: its path from the
  notebook's folder, which JupyterLab rewrites to its `/files/` URL and downloads because of the `download`
  attribute, or in a window a data: URL up to `_LIST_LINK_BYTES`, since JupyterLab doesn't resolve links in widgets),
  and in `bedrock_chat` `_Code` (code to copy: Python highlighted by
  `_python_html`, from `tokenize`, JSON by `_json_source_html` or an AWS CLI command by `_shell_html`, by its `lang`)
  and `_Results` (a test run's questions, a line each that opens to its answer, `_result_html`)) and pass them to
  `self._show(blocks)`, which renders HTML in Jupyter or plain text elsewhere (`mode="auto" | "html" | "text"`).
  Don't emit HTML or print directly; add to the block list so both renderers handle it.
- The renderer draws each block by what's registered for its type, so a new block type registers both of its
  drawings next to it: `@_draws(_Steps, "html")` on `f(block, report) -> str` and `@_draws(_Steps, "text")` on
  `f(block, report) -> list[str]`, where `report` is the `_Report` being drawn (the view's `max_rows`, the service's
  badge and root class, and the whole report's blocks). What a report's blocks share while it's drawn goes on it: a
  table adds the CSS rules its filters and columns need to `report.rules` (written in a `<style>` after the
  stylesheet, so they ship with the table), and S3's PDF pages share one `_Zoom` through `report.shared(...)`. A
  table cell of a new type registers `"cell"`, `f(cell, text) -> str` (`bedrock_kb`'s `_Link`); text mode shows
  `str(cell)`. A block that nothing draws shows as a warning note saying so. The shared blocks' drawers are in
  `_kit/render.py`; a window that has the stylesheet already draws blocks with `_blocks_html([...], root=_ROOT)`
  (the chat's Test and Runs tabs). The shared rules use class names a service's own blocks and windows must not
  reuse for something else (`.fi`, `.hd`, `.dt`, `.chip`, `.cols`, `.tl`, `.tb`, `.pd`, `.m`, ...): a window's
  `.kbc .hd{display:flex}` would also restyle every finding's headline.
- The HTML is plain HTML and CSS, never JavaScript (Jupyter drops scripts from reopened notebooks), so anything
  interactive uses CSS or `<details>`. What the blocks offer:
  - Cards take an optional tone: `(label, value, "warn" | "bad" | "ok")`. Text mode adds ` (!)` to warn and bad.
  - A table cell can be `_Tone(text, tone)`, a coloured pill in HTML and plain text elsewhere. `code_cols` shows
    columns of calls as code, and `prose_cols` passes columns of tool-written sentences (the Warnings tables)
    through `_prose`. Tables over 30 rows scroll under a sticky header.
  - A table (`_table_html`) of 3+ rows sorts by a click on a header (largest, newest or A to Z first,
    then the other way, then the original order; `_sort_key` reads sizes, money, ages and dates), a column of a
    few repeated values (storage class, region, status) gets a filter, and a table of 3+ columns gets a Columns
    menu of checkboxes. They are radio buttons, selects and checkboxes in a `<form method="dialog">` that `:has()`
    rules read: rows carry their sort place as `--a<j>` / `--d<j>` and are ordered with CSS grid `order` on a
    subgrid. The controls carry `hidden="hidden"` (JupyterLab's sanitizer keeps it, not a bare `hidden`), so an
    untrusted notebook, which loses its `<style>`, shows a plain table. `path_cols` marks columns of keys: an icon
    for the file's type (`_file_icon` from `_ICONS`, which the explorer's list uses too; drawn only, so the cell
    still sorts and copies as the key), the folder dimmed and shortened from the left, the file name whole, the
    full key on hover (text mode draws no icons, only the 📁 that `ls` writes in a folder's cell, and drops the
    start of the folder, never the name). `sortable=False` opts a table out; tree tables and help() never sort.
  - `_Table(collapsed=True)` / `_Text(collapsed=True)` fold a secondary view (tags, raw JSON) under its title, and
    `_Text(code=True)` marks a snippet to copy: one click selects all of it.
  - `_prose` renders every tool-written sentence (notes, findings, table titles, subtitles): it escapes the text
    and shows the calls in it (`documents(status='FAILED')`) and AWS CLI commands as code that one click selects.
    Never use it on table cells that hold data. `_split_lead` finds what a sentence says first (up to its first
    `:`, `;` or full stop, never inside a call or brackets): a finding shows it as a bold headline over the rest as
    points (`_finding_html`), and notes and Warnings cells get it as a bold lead-in (`_lead`); amounts of money
    in them are stressed. So write a finding as "what's wrong: why it matters. What to do (the call)", and keep
    other colons out of its first clause ("The most common error happened twice (the last 1h ago): KeyError: 'x'",
    not "The most common error, KeyError: 'x', happened twice").
- Every public View method is decorated with `@_friendly_errors` (`_kit/view.py`), which turns `ClientError` /
  `BotoCoreError` / data-decoding errors into a warning note instead of a traceback, and asks the View what to
  say: `_explain(code, message)` adds what an AWS error means and what to do (the windows call it too), and
  `_explain_call(code, message, command, args, kwargs)` is for a View that needs the command's arguments
  (DynamoDB names the table that wasn't found, and the closest names); `_DATA_ERRORS` are the other errors shown
  as `"ValueError: ..."` (`ValueError`, `TypeError`; S3's are what decoding a file raises); a `_Hint` shows as a
  plain note; `_extra_note(exc, command)` returns the note for the service's own errors (OpenSearch's failed
  requests and unexpected answers), and anything else still raises.
- `help()` lists the commands `_commands(cls)` finds (the public methods of the View and its bases, the View's own
  first, so `_BaseView`'s `help` too), grouped by the View's `_GROUPS` (anything missing lands in "Other"; a test
  checks nothing does), with their signatures without type hints and the **first paragraph of each docstring** as
  the description, after a "Start here" `_Next` from `_START`. `help("name")` shows one command's whole docstring.
- Long operations wrap the analyzer call in `with self._progress(label, unit) as tick:` and pass `progress=tick`
  (without them, `_progress()` says the View's `_PROGRESS`).
  The analyzer calls `progress(count)` with a running count, or `progress(done, total)` when it knows the total
  (a new total starts a new bar; `unit="B"` counts bytes). `_progress` shows a tqdm bar when tqdm is installed and a
  plain line with the rate and time left otherwise, only one at a time (a nested `_progress` replaces the outer
  bar), and nothing with `View(progress="off")`. Work spread over threads reports progress from the calling thread
  only (S3's `_run_in_threads`), never from a worker, so notebook widgets aren't touched from other threads.
- Downloads without a path go into one folder, `S3Analyzer.downloads` (`DOWNLOADS`, `s3-downloads` next to the
  notebook; `S3View(downloads=)` / `S3Explorer(downloads=)` set it, and the explorer's ⚙ edits it), which
  `downloads_folder()` resolves at call time. A path given to a download is used as is, from the notebook's folder.
  `list_downloads()` / `downloads()` list its top-level entries (a folder download is one entry; when it was downloaded
  is the newest change time, since downloads keep S3's modified time) and `clean_downloads()` deletes whole entries
  only, never part of a folder download.
- `DynamoDBView` keeps `self._pager` so `more()` continues the last `scan` / `query` / `sql`. `BedrockKBView` keeps
  `self._last` (the last search or answer, for `chunk()`) and `self._conversation` (for `follow_up()`, with the
  data sources it searches), and `self.kb`, the default knowledge base that `use()` sets. `data_source=` (a name, ID
  or list; `resolve_sources` turns it into `{ID: name}` from a cached ListDataSources) narrows a search with a filter
  on Bedrock's own `x-amz-bedrock-kb-data-source-id` key (`with_data_sources`, ANDed with `where=`'s), the same in
  `bedrock_chat`. `OpenSearchView` keeps `self.target` and `self.index`
  (what `use()` sets): commands name an index like a path, `"domain/index"` or `"collection/index"`
  (`parse_location`, which also takes collection IDs, ARNs and endpoint URLs), and `_index_ref` falls back to `use()`'s,
  then to the only domain or collection in the region and its only vector index, or raises `_Hint` to ask.
- `LambdaView` commands about one function take a name, `"name:alias"`, an ARN or a console link
  (`parse_function_ref`) and `region=`. `LambdaAnalyzer` makes clients per region (`_service(name, region)`, behind a
  lock since sessions aren't thread-safe), and `overview(regions="all" | [...])` reads each region; `_seen` remembers
  where it found each name, so `locate()` looks for a name in the one other region it was seen in. `LambdaView._named`
  turns GetFunction's not-found into a `_Hint` with the closest names. Daily CloudWatch numbers cover whole UTC days
  (`_midnight`), so "last called" reads today / yesterday / 3d ago; `_errors_level` is the one rule for when failed
  calls are a warning (in findings, the Error rate card and the table's tone). Log reads go backwards in growing
  slices (`_filter`) so the newest lines are the ones kept when a window holds more than `limit`.
- `LambdaView.how_it_works()` (its pure side in `lambda_functions/analysis/codemap/`: `terms`, `aws_model`, `syntax`,
  `model`, `reader`, `mapping`, `findings`, `graph`) is `LambdaAnalyzer.code_map()` -> `map_package()` (the
  package's own `.py` files;
  libraries are told apart by their `.dist-info` RECORD and `_LIBRARIES`) -> `map_code()`, all pure. A `_CodeReader`
  loads the handler's module the way Python imports it (its packages' `__init__` first; module-level code is the
  start-up, `loading > 0`), then follows the handler with `ast`, keeping what each name holds as a `_Sym` (a boto3
  client, resource or paginator, part of the event, `os.environ`, one of its own functions, classes or objects) so a
  call resolves to an AWS operation, named from botocore's own service model (`_aws_model`, read from the files boto3
  installs: no AWS call), or to one of its own functions, which it opens with the arguments' `_Sym`s bound (`_run`,
  memoized per binding, `_MAP_DEPTH` deep, its steps shown at the first call only). It fills a `CodeMap`: the
  `startup` and `steps` outlines (`CodeStep`, tags in `notes`), `AwsCall`, `EnvRead`, `EventField`, `ImportUse`,
  `CodeFunction` and `CodeSpot` (the lines findings are about: HTTP without a timeout, writes outside /tmp, secrets
  by name only, dropped errors, clients made per call). `code_map_findings` and `_code_summary` read it, and
  `code_graph` turns it into a `CodeGraph` (`GraphNode`s in columns: inputs, the handler and its functions by depth,
  what it reaches; `GraphEdge`s by kind; at most `_GRAPH_FUNCTIONS`, `_GRAPH_DEPTH` deep), with `.mermaid()`. Strings in
  `_GUESSES` / `_BATCHES` are other services' operations: `rules.py` checks only the services this file makes clients
  for (lambda, logs, cloudwatch, ec2), so a writing operation of those can't be named in a literal.
- Text from a knowledge base is untrusted: HTML blocks escape every piece before wrapping it in markup, and
  `build_prompt` sends passages to a model as data inside `<source>` tags, never as instructions. Answers are
  markdown: `_Markdown` (stdlib only, in `_bedrock/markdown.py` for both) parses blocks and inline markup
  keeping each character's offset in the answer, because citations are offsets into the raw text. Raw HTML stays
  text, links open only http(s) and mailto, and pictures become links. Text mode prints the markdown as written
  (`_answer_lines` leaves code and tables unwrapped).
- **The windows' kit** (`_kit/window.py`) is what the four windows share, written once: `_css_height`,
  `_running_loop`, `_cell_number`, `_class_if`, `_skeleton`, `_icon_url` (a line drawing as the CSS `url()` every
  window masks its icons with), `_row_button` (a line whose whole width is the click target: a full-width button under
  its HTML face, in a box), `_for_window(blocks, words)` and `_terminal_theme(roots, prefix)`, the chat windows' look
  (`_MONO_FONT` and a code editor's colours, light and dark, as `--<prefix>-*` properties: the agent's window uses
  `ag`, `bedrock_chat` `kt`, with its own `--kc-*` tokens made of them), so the two chats can't drift apart.
  `_Window`, which `_ChatApp` and both explorers subclass,
  has `_safely` (a widget callback's error said in the window), `_on_enter` (a text box's `submit` message),
  `_error_text`, `_quietly`, `_set` (only HTML that changed) and `_close_lists` (the backdrop's click), with three
  hooks: `_warn` (where an error is said), `_explain` (its View's) and `_reword` (an explorer's `_window_text`).
  `_Explorer(_Window)`, which `KBExplorer` and `LambdaExplorer` subclass, adds their state (its `__init__`, called
  first), `_load_widgets`, the status line (`_status` with the `owner` a busy line belongs to, `_finish`, `_said`),
  `_tab_bar` and `_frame` (CSS classes from `_PREFIX`), `_draw` (with the explorer's `_html`), `_workers` (threads
  named by `_THREADS`), `_later`, `_drop`, and the window shown once per cell (`_display`, `_ipython_display_`).
  `@_window_errors` guards a window's own commands through its `_ready` and `_command_failed` (the S3 explorer's
  too). `_Picker` is a field that opens a searchable list over the window, which the KB explorer's `_Chooser`, the
  Lambda explorer's `_FunctionField` and the chat's `_ChoicePicker` subclass: the button under its face, the panel
  with its search box and close button, one list open at a time across the window's `pickers`, the backdrop shown
  while one is, pooled lines (`_line`), `matches()` by `search_rank` over a subclass's `_query` and `_fields`, and
  Enter (`_entered`; the chat has its own). A subclass draws the face, the list and its lines, and says what a click
  does (`_clicked`); `_can_open` and `_opening` let it refuse or prepare. What stays in each window does a different
  job there: the S3 explorer's `_guard` (it runs a handler at once and mends an unfinished status line), report
  loader and `_renew`, the chat's test runner (`_start` / `_run_later`, a queue drained on the loop), and the
  explorers' `_html`, `_reports`, `_show_tab`, `_tab_line`, `_renew` and stylesheets (`_EXPLORER_CSS`: about half
  their rules match, in different orders). `_CLICK_GRACE` (S3) and `_TOGGLE_GRACE` (Lambda) drop different clicks.
- `bedrock_chat` is, with the S3 explorer, one of the two interactive UIs, but not a companion: like the other
  analyzers, it imports `_kit` (and, like `bedrock_kb`, `_bedrock`), and its window is a `_Window`. `chat()`
  (`bedrock_chat/view/chat_view.py`, with `BedrockChatView`, made of a `_<Topic>Commands` mixin per `_GROUPS` entry
  in `view/`) builds a `BedrockChatView` and calls
  `app()`, which shows `_ChatApp` (`bedrock_chat/window/`: `app.py` composes it from `_PickersPane`,
  `_ConversationPane`, `_SettingsTab`, `_RequestTab` (Request, Response and Code), `_TestTab` and `_RunsTab`, a module
  each, and keeps `__init__`, the layout and what the View tells it: `sync`, `added`, `ran`, ...), an ipywidgets window (pickers, the conversation as `HTML` widgets in a
  `column-reverse` box so it stays scrolled to the newest, and the side tabs).
  Widgets live in the kernel, so the window doesn't survive a reopened notebook; `transcript()` renders the
  conversation as an ordinary report that does. Its settings come from botocore's service model:
  `request_schema()` walks RetrieveAndGenerate's input shape into `Field`s (path, kind, range, docs), with the short
  names, plain-English docs and starting values in `_KNOWN`, so a field AWS adds appears with a newer boto3. The
  Settings tab keeps to what's sent: one line per setting (`_row`: a short value's box beside its name, `_BESIDE`,
  and where it goes in the name's tooltip), Add a setting folded behind its button (`_show_adding`), and the setup
  call in a `<details>`.
  Settings are `{key: value}` (`view.values`); `build_request()` places them in the request and fills required
  one-value enums (`Schema.auto`), `settings_from_request()` reads an edited request back, and `validate_request()`
  runs botocore's `ParamValidator`. The data source is picked like the knowledge base and model, not a setting:
  `view.data_source` (`{ID: name}`, reset by another knowledge base) goes into the filter through
  `build_request(data_sources=)`, `split_data_sources()` takes it back out of an edited request, and the window's
  Data source field (`_fill_sources`) shows only when the knowledge base has more than one. Files work the same way
  on `x-amz-bedrock-kb-source-uri`: `view.picked_files` (s3:// paths; `resolve_files` / `match_files` turn names into
  paths from `core.files()`, a cached ListKnowledgeBaseDocuments), `build_request(files=)`, `split_condition()`, and
  the Files field (`_list_files` fills it only when it's first opened; ticked files are chips, `_draw_files`; its
  ⬇ List, `files_list_button` beside the search box, saves what the list shows with `core.save_file_list` and draws
  a `_Download` in `files_saved` at the bottom of the panel, `_save_file_list`).
  The header's four fields are `_ChoicePicker`s (`_kit`'s `_Picker`): a button under its face (label, name, ID)
  that opens a list of `_Choice` lines over the chat (`.kbc-pop`, absolutely placed, so the boxes around it are
  `overflow:visible`), each line a
  button under its text, with a search box that ranks lines by `search_rank` (name, ID, description...; `match_kbs`
  and `kbs(match=)` use it too) and marks what it found (`_marked`). Enter picks the first line, or hands text the
  list doesn't hold to `on_text` (an ID or ARN, through `core.resolve`); `multi=True` (Files) ticks lines and stays
  open. While a list is open, `_ChatApp.backdrop` (a transparent button over the whole window, `.kbc-backdrop`, under
  the open field, which `.kbc-open` lifts above it) takes a click anywhere else and closes it. Set a picker's value
  from code with `set_value`, which doesn't call `on_pick`. **Add a setting** lists `Schema.search(text)` (names, then paths, then
  descriptions; a near miss falls back to difflib), or every field by group with Browse all. With Edit JSON open, the
  view buttons are disabled, and `_follow_edit` refills an untouched editor when the request changes, or warns what
  Apply would undo in an edited one. Each tab scrolls on its own (the box inside the tab's frame), and `_set` only
  assigns an HTML widget a value that changed, so a re-render doesn't fold up what the user opened. Every widget
  callback goes through `_safely` (`_Window`'s), which shows errors in
  the window (a callback's exception would only reach the browser log); Enter in a text box is the box's `submit`
  message (`_on_enter`, as in the explorers: `on_submit` is deprecated). View commands run from other cells
  update an open window through `view._changed()`. The composer's Answer / Retrieve only switch (`mode_pick`, saved as
  `view.retrieve_only`) makes Send a Retrieve search: `build_retrieve_request` takes the RetrieveAndGenerate request's
  `retrievalConfiguration` and nothing else (`retrieve_settings`; the answer's rows are dimmed, `_off`), and the
  result is an `Answer` with `retrieve_only=True` (no text; `sources` is every passage, ranked, with scores) kept in
  `view.answers` without touching the session. `ask()` always answers and `retrieve()` always searches; `_counterpart`
  pairs a turn with the same question asked the other way, for `cited_ranks` and `compare_findings`.
  The side tabs are Settings, Test, Runs, Code, Request and Response, from `_TABS` (each title with its line
  drawing; `_ChatApp.TEST_TAB` / `RUNS_TAB` are their places): `_tab_rules()` keeps them on one row that never wraps,
  each icon a mask in the text's colour, shown only while the bar is 480px wide or more (a container query).
  **Test** asks a list of questions
  (`parse_questions`: one per line, `question | expected file`) with the window's setup, each on its own (no session):
  `BedrockChatAnalyzer.ask_all` is `_prepare_batch` (resolves once, builds every request) then `_run_batch`, which
  is `_run_batches` for one run: it sends one question per free thread (`workers`, so nothing is queued once `stop` is
  set), the first question of every run before the second, and fills each `BatchItem`
  (answer or error, and cost) on its calling thread, where `progress` and `on_item` run too. In a notebook the tab
  runs the work in the loop's executor (`_start` / `_run_later`) and draws each line on the event loop as it comes back,
  from a queue `on_item` fills, so Stop (a `threading.Event`) and the rest of the window keep working; without a loop it
  runs inline. Runs are kept in `view.batches` (`view.questions` is the list), numbered from 1 as the reports show them
  (`_run_number`; `_run` turns a number, or -1, into the run), and `_before` / `batch_changes` compare a run with the
  last one of the same kind (outside its own sweep; a sweep before it gives its setup that matches, else its best).
  **Try variations** (the Test tab's card, `parse_variations`: `n = 5, 10` lines) and `sweep()` make a `Sweep`:
  `sweep_setups` turns lists of values into every combination, `_prepare_sweep` prepares a `Batch` per setup
  (`apply_setup` on the settings in use; `model` / `data_source` / `files` are picked, not settings; setups that come out
  the same are dropped) and `_run_sweep` sends them all through one `_run_batches`, so a stopped sweep leaves every setup
  with the same questions. `rank_runs` scores runs (`run_score`, on `shared_questions`) by expected sources cited, then
  answers, then grounded share in 5-point steps (searches: found, MRR, passages), the cheaper first when tied, and
  `ranking_findings` says which beats the setup in use (`_now_in`, or an earlier run of the same questions), what each
  varied setting changed, when a lead could be chance, and which questions no setup handled. `_compare_blocks` draws a
  sweep or `compare_runs()`: the ranking table, then the question × setup matrix. A sweep over `SWEEP_MAX_COST`
  (estimated, `sweep_estimate`) isn't sent without `max_cost=` or, in the window, a second click (`_ChatApp.confirm`).
  **Runs** lists `view.batches` newest first (`_runs_blocks`, ranked within `_families`), and Show / Use this setup
  (`_switch_to`) / Compare act on the picked one. `save_runs()` and `log=` append `run_record` JSON lines to a local file
  (`_append_runs`: only runs whose ID isn't in it yet, never rewriting it; Bedrock's raw responses are left out), and
  `load_runs()` reads them back (`read_runs`), sorted by when they ran, sweeps rebuilt from their batches' `sweep` ID.
  **Code** shows the setup from `_preview` as `python_script` (boto3 only, asking the
  test questions), `config_json` (`config_of`: the request without its question and session) or `cli_command`; `code()`
  shows all three as a report.
  `_ipython_display_` shows the window once per cell, so a cell
  ending in `chat()` doesn't show it twice. A setting named `rerank` would read as the Bedrock `Rerank` operation to
  `rules.py`, which is why it's `reranker`.
- `bedrock_kb`'s explorer window, `KBExplorer` (`bedrock_kb/explorer/`: `window.py` with `explore()`, `frame.py` its
  tabs, stylesheet and `_window_text`, `widgets.py` the field and rows; `BedrockKBView.explore()` imports it lazily and
  keeps it in `view.explorer`), is the third interactive UI, not a companion, like `bedrock_chat`, and built the same way, on the
  windows' kit (an `_Explorer`): ipywidgets styled by `_EXPLORER_CSS` (scoped under `.kbx-app`, report CSS `_CSS`
  included once in its style widget, so `_html` strips it from each report), no JavaScript, full-row buttons under
  their faces (`_FileRow`, the knowledge base field `_Chooser`, a `_Picker`, with its searchable list and `backdrop`),
  custom tab buttons over pages (`_EXPLORER_TABS`, `_tab_bar`, icons from `_explorer_rules()`, which also gives each
  `FILE_STATES` key its colour). Its data is the analyzer's file layer:
  `file_inventory()` reads Bedrock's document list (ListKnowledgeBaseDocuments) next to the bucket (ListObjectsV2 under
  the inclusion prefixes, `<file>.metadata.json` attached to its file) and `inventory_files` / `file_state` join them
  into one state per file (`FILE_STATES`: failed, changed, new, skipped, deleted, partial, ignored, indexing,
  unchecked, indexed); `document_chunks()` reads a file's chunks with a Retrieve filtered on
  `x-amz-bedrock-kb-source-uri` (`SOURCE_KEY`, at most `CHUNK_LIMIT` = 100), puts them in document order (`place_chunks`
  in a .txt / .md file's text, else `order_chunks` by page and `chunk_overlap`) and counts other files' passages in
  `outside`; `metadata_file()`, `probe_file()` (the file's passages and its rank in the whole knowledge base) and
  `document_status()` (GetKnowledgeBaseDocuments, for an unchecked file when it's opened: `BedrockKBView._recheck`).
  The View's `files()` / `file()` / `search_file()` show the same as reports, and the window draws their blocks
  (`_files_blocks`, `_file_blocks`, `_probe_blocks` with `window=True`) through `_for_window`, which drops `_Next` and
  rewrites what a sentence tells you to call into where the window shows it (`_window_text`: `syncs()` -> "the Syncs
  tab", `where=` filters -> metadata filters). AWS is read off the loop: `_later(key, work, done, failed)`
  (`_Explorer`'s) runs `work` in `_workers()` through the running loop's executor and `done` on the loop, inline
  without one; `self._jobs[key]`
  drops a stale result, `_open_kb` bumps every key so an earlier knowledge base's results are dropped, and `_tasks`
  holds the asyncio tasks (tests await them). `_renew_pane` puts the file page in a new box so it starts at the top,
  `_set` only sends HTML that changed, the status line says what each tab holds when nothing's going on (`_said`,
  `_tab_line`), and callbacks go through `_safely`, public commands (`open`, `file`, `search`, `refresh`) through
  `_window_errors`. ⬇ List (`_save_list`) saves the list's `_visible` files in their order with `core.save_file_list`
  and draws a `_Download` under the list (`saved_view`; `x.saved`), its link a data: URL up to `_LIST_LINK_BYTES`. Without ipywidgets or Jupyter it shows the reports instead (`_reports`).
- `lambda_functions`' explorer window, `LambdaExplorer` (`lambda_functions/explorer/`: `window.py` composes it from a
  mixin per tab, `_FunctionsTab`, `_FunctionTab`, `_LogsTab`, `_ErrorsTab`, `_PerformanceTab` and `_CodeTab`, each in
  its module with the constants and line drawings only it uses; `frame.py` holds what the tabs share and `widgets.py`
  the field and rows; with `explore()`; `LambdaView.explore()` imports it lazily and keeps it
  in `view.explorer`), is the fourth interactive UI, not a companion, and built like `KBExplorer`, on the windows' kit:
  ipywidgets styled by `_EXPLORER_CSS` (scoped under `.lmx-app`, report CSS `_CSS` included once), no JavaScript,
  full-row buttons under their faces (`_Row`, which can carry a small action button such as the function list's Logs;
  `_RunRow`, whose button opens or folds the run's lines below it; the function field `_FunctionField`, a `_Picker`,
  with its searchable list and `backdrop`), custom tab buttons over pages (`_EXPLORER_TABS`, `_tab_bar`). The region
  field (`region_pick`: a region, or `"all"`) and the
  function field pick what it shows. The Functions tab is `overview()` read in three background steps so the list shows
  at once: `overview(metrics=False, details=False)`, then `overview_numbers()` (CloudWatch) and `_all_extras()` (resource
  policies, provisioned concurrency), which return data that `Overview.add_numbers` / `add_extras` merge on the loop
  (`overview()` itself is built from the same three). `_function_facts` works out each line (findings, cost, the dot's
  tone); the list sorts by a click on a column header (`_COLUMNS`, `_sort_by`) and filters with `_FUNCTION_CHIPS` and
  `search_rank`. A picked function is read with `describe()` for the Overview (function_info's findings, with the cards
  the header already shows left out (`_SETUP_CARDS`), then `_Wiring`, `_Columns` and the cost) and Settings tabs
  (`LambdaView._function_sections` splits function_info()'s blocks by part, `_INFO_ORDER`); the other tabs read when
  first opened (`_asked`). The Logs tab is `LambdaAnalyzer._log_runs()` on the Function already read (no GetFunction):
  the newest `_RUN_LINES` lines of the time range, grouped into runs by `split_runs()` (a stream runs one call at a time:
  START to REPORT, the start-up lines before START, lines naming an open run's request ID go to it) and drawn
  `_RUN_PAGE` to a page, newest first, `_run_face` / `_run_body` (`_line_parts` strips what Lambda writes before a
  line, shows JSON as its message with its fields folded, and an error as a traceback). Typing filters the runs read;
  Enter is `_log_runs(search=...)`: CloudWatch's matching lines (`_SEARCH_HITS`), then each one's stream read from a
  timeout before to a timeout after (`_around`, `_SEARCH_WINDOWS` reads), so the runs come back whole with the matches
  marked (`LogEvent.matched`). Errors and Performance (`_errors` / `_performance`, `for_window=True` drops their tables
  of runs) list failed and slow runs to click: `_run_around()` reads that run from its stream (`Invocation.stream`).
  **Live** polls `_filter()` every `_LIVE_SECONDS` in an asyncio task (`_live_loop`, the read on a worker thread) for
  `_LIVE_MINUTES`, merging new lines into `logs_page` (`_merge_live`); it stops when the Logs tab is left. Code
  downloads the package once (`_package`, a fresh link from GetFunction) and `read_package()` (pure) reads each file
  clicked from the cached bytes; over 50 MB, a button downloads it anyway. Its files are a tree (`_file_tree`, pure:
  folders first, `_code_open` the folders shown open, the handler's at first, a folder holding only a folder on its
  line; with a search, every folder holding a match is open unless in `_code_shut`), drawn `_CODE_PAGE` lines to a
  page by `_tree_face`; a click on a folder toggles it (`_clicked_code_line`, which drops a double click's second
  click within `_TOGGLE_GRACE`). The file's **Copy** (`_copy_html`) is a `user-select:all` span holding the text
  hidden, its label drawn by `::before`, so a click selects the file and Ctrl+C copies it, with no JavaScript.
  **Download .zip** (`_save_code`) writes the cached bytes with `_save_package`, no new download; `x.saved` is the
  `PackageDownload`. **↗ Open in a tab** (`page_button`, `_open_page`, shown while How it works is) saves
  `LambdaView._save_how_page(x.code_map)` and puts the `_PageLink` in `page_link`, an `Output`, because JupyterLab
  serves the notebook's files only to links in a cell's output, not in a widget's HTML; `x.page_saved` is the
  `SavedPage`. **Hide files** (`files_toggle`, `_toggle_files`) hides the tree's pane, `code_left`, so the source
  has the tab's width; `_files_shown` isn't reset by `_open_function`, so it stays hidden for the next function.
  **How it works** (`how_button`, `_toggle_how`) shows `how_it_works()`'s blocks in place of the source, for a Python
  function: `map_package` on the cached bytes runs as the `map` job (`_later`), `x.code_map` keeps it, and a click on a
  file in the tree, or on the button again, goes back to the source.
  Background work is `_later` (`_Explorer`'s, as in
  `KBExplorer`), the status line's busy text carries its job (`_status(owner=)`) so only that job, or the user being on
  its tab, replaces it (`_finish`), and `_drop` bumps keys so a function's results that come back after another was
  picked are dropped. `_renew` / renewing `runs_box` puts a page's scrolling box anew so it starts at the top.
  Sentences from reports go through `_for_window` / `_window_text`, which turn calls into tabs (`errors('etl')` -> "the
  Errors tab", `logs(..., request_id=...)` -> "run 8f5ce35b in the Logs tab") and capitalise them at a sentence's start.
  Without ipywidgets or Jupyter it shows the reports instead (`_reports`, `_report_for`).
- `sagemaker_env` also reads the machine it runs on (`machine.py`'s `_MachineReads`; the Python environments are
  `envs.py`'s `_EnvReads`, and AWS is `analyzer.py`): SageMaker's `/opt/ml/metadata/resource-metadata.json` (which
  says whether this is a notebook instance or a Studio app, and which), `/proc` (load, memory, uptime,
  processes and which are Jupyter kernels), the disks and `nvidia-smi`. `SageMakerAnalyzer(root=...)` points all of
  that at a folder of fake files, and `_shown()` / `_real()` make paths look and work as the machine sees them
  (`/home/sagemaker-user`, not the temp folder). `_cpu_count`, `_disk_usage` and `_gpu_query` are the other
  hooks tests and the demo replace. Local reads that fail go in `Machine.errors`, like AWS sections in `errors`.
  `environments()` finds Python environments by their files, never by running them (`conda-meta/python-*.json`,
  `pyvenv.cfg`, `conda-meta/history`'s first entry for when it was made), in each conda installation (`/opt/conda` in
  Studio, `~/anaconda3` on a notebook instance), `~/.conda/envs`, `envs_folder()` and `~/.conda/environments.txt`, and
  kernels in the folders Jupyter reads (yours, the Jupyter server's environment's, the system's; a notebook instance's
  Jupyter also lists `~/anaconda3/envs/*` as `conda_<name>` by itself). `_kept()` is whether a path is under `home()`,
  the disk kept when the notebook stops, so `envs_folder()` is `~/SageMaker/envs` on a notebook instance and
  `~/.conda/envs` elsewhere, and a notebook instance's kernels (in `~/.local`) aren't kept: `new_env()` again adds one
  back, and `kernels_lifecycle()` is the on-start lifecycle configuration that does it at every start. `tools()` looks
  for mamba, conda, micromamba and uv under `root` too, and `_run` is the hook tests (`Runner`) and the demo replace.

Cost estimates come from module-level price tables (`S3_PRICES`, `DYNAMODB_PRICES`, `BEDROCK_PRICES`, and
`MODEL_PRICES` for $ per 1M tokens by model family, with `GLOBAL_MODEL_PRICES` for the cheaper `global.` inference
profiles, and `SAGEMAKER_PRICES` (`sagemaker_env/prices.py`), storage plus the hourly price of each type in `INSTANCE_TYPES`, which also holds
its vCPUs, memory and GPUs, and `OPENSEARCH_PRICES`, EBS storage and Serverless OCUs plus the hourly price of each
OpenSearch instance type in `opensearch/prices.py`'s own `INSTANCE_TYPES` (vCPUs and memory, which give each node's k-NN memory),
with `EMBEDDING_MODELS` for the Bedrock embedding models `search()` can call, and `LAMBDA_PRICES`, requests, GB-seconds
(x86_64 and arm64), provisioned concurrency, `/tmp` and CloudWatch Logs ingestion and storage; us-east-1 list prices
with the date they were read) that callers override with `prices={...}` (and
`model_prices={...}`); the View shows whether list prices or the caller's prices were used. Check them against the
AWS Price List API (`pricing.us-east-1.amazonaws.com/offers/v1.0/aws/<AmazonS3|AmazonBedrock|
AmazonBedrockFoundationModels|AmazonES|AmazonSageMaker|AWSLambda|AmazonCloudWatch>/current/us-east-1/index.json`;
`index.csv` is easier to grep), which is what AWS bills from. `lambda_functions/helpers.py`'s `RUNTIMES` holds each managed
runtime's end of support, block-create and block-update dates from AWS's Lambda runtimes page (cfn-lint's
`LmbdRuntimeLifecycle.json` carries the same data when the page can't be reached), and `LATEST_RUNTIMES` the newest
runtime per language that findings suggest moving to. A model missing from `MODEL_PRICES` shows its cost as unknown rather than a guess.
`BEDROCK_PRICES`, `MODEL_PRICES`, `GLOBAL_MODEL_PRICES`, `DEFAULT_MODEL` and the model helpers are in
`_bedrock/prices.py`, once for `bedrock_kb/` and `bedrock_chat/`, and `_bedrock/` also holds `_Json` with
`_json_html` / `_plain_json` (`blocks.py`), `SEARCHABLE` (`helpers.py`), and `presigned_url` (`files.py`) (both analyzers' `file_url`, signed by an `s3` client each makes).
Both Bedrock analyzers make their clients with the base's `_cached_client` (once, under a lock, since both windows
read from threads). What the windows share is in `_kit/window.py` (see "The windows' kit"); `lambda_functions/explorer/`'s
`_EXPLORER_CSS`, `_EXPLORER_TABS`, `_explorer_rules`, `_LOGO`, `_IN_WINDOW`, `_WINDOW_WORDS`, `_window_text` and
`explore` share names with `bedrock_kb/explorer/`'s but are its own, as `_CSS` is.

## Architecture of a service package

A service that has been split (see the progress table in `.claude/skills/modularize/SKILL.md`) is a package
`src/aws_analyzer/<service>/` instead of one file. Each of the five sections above becomes modules: the core is
`helpers`, `models`, `analysis` (or an `analysis/` package by topic) and `analyzer`, plus the service's own core
modules. The UI is `style` (`_ROOT`, `_CSS`, `_BADGE`, `_render_html`), `blocks`, `view` and `explorer`. The package's
`__init__.py` holds the old module docstring, re-exports every public name and lists them in `__all__`, so
`from aws_analyzer.<service> import ...` doesn't change. Private names live in the module that defines them, so
tests import and patch them there.

`rules.py` checks what keeps the layers apart:

- The core never imports the UI. That covers the package's UI modules and `_kit`'s `blocks`, `render`, `view`,
  `window`, `progress`, `highlight` and `jsontree`; `render._call` / `_signature` are allowed.
- `analysis` never imports `analyzer`.
- No function or class is defined in two modules of a package.
- Every `*View`, `*Explorer`, `_<Topic>Commands`, `_<Topic>Tab` and `_<Topic>Pane` class (a View or window split into
  mixins) has its public commands guarded and documented.
- The read-only check reads the clients every module of the package makes.
- `agent/` reaching into an analyzer's private names is a warning.

`tests/test_style.py` keeps modules under `MAX_MODULE_LINES` (1,500), except the files in `LONG_FILES` that haven't
been split yet.

## The agent (`agent/`)

A package, not one file: instead of five sections in one file, one concern per module (its `__init__.py` docstring
maps them), so a new service's tools or a new capability of the agent goes in one place and touches nothing else.
`aws_analyzer.agent` re-exports the public names (`AgentView`, `AgentAnalyzer`, `Budget`, `Toolkit`, the pure
functions); a test patches a name in the module that looks it up (`harness._MIN_PYTHON`, `view._render_html`).

- **Toolkits** (`toolkits/`): what the agent can read, one `Toolkit` subclass per AWS service (`base.py` says what one
  gives). `LambdaToolkit` (`lambda_tools.py`) has seven tools (`find_functions`, `function_health`, `function_errors`,
  `function_logs`, `function_performance`, `function_details`, `function_code`), building blocks over `LambdaAnalyzer`
  methods, not one per question: methods of the toolkit that its `tools()` lists. Their type hints and
  docstrings are the schema and description the model sees (LangChain reads them), so write a docstring for the
  model: what it returns, how to pass times (ISO UTC or '24h'), when to use it, and nothing about how it works
  inside (that goes in a helper's docstring). Each returns a compact dict from the
  module's pure `summarize_*` (what the model reads: counts, causes, a few lines, secrets hidden by `results.py`'s
  `hide_secrets`) with its coverage (`window`, `regions`, `cut_short`, `covered_from`), plus `_summary` (the report's
  one line) and `_next` (View calls, made by `Toolkit._view_call`), which the runtime takes out before the model sees
  the result. A toolkit also declares `reads` (each tool's AWS reads, for `tools()` and README: `TOOL_READS`),
  `service` / `services` (the IAM prefix of each operation, for `permission_for`), `covers` (the system prompt's
  first paragraph), `instructions(budget)` (its section of the prompt: Lambda's says start with `function_health`),
  `privacy` (the tools() note), `not_found` (close names for a resource that isn't there), `permissions` (an
  operation's IAM permission where it isn't `<prefix>:<operation>`: S3's `ListObjectsV2` is `s3:ListBucket`),
  `request_cost(counts)` (what its requests cost, from the guard's counts: S3's, at `S3_REQUEST_PRICES`) and
  `client_factory` (the analyzer method whose clients the guard hooks: Lambda's `_service`). Its `analyzer` is made on
  the agent's session on first use and hooked to the guard every time.
  `S3Toolkit` (`s3_tools.py`) has `find_buckets`, `bucket_details`, `list_folder`, `find_files`, `folder_summary` and
  `file_info` over `S3Analyzer`; listings stop at the budget's `s3_keys` and say so (`scanned`, `cut_short`). **A tool
  that joins two services** goes in one of their toolkits and reads the other through the agent's toolkit for it,
  `peer(name)` (None when the agent hasn't got it, and then `tools()` leaves the tool out): S3's `s3_triggers` reads
  the bucket's notifications (`S3Analyzer.notifications`), lists the files each one covers in the window
  (`count_arrivals`, pure: by prefix and case-sensitive suffix, and by upload kind, since a multipart upload sends
  CompleteMultipartUpload, not Put), and reads the function, its resource policy (Lambda's `parse_policy`) and its
  CloudWatch numbers through `peer("lambda").analyzer`; `trigger_findings` (pure) says what broke each link. Its
  `covers` and `instructions()` add the S3-and-Lambda part only when the peer is there. `TOOLKITS` names the built-in ones and `DEFAULT_TOOLKITS` what an
  agent gets; `AgentAnalyzer(toolkits=[...])` takes names, `Toolkit` subclasses or objects (a user's own too), and
  `lambda_core=` / `lambda_clients=` / `prices=` go to the Lambda toolkit it makes, `s3_core=` to the S3 one. Two toolkits can't share a tool
  name (`tools()` refuses). **A new service** is a `<service>_tools.py` here with its toolkit, its name in `TOOLKITS`
  (and `DEFAULT_TOOLKITS`), its analyzer in `COMPANIONS["agent"]` in `rules.py`, and its reads in README.
- **The runtime** (`runtime.py`, `as_tool`) wraps every toolkit's tools alike: it records a `ToolCall` on the
  question's `_Run`, checks the `Budget` first (`budget.py`'s `check_budget`: tool calls, time, cost; a toolkit that
  reads logs checks `log_days` itself, as `LambdaToolkit._log_window` does), turns `_Refused` into a refusal the model
  reads ("answer now with what you found"), `ClientError` into an error with the permission it needed (the toolkit's
  `not_found` first, for close names), and anything else into an error result: a tool never raises into the agent
  loop. Sections the analyzer couldn't read (`errors` dicts) become the call's error. LangGraph runs a turn's tool
  calls side by side, so `_Run` is behind a lock and the budget counts under it (`_Run.read_metrics` too: the
  CloudWatch metrics a tool requested, and their cost). LangChain passes every default, so the `ToolCall` keeps only
  the arguments that differ from the tool's defaults, and `first` (its first parameter, when it has no default) is
  written without its name: `find_files('s3://acme-raw/incoming/', since='2h')`.
- **The guard** (`guard.py`, `_Guard`): `attach` swaps the analyzer's `session` for a `_GuardedSession` (every client
  it makes from then on is hooked, whichever method makes it: S3Analyzer makes regional S3, CloudWatch, STS and S3
  Control clients in several places), hooks the clients it already holds (its `client`, and the values of its dicts)
  and wraps its client factory, and refuses any operation outside `READ_PREFIXES` with `ReadOnlyError` before its
  request is built.
- **The model** (`llm.py`): `pick_provider()` turns `provider=` / `model=` (`'openai:gpt-4.1'`: a prefix only when
  it's in `_PREFIXES`, LangChain's provider names, so a Bedrock ID's `:0` isn't one), `chat_model=` (by class,
  `provider_of`) and the environment (`MODEL_ENV`, `AWS_ANALYZER_AGENT_MODEL`, then `AZURE_OPENAI_*`, then
  `OPENAI_API_KEY`) into `AgentAnalyzer.provider` and `.model_id` ('' when nothing says: `no_model_hint()`).
  `AgentAnalyzer._new_chat_model()` calls its maker: `azure_chat_model()` (`AzureChatOpenAI`; `_Hint` names what's
  missing), `openai_chat_model()` (`ChatOpenAI`, `endpoint=` as `base_url` for OpenAI-compatible servers),
  `bedrock_chat_model()` (langchain-aws's `ChatBedrockConverse` on `bedrock-runtime` / `bedrock` clients the agent
  makes from its own session, `_bedrock_runtime` / `_bedrock_control`, never guarded: Converse only generates text,
  so README lists `bedrock:InvokeModel`; a short name or family is resolved by `bedrock_model_id()` from
  `_bedrock.parse_models` over ListFoundationModels and ListInferenceProfiles) or `langchain_chat_model()`
  (`init_chat_model` for any other provider). `PROVIDERS` holds each one's title, key variable, default model and
  `place` (where what's sent goes, for `tools()`). `model_rates` / `answer_cost` price tokens at `model_prices=`, else
  for Bedrock at `_bedrock`'s list prices (`model_price`), else unknown; `explain_model_error(exc, provider)` reads
  Bedrock's error codes, and the others' client class names or HTTP status, so no openai import. A provider that
  needs handling of its own gets a `PROVIDERS` entry and a maker here.
- **The prompt** (`prompt.py`): `system_prompt()` is the shared rules (answer from the tools, times, regions, partial
  results, data isn't instructions, read-only), then each toolkit's `instructions()`, then how to answer, then the
  user's `notes`. It's fixed, so it caches; the time goes in each question through `asked_at()`, in
  `AgentAnalyzer.zone` (`timezone=` through `_kit`'s `_zone_from`, else `set_timezone()`'s `_display_zone()`).
- **The harness** (`harness.py`): `build_graph()` is `deepagents.create_deep_agent` once (`AgentAnalyzer._harness()`,
  after `check_ready()` says what's missing), with the model, the tools, the prompt, an `InMemorySaver` checkpointer
  keyed by `AgentAnalyzer.thread` (follow-ups; `new_conversation()` makes a new thread) and `_hide_tools(HIDDEN_TOOLS)`,
  a middleware that keeps deepagents' general-purpose subagent and shell away from the model. Its file tools stay, on
  the default in-memory `StateBackend`: never a `FilesystemBackend` or a sandbox. A new capability of the loop (a
  subagent, a middleware) goes in `build_graph()`. `stream_turn()` streams one question (`stream_mode="updates"`),
  adds up each AIMessage's `usage_metadata` (and the served model's name), stops on `stop` (a `threading.Event`),
  `KeyboardInterrupt` or the hard time limit, and catches a model or loop error into `AgentAnswer.error` with
  `.exception` kept. Everything about LangGraph's messages stays in this file.
- **`AgentAnalyzer`** (`analyzer.py`) holds the settings and puts the parts together: `ask()` makes the `_Run` and
  the `AgentAnswer`, calls `stream_turn()`, and fills in what the guard and the run counted. `AgentView` (`view.py`)
  draws the answer with `_bedrock._Answer` (markdown; `.ans` rules in its `_EXTRA_CSS`), cards, `answers.py`'s
  `answer_findings()`, the table of tool calls (each window in the reports' zone, `_fmt_dt`; what the model reads
  stays ISO UTC), the AWS reads (folded) and `answer_next()` under a title naming the Views they're on.
- **The chat window** (`window/`: `chat.py`'s `_AgentChat`, a `_Window`, and `model_panel.py`; `AgentView.chat()` imports it lazily and keeps it in
  `view.window`): the agent's steps drawn as they happen. `ask(on_step=)` puts a listener on the `_Run` (`_Run.tell`):
  `stream_turn` tells `AgentStep('thinking')` for a model message with tool calls and text, and `as_tool` tells
  `'tool'` before a call runs and `'tool_done'` after (the same `ToolCall`, filled in). Tools run on LangGraph's
  threads, so the window runs `core.ask` in the loop's executor and drains a queue of steps on the loop every
  `_DRAW_EVERY` (`_run_later`; inline without a loop). It's drawn like a terminal (`--ag-mono`, questions on the right marked `_HUMAN` 🧑 and answers on the left `_MACHINE` 🤖, both `_kit`'s, as in `bedrock_chat`, `●`
  the model's words, `✻ Thinking…`). Each step is its own HTML widget in the `_Turn`'s box, so a line that changes
  redraws alone. A tool call is its call in colour (`_python_html`) with a badge per service it reads and a `# comment`
  from `_TOOL_WORDS` (`{t}` from `_target`; a tool it doesn't know reads as its name). The services come from
  `ToolCall.reads` (`as_tool` copies the toolkit's `reads` for the tool; `services_of` takes their prefixes), and
  `_SERVICES` maps each to its badge (AWS's category colour and a `_DRAWINGS` line drawing, `ag-s-<code>`); an unknown
  service gets a grey badge with the start of its name, so a new toolkit's calls show without a change here, and a
  new service only needs its line in `_SERVICES`. A finished call opens to its operations, coverage, next calls and
  `ToolCall.result` (what `_reported` handed the model) as a `_JsonTree`. Once answered, the steps fold into one
  `<details>` line (`_trail_html`, open when something went wrong) naming the services, drawn into the pulse's widget
  so the log stays pinned to the newest; the rest of the report is `AgentView._checked_blocks` under details. Every
  box in the log is `flex: 0 0 auto` (`_box`), or the log shrinks it and cuts off its end. An empty conversation is
  `empty` (the services' legend and `_SUGGESTIONS` rows, `_row_button`s); new chat clears the log back to it. Send
  turns into stop (the `_Run`'s stop Event) while `busy`, and `AgentView.ask()` refuses while it is and adds its
  answer to an open window (`added`). The model isn't streamed token by token: on Bedrock that would need
  `InvokeModelWithResponseStream`. **⚙ model** (`model_button`) toggles `_ModelPanel` (`panel`, between the head and
  the log; a package module named `window` because `rules.py` reads its first part as the UI layer): a Dropdown of
  `provider_options()` (`llm.provider_names()`, plus the chat model passed in, `AgentAnalyzer.given_chat_model`), a
  Combobox for the model (Bedrock's region list, `bedrock_choices` over `_bedrock_models()`, read off the loop once),
  and only the rows `form_for(provider)` (`_FORMS`) asks for: endpoint, key (a `Password`, cleared after use) and
  API version. Its note says `model_place` and `price_text`; Use this model is `AgentAnalyzer.use_model()`, which
  makes the chat model at once and restores every `_MODEL_SETTINGS` attribute when that raises, then starts a new
  thread; the window's `model_changed()` (also called by `AgentView.use_model()`) redraws the head and draws an
  `_switch_html` line across the conversation. With no model set up, the window opens the panel on Bedrock. Tests
  drive it like the other windows (`window()`, `enter()`, `page()` in `test_agent.py`), holding the scripted model
  back with a `threading.Event` among its replies.

## The S3 explorer (`s3/explorer/`)

`navigator.py` holds the pure helpers (`parse_location`, which also takes S3 console links and object URLs,
`breadcrumbs`, `sort_entries`, `parse_filter`, `filter_entries`, `count_types`, `folder_stats`) and `S3Navigator`, the
logic layer (one `list_objects_v2` level per page, `list_rest()` up to `list_limit` entries, or everything below a
folder with `below()`, back / forward / up history, a folder cache capped by count and by `_CACHED_ENTRIES`, listing
errors in `Folder.error`, never prints). `S3Explorer` (`window.py`) is the UI, made of a mixin per pane: `_ListPane`
(`listing.py`, the list on the left), `_ReportPane` (`report.py`, the right pane), `_SelectionPane` (`selection.py`)
and `_SettingsPane` (`settings.py`), with what they share in `frame.py` (the stylesheet, `_Row`). Listing is split so
it can run off the main thread: `_request()` makes one request and changes nothing, and `_add()` adds its page to the
`Folder` (`_list()` does both, a page at a time). How the UI works:

- It's part of the s3 package and imports what it draws from there; `_s3_module()` (`x.s3`) is that package.
  Without ipywidgets, a text listing (`mode="text"` forces that).
- The right pane is a private `S3View` whose `_show` is replaced by `_capture`: its reports (`preview`, `head`,
  `document`, `download`, `link`, `summary`, `bucket_info`, `overview`) land in an `HTML` widget, with the `_Next`
  block dropped and the file's path shortened to its name. Progress bars go into an `Output` above it. `x.ui` is a
  normal `S3View` for the user's own cells.
- Still no JavaScript: every click is an ipywidgets `Button`, styled by the `<style>` in a hidden `HTML` widget
  (`.s3x` classes, overriding ipywidgets' own hover / focus shadows). A row is a full-width button under its size and
  age labels (`pointer-events:none`), so the whole row is the click target. Rows are pooled and reused.
- Icons are drawn by the CSS: each `_ICON_PATHS` line drawing becomes a `.s3x-i-<name>` mask in the text's colour, and
  the button keeps its glyph (← ✎ ⚙) as its text, hidden by the style.
- The panes' height is the style's (`.s3x-body`): the browser window's less JupyterLab's bars, at least 560px. VS Code
  gets 560px, since its `100vh` is the whole notebook's height. `height=` (pixels or CSS) goes on the box instead.
  `bedrock_chat`'s conversation (`.kbc-log`, at least 540px) and side tabs work the same way.
- Widgets can't scroll, so `_renew()` puts the list or the report in a new box, which starts at the top. The search
  box and its buttons (`_finder`) sit above that box and stay put, and so does the page bar under it (`_pages`): the
  list shows `page_size` rows from `_offset`, and « ‹ › » (`_on_page`) move it. Never more rows than a page, since
  each row is six widgets.
- Big folders: the search, the counts and the sort cover the whole folder, so `_follow()` lists the rest of the one
  the list shows, up to `nav.list_limit` entries (`deep_limit` files with Include subfolders). In a notebook
  (`_background()`: widgets and a running loop) `_list_later` does it a page at a time, like `_later`: the request on a
  worker thread (`_threads()`), and `_add` plus the redraw (`_listed`, which also refreshes the overview in place)
  on the loop, so nothing changes under a click. `_lister` is the folder being listed; moving to another folder
  stops it (`_stop_listing`), and coming back carries on from the folder's token. Without a loop (the text view,
  scripts, the tests) `_list_more` lists right away. The tests drive the background path inside `asyncio.run`, with
  `S3Navigator._request` held back by a `threading.Event`. `_ordered()` and `_stats()` keep the sorted entries and
  the counts until more is listed, so a key in the search box only filters.
- Searching: `_draw_filters` draws the search box (`_query`), All / Folders / Files (`_kind`, which stays as you move,
  like the sort) and a chip per file type (`count_types`); `_shown_entries` matches with `filter_entries` /
  `parse_filter`. A chip writes `.csv` into the search box (`_toggle_type`), so the box is the one record of a filter.
  "Include subfolders" (`_deep`) swaps the list's source (`_source()`) for `S3Navigator.below()`, a listing without
  the `/` delimiter, `deep_limit` files at a time, with the folders between derived from the keys; its rows show their
  folder under the name. Opening another folder clears the search and the subfolders; ↻ keeps them. Past what's
  listed, "Look up" (`S3Navigator.lookup`) asks S3 for names starting with the search text, and opening a file's path
  looks it up the same way when its folder is bigger than what's listed.
- Selecting: each row has a checkbox (`_Row.check`, hidden by the style until the row is pointed at or `_picked` has
  something, `s3x-picking`), and the header's ticks everything in `_visible`. `_picked` (uri -> Entry) feeds the bar
  under the list (`_draw_picks`) and the panel on the right (`_open_picks`), which shows what goes in and a name from
  s3's `_zip_layout`, made unique on disk. `_save_picks` passes files as `ObjectInfo` (no request to find them) and
  folders as uris to `download_zip`, which takes a list; it refuses a name that's taken rather than replace a file.
  Opening another folder clears the selection.
- A click within `_CLICK_GRACE` seconds after the rows changed is dropped: it was aimed at the old rows (a double
  click on a folder would otherwise open whatever took its place).
- The path box navigates on Enter only: it listens for the `submit` message the text box sends (`on_submit` is
  deprecated), so leaving the box or clicking ✕ doesn't navigate.
- Callbacks go through `_guard()`, which turns any exception into a note on the right; an exception in a widget
  callback would otherwise go to Jupyter's log, and the click would seem to do nothing.
- Quick reports (`_BACKGROUND`: preview, head) load on `_WORKERS` threads when the kernel's event loop is running
  (`_running_loop()`), so a click returns at once and the next click isn't kept waiting. `_later` awaits the worker's
  future
  on that loop and shows the report only if `_job` (bumped by every `_set_pane`) hasn't moved since; it's cached
  either way, and a job already out of date when a worker picks it up is skipped. A worker uses its own
  `S3View(progress="off")` and touches no widgets. Without a running loop (scripts, the tests) reports load inline;
  tests drive the background path inside `asyncio.run`.
- "▾ Expand all" (`_draw_expand`, beside ✕ while the report has a `_JsonTree` with something folded) opens every
  object and array with `_JsonTree.unfold("all")` and draws the report again in place; it stays on for the next JSON
  files (`_expand_all`, applied in `_set_pane`) until it's clicked again (`unfold("start")`).
- "Read all" on a PDF (`_read_pdf`) draws `_MAX_PICTURES` pages from `_first_page`, after counting the pages once per
  file version (`_page_count`); `_draw_pager` puts the buttons for the pages before and after under the report.
- "⬇ Download .zip" on a folder runs `download_zip` with `zip_max_size` / `zip_max_files`, which the ⚙ settings panel
  edits (Text widgets shown under the report's title; Enter in a box saves, like the path box), and saves where
  "⬇ Download" does: `x.downloads`, a property over `core.downloads` (`zip_folder` is its old name), which ⚙ edits too.

## The PyPI package

- `pyproject.toml` (hatchling) ships `src/aws_analyzer/` as the wheel's `aws_analyzer` package: each analyzer is a
  module there, next to `__init__.py`, which holds `__version__` and re-exports the Analyzer / View / Explorer
  classes lazily through a module `__getattr__` (importing `aws_analyzer` loads no analyzer). A new analyzer needs
  its module name in `__init__.py`'s `_MODULES` (`tests/test_package.py` checks every module is listed) and its
  classes in `__all__`, `_EXPORTS` and the `TYPE_CHECKING` imports.
- Every analyzer imports `._kit` relatively, so it runs only as part of the package.
- The one dependency is `boto3>=1.35.72`, the first release whose service models have every AWS operation the
  analyzers call (Bedrock's `ListKnowledgeBaseDocuments`). A call to a newer operation means raising it; otherwise
  keep it low, so installing doesn't upgrade the boto3 a SageMaker image ships with. Dependabot's
  `versioning-strategy: increase-if-necessary` bumps the `==` pins in requirements files but leaves `>=` floors alone.
- Optional packages are extras: `data` (pandas, pyarrow), `files` (the S3 file readers), `notebook` (IPython,
  ipywidgets, tqdm), `agent` (deepagents, langchain-openai; Python 3.11+), and `all`. A new optional package goes in one of them as well as in README's install lines.
- PyPI shows README.md, through `hatch-fancy-pypi-readme`, which points its relative links and pictures at GitHub
  and turns `> [!NOTE]` callouts into bold labels (PyPI renders neither). `twine check --strict` with
  `readme-renderer[md]` checks that it renders.
- Releasing: `/release` (`.claude/skills/release`). `release.py bump` moves `CHANGELOG.md`'s `## [Unreleased]`
  entries under the new version and sets `__version__` to match (`tests/test_package.py` checks the changelog has
  an entry for `__version__`); that goes through a release pull request, and then a GitHub release tagged
  `v<version>` on its merge commit, with `release.py notes` as the notes. `.github/workflows/release.yml` builds,
  checks, imports the wheel with only boto3, refuses a tag that doesn't match `__version__`, and uploads with PyPI
  trusted publishing from the `pypi` environment (no token stored). A version can be uploaded only once.

## Tests

- `tests/conftest.py` puts `src/` on `sys.path` so tests import the analyzers from the package
  (`from aws_analyzer import s3`, `from aws_analyzer.dynamodb import ...`) the way an installed notebook does, and
  sets fake AWS credentials plus `AWS_DEFAULT_REGION=us-east-1`. The run fails if a module also loads under its bare
  name (`import s3`): its classes would be a second copy that `isinstance` doesn't match.
- One test file per analyzer. Pure functions are tested directly; AWS-backed methods use an `aws` fixture wrapping
  `moto.mock_aws()` and fixtures that seed a bucket / table. A new AWS service needs its moto extra added to
  `moto[...]` in `requirements-dev.txt`. `_kit` is tested through the services, and `tests/test_kit.py` covers what
  no service reaches in normal use (a block nothing draws, `_base_css`'s checks on a restyle, each error kind
  `@_friendly_errors` meets on a toy View, that `prices=` given to one analyzer changes no other's, and the windows'
  guards on a toy `_Window`: a command that can't run, an error the window doesn't say).
- Bedrock has little moto support (moto 5.2 only creates / gets / lists / deletes knowledge bases), so
  `tests/test_bedrock_kb.py` uses botocore `Stubber` with injected clients:
  `BedrockKBAnalyzer(client=agent, clients={"bedrock-agent-runtime": ..., "bedrock-runtime": ..., "bedrock": ...})`.
  Stubber answers in the order calls are queued and checks each request against the service model; set
  `core.max_workers = 1` when a test lists several knowledge bases. moto is only used for the S3 bucket behind
  `unsynced()` and the file reports. For the same reason the Bedrock seeder in `.claude/skills/demo/demo.py` returns
  fake clients (`_FakeAWS`, which validates requests and responses against the service model) that `demo.py` passes to
  the analyzer; only the buckets are moto (support-docs' files match its document records, plus a few in each state the
  explorer shows, and `file_chunks` answers a Retrieve limited to one file). The explorer window's tests use a `World`:
  `Fake` bedrock-agent and bedrock-agent-runtime clients (any order, checked against the service model) over a moto
  bucket, with `holds` (a `threading.Event` per operation) to hold calls back for the background tests inside
  `asyncio.run`, and `errors` to make one fail.
- SageMaker: moto covers notebook instances (with an old instance-type list: no `ml.g5`), lifecycle configs,
  domains and STS, so `tests/test_sagemaker_env.py` uses it for notebook instances. moto has no `ListApps`,
  `DescribeApp` or spaces, so the Studio and `running()` tests use `Stubber` on injected clients
  (`SageMakerAnalyzer(clients={"sagemaker": ..., "sts": ..., "cloudwatch": ...})`, `max_workers = 1`), and
  `write_root()` / `fake_machine()` build the fake machine. demo.py's `seed_sagemaker_env()` returns fake clients
  and a fake root with sparse files, so the disk shows gigabytes without writing them, and conda environments and
  kernels (`_seed_python_envs`, linked into the home folder from outside it so `disk()` stays as the docs show it, with
  a `_run` that makes environments without downloading). The environment tests build a notebook instance's
  (`notebook_envs()`) and run `new_env()` with `Runner` as `core._run`.
- OpenSearch: moto covers domains (`create_domain`, `describe_domains`), so `tests/test_opensearch.py` uses it for
  them, but not `DescribeDomainHealth`, Serverless without a KMS key, `GetAccountSettings` or access policies, so
  Serverless, CloudWatch, STS and Bedrock come from a `Fake` client (like `test_bedrock_chat.py`'s) passed in
  `clients=`. The REST side is `tests/fake_opensearch.py`'s `FakeCluster`, passed as `OpenSearchAnalyzer(http=...)`:
  an in-memory OpenSearch that answers `_cat/indices`, mappings, settings, `_count`, k-NN `_search` with
  OpenSearch's score formulas, `_stats`, `_cluster/health` and `_plugins/_knn/stats` (`serverless=True` refuses the
  cluster-level ones, like Serverless; `scale=` makes counts report millions of documents for the demo).
  demo.py's `seed_opensearch()` imports it from `tests/` and returns an `http` router over a cluster per endpoint,
  with fake Serverless, CloudWatch, STS and Bedrock clients (a toy embedding model that knows four topics).
- Lambda: moto covers functions, versions, aliases, resource policies, event source mappings, function URLs,
  CloudWatch (`Sum` and `Maximum`, not percentiles, so the analyzer reads neither) and Logs, so
  `tests/test_lambda_functions.py` uses it, through `clients={"lambda": factory}` (a client per region): the factory
  wraps moto's client in `Patched`, which answers what moto lacks (`GetAccountSettings`,
  `ListProvisionedConcurrencyConfigs`, `GetRuntimeManagementConfig`, `Concurrency` in `GetFunction`, AWS-shaped
  function-URL policy statements, which moto writes outside `Condition`) with functions checked against the service
  model. `core._download` is replaced, since moto doesn't serve the package link. demo.py's
  `seed_lambda_functions()` does the same, and also patches the Logs client's log sizes and `urllib.request.urlopen`
  for the packages; its logs are orders-etl's text logs (with INIT_START lines on cold starts) and report-api's JSON
  ones (`_report_api_logs`), timed with `_epoch_ms` (moto keeps naive UTC, which `.timestamp()` alone would read as
  local time). The explorer window's tests build `LambdaExplorer(core=core, mode="widgets")` on the same moto data,
  click and type through it like the KB explorer's (`seed_runs` adds runs to a stream of their own), and drive the
  background path and Live inside `asyncio.run`, with `filter_log_events` held back by a `threading.Event` through a
  `Patched` Logs client (`clients={"logs": ...}`) and `_LIVE_SECONDS` made short. `map_code()` is tested on `APP`, a
  three-file package that does most of what it follows, and `how_it_works()` and the Code tab's How it works on `HOW`;
  demo.py's `support-agent-actions` has `AGENT_SOURCES`, a Bedrock agent action group that sets off its findings.
- `tests/test_bedrock_chat.py` uses `Stubber` for the requests the analyzer sends, and a `Fake` client elsewhere
  (answers in any order, checks every request, response and stream event against the service model, and has no
  method for an operation without a handler, like an old boto3). The window's tests build it with `mode="html"`,
  replace `view._display`, and click and type through the widgets in Python (ipywidgets is in
  `requirements-dev.txt` for this). The Test tab's background run is tested inside `asyncio.run`, with a handler held
  back by a `threading.Event`, as the explorer's tests do (a sweep's too); `python_script`'s output is run with `exec`
  against a `Fake` client put in `sys.modules["boto3"]`. `save_runs()` / `load_runs()` tests `monkeypatch.chdir` into
  `tmp_path`.
- `tests/test_agent.py` runs the tools on moto (with `test_lambda_functions`' `create`, `put_logs` and
  `lambda_clients`, whose `policies=` gives a function the resource policy `s3_triggers` reads; the `buckets` fixture
  uploads its files before it sets a bucket's notifications, so moto doesn't try to run the function), a toy `Notes` toolkit with no AWS behind it to show what a new service's toolkit plugs into, and
  the agent loop with `scripted(...)`, a LangChain chat model that answers with AIMessages in
  turn (`calls(...)` for tool calls, `says(...)` for text) and records the tools it was given and the messages it was
  sent; `test_asking_through_azure_openai` runs `AzureChatOpenAI` itself over an `httpx.MockTransport` that answers as
  Azure does. Tests that need deepagents or langchain-openai skip without them (Python 3.10).
- UI tests build the View with `mode="text"` and assert on `capsys` output through a small `run(capsys, fn, ...)`
  helper. Each service's help test checks that `set(_commands(<Service>View))` is what its `_GROUPS` list.
- `tests/test_s3_explorer.py` builds `S3Explorer(mode="widgets")` without a kernel (ipywidgets works without one),
  clicks with `button.click()`, sets text boxes' `.value`, sends the path box's Enter with
  `_handle_custom_msg({"event": "submit"}, [])`, sets `_CLICK_GRACE` to 0 (on `s3.explorer.listing`, the module that
  reads it), and reads the right pane as blocks in `x.shown`.

## Docs and dependencies

- `README.md` is the user documentation: per service, a quick start, a command table, the pure functions, cost
  notes and IAM permissions. Update it together with the analyzer.
- `CHANGELOG.md` ([Keep a Changelog](https://keepachangelog.com/en/1.1.0/)): every change a user would notice adds a
  bullet under `## [Unreleased]`, in Added / Changed / Fixed / Removed, written for someone upgrading (starts with
  the command, says what they can do now, ends with the pull request link; `**Breaking**:` first when a notebook
  would have to change). Tests, CI, refactors and docs edits get none. `CONTRIBUTING.md`, `SECURITY.md` and the
  issue and pull request templates in `.github/` are for outside contributors; keep them in line with these rules.
- `docs/` is the guide site: Markdown built by MkDocs with the Material theme (`mkdocs.yml`, versions pinned in
  `requirements-docs.txt`) and published to GitHub Pages by `.github/workflows/pages.yml` on pushes to `main`;
  pull requests only build it, with `--strict`. `use_directory_urls: false` keeps the pages at `s3.html`, ... so
  README links and old links still work. `index.md` is the home page, with no sidebars: a hero on a field of dots
  (`.hero`: a `.chip`, the title's `.accent`, the install line, the buttons and `.works`, the services' icons) beside a
  notebook cell drawn in HTML (`.nb`, under a JupyterLab tab, `.nb-bar`), lines from real reports beside the AWS data
  they read (`.tr`), a card per service led by the questions it answers (`.svc`), how a report reads (`.anatomy`),
  what it never does (`.never`, a card each with its icon), time zones (`#timezone`) and a closing band (`.cta`); each
  section's label sits above its heading (`{ .kicker }`); each service has its own guide (`s3.md`, `dynamodb.md`, `bedrock_kb.md`, `bedrock_chat.md`,
  `sagemaker_env.md`, `opensearch.md`, `lambda_functions.md`), and so do the S3 explorer (`s3_explorer.md`, which `s3.md#explorer` points to) and the agent (`agent.md`, with Lambda's icon, the service it covers). A new
  analyzer gets its own `docs/<service>.md`, a line in `index.md`'s `.svc` list, an entry in `mkdocs.yml`'s `nav`
  and a link in README. Each service shows its AWS Architecture icon (`docs/images/aws/<service>.svg`, the 64 px service icon from
  AWS's [icon package](https://aws.amazon.com/architecture/icons/), copied unchanged, never recoloured or
  cropped) on its home-page line, in its guide's eyebrow, and in README's Services table and
  section; a new service copies its icon in from the package. `index.md` ends with a script that forwards old `/#section` links (from when it was the S3 guide) to
  `s3.html` when the id isn't on the home page. A guide's building blocks: section headings keep explicit ids
  (`## Permissions { #permissions }`) because README and other pages link to them; code blocks are fenced with a
  language and an optional `title="IAM policy"`; callouts are `!!! note ""` / `!!! warning ""`, troubleshooting
  entries `??? question "..."`, card grids `<div class="grid cards" markdown>`, the set-up steps
  `<div class="steps" markdown>` and the command reference `<div class="ref" markdown>`; `docs/stylesheets/extra.css`
  styles them. The site's look is there too: ink navy chrome, the blue of a selected Jupyter cell (`--aa-blue`) for
  links and the bar beside every screenshot, highlighter amber (`--aa-mark`, `<mark>`) only for what a report wants
  noticed, Bricolage Grotesque headings over IBM Plex Sans and Mono (`theme.font`); no green. Content sits on soft cards (`--aa-surface`, `--aa-shadow`, `--aa-lift`):
  screenshots are framed reports a click enlarges, questions and folds are cards that open, tables are rounded with
  small capitals over the columns, `p.part` is a dark band, and the navigation carries each service's AWS icon
  (`a.md-nav__link[href=...]::before`; a new guide adds its line). Line-drawn icons are `--aa-i-*` masks. Its motion is in `docs/javascripts/motion.js` (`extra_javascript`) and the Motion section of `extra.css`: a guide's opening rises in, what's below the fold rises as it's scrolled to (`.aa-reveal` / `.aa-in`, a group's items one after another by `--aa-i`), the highlighter sweeps over each `<mark>`, the home page's notebook cell types and runs its call, a bar under the header shows how far through the page you are, and a click on a screenshot opens it full size. All of it is short and small, nothing is hidden unless the script has put `.aa-motion` on `<html>`, and `prefers-reduced-motion` turns it off. A guide opens with its
  eyebrow (icon, service name, module as code), an H1 that says what the reader gets ("Find out which Lambda failed,
  and why, without the console"), a short `.lede` and the `pills` it promises. Write for a data scientist: the
  question they have first, then what the page lets them do, in plain words. A guide reads in layers, so a reader who
  skims still gets the point: `## Start here { #tour }` is a `<div class="quick" markdown>` list of tiles (the
  question in bold, the one call, a link to its section); each section opens with its takeaway in one sentence marked
  `{ .gist }`; detail most readers can skip (the offline install, options, how it works inside, every filter) goes in
  `??? more "what's inside"` folds; the one thing that costs money or loses data if missed is `!!! tip "Worth
  knowing"`; and `<p class="part">` marks where the guide ends and reference begins (the data in Python, cost,
  permissions, troubleshooting, the command reference). Keep a visible paragraph under about 60 words.
  A screenshot is two images and a caption:
  `![alt](images/<name>-light.webp#only-light){ width="984" height="..." loading=lazy }`, the same line with
  `-dark` / `#only-dark` (Material shows the one for the reader's theme), then `/// caption` ... `///`.
  The screenshots (`docs/images/*-{light,dark}.webp`) are the tool's own output, made by
  `.claude/skills/demo/shots.py` from the "acme" scenes the guides are written around (S3, DynamoDB) and demo.py's
  fake Bedrock, SageMaker and OpenSearch, and demo.py's Lambda functions: `shots.py <name>` remakes one figure and sets
  the `height=` of both its images (`CHROME=/opt/pw-browsers/chromium_headless_shell-*/chrome-linux/headless_shell` in
  a cloud session). The figures are drawn in Liberation Sans, what Helvetica becomes on Linux; a cloud session's
  `/etc/fonts/conf.d/56-prefer-inter.conf` makes the system fonts Inter, which is wider, so point `FONTCONFIG_FILE` at
  a copy of `/etc/fonts/fonts.conf` that includes every `conf.d` file but that one (an unchanged S3 figure then comes
  out as committed).
  The explorer is a live widget, so its figures (`explorer`, `explorer-docx`, `explorer-buckets`, and `explorer-tour`,
  an animated WebP of a pointer clicking through it, all in `s3_explorer.md`) come from `explorer_shots.py`, which
  runs it in a real JupyterLab with Playwright (`pip install jupyterlab playwright`). The chat window's figures
  (`chat-*`, in `bedrock_chat.md`) come from `chat_shots.py` the same way: it opens the window on demo.py's fake
  Bedrock, types and clicks through it, and sets the heights with `shots.set_height`, and so do the knowledge base
  explorer's (`kb-explorer*`, in `bedrock_kb.md`), from `kb_explorer_shots.py`, and the Lambda explorer's
  (`lambda-explorer*`, in `lambda_functions.md`), from `lambda_explorer_shots.py`, and the agent's chat window's
  (`agent-chat*`, in `agent.md`), from `agent_chat_shots.py`, which needs Python 3.11+ with the agent's packages and
  plays a scripted model over demo.py's Lambda functions. Remake the affected figures when
  a report's look changes, and check their captions and alt text still match, in the guides and in README, which
  shows thirteen of them (`overview`, `dynamodb-table-info`, `preview-parquet`, `explorer-tour`, `dynamodb-scan-filter`,
  `bedrock-ask`, `kb-explorer-file`, `chat-window`, `sagemaker-instance`, `opensearch-index-info`, `lambda-functions`,
  `lambda-explorer-logs`, `agent-chat`) as `<picture>`s that
  switch to the `-dark` file in dark mode.
- Versions in `requirements-dev.txt` (which also pins `build`, `twine` and `readme-renderer[md]` for the package
  checks) and `requirements-docs.txt` are pinned and updated by Dependabot; the
  `python_version < "3.11"` lines are intentionally held back, and so is mkdocs at 1.x (2.0 drops the plugins and
  themes Material needs). `ruff.toml` selects `E4`, `E7`, `E9`, `F` (real errors) and the "Code style" rules ruff
  can check (`B006`, `BLE001`, `ERA001`, `C901`, `PLR0912`, `PLR0913`, `PLR0915`), listed explicitly so ruff upgrades
  don't change them; there is no formatter, and lines run to about 120 characters.
