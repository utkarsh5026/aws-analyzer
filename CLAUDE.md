# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Copy-paste AWS analysis utilities for SageMaker / Jupyter notebooks. Each service is **one self-contained file**
in `src/aws_analyzer/` (`s3.py`, `dynamodb.py`, `bedrock_kb.py` for Bedrock Knowledge Bases and `explore()`, a window to look
through one by clicking, `bedrock_chat.py` for a chat window on a knowledge base, `sagemaker_env.py` for the SageMaker notebook itself and what's running, `opensearch.py` for
OpenSearch vector indexes in Service domains and Serverless collections, `lambda_functions.py` for Lambda functions in one
region or all of them, and `explore()`, a window to click through them and read their logs run by run) that a user pastes
into a notebook cell or uploads next to a notebook and `import`s. `sagemaker_env.py` isn't `sagemaker.py` because that
would hide the SageMaker Python SDK, and `lambda_functions.py` isn't `lambda.py` because `lambda` is a Python keyword
(`import lambda` is a syntax error). The same
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
python .claude/skills/check/snapshot.py run OUT [--root CHECKOUT]  # every report as text and HTML (pip install time-machine)
python .claude/skills/check/snapshot.py compare BASE NEW           # what a refactor changed in them (see /check)
```

CI (`.github/workflows/ci.yml`) also checks that each analyzer imports on its own with only boto3 installed, and
its Package job builds the wheel, runs `twine check` and imports the installed package with only boto3
(`.claude/skills/check/run.py` does both). To reproduce the first locally:

```bash
for f in src/aws_analyzer/[!_]*.py; do d=$(mktemp -d); cp "$f" "$d/"; (cd "$d" && python -c "import $(basename "$f" .py)") && echo "ok: $f"; done
```

## Hard constraints

- **No imports between analyzers and no shared module.** Each file must work alone in a notebook. Helpers that
  every file needs (`human_size`, `human_money`, `_require`, `_in_notebook`, `_esc`, `_prose`, `_call`,
  `_signature`, the render blocks and `_render_html` / `_render_text` with their small helpers, `_friendly_errors`,
  `View._progress` with `_progress_bar_class` / `_progress_bar` / `_progress_text` / `_duration`, `View.help`) are
  deliberately duplicated in all seven analyzers. Only the CSS root class, `_BADGE` and the View's `_GROUPS` /
  `_START` differ between the copies. When you fix or change one of them, check the copies in the others.
  For now `s3.py`'s renderer is ahead of the others: its tables sort, filter and pick columns, and its key columns
  and findings are laid out as described under "How the View layer works" (asked for S3 first). Until that's
  ported (`/sync-helpers`), `drift.py` lists `_CSS`, `_Table`, `_prose`, `_findings_html` and `_render_html` /
  `_render_text` for `s3.py`; don't "fix" that drift by reverting `s3.py`.
  The exception is a companion (`COMPANIONS` in `.claude/skills/check/rules.py`): `s3_explorer.py` imports `s3`
  (lazily, inside `_s3_module()`, so it still imports alone), reuses its helpers instead of copying them, and is
  left out of `drift.py`. Nothing imports a companion.
- **boto3 + stdlib only at import time.** pandas, pyarrow, IPython, pypdf, pypdfium2, pillow, openpyxl, etc. are
  optional and are imported lazily inside the function that needs them, via `_require(module, purpose)` (raises an
  ImportError that says what to `pip install`; pass `package=` when the pip name differs, `_require("PIL.Image",
  ..., "pillow")`) or a local `from IPython.display import ...`. tqdm (and ipywidgets for its
  notebook widget) is the exception that fails quietly: `_progress_bar_class` loads it with `importlib`, and without
  it the progress line is plain text. `bedrock_chat`'s window loads ipywidgets with `_require` in `app()`.
- **Read-only against AWS.** Nothing writes to a bucket, table or knowledge base (e.g. S3 `deleted()` shows the
  restore call but never runs it, and Bedrock findings show the `start-ingestion-job` command instead of syncing),
  and nothing stops a notebook, deletes an app or endpoint, or deletes a local file (`sagemaker_env` shows the
  `aws sagemaker stop-notebook-instance ...` / `rm -rf ~/.../.Trash-1000/*` command instead). The one exception is
  `s3.py`'s `clean_downloads()`, which deletes what the user downloaded, and only from a downloads folder s3.py made:
  `S3Analyzer._make_downloads_folder` writes `_DOWNLOADS_MARK` (a `.gitignore` of `*`) when the folder is new or
  empty, `_made_for_downloads` checks for it, and `_protected_folder` refuses the notebook's own folder, the home
  folder and the ones above them, even when marked. Local files are only written where the user asks: S3 downloads,
  and `bedrock_chat`'s `save_runs()` / `log=`, which only append lines.
  Bedrock `Converse` generates text and changes nothing, so its call line carries a `# read-only:` comment for
  `rules.py` (RetrieveAndGenerate and RetrieveAndGenerateStream pass as `Retrieve*`; `rules.py` maps the stream to
  the `bedrock:RetrieveAndGenerate` permission), and so does `opensearch.py`'s `InvokeModel`, which only embeds a
  question. `opensearch.py` also talks to each domain's or collection's own REST API, signing requests with botocore's
  `SigV4Auth` (service `es` or `aoss`) and sending them with botocore's `URLLib3Session`, so no OpenSearch client
  library is needed. `rules.py` can't see those calls: `OpenSearchAnalyzer.request()` sends GET, and POSTs only to
  `_search` / `_count` (`_READ_POSTS`), refusing anything else before it's sent, and `tests/fake_opensearch.py` fails a
  test on any other request. Index fixes (replicas, force merge) are shown as opensearch-py calls, never run.
  `lambda_functions.py` never invokes, changes or deletes a function (findings show the `aws lambda ...` /
  `aws logs put-retention-policy ...` command instead); its `filter_log_events` call lines carry `# read-only:`
  comments (`FilterLogEvents` isn't a Get/List/Describe name), and `code()` downloads the deployment package with
  urllib from the short-lived S3 link `GetFunction` returns (`LambdaAnalyzer._download`, https only), which
  `rules.py` can't see. Environment variable values never reach a report: `parse_function` keeps their names, and
  `Function.raw` (the configuration shown folded) has the values replaced by `(hidden)`. Keep it that way; README lists the read-only IAM permissions per service, so update that list
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
  S3 also `_Frame`, `_Image`, `_Link`, `_Media`, `_Pages` (PDF pages drawn as pictures; `_Zoom` gives each a hidden
  radio button that its picture's `<label>` checks, and a checked one makes the page a fixed overlay whose ‹ › ✕
  are labels for the neighbouring pages' radios and the report's "none" radio, so a click shows the page full size
  and steps through every drawn page in the report; JupyterLab confines fixed elements to the notebook panel, so
  it fills the notebook, not the window), `_Flow` (a Word
  document laid out: headings, lists, tables and its pictures in place) and `_JsonTree` (a JSON file as nested
  `<details>` with coloured tokens, capped and opened breadth-first; not `bedrock_chat`'s `_Json`, which shows a
  request with its settings marked), and in Bedrock `_Passage` (a retrieved
  passage with `<mark>` highlights) and `_Answer` (an answer laid out from its markdown by `_Markdown`, with shaded
  cited spans and `[n]` superscripts), in `bedrock_kb` also `_Steps` (a dot per step on a line: how a file was
  indexed, the Syncs timeline), `_Pipeline` (each data source's stages, left to right), `_Chunks` (a file's chunks in
  document order: a bar of their sizes, and of where each sits in the file when its text was read, then each folded,
  the text it repeats from the one before in `<mark class="ov">`) and `_Shares` (parts of a whole in one bar: files by
  state), plus a copy of `bedrock_chat`'s `_Json`, and in `bedrock_chat` `_Code` (code to copy: Python highlighted by
  `_python_html`, from `tokenize`, JSON by `_json_source_html` or an AWS CLI command by `_shell_html`, by its `lang`)
  and `_Results` (a test run's questions, a line each that opens to its answer, `_result_html`)) and pass them to
  `self._show(blocks)`, which renders HTML in Jupyter or plain text elsewhere (`mode="auto" | "html" | "text"`).
  Don't emit HTML or print directly; add to the block list so both renderers handle it.
- The HTML is plain HTML and CSS, never JavaScript (Jupyter drops scripts from reopened notebooks), so anything
  interactive uses CSS or `<details>`. What the blocks offer:
  - Cards take an optional tone: `(label, value, "warn" | "bad" | "ok")`. Text mode adds ` (!)` to warn and bad.
  - A table cell can be `_Tone(text, tone)`, a coloured pill in HTML and plain text elsewhere. `code_cols` shows
    columns of calls as code, and `prose_cols` passes columns of tool-written sentences (the Warnings tables)
    through `_prose`. Tables over 30 rows scroll under a sticky header.
  - In `s3.py` (`_table_html`), a table of 3+ rows sorts by a click on a header (largest, newest or A to Z first,
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
    Never use it on table cells that hold data. In `s3.py`, `_split_lead` finds what a sentence says first (up to
    its first `:`, `;` or full stop, never inside a call): a finding shows it as a bold headline over the rest as
    points (`_finding_html`), and notes and Warnings cells get it as a bold lead-in (`_lead`); amounts of money
    in them are stressed. So write a finding as "what's wrong: why it matters. What to do (the call)."
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
- Text from a knowledge base is untrusted: HTML blocks escape every piece before wrapping it in markup, and
  `build_prompt` sends passages to a model as data inside `<source>` tags, never as instructions. Answers are
  markdown: `_Markdown` (stdlib only, the same in `bedrock_kb` and `bedrock_chat`) parses blocks and inline markup
  keeping each character's offset in the answer, because citations are offsets into the raw text. Raw HTML stays
  text, links open only http(s) and mailto, and pictures become links. Text mode prints the markdown as written
  (`_answer_lines` leaves code and tables unwrapped).
- `bedrock_chat` is, with the S3 explorer, one of the two interactive UIs, but standalone (not a companion): it
  copies its helpers like the other analyzers. `chat()` (module level) builds a `BedrockChatView` and calls
  `app()`, which shows `_ChatApp`, an ipywidgets window (pickers, the conversation as `HTML` widgets in a
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
  the Files field (`_list_files` fills it only when it's first opened; ticked files are chips, `_draw_files`).
  The header's four fields are `_Picker`s: a button under its face (label, name, ID) that opens a list of `_Choice`
  lines over the chat (`.kbc-pop`, absolutely placed, so the boxes around it are `overflow:visible`), each line a
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
  callback goes through `_ChatApp._safely`, which shows errors in
  the window (a callback's exception would only reach the browser log); Enter in a text box is the box's `submit`
  message (`_ChatApp._on_enter`, as in the explorer: `on_submit` is deprecated). View commands run from other cells
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
- `bedrock_kb`'s explorer window, `KBExplorer` (after the View, with `explore()`; `BedrockKBView.explore()` keeps it in
  `view.explorer`), is the third interactive UI, standalone like `bedrock_chat` and built the same way: ipywidgets
  styled by `_EXPLORER_CSS` (scoped under `.kbx-app`, report CSS `_CSS` included once in its style widget, so `_html`
  strips it from each report), no JavaScript, full-row `Button`s under their faces (`_FileRow`, the knowledge base
  field `_Chooser` with its searchable list and `backdrop`), custom tab buttons over pages (`_EXPLORER_TABS`, icons from
  `_explorer_rules()`, which also gives each `FILE_STATES` key its colour). Its data is the analyzer's file layer:
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
  tab", `where=` filters -> metadata filters). AWS is read off the loop: `_later(key, work, done, failed)` runs `work`
  in `_workers()` through the running loop's executor and `done` on the loop, inline without one; `self._jobs[key]`
  drops a stale result, `_open_kb` bumps every key so an earlier knowledge base's results are dropped, and `_tasks`
  holds the asyncio tasks (tests await them). `_renew_pane` puts the file page in a new box so it starts at the top,
  `_set` only sends HTML that changed, the status line says what each tab holds when nothing's going on (`_said`,
  `_tab_line`), and callbacks go through `_safely`, public commands (`open`, `file`, `search`, `refresh`) through
  `_window_errors`. Without ipywidgets or Jupyter it shows the reports instead (`_reports`).
- `lambda_functions`' explorer window, `LambdaExplorer` (after the View, with `explore()`; `LambdaView.explore()` keeps it
  in `view.explorer`), is the fourth interactive UI, standalone and built like `KBExplorer`: ipywidgets styled by
  `_EXPLORER_CSS` (scoped under `.lmx-app`, report CSS `_CSS` included once), no JavaScript, full-row `Button`s under
  their faces (`_Row`, which can carry a small action button such as the function list's Logs; `_RunRow`, whose button
  opens or folds the run's lines below it; the function field `_FunctionField` with its searchable list and `backdrop`),
  custom tab buttons over pages (`_EXPLORER_TABS`). The region field (`region_pick`: a region, or `"all"`) and the
  function field pick what it shows. The Functions tab is `overview()` read in three background steps so the list shows
  at once: `overview(metrics=False, details=False)`, then `_numbers()` (CloudWatch) and `_all_extras()` (resource
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
  clicked from the cached bytes; over 50 MB, a button downloads it anyway. Background work is `_later` (as in
  `KBExplorer`), the status line's busy text carries its job (`_status(owner=)`) so only that job, or the user being on
  its tab, replaces it (`_finish`), and `_drop` bumps keys so a function's results that come back after another was
  picked are dropped. `_renew` / renewing `runs_box` puts a page's scrolling box anew so it starts at the top.
  Sentences from reports go through `_for_window` / `_window_text`, which turn calls into tabs (`errors('etl')` -> "the
  Errors tab", `logs(..., request_id=...)` -> "run 8f5ce35b in the Logs tab") and capitalise them at a sentence's start.
  Without ipywidgets or Jupyter it shows the reports instead (`_reports`, `_report_for`).
- `sagemaker_env` also reads the machine it runs on: SageMaker's `/opt/ml/metadata/resource-metadata.json` (which
  says whether this is a notebook instance or a Studio app, and which), `/proc` (load, memory, uptime,
  processes and which are Jupyter kernels), the disks and `nvidia-smi`. `SageMakerAnalyzer(root=...)` points all of
  that at a folder of fake files, and `_shown()` / `_real()` make paths look and work as the machine sees them
  (`/home/sagemaker-user`, not the temp folder). `_cpu_count`, `_disk_usage` and `_gpu_query` are the other
  hooks tests and the demo replace. Local reads that fail go in `Machine.errors`, like AWS sections in `errors`.

Cost estimates come from module-level price tables (`S3_PRICES`, `DYNAMODB_PRICES`, `BEDROCK_PRICES`, and
`MODEL_PRICES` for $ per 1M tokens by model family, with `GLOBAL_MODEL_PRICES` for the cheaper `global.` inference
profiles, and `SAGEMAKER_PRICES`, storage plus the hourly price of each type in `INSTANCE_TYPES`, which also holds
its vCPUs, memory and GPUs, and `OPENSEARCH_PRICES`, EBS storage and Serverless OCUs plus the hourly price of each
OpenSearch instance type in that file's own `INSTANCE_TYPES` (vCPUs and memory, which give each node's k-NN memory),
with `EMBEDDING_MODELS` for the Bedrock embedding models `search()` can call, and `LAMBDA_PRICES`, requests, GB-seconds
(x86_64 and arm64), provisioned concurrency, `/tmp` and CloudWatch Logs ingestion and storage; us-east-1 list prices
with the date they were read) that callers override with `prices={...}` (and
`model_prices={...}`); the View shows whether list prices or the caller's prices were used. Check them against the
AWS Price List API (`pricing.us-east-1.amazonaws.com/offers/v1.0/aws/<AmazonS3|AmazonBedrock|
AmazonBedrockFoundationModels|AmazonES|AmazonSageMaker|AWSLambda|AmazonCloudWatch>/current/us-east-1/index.json`;
`index.csv` is easier to grep), which is what AWS bills from. `lambda_functions.py`'s `RUNTIMES` holds each managed
runtime's end of support, block-create and block-update dates from AWS's Lambda runtimes page (cfn-lint's
`LmbdRuntimeLifecycle.json` carries the same data when the page can't be reached), and `LATEST_RUNTIMES` the newest
runtime per language that findings suggest moving to. A model missing from `MODEL_PRICES` shows its cost as unknown rather than a guess.
`bedrock_chat.py` carries its own copies of `BEDROCK_PRICES`, `MODEL_PRICES`, `GLOBAL_MODEL_PRICES`, `DEFAULT_MODEL`
and the model helpers; change them together with `bedrock_kb.py`'s (`drift.py` lists any that differ). The two windows
also share copies: `_css_height`, `_running_loop`, `_cell_number`, `search_rank`, `_marked`, `_class_if`, `_Json` with
`_json_html` / `_plain_json`, `SEARCHABLE` and `Analyzer._cached_client` (which makes each client once, under a lock,
since both windows read from threads). `lambda_functions.py`'s explorer carries copies of `_css_height`,
`_running_loop`, `_cell_number`, `search_rank`, `_marked`, `_class_if`, `_skeleton` and `_for_window` (as in
`bedrock_kb.py`) and of `_python_html`, `_json_source_html`, `_JSON_TOKEN_RE` and `_PY_LITERALS` (as in
`bedrock_chat.py`); `_EXPLORER_CSS`, `_EXPLORER_TABS`, `_explorer_rules`, `_LOGO`, `_IN_WINDOW`, `_WINDOW_WORDS`,
`_window_text`, `_window_errors` and `explore` share names with `bedrock_kb.py`'s but are its own, as `_CSS` is.

## The S3 explorer (`s3_explorer.py`)

Same five sections: pure helpers (`parse_location`, which also takes S3 console links and object URLs,
`breadcrumbs`, `sort_entries`, `parse_filter`, `filter_entries`, `count_types`, `folder_stats`), `S3Navigator` as the
logic layer (one `list_objects_v2` level per page, `list_rest()` up to `list_limit` entries, or everything below a
folder with `below()`, back / forward / up history, a folder cache capped by count and by `_CACHED_ENTRIES`, listing
errors in `Folder.error`, never prints), and `S3Explorer` as the UI. Listing is split so it can run off the main
thread: `_request()` makes one request and changes nothing, and `_add()` adds its page to the `Folder` (`_list()`
does both, a page at a time). How the UI works:

- It finds `s3.py` with `_s3_module()`: the module `core` came from, else `import s3`, else `__main__` (s3.py pasted
  into a cell). Without it, a note; without ipywidgets, a text listing (`mode="text"` forces that).
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
  (`_loop()`), so a click returns at once and the next click isn't kept waiting. `_later` awaits the worker's future
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
  `moto[...]` in `requirements-dev.txt`.
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
  and a fake root with sparse files, so the disk shows gigabytes without writing them.
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
  `Patched` Logs client (`clients={"logs": ...}`) and `_LIVE_SECONDS` made short.
- `tests/test_bedrock_chat.py` uses `Stubber` for the requests the analyzer sends, and a `Fake` client elsewhere
  (answers in any order, checks every request, response and stream event against the service model, and has no
  method for an operation without a handler, like an old boto3). The window's tests build it with `mode="html"`,
  replace `view._display`, and click and type through the widgets in Python (ipywidgets is in
  `requirements-dev.txt` for this). The Test tab's background run is tested inside `asyncio.run`, with a handler held
  back by a `threading.Event`, as the explorer's tests do (a sweep's too); `python_script`'s output is run with `exec`
  against a `Fake` client put in `sys.modules["boto3"]`. `save_runs()` / `load_runs()` tests `monkeypatch.chdir` into
  `tmp_path`.
- UI tests build the View with `mode="text"` and assert on `capsys` output through a small `run(capsys, fn, ...)`
  helper.
- `tests/test_s3_explorer.py` builds `S3Explorer(mode="widgets")` without a kernel (ipywidgets works without one),
  clicks with `button.click()`, sets text boxes' `.value`, sends the path box's Enter with
  `_handle_custom_msg({"event": "submit"}, [])`, sets `_CLICK_GRACE` to 0, and reads the right pane as blocks in
  `x.shown`.

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
  README links and old links still work. `index.md` is the home page with one card per service (Material grid
  cards); each service has its own guide (`s3.md`, `dynamodb.md`, `bedrock_kb.md`, `bedrock_chat.md`,
  `sagemaker_env.md`, `opensearch.md`, `lambda_functions.md`), and so does the S3 explorer (`s3_explorer.md`, which `s3.md#explorer` points to). A new
  analyzer gets its own `docs/<service>.md`, a card on `index.md`, an entry in `mkdocs.yml`'s `nav` and a link in
  README. Each service shows its AWS Architecture icon (`docs/images/aws/<service>.svg`, the 64 px service icon from
  AWS's [icon package](https://aws.amazon.com/architecture/icons/), copied unchanged, never recoloured or
  cropped) on its home-page card, in its guide's eyebrow, and in README's Services table and
  section; a new service copies its icon in from the package. `index.md` ends with a script that forwards old `/#section` links (from when it was the S3 guide) to
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
  fake Bedrock, SageMaker and OpenSearch, and demo.py's Lambda functions: `shots.py <name>` remakes one figure and sets
  the `height=` of both its images (`CHROME=/opt/pw-browsers/chromium_headless_shell-*/chrome-linux/headless_shell` in
  a cloud session).
  The explorer is a live widget, so its figures (`explorer`, `explorer-docx`, `explorer-buckets`, and `explorer-tour`,
  an animated WebP of a pointer clicking through it, all in `s3_explorer.md`) come from `explorer_shots.py`, which
  runs it in a real JupyterLab with Playwright (`pip install jupyterlab playwright`). The chat window's figures
  (`chat-*`, in `bedrock_chat.md`) come from `chat_shots.py` the same way: it opens the window on demo.py's fake
  Bedrock, types and clicks through it, and sets the heights with `shots.set_height`, and so do the knowledge base
  explorer's (`kb-explorer*`, in `bedrock_kb.md`), from `kb_explorer_shots.py`, and the Lambda explorer's
  (`lambda-explorer*`, in `lambda_functions.md`), from `lambda_explorer_shots.py`. Remake the affected figures when
  a report's look changes, and check their captions and alt text still match, in the guides and in README, which
  shows twelve of them (`overview`, `dynamodb-table-info`, `preview-parquet`, `explorer-tour`, `dynamodb-scan-filter`,
  `bedrock-ask`, `kb-explorer-file`, `chat-window`, `sagemaker-instance`, `opensearch-index-info`, `lambda-functions`,
  `lambda-explorer-logs`) as `<picture>`s that
  switch to the `-dark` file in dark mode.
- Versions in `requirements-dev.txt` (which also pins `build`, `twine` and `readme-renderer[md]` for the package
  checks) and `requirements-docs.txt` are pinned and updated by Dependabot; the
  `python_version < "3.11"` lines are intentionally held back, and so is mkdocs at 1.x (2.0 drops the plugins and
  themes Material needs). `ruff.toml` selects only `E4`, `E7`, `E9`, `F` (real errors, not style), listed
  explicitly so ruff upgrades don't change them; there is no formatter, and lines run to about 120 characters.
