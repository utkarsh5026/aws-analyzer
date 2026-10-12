# Plan: one shared code base for every analyzer

**Status:** Phase 0 (`snapshot.py`) and Phase 1 (the move to `src/aws_analyzer/`) merged in #61, Phase 2 (`_kit`
with the leaf helpers, install-only docs) in #62, Phase 3 (one renderer in `_kit`) in #63 and Phase 4 (base View and
base Analyzer) in #64, released in 0.14.0, Phase 5 (`_bedrock.py`) in #69 and Phase 6 (S3's renderer for every
service) in #70. Phase 7 (the window kit) is done on the branch `claude/practical-curie-63dsws`, for the next pull
request; Phase 8 (cleanup) follows. The last pull request deletes this file.

**Decisions confirmed:** D1 `src/aws_analyzer/`, one pull request per phase, D5 per-service CSS roots kept.

**Decision already made:** `pip install aws-analyzer` becomes the way to use the project. The analyzers stop being
standalone files and import their shared code from one place. There is no code generator.

Numbers below are from `main` at `3a2d534` (v0.13.0, with the Lambda explorer), measured in Phase 0.

---

## 1. Why, in numbers

- 7 analyzers plus the `s3_explorer.py` companion add up to 58,881 lines.
- 50 helpers are copied into all 7 analyzers. Each copy is about 770 lines, so about 5,400 lines are repeats.
- `bedrock_kb.py` and `bedrock_chat.py` also share 103 more names, about 1,600 lines.
- `drift.py` finds 201 names defined in more than one analyzer. 155 are identical, 40 differ in code and 6 differ
  only in docstrings.
- Improvements stay in one file. S3's tables sort, filter and choose columns (its `_CSS` is 200 lines, 54
  elsewhere), and no other service has that. Only 2 of the 56 commits to `analyzers/` changed 4 or more files.
- The four windows (S3 explorer, chat, KB explorer and now the Lambda explorer) each have their own picker,
  background runner, error guard and row button. The Lambda explorer copied about 440 lines from the KB explorer
  (`_EXPLORER_CSS`, `_explorer_rules`, `_window_text`, `_for_window`, `_window_errors`, `explore`, ...), shares
  `_cell_number`, `_class_if`, `_css_height`, `_marked`, `_running_loop` and `search_rank` with both Bedrock files,
  and `_python_html` / `_json_source_html` with the chat. The four window classes share 39 method names
  (`_later`, `_safely`, `_set`, `_renew`, `_draw_rows`, ...), and only 5 of them are identical; `drift.py` doesn't
  compare window classes, so it sees none of this.

**What success looks like:** a change to reports, tables, findings, progress, `help()` or a window primitive is
one edit in one file, and every service gets it. A new service subclasses two base classes instead of pasting
about 800 lines.

## 2. Decisions to confirm before Phase 0

| # | Decision | Recommendation | Why |
|---|---|---|---|
| D1 | Where the code lives | Move `analyzers/*.py` to `src/aws_analyzer/` with `git mv` | A normal package: relative imports just work, no `force-include` mapping, and tests import what users import. A pure rename keeps `git log --follow` and blame. |
| D2 | One import line for everyone | `from aws_analyzer import S3View` | The same line works for pip, a wheel copied from S3, or the `aws_analyzer/` folder uploaded next to the notebook. `from s3 import S3View` goes away. |
| D3 | Name of the shared package | `aws_analyzer/_kit/` (private) | Helpers keep their names and are imported into each service, so `aws_analyzer.s3.human_size` keeps working. |
| D4 | Bedrock's shared domain code | `aws_analyzer/_bedrock.py` | It's Bedrock logic (models, prices, markdown, filters), not UI, so it stays out of `_kit`. |
| D5 | CSS root class per service (`.s3a`, `.ddb`, `.kba`, `.kbc`, `.smk`, `.osv`, `.lmb`) | Keep them; the shared CSS takes the root as a parameter | The HTML stays byte-identical, and the window CSS that targets these roots needs no edits. |
| D6 | Services importing each other | Still forbidden. Services import only `_kit` and `_bedrock`; `s3_explorer` may import `s3` (the one companion) | Importing `aws_analyzer.dynamodb` must not load S3's 12,000 lines. Each service stays readable on its own. |
| D7 | Releases | Each phase is a separate pull request that changes no behaviour, so a release can go out between any two. The first release containing Phase 2 carries the **Breaking** note for file users. | pip users see no API change. People using copied files break at Phase 2 whether or not a release goes out. |
| D8 | Offline / VPC-only notebooks | Install the wheel from S3: `aws s3 cp s3://bucket/tools/aws_analyzer-X-py3-none-any.whl . && pip install --no-deps aws_analyzer-X-py3-none-any.whl`. Optionally attach the wheel to each GitHub release. | This replaces "copy s3.py from S3". boto3 is already on SageMaker images, hence `--no-deps`. |

## 3. Target layout

```
src/aws_analyzer/
  __init__.py          lazy exports, unchanged API (S3View, DynamoDBView, chat, KBExplorer, ...)
  _kit/                shared by every service. Top-level imports: stdlib + boto3/botocore only. Never imports a service.
    fmt.py             human_size, human_money, human_age, _plural, _fmt_dt, _utcnow, parse_size, parse_time, _as_int, ...
    deps.py            _require, _in_notebook
    errors.py          _error_code, _error_name, _why, _Hint
    blocks.py          _Title, _Cards, _Tone, _Table, _Note, _Text, _Findings, _Next, plus the block/cell registries
    render.py          base CSS (templated on the root class), _prose, _call, _signature, _render_html, _render_text
    progress.py        _progress_bar_class, _progress_bar, _progress_text
    view.py            _BaseView: _show, help, _progress, _price_basis common part; _commands(cls), _friendly_errors
    analyzer.py        _BaseAnalyzer: session/region/profile/prices, _cached_client, _paginate, _map
    window.py          Phase 7: the windows' helpers, _Window, _Explorer, @_window_errors, _Picker, _row_button
    highlight.py       Phase 7: _python_html, _json_source_html (the chat's code to copy, the Lambda Code tab)
  _bedrock.py          shared by bedrock_kb and bedrock_chat
  s3.py  s3_explorer.py  dynamodb.py  bedrock_kb.py  bedrock_chat.py  sagemaker_env.py  opensearch.py  lambda_functions.py
```

The table above is a guide. In each phase, a helper goes where its own imports say it belongs.

**Each service keeps** its five numbered sections: helpers that only it uses, data models, pure analysis and
findings, the `<Service>Analyzer`, and the `<Service>View` commands, plus any block types only it draws (`_Passage`,
`_Pages`, `_Steps`, ...). It sets class attributes on its View instead of editing shared code: `_ROOT`, `_BADGE`,
`_GROUPS`, `_START`, `_EXTRA_CSS`, `_DATA_ERRORS`, progress labels.

## 4. What moves, and what doesn't

**Moves:** a name moves only when its copies are identical once the service names are normalized (what `drift.py`
already computes), or when the only difference can be written as a parameter or a hook.

**Never merged just because the names match.** These share a name but do different things, so they stay in their
services:

- `IndexInfo` (DynamoDB vs OpenSearch)
- `Overview` (Lambda vs OpenSearch)
- `INSTANCE_TYPES` (OpenSearch's instance types vs SageMaker's)
- `build_filter` / `describe_filter` (DynamoDB, OpenSearch and Bedrock filter languages)
- `_SECTIONS`, `_section`, `Analyzer._service`, `View._explain`, `View._settings_rows`, `_number`
- in the Bedrock pair, `Answer`, `DEFAULT_PROMPT`, `parse_rag`, `describe_filter`, `answer_findings`,
  `_question_text` and `View._answer_blocks` (Phase 5): the knowledge base's answers also come from Converse, with
  its own prompt, and its `describe_filter` reads `where=`; the chat's keep their request, response and settings,
  and its `describe_filter` reads a raw RetrievalFilter

**Drifted copies** (30 today) are sorted the `/sync-helpers` way before moving. If one copy has a fix the other
lacks, port the fix first in its own commit; if its output changes, the snapshot diff shows it and it gets a
CHANGELOG "Fixed" line. If the difference is intentional, it becomes a parameter. If it's unclear, ask.

## 5. Guardrails (in place from the phase that needs them, kept afterwards)

1. **Output snapshots** (new in Phase 0, `.claude/skills/check/snapshot.py`). For every service, it runs the demo
   tour from `/demo` plus `help()`, in text mode and in HTML mode, on demo.py's seeded data. It normalizes what
   changes from run to run (durations, rates, today's timestamps, moto's random version and request IDs) and writes
   one file per command. `--compare <dir>` prints the diffs. The baseline is taken from `main` in a `git worktree`.
   **Every phase except Phase 6 must produce byte-identical snapshots.** An unexpected diff stops the work: find
   the cause, never refresh the baseline to make it pass.
2. **Public names.** A dump of every name each module exposes (`dir(module)`, with and without `_`), taken before
   the work. After each phase, every name that existed is still importable from the same module.
3. **AWS calls.** `rules.py --apis` output is identical before and after. Clients are still created with the
   service name spelled out, in the service file (`self.session.client("lambda", ...)` passed to the base's
   `_cached_client(name, make)`), so `rules.py` keeps finding every operation and the README IAM list stays right.
4. **Independence test** (`tests/test_package.py`): in a fresh subprocess, importing `aws_analyzer.<service>` loads
   no other service module (except `s3` for `s3_explorer`). `_kit` never imports a service.
5. **No re-copying** (new `rules.py` error): a service module may not define a top-level name that `_kit` (or, for
   the Bedrock pair, `_bedrock`: `SHARED` in `rules.py`) defines. This replaces `drift.py` for everything that has
   moved. The one exception is S3's own renderer until Phase 6 (`OWN_RENDERER`), which `drift.py` compares with
   `_kit`'s.
6. **One copy of each module** (conftest): the test session fails if a top-level `s3`, `dynamodb`, ... module is in
   `sys.modules` beside `aws_analyzer.s3`. Two copies would make `isinstance` checks fail between them.
7. **Only boto3 at import time**, as now: CI imports every module, `_kit` included, with only boto3 installed.

**Definition of done for every pull request:** `ruff check .`; `python -m pytest` (CI runs 3.10–3.14);
`rules.py` passes with `--apis` unchanged; the package builds, passes `twine check --strict` and imports with
only boto3; snapshots and public names are unchanged; `mkdocs build --strict` passes; CHANGELOG has an entry if
a user would notice; CLAUDE.md and the skills describe what the pull request changed.

## 6. Phases

Each phase is one pull request, or a few. Each one reverts cleanly. Within a phase, convert one service per
commit and run the tests after each.

### Phase 0: baseline (no change to `analyzers/`)

- Start the branch fresh from `main` once the Lambda window has merged.
- Run `drift.py` again, and compare the windows (now four) primitive by primitive, to update section 1 and the
  Phase 7 list.
- Add `snapshot.py` (guardrail 1) and the public-names dump (guardrail 2). Record the baselines from `main`.
- Confirm D1–D8 with the user.

### Phase 1: move to a package (renames and paths only)

- `git mv analyzers/*.py src/aws_analyzer/`, in a commit with no content changes, so git records 100% renames.
- `pyproject.toml`:
  - drop `force-include` and `/analyzers/*.py` from the sdist
  - add `[tool.pytest.ini_options] pythonpath = ["src"]`
  - update the comments
- Tests: `import s3 as s3mod` becomes `from aws_analyzer import s3 as s3mod`, and `from s3 import (...)` becomes
  `from aws_analyzer.s3 import (...)`. conftest drops the `analyzers/` path and adds guardrail 6.
  `test_wheel_ships_every_analyzer` checks the package folder instead of `force-include`.
- `mypy.ini`: set `mypy_path = src` and drop the `aws_analyzer.*` ignore. In `__init__.py`, drop the pyright
  comment.
- Point these at `src/aws_analyzer/` and the package imports:
  - CI's "import each analyzer on its own" step: each module, with only boto3, `PYTHONPATH=src`
  - `run.py`'s imports step
  - `rules.py`'s `ANALYZERS` (skip `__init__.py`), `drift.py` and `release.py`'s package paths
  - `demo.py`, `shots.py`, and the three JupyterLab shot scripts (each writes a `sys.path` line into the kernel)
- README and docs: update links from `analyzers/x.py` to `src/aws_analyzer/x.py`. The files still work alone in
  this phase, so the install text stays true. Update CLAUDE.md's paths.
- Gates: everything in section 5. Snapshots are identical by construction.

### Phase 2: `_kit` foundation (leaf helpers) and the switch to install-only

Done: `_kit/fmt.py`, `text.py` (`_esc`, `_clip`, `_pad`, `_width`, `_text_bar`), `deps.py` and `errors.py`, 31 names
in all; every copy was checked to be the same code before it was removed. Dead private copies were dropped rather
than imported (`bedrock_chat._fmt_dt`, three `_share`, `sagemaker_env._count`, `lambda_functions._error_name`, and the
regexes only the moved helpers used), and `s3_explorer`'s own `_fmt_dt`, which differs, became `_tip_time`.

- Create `_kit/__init__.py`, `fmt.py`, `deps.py` and `errors.py` (only `_error_code`, `_error_name`, `_why`, `_Hint`).
  Move the identical leaf helpers: formatting, `_require` / `_in_notebook`, `_esc`, `_clip`, `_pad`, `_width`,
  `_plural`, `parse_size` / `parse_time` and the partly shared ones (`_as_int`, `_as_count`, `_count`, `_share`,
  `HOURS_PER_MONTH`, ...). Each service deletes its copy and imports the names it uses (only those, for ruff F401).
- Add `rules.py` guardrail 5, and import rules: a service imports only `._kit.*`, `._bedrock` and its companion
  parent; `_kit` imports no service. `_kit` files skip the banner and View checks but get the import, `print()`
  and read-only checks.
- Tests that patch a moved helper on a service module now patch it where it's looked up: `_require` and
  `_in_notebook` here; `_progress_bar_class` in Phase 4; `_cell_number` (14 places) in Phase 7. Run
  `grep -n 'setattr(<mod>, "<name>"'` for every moved name.
- **This pull request also rewrites the install documentation**, because it's the first one where a file no
  longer works alone:
  - README "Get started" (pip, the wheel from S3 for VPC-only, or upload the `aws_analyzer/` folder), the
    "One file, boto3 only" card, the Services table's File column, and every `from s3 import ...` line
  - each guide's setup steps in `docs/*.md`, and `s3_explorer.md`'s "put both files"
  - CONTRIBUTING.md, the `pyproject.toml` comments and the `__init__.py` docstring
  - CLAUDE.md's Hard constraints and "What this is"
  - CHANGELOG, under Unreleased, a **Breaking** entry: install with pip and import from `aws_analyzer`. Copied
    0.12.0 files keep working as they are.
- Gates: snapshots identical.

### Phase 3: one renderer, extended per service

Done: every service but S3 draws its blocks through one registry, and the shared blocks (`_kit/blocks.py`: the
eight blocks, `_Report`, `_HTML` / `_TEXT` / `_CELLS`, `@_draws`) and renderer (`_kit/render.py`: `_base_css`,
`_prose`, `_call`, `_signature`, the shared blocks' drawers, `_report_html`, `_render_text`, `_blocks_html`) live
in `_kit`. A drawing function takes the block and the `_Report` being drawn (the view's `max_rows` and the badge);
each service's `_render_html` is `_report_html(blocks, max_rows, _CSS, _ROOT, _BADGE)`. The chat's stylesheet
isn't the shared rules plus its own: it restyles eleven of them and adds one in the middle, to match its window,
so `_base_css` also takes `theme=`, `restyle=` and `after=`, and a restyle that stops matching exactly one shared
rule raises. S3 imports the blocks and helpers it shared unchanged and keeps the rest of its renderer
(`OWN_RENDERER` in rules.py). A service imports only the renderer names it uses, so 56 private names it no
longer used are importable from `_kit.render` only (`_counts`, `_ordered`, `_tone`, `_visible_rows`, `_hidden`,
`_NUMERIC_RE`, `_CALL_RE`, `_MARKS`, `_SELECT`, `_TONES`, `_prose`, and the chat's `_cards_html` /
`_findings_html`, which `_blocks_html` replaced); `snapshot.py compare` lists them as lost. New: a block nothing
draws shows as a note (`tests/test_kit.py`), the window tests check that each window holds the report CSS once, and
snapshot.py normalizes download_zip's Free disk card.

1. **Inside each service, before anything moves**, replace the `isinstance` chains in `_render_html` /
   `_render_text` with registries:
   - `_HTML[type] = fn` / `_TEXT[type] = fn`, filled by a decorator (`@_draws(_Passage)`)
   - a cell registry for `_Tone` and `_Link` cells
   - a block type with no renderer becomes a note, never a traceback

   After this step the shared part of the renderer is identical in all services. Snapshots identical.
2. Split each `_CSS` into the common rules (identical once `.<root>` is normalized) and `_EXTRA_CSS`. The common
   rules become `_base_css(root, extra)`. Three windows strip the report CSS from `_render_html`'s output by exact string:
   `KBExplorer._html` and `LambdaExplorer._html` (their hidden style widget holds `_CSS + _EXPLORER_CSS` once, and
   each pane drops its copy with `.replace(_CSS, "", 1)`), and the chat window's Test tab (`.removeprefix(_CSS)`).
   Both calls do nothing, silently, when the string doesn't match, and every pane would then carry its own copy of
   the stylesheet. So each service keeps `_CSS` as one module-level string, exactly what its `_render_html`
   writes, and a test checks that those panes come out without it: the snapshots cover reports, not windows, and
   no window test checks it today. (`s3_explorer` doesn't strip it: each of its panes carries the report's own
   `<style>`, and S3's renderer doesn't move in this phase.)
3. Move the blocks, the registries, `_prose`, `_call`, `_signature`, `_CALL_RE`, `_MARKS`, `_SELECT`, the findings
   HTML and both renderers into `_kit/blocks.py` and `_kit/render.py`. Service-only blocks stay in their service
   and register themselves when it's imported.
4. **S3 keeps its own, more advanced renderer in this phase** (sort, filter, columns, path columns). It's the one
   listed exception until Phase 6.

- Gates: snapshots identical.

### Phase 4: base View and base Analyzer

Done: every `<Service>View` subclasses `_BaseView` (`_kit/view.py`) and every `<Service>Analyzer` `_BaseAnalyzer`
(`_kit/analyzer.py`); the progress helpers are in `_kit/progress.py`. One commit per service, and all 303 snapshot
outputs, the AWS operations and every error path (a script raising each kind of error through each service's
decorator, on main and on the branch) came out the same. What differs from the plan below:

- `_explain(code, message)` keeps its two arguments: the windows and the chat's batch findings call it too
  (`explain=self._explain`). The decorator asks `_explain_call(code, message, command, args, kwargs)`, which is
  `_explain`'s unless a View needs the command's arguments: DynamoDB's names the table and the closest names. S3's
  "object not found" is its `_explain`.
- `_extra_note(exc, command)` returns a `_Note` or None; anything that isn't a `ClientError`, `_Hint`, `ImportError`
  or one of `BotoCoreError` + `_DATA_ERRORS` goes to it, and still raises when it returns None.
- `_DATA_ERRORS` is `(ValueError, TypeError)` in the base, and S3's own tuple (decoding errors, no `TypeError`), as
  its decorator caught before. S3 and DynamoDB gain `_Hint` as a plain note; neither raises one.
- S3 draws with its own renderer until Phase 6, which doesn't know `_kit`'s `_Table`: so the View's renderers are
  class attributes (`_render_html = staticmethod(_render_html)` in every service, `_render_text` and `_TABLE` in
  S3), and help() builds its tables with `self._TABLE`. Phase 6 drops S3's three.
- `_PROGRESS = ("Reading", "items read")` is the base's progress label and unit; S3's is `("Scanning", "objects")`.
- `_BaseView.__init__` takes only `mode`, `max_rows` and `progress` (checked first, as before); each View then sets
  `self.core: <Service>Analyzer = core or <Service>Analyzer()`, so editors keep the analyzer's type.
- `_BaseAnalyzer` takes `session`, `region`, `profile` and `prices`, with the service's table as `_LIST_PRICES`
  (which `_price_basis` compares with). It also gives `region` (S3Analyzer gains it), `_paginate`, `_map` and
  `_cached_client`; `max_workers`, `_clients` and `_lock` are set by the services that use them, since Lambda keys
  its clients by region and S3 and DynamoDB keep theirs in attributes of their own.
- Shared state: nothing writes to a module's price table, and the analyzer copies it; `test_kit.py` checks that
  `prices=` (and Bedrock's `model_prices=`) given to one analyzer changes neither the table nor another's prices.
- The S3 explorer's own decorator (it shows an error in the explorer) is now `_window_errors`, the name the other
  windows use, since `_friendly_errors` is `_kit`'s; `rules.py` accepts it on a companion window's commands. The
  explorer takes `_in_notebook` from `_kit.deps`, which `s3.py` no longer imports.
- demo.py, shots.py and snapshot.py pick the View and Analyzer a module defines, not `_BaseView` / `_BaseAnalyzer`.
- Tests: the five `_progress_bar_class` patches and the chat's `_in_notebook` one patch `_kit.view`, the seven help
  tests compare `_commands(<Service>View)` with `_GROUPS`, and tests that drew text with `<service>._render_text`
  use `_kit.render`'s (S3 keeps its own).
- Lost names (`snapshot.py compare` reports them): the 21 progress helpers each service imported
  (`<service>._progress_bar_class`, `_progress_bar`, `_progress_text`), importable from `_kit.progress` only, and
  `s3_explorer._friendly_errors`.

As planned:

- `_kit/view.py`, `_BaseView`:
  - the common part of `__init__` (mode, max_rows, progress), `_show` / display, `_progress` and its helpers,
    and the common part of `_price_basis`
  - `help()` lists commands with `_commands(cls)`, which walks the MRO and leaves out `_BaseView`'s own methods
    except `help`. Today `help()` and the `test_ui_help_groups_every_command` tests read `vars(type(self))`, which
    would miss `help` once it moves to the base. The tests switch to `_commands` too.
  - The base defines no public method other than `help`.
- `_friendly_errors` becomes one decorator with hooks, replacing today's seven variants:
  - `self._explain(code, message, method, args, kwargs)`: S3 maps 404s to "object not found", DynamoDB adds
    `_not_found`'s close names, the base returns the message
  - `_DATA_ERRORS` as a class attribute (S3's extra decoding errors)
  - `self._extra_note(exc, name)` for OpenSearch's `OpenSearchError` and its notes on unexpected response shapes
  - `_Hint` is handled for every service (S3 and DynamoDB gain it, which is harmless)
- `_kit/analyzer.py`, `_BaseAnalyzer`: session, region, profile and prices; `_cached_client(name, make)`;
  `_paginate`; `_map`. Each service keeps its own `__init__` signature and calls `super().__init__`.
- **Shared state audit.** Module-level price tables and caches are now one object reached by several services.
  Check that `prices=` / `model_prices=` never mutate a module dict (copy into the instance), and add a test
  that one analyzer's overrides don't change another's.
- Gates: snapshots identical, and `--apis` unchanged.

### Phase 5: `_bedrock.py`

Done: `src/aws_analyzer/_bedrock.py` holds the 92 names `bedrock_kb` and `bedrock_chat` defined identically, moved
unchanged in `bedrock_kb`'s order (one commit per service): the price tables, `DEFAULT_MODEL` and the model helpers,
`Passage`, `Citation`, `KBDocument` and `ModelInfo` with their `parse_*` readers, the filter helpers, the token and
cost estimates, the question's words and snippets, `_Markdown`, and the `_Answer` and `_Json` blocks with their
drawings. All 313 snapshot outputs and the AWS operations came out the same. What differs from the plan below:

- 92 names, not 97. By the plan's rule it was 98: `drift.py` didn't see annotated assignments, so it never compared
  the three price tables (it does now; they matched). The six window helpers `lambda_functions` has identical copies
  of too (`search_rank`, `_marked`, `_cell_number`, `_class_if`, `_css_height`, `_running_loop`) aren't Bedrock's:
  they stay in the three files for Phase 7, so the 15 `_cell_number` test patches stay put. Nothing that moved uses
  them.
- The "6 that differ" were 7 (`describe_filter` too), and none was drift: each file has its own thing under the
  name, so they stay, listed in section 4.
- Identical but staying: `_render_html` (it reads its own module's `_CSS`, `_ROOT` and `_BADGE`), the View's
  `_price_basis`, `_profile_for`, `_source_names` and `_top_source`, and the Analyzer's `_bedrock_client`,
  `_runtime_client` and `_kb_summaries` (the client methods keep `session.client("bedrock-runtime", ...)` in the
  service, for `rules.py`).
- Tooling first: `rules.py`'s `SHARED` (`_bedrock` is checked like `_kit`, only the pair may import it, it imports no
  analyzer, and the pair may not define its names again), `drift.py` (leaves `_bedrock.py` out of the analyzers,
  lists a re-copied name next to it, compares annotated tables, and takes a service's price table from its
  Analyzer's `_LIST_PRICES`, since every table is annotated) and `test_package.py` (the pair may load `_bedrock`).
- Names: each imports what it uses, and the public names it doesn't use as `X as X` (`bedrock_kb`:
  `DATA_SOURCE_KEY`, `GLOBAL_MODEL_PRICES`, `data_source_filter`, `model_price`, `split_metadata`; `bedrock_chat` the
  same but `DATA_SOURCE_KEY`, which it uses), so every public name stays where it was. 43 private names of
  `bedrock_kb` and 41 of `bedrock_chat` that they no longer use (`_Markdown`, the `_md_*` helpers, the `_MD_*`
  patterns, ...) are importable from `_bedrock` only; `snapshot.py compare` lists them as lost.
- The price tables are one dict for both modules now. Nothing writes to them (`prices=` and `model_prices=` are
  copied per analyzer, which `test_kit.py` checks since Phase 4).
- `snapshot.py` gained five cases for what no case used: the knowledge base's search with `where=` and with
  `data_source=`, ask with `engine="converse"`, and the chat's ask with one data source and with a filter setting.

As planned:

- Move the 97 names `bedrock_kb` and `bedrock_chat` share whose code matches (`_highlight` differs only in its
  docstring), including the price tables, `DEFAULT_MODEL`,
  the model helpers, `_Markdown`, `_Json` / `_json_html`, the filter helpers, `parse_*`, `search_rank` and `_marked`
  (`Analyzer._cached_client` went to `_BaseAnalyzer` in Phase 4).
- Resolve the 6 that differ (`Answer`, `DEFAULT_PROMPT`, `View._answer_blocks`, `_question_text`,
  `answer_findings`, `parse_rag`) by the section 4 rule. Find which copy is newer with `git log -L`.
- Gates: snapshots identical, except any drift fix ported on purpose (listed in the pull request and the
  CHANGELOG).

### Phase 6: S3's renderer becomes everyone's (the one visible change)

Done: `_kit/render.py`'s stylesheet, `_prose`, notes, findings and tables are S3's, so every service's tables sort,
filter and choose columns and every finding reads as a headline over points. `s3.py` has `_ROOT = "s3a"`,
`_EXTRA_CSS` (its own blocks' rules) and `_CSS = _base_css(_ROOT, _EXTRA_CSS)`, which is byte-identical to the
stylesheet it had; its `_Frame`, `_Image`, `_Link`, `_Media`, `_Pages`, `_Flow` and `_JsonTree` register their
drawings, and `OWN_RENDERER` is gone from `rules.py` (and `_TABLE` from `_BaseView`). Of the 312 snapshot outputs,
every S3 output came out the same, HTML too; the other services' HTML changed (110 files), and their text only where
findings were reworded (7 files; SageMaker's `instance` also lists whatever is installed, so it moves with the
environment). What differs from the plan below:

- `_Report` carries the root class, the report's blocks, `rules` (a table's filter and column rules, written in a
  `<style>` after the stylesheet) and `shared(key, make)` (S3's `_Zoom`). The explorer windows' panes and the chat's
  tabs keep that small `<style>`: the window tests now check a pane holds only those rules, not the report CSS.
- `file_extension` moved to `_kit/fmt.py` (`_file_icon` needs it; `aws_analyzer.s3.file_extension` still works), and
  `s3_explorer` takes `_file_icon` from `_kit.render` (`entry_icon`'s `s3=` is no longer needed).
- No table needed `sortable=False`: none has rows that depend on the one above, and S3 never opted one out.
- Text output changed only where a finding read wrong as a headline, which `_split_lead` makes of its first clause:
  DynamoDB's idle capacity, Lambda's most common error (`KeyError: 'x'` split it), OpenSearch's k-NN memory and the
  chat's questions no setup answered (no headline at all) were reworded. `_capitalized` no longer capitalizes a detail that starts with a command
  (`aws bedrock-agent ...` became `Aws ...`).
- Class names the shared rules use clashed with three windows' own: the chat's header `.hd` (now `.whd`), the Lambda
  chart's `.cols` (now `.colbars`) and the Lambda explorer's code tree `.tl` (now `.ftl`); the chat's setting docs
  pin their margin against the findings' `.fd`. The chat restyles `.chip`, `.chip.info` and `.fi` to its theme, and
  both explorers round `.fi` like their notes.
- The figures were remade with this container's fontconfig minus `56-prefer-inter.conf` (it maps the system fonts to
  Inter); with it, the text is wider than in the existing figures and Lambda's 12-column table no longer fits.

- Port S3's `_table_html` (sort, filter, the Columns menu, `path_cols`, `sortable=`), `_split_lead`,
  `_finding_html`, `_lead` and the text renderer's path handling into `_kit`. Delete S3's own copy. Mark tables
  that shouldn't sort `sortable=False`.
- With Phase 3's registry, that means: `_kit`'s `_Table`, `_prose`, `_findings_html`, `_table_html` and
  `_table_lines` become S3's; what a report's blocks share goes on `_Report` (the table rules S3's renderer collects
  and inserts after the stylesheet, the PDF pages' `_Zoom`); S3's own blocks (`_Frame`, `_Image`, `_Link`,
  `_Media`, `_Pages`, `_Flow`, `_JsonTree`) register their drawings with `@_draws`; S3 gets `_ROOT` and
  `_base_css`; and `OWN_RENDERER` in rules.py empties. S3's `_CSS` restyles some shared rules (`.sub`, the table
  header, `.fh`): they become the shared ones, and the chat's `restyle=` is checked against them.
- Review every snapshot diff. HTML is expected to change in every service; text should change only in finding and
  note lead-ins. Update HTML assertions one at a time; never regenerate them in bulk.
- Remake the screenshots (`shots.py`, plus the window shot scripts where a window shows reports). Check captions
  and alt text in the guides and in the README's twelve `<picture>`s.
- CHANGELOG "Changed": tables in every report sort, filter and choose columns, as S3's already do.

### Phase 7: window kit (four windows: S3 explorer, chat, KB explorer, Lambda)

Done: `_kit/window.py` holds what the four windows share, `_kit/highlight.py` the code colouring the chat and the
Lambda explorer both had, and `_kit/text.py` gained `search_rank` and `_marked`. One commit per primitive and window,
with the window tests passing after each. Two new comparisons, run on `main` and on the branch, came out the same:
every window's whole widget tree (each widget's type, CSS classes, layout, text, tooltips and HTML, the stylesheets
included) at 43 points of clicking and typing through the four windows on demo.py's data, and every kind of error
through every window's guards (callbacks, commands with and without the window, background jobs, Enter). The report
snapshots and the AWS operations are unchanged. What differs from the plan below:

- One module, `_kit/window.py`, not a `widgets/` package. Its parts:
  - the helpers: `_css_height`, `_running_loop`, `_cell_number`, `_class_if`, `_skeleton`, `_for_window` (it takes
    the window's words, its `_window_text`), `_icon_url` (the one SVG template all four windows used) and
    `_row_button` (the nine full-width lines: rows, run rows, picker fields and their lines)
  - `_Window`, the base of `_ChatApp`, `KBExplorer` and `LambdaExplorer`: `_safely`, `_on_enter`, `_error_text`,
    `_quietly`, `_set` and `_close_lists`, with the hooks `_warn`, `_explain` and `_reword`
  - `_Explorer(_Window)`, the two explorers' base: their state, `_load_widgets`, `_status` (Lambda's, with `owner`),
    `_finish`, `_tab_bar`, `_frame`, `_draw`, `_workers`, `_later`, `_drop`, `_display` and `_ipython_display_`.
    KB's counting line says `{n}`, as Lambda's do, and comes out the same.
  - `@_window_errors` for every window's commands, through the window's `_ready` and `_command_failed`: the S3
    explorer's decorator becomes those two methods (nothing runs without s3.py; its errors as notes, the same ones)
  - `_Picker`, which the KB explorer's `_Chooser`, the Lambda explorer's `_FunctionField` and the chat's picker
    (renamed `_ChoicePicker`, since the kit's is `_Picker`) subclass: the widgets, opening and closing (one list open
    at a time; the chat's version, which is the same for one picker), `matches()`, pooled lines and Enter (the chat
    keeps its own Enter, for multi). `_FunctionField.box` is now `.field`, as in the other two.
- Not shared, because the copies do different jobs: the S3 explorer's `_guard` (it runs the handler at once, says
  KeyboardInterrupt and mends an unfinished status line), so the S3 explorer isn't a `_Window` and its text boxes'
  Enter stays a one-liner through `_guard`; the chat's test runner (`_start` / `_run_later`, a queue drained on the
  loop as answers come back) and the S3 explorer's report loader (one job number for the pane, a cache, a worker
  that skips a stale job); `_renew` (Lambda's page box, S3's panes, KB's `_renew_pane`); `_CLICK_GRACE` (S3: a click
  on rows that just changed) and `_TOGGLE_GRACE` (Lambda: a double click's second click on a folder); the chat's
  tabs (ipywidgets' `Tab`, `_tab_rules`); and the explorers' `_html`, `_reports`, `_show_tab` and `_tab_line`.
- The explorers' stylesheets aren't merged: 112 of their 218 and 331 rules match once the prefixes are normalized,
  but between each one's own, and CSS order decides which rule wins. One templated explorer stylesheet (like
  `_base_css`) would change the order, so it needs before and after screenshots; it's left for later.
- Test patches: 2 `_cell_number` patches moved to `_kit.window` (the explorers' once-per-cell display moved); the
  chat's 13 stay, because the chat checks the cell in its View's `_ipython_display_`, which stays. The KB test of
  `_for_window` passes `_window_text`. `test_kit.py` checks `@_window_errors` on a toy window (a command that can't
  run, an error the window doesn't say).
- Screenshots weren't remade: the widget-tree comparison covers what the shot scripts would show (the tree, with its
  HTML and stylesheets, is everything the browser draws from), and exactly.
- Lost names (`snapshot.py compare` lists 11, all private): `_JSON_TOKEN_RE` and `_PY_LITERALS` of `bedrock_chat` and
  `lambda_functions` (only the highlighters used them), the `_cell_number`, `_css_height` and `_running_loop` that
  `bedrock_kb` and `lambda_functions` (not `_running_loop`: Live uses it) no longer use since `_Explorer` does, all
  importable from `_kit` only; `S3Explorer._execution` and `_loop` (`_cell_number` and `_running_loop` now); and
  `bedrock_chat._Picker`'s own methods, since that name is the kit's base now and the chat's is `_ChoicePicker`.

As planned:

- Inventory first: put each primitive's four versions side by side and choose the most complete one.
- Primitives, in order of risk:
  1. small helpers: `_css_height`, `_running_loop`, `_cell_number`, `_class_if`, `search_rank` and `_marked`
     (identical in `bedrock_kb`, `bedrock_chat` and `lambda_functions`; left out of `_bedrock.py` in Phase 5),
     once-per-cell `_ipython_display_`
  2. the callback guard (`_safely` / `_guard`, plus `_window_errors`) and `_set`, which only sends HTML that changed
  3. background work (`_later`, `_workers` / `_threads`, job keys, `_tasks`): every window runs AWS reads on the
     loop's executor and drops stale results
  4. the style widget, icon masks (`_ICON_PATHS`, `_tab_rules`, `_explorer_rules`) and the tab bar
  5. full-row buttons (`_Row`, `_FileRow`), `_renew`, `_CLICK_GRACE`, and Enter handled through the text box's
     `submit` message
  6. the searchable picker with its backdrop (`_Picker` + `_Choice`, `_Chooser`)
- Use one pull request per one or two primitives, each changing no behaviour. Gates: the window tests, which
  click and type through the widgets (including their background paths inside `asyncio.run`), plus before and
  after screenshots from `explorer_shots.py`, `chat_shots.py`, `kb_explorer_shots.py` and the Lambda window's
  shot script.
- Move test patches to the kit (`_cell_number`: 14 places).

### Phase 8: cleanup

- Retire `drift.py` and `/sync-helpers`, and remove `run.py`'s drift step. Guardrail 5 now does that job.
- Update the skills:
  - `/new-analyzer`: subclass `_BaseAnalyzer` / `_BaseView`, register blocks, add the module to `__init__.py`
  - `/add-command`, `/analyzer-review` (the new constraints), `/check`, `/demo` and `/release`
- A final pass over CLAUDE.md: "Hard constraints", "Architecture of an analyzer file" and "How the View layer works".
- Delete this file.

## 7. Risks

| Risk | Where it bites | How it's handled |
|---|---|---|
| A test patches a helper on the service module, but the code now calls the kit's copy, so the patch does nothing | `_cell_number` ×14, `_progress_bar_class` ×2, `_require`, `_in_notebook` | Patch where the name is looked up; grep checklist in every phase |
| `help()` loses `help` when it moves to the base | `vars(type(self))` in `help()` and in the 7 group tests | `_commands(cls)` walks the MRO; tests use it |
| Merging two things that only share a name | `IndexInfo`, `Overview`, `build_filter`, ... | Section 4 rule: identical after normalizing, or a parameter |
| Two copies of a module break `isinstance` | Tests or demo scripts importing both `s3` and `aws_analyzer.s3` | Guardrail 6; every import path goes through the package |
| Shared price tables or caches leak between services | `prices=`, `model_prices=`, module-level caches | Phase 4 audit and test |
| Importing one service loads all of them | A kit module importing a service | Guardrail 4; `_kit` is a leaf |
| `rules.py` stops seeing AWS calls | Moving client creation into the base | Literal `session.client("...")` stays in services; `--apis` gate |
| HTML or CSS shifts, breaking window CSS or old notebook outputs | Root class renames, CSS splits | Roots kept (D5); byte-identical snapshots; Phase 6 is the only visual change |
| `KBExplorer`, `LambdaExplorer` and the chat's Test tab strip the embedded report CSS by string | Phase 3 CSS split | `_CSS` stays the exact string `_render_html` writes; a test checks that their panes come out without it (the snapshots cover reports only) |
| Copied-file users break | Phase 2 | **Breaking** note; docs in the same pull request; 0.12.0 files keep working |
| VPC-only notebooks can't reach PyPI | Phase 2 docs | The wheel from S3 (D8) |
| Conflicts with the Lambda window branch | Every service file and the windows | Don't start until it's merged; Phase 0 starts from the new `main` |
| One huge, unreviewable change | Everything | One pull request per phase, one commit per service, each reverts cleanly |

## 8. Files to update (checklist)

- **Code and config:** `pyproject.toml`, `src/aws_analyzer/__init__.py`, `mypy.ini`, `tests/conftest.py`, every
  `tests/test_*.py` import, and `tests/test_package.py`.
- **CI and tooling:** `.github/workflows/ci.yml` (and `release.yml` if the wheel is attached to releases);
  `.claude/skills/check/{run.py,rules.py,SKILL.md}`, `.claude/skills/sync-helpers/*`,
  `.claude/skills/demo/{demo.py,shots.py,explorer_shots.py,chat_shots.py,kb_explorer_shots.py}` and the Lambda
  window's shot script, and `.claude/skills/release/release.py`.
- **Docs:** `README.md` (install, cards, Services table, import lines), `docs/index.md` and every `docs/<service>.md`
  setup section, `CONTRIBUTING.md`, `SECURITY.md` if it mentions files, `.github/pull_request_template.md` and
  the issue templates if they say "file", `CHANGELOG.md`, and `CLAUDE.md`.
- **Skills:** `new-analyzer`, `add-command`, `analyzer-review`, `check`, `demo`, `release`, `sync-helpers`.

## 9. Start trigger: when the Lambda window merges

1. The user says the Lambda window is merged.
2. Restart this branch from the new `main`. It holds only this plan, so carry the plan over.
3. Phase 0: measure again (four windows now), build the snapshots, and confirm D1–D8.
4. Phases 1 to 8 in order, one pull request at a time, each meeting the section 5 definition of done.

A tip for the Lambda window branch (optional): reusing `KBExplorer`'s names and shapes (`_later`, `_safely`,
`_set`, `_renew`, `_Chooser`, `_FileRow`) instead of new variants leaves less to reconcile in Phase 7.
